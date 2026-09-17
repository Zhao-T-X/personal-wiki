"""Step 9.1 — Entity Eligibility Boundary Closure.

Proves the canonical eligibility contract:
  * Semantic Pool (resolution / duplicate / object-linking) = KEEP only.
  * Review Queue = REVIEW + unlabelled legacy rows.
  * Every automatic Entity creation carries an explicit eligibility.
  * A merge/update that omits eligibility never wipes a stored verdict.
  * A rejected claim can never grant KEEP.
"""
import importlib
import json
import sys

import pytest

import claim_entries


@pytest.fixture(scope='module', autouse=True)
def _db(tmp_path_factory):
    claim_entries.prepare_database(tmp_path_factory.mktemp('eligibility-boundary'))
    # integrity / review are not in claim_entries' reload list but hold repositories,
    # so they must be re-bound to the fresh database as well.
    for name in ('app.integrity', 'app.review'):
        mod = sys.modules.get(name)
        if mod is not None:
            importlib.reload(mod)


# --- helpers (lazy imports: modules were reloaded by the fixture) -------------

def _repo():
    from app.repositories import EntityRepository
    return EntityRepository()


def _insert(eid, name, etype='Organization', eligibility='__legacy__'):
    props = {} if eligibility == '__legacy__' else {'eligibility': eligibility}
    _repo().insert(eid, type=etype, types=[etype], name=name, aliases=[],
                   description=None, properties=props)


def _resolution_pool_names():
    return {c['name'] for c in _repo().resolution_candidates(500)}


def _review_queue_names():
    return {c['name'] for c in _repo().candidates(500)}


def _entity_pool_names():
    from app.integrity import entity_pool
    return {e['name'] for e in entity_pool()}


def _scan_pairs():
    from app.resolution import scan_duplicate_entities
    return scan_duplicate_entities(None, pair_limit=60)


def _similar_names(entity_id, limit=5):
    from app.db import connect
    from app.resolution import find_similar_entities
    return {h['name'] for h in find_similar_entities(connect(), entity_id, limit=limit)}


# --- Case 1: KEEP -----------------------------------------------------------

def test_case1_keep_is_semantic_visible_and_review_hidden():
    _insert('k1', 'Apple Inc.', eligibility='keep')
    _insert('k2', 'Apple Computer', eligibility='keep')
    assert {'Apple Inc.', 'Apple Computer'} <= _resolution_pool_names()
    assert {'Apple Inc.', 'Apple Computer'} <= _entity_pool_names()
    assert 'Apple Computer' in _similar_names('k1')
    assert any({p['entity_a']['id'], p['entity_b']['id']} == {'k1', 'k2'} for p in _scan_pairs())
    assert 'Apple Inc.' not in _review_queue_names()


# --- Case 2: REVIEW ---------------------------------------------------------

def test_case2_review_is_semantic_hidden_and_in_review_queue():
    _insert('r1', 'Beta Corp', eligibility='review')
    _insert('r2', 'Beta Corporation', eligibility='review')
    assert 'Beta Corp' not in _resolution_pool_names()
    assert 'Beta Corp' not in _entity_pool_names()
    assert 'Beta Corporation' not in _similar_names('r1')
    assert not any({p['entity_a']['id'], p['entity_b']['id']} == {'r1', 'r2'} for p in _scan_pairs())
    assert {'Beta Corp', 'Beta Corporation'} <= _review_queue_names()


# --- Case 3: legacy (no eligibility) ----------------------------------------

def test_case3_legacy_is_semantic_hidden_and_in_review_queue():
    _insert('l1', 'Gamma Ltd', eligibility='__legacy__')
    assert 'Gamma Ltd' not in _resolution_pool_names()
    assert 'Gamma Ltd' not in _entity_pool_names()
    assert 'Gamma Ltd' in _review_queue_names()


# --- Case 4/6: auto-created fallback entity + accepted-claim KEEP -----------

def _persist(envelope):
    import uuid
    from app import service
    from app.db import transaction
    from app.extraction import normalize_extraction
    from app.knowledge import persist_extraction
    marker = uuid.uuid4().hex[:8]
    content = f'seed {marker}'
    doc = service.create_document(title=f'boundary {marker}', content=content, source_type='note')
    chunk = service.write_chunks(doc, content)[0]
    env = json.loads(json.dumps(envelope))
    for c in env['claims']:
        c['source_chunk'] = chunk['id']
        c['evidence_quote'] = 'seed'
    normalized = normalize_extraction(env)
    with transaction() as conn:
        persist_extraction(conn, document_id=doc, extraction=normalized)
    return doc


def test_case4_fallback_creation_always_carries_eligibility():
    _persist({
        'entities': [{'name': 'Declared Thing', 'types': ['Concept'], 'aliases': []}],
        'claims': [{'subject': 'Undeclared Subject', 'predicate': 'used_for',
                    'object': 'Declared Thing', 'claim_type': 'factual',
                    'polarity': 'positive', 'modality': 'asserted', 'context': {},
                    'confidence': 0.9}],
        'events': [], 'ideas': [], 'questions': [],
    })
    auto = _repo().by_name('Undeclared Subject')
    assert auto is not None, 'fallback subject should have been created'
    props = _repo().get(auto['id'])['properties']
    assert props, 'an Entity must never be persisted with empty properties'
    assert props.get('eligibility') == 'keep'  # supported by an accepted claim


def test_case6_accepted_claim_grants_keep_and_unsupported_gets_review():
    _persist({
        'entities': [{'name': 'Supported Concept', 'types': ['Concept'], 'aliases': []},
                     {'name': 'Orphan Concept', 'types': ['Concept'], 'aliases': []},
                     {'name': 'Another Declared', 'types': ['Concept'], 'aliases': []}],
        'claims': [{'subject': 'Supported Concept', 'predicate': 'used_for',
                    'object': 'Another Declared', 'claim_type': 'factual',
                    'polarity': 'positive', 'modality': 'asserted', 'context': {},
                    'confidence': 0.9}],
        'events': [], 'ideas': [], 'questions': [],
    })
    supported = _repo().by_name('Supported Concept')
    orphan = _repo().by_name('Orphan Concept')
    assert _repo().get(supported['id'])['properties']['eligibility'] == 'keep'
    # Case 5 (rejected/absent support): no accepted claim references it => REVIEW.
    assert _repo().get(orphan['id'])['properties']['eligibility'] == 'review'
    assert 'Orphan Concept' not in _resolution_pool_names()
    assert 'Orphan Concept' in _review_queue_names()


# --- Case 7/8: merge must not wipe eligibility ------------------------------

def test_case7_merge_without_eligibility_preserves_keep():
    _insert('m1', 'Preserve Keep Co', eligibility='keep')
    _repo().merge('m1', types=['Organization'], aliases=['Preserve Keep Co'],
                  description=None, properties={})
    assert _repo().get('m1')['properties'].get('eligibility') == 'keep'


def test_case8_merge_without_eligibility_preserves_review():
    _insert('m2', 'Preserve Review Co', eligibility='review')
    _repo().merge('m2', types=['Organization'], aliases=['Preserve Review Co'],
                  description=None, properties={})
    assert _repo().get('m2')['properties'].get('eligibility') == 'review'


# --- Case 9/10: transitions -------------------------------------------------

def _set_eligibility(eid, value):
    _repo().update(eid, {'properties_json': json.dumps({'eligibility': value})})


def test_case9_review_to_keep_enters_semantic_pool():
    _insert('t1', 'Upgrade Co', eligibility='review')
    assert 'Upgrade Co' not in _resolution_pool_names()
    _set_eligibility('t1', 'keep')
    assert 'Upgrade Co' in _resolution_pool_names()
    assert 'Upgrade Co' in _entity_pool_names()
    assert 'Upgrade Co' not in _review_queue_names()


def test_case10_keep_to_review_leaves_semantic_pool():
    _insert('t2', 'Downgrade Co', eligibility='keep')
    assert 'Downgrade Co' in _resolution_pool_names()
    _set_eligibility('t2', 'review')
    assert 'Downgrade Co' not in _resolution_pool_names()
    assert 'Downgrade Co' not in _entity_pool_names()
    assert 'Downgrade Co' in _review_queue_names()


# --- system-level invariant -------------------------------------------------

def test_semantic_pools_never_contain_ineligible_entities():
    """Every semantic consumer must yield KEEP entities only — REVIEW and legacy
    entities must never appear, so a future consumer that forgets the boundary fails
    here instead of silently leaking."""
    from app.domain.entity_eligibility import is_entity_eligible_for_semantic_pool
    _insert('inv-keep', 'Delta Keep Inc', eligibility='keep')
    _insert('inv-rev', 'Delta Review Inc', eligibility='review')
    _insert('inv-leg', 'Delta Legacy Inc', eligibility='__legacy__')

    for row in _repo().resolution_candidates(500):
        assert is_entity_eligible_for_semantic_pool(row.get('properties')), row
    from app.integrity import entity_pool
    for row in entity_pool():
        # entity_pool only exposes id/name/types/status; re-read properties to assert.
        raw = _repo().get(row['id'])
        assert is_entity_eligible_for_semantic_pool(raw.get('properties')), row

    leaked = {'Delta Review Inc', 'Delta Legacy Inc'}
    assert not (leaked & {p['entity_a']['name'] for p in _scan_pairs()})
    assert not (leaked & {p['entity_b']['name'] for p in _scan_pairs()})
    assert not (leaked & _similar_names('inv-keep'))

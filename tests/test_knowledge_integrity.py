"""Knowledge Integrity: the point is not a clean database, it is a visible one.

Seven acceptance criteria, in the order they matter:

1. duplicate entities are *found*;
2. a merge does not silently change what the wiki asserts;
3. a merge previews its impact before writing anything;
4. a merge rechecks the claims it moved;
5. a conflict the merge created is reported explicitly;
6. disposing of it goes through the machinery that already exists (candidate
   relation → Conflict Center → SUPERSEDE), not through a new one;
7. search reflects the final state immediately.

The case under test is not invented — it is the real state of the wiki this was
written against: 苹果 and 苹果公司 as two entity rows, each holding its own CEO.

The last criterion is the one that decides whether any of this is trustworthy: if
the background is fixed but search still shows the old answer, the fix is worse than
the problem, because the user now believes something that is no longer true.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_RELOAD = (
    'app.repositories.base',
    'app.repositories.document_repo',
    'app.repositories.entity_repo',
    'app.repositories.claim_repo',
    'app.repositories.curation_repo',
    'app.repositories.relation_repo',
    'app.repositories.evidence_repo',
    'app.repositories.event_repo',
    'app.repositories.idea_repo',
    'app.repositories.question_repo',
    'app.repositories.research_repo',
    'app.repositories.run_repo',
    'app.repositories.catalog_repo',
    'app.repositories.operation_repo',
    'app.resolution',
    'app.integrity',
    'app.knowledge',
    'app.claim_relations',
    'app.service',
    'app.retrieval',
)


# --- the detectors are pure logic ---------------------------------------------

def test_name_similarity_offers_what_auto_merge_refuses():
    """The reporting threshold is deliberately looser than the writing one."""
    from app.resolution import name_similarity, types_compatible

    # Containment: 苹果 / 苹果公司 are never auto-merged (that needs 0.93 on
    # normalize_name) but must still be *offered*.
    assert name_similarity('苹果', '苹果公司') == 1.0
    assert name_similarity('Apple Inc.', 'Apple') == 1.0
    assert name_similarity('苹果', '微软') < 0.65
    assert name_similarity('', '苹果') == 0.0
    # Unknown type is not evidence against a pair.
    assert types_compatible(set(), {'Organization'}) is True
    assert types_compatible({'Organization'}, {'Person'}) is False


def test_conflict_pairs_reuse_the_import_verdict():
    """An integrity report must show the conflicts the import path would record."""
    from app.integrity import potential_conflicts

    claims = [
        {'id': 'c1', 'subject_id': 'e1', 'subject_name': '苹果', 'predicate': 'has_ceo',
         'object_text': '约翰·特努斯', 'status': 'verified', 'polarity': 'positive'},
        {'id': 'c2', 'subject_id': 'e1', 'subject_name': '苹果', 'predicate': 'has_ceo',
         'object_text': '蒂姆·库克', 'status': 'verified', 'polarity': 'positive'},
    ]
    conflicts = potential_conflicts(claims)
    assert len(conflicts) == 1
    assert conflicts[0]['functional'] is True
    assert conflicts[0]['objects'] == ['约翰·特努斯', '蒂姆·库克']
    # A superseded claim is history, not a conflict.
    claims[1]['status'] = 'superseded'
    assert potential_conflicts(claims) == []


# --- the workflow, against a database -----------------------------------------

def _reload_db(tmp_path):
    os.environ['DATABASE_PATH'] = str(tmp_path / 'test.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import app.config as config
    import app.db as db
    importlib.reload(config)
    importlib.reload(db)
    for name in _RELOAD:
        module = sys.modules.get(name)
        if module is not None:
            importlib.reload(module)
    db.init_db()
    return db


def _seed(tmp_path):
    """苹果 and 苹果公司 as two rows, each holding its own CEO — the real defect."""
    db = _reload_db(tmp_path)
    from app.ontology import normalize_name
    from app.service import create_document, write_chunks

    documents = {}
    chunks = {}
    for key, (title, text) in {
        'a': ('苹果公告', '苹果 的首席执行官是 约翰·特努斯。'),
        'b': ('苹果公司公告', '苹果公司 的首席执行官是 蒂姆·库克。'),
    }.items():
        doc_id = create_document(title=title, content=text, source_type='note',
                                 source_uri=None, metadata={})
        documents[key] = doc_id
        chunks[key] = write_chunks(doc_id, text)[0]

    entities = {
        '苹果': ('e-apple', 'Organization', ['苹果']),
        '苹果公司': ('e-apple-inc', 'Organization', ['苹果公司']),
        '乔布斯': ('e-jobs', 'Person', ['乔布斯']),
        '约翰·特努斯': ('e-john', 'Person', ['John Ternus']),
    }
    conn = db.connect()
    for name, (eid, etype, aliases) in entities.items():
        conn.execute('INSERT INTO entities(id,type,types_json,name,aliases_json,properties_json,status) '
                     'VALUES(?,?,?,?,?,?,?)',
                     (eid, etype, json.dumps([etype]), name, json.dumps(aliases),
                      '{"eligibility": "keep"}', 'verified'))
        for alias in aliases:
            conn.execute('INSERT OR IGNORE INTO entity_aliases(entity_id,alias,alias_normalized) '
                         'VALUES(?,?,?)', (eid, alias, normalize_name(alias)))

    def claim(key, *, subject, predicate, object_id, object_text, status='verified'):
        cid = str(uuid.uuid4())
        chunk = chunks[key]
        text = f'{subject} {predicate} {object_text or object_id}.'
        conn.execute(
            '''INSERT INTO claims(id,subject_id,predicate,object_id,object_text,content,context_json,
                   claim_type,polarity,modality,confidence,status,created_by,source_document_id,
                   source_chunk_id,source_start_offset,source_end_offset,source_quote)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (cid, subject, predicate, object_id, object_text, text, '{}', 'factual', 'positive',
             'asserted', 0.9, status, 'llm', documents[key], chunk['id'],
             chunk['start_offset'], chunk['end_offset'], text))
        return cid

    ids = {
        # Both objects are free text: the literal problem, in the same fixture.
        'ceo_apples': claim('a', subject='e-apple', predicate='has_ceo', object_id=None,
                            object_text='约翰·特努斯'),
        'ceo_inc': claim('b', subject='e-apple-inc', predicate='has_ceo', object_id=None,
                         object_text='蒂姆·库克'),
        # A literal that *exactly* matches an existing entity — the L1 case.
        'is_jobs': claim('a', subject='e-apple', predicate='is', object_id=None,
                         object_text='乔布斯'),
        # A literal that only *resembles* one — the L2 case. A non-functional
        # predicate on purpose, so it cannot change the conflict arithmetic.
        'includes_medium': claim('b', subject='e-apple-inc', predicate='includes',
                                 object_id=None, object_text='苹果公司集团'),
    }
    conn.commit()
    conn.close()
    return db, ids, entities


def _client(db):
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


# 1 --------------------------------------------------------------------------

def test_duplicate_entities_are_discovered(tmp_path):
    db, _, _ = _seed(tmp_path)
    body = _client(db).get('/api/integrity/scan').json()

    pairs = body['duplicate_entities']
    names = {frozenset((p['entity_a']['name'], p['entity_b']['name'])) for p in pairs}
    assert frozenset({'苹果', '苹果公司'}) in names
    assert body['counts']['duplicate_entities'] == len(pairs)


# 2 + 3 ----------------------------------------------------------------------

def test_merge_previews_impact_and_stays_a_dry_run(tmp_path):
    db, ids, _ = _seed(tmp_path)
    client = _client(db)
    before = client.get(f"/api/claims/{ids['ceo_inc']}").json()

    impact = client.get('/api/integrity/merge-impact?keep=e-apple&drop=e-apple-inc').json()
    assert impact['keep']['name'] == '苹果' and impact['drop']['name'] == '苹果公司'
    assert impact['claims_moved'] >= 1
    assert impact['affected_claims'] >= 2
    # The prediction that makes the preview worth reading.
    assert len(impact['new_conflicts']) == 1
    conflict = impact['new_conflicts'][0]
    assert conflict['predicate'] == 'has_ceo' and conflict['functional'] is True
    assert sorted(conflict['objects']) == ['约翰·特努斯', '蒂姆·库克']
    # Reported against the subject that will exist *after* the merge, since this is
    # the sentence the user is being asked to act on.
    assert conflict['subject_name'] == '苹果'

    # …and nothing was written by looking.
    assert client.get(f"/api/claims/{ids['ceo_inc']}").json() == before


def test_merge_only_moves_the_subject_and_never_picks_a_winner(tmp_path):
    db, ids, _ = _seed(tmp_path)
    client = _client(db)
    before = {cid: client.get(f'/api/claims/{cid}').json() for cid in ids.values()}

    client.post('/api/integrity/merge', json={'keep_id': 'e-apple', 'drop_id': 'e-apple-inc'})

    for cid, original in before.items():
        after = client.get(f'/api/claims/{cid}').json()
        # Which subject it is filed under may change; what it says may not.
        assert after['predicate'] == original['predicate']
        assert after['content'] == original['content']
        assert after['source_quote'] == original['source_quote']
        assert after['confidence'] == original['confidence']
        # No claim was silently retired to resolve the disagreement.
        assert after['status'] == original['status']
    assert client.get(f"/api/claims/{ids['ceo_inc']}").json()['subject_name'] == '苹果'


# 4 + 5 + 6 ------------------------------------------------------------------

def test_merge_rechecks_and_hands_the_new_conflict_to_the_conflict_center(tmp_path):
    db, ids, _ = _seed(tmp_path)
    client = _client(db)

    result = client.post('/api/integrity/merge',
                         json={'keep_id': 'e-apple', 'drop_id': 'e-apple-inc'}).json()

    recheck = result['recheck']
    assert recheck['claims_examined'] >= 2
    assert len(recheck['conflicts']) == 1
    assert sorted(recheck['conflicts'][0]['objects']) == ['约翰·特努斯', '蒂姆·库克']

    # Recorded as a *candidate* — the same row an import would have written, which is
    # what puts it in front of a human instead of deciding for them.
    queued = client.get('/api/claim-relations?status=candidate&limit=50').json()
    pairs = {(r['new_subject'], r['old_subject'], r['relationship']) for r in queued}
    assert ('苹果', '苹果', 'contradicts') in pairs

    # Disposal reuses the existing operation: the candidate can be superseded.
    relation_id = next(r['id'] for r in queued if r['relationship'] == 'contradicts')
    assert relation_id
    claim_relations = client.get(f"/api/claims/{ids['ceo_inc']}/relations").json()['relations']
    assert any(r['relationship'] == 'contradicts' and r['status'] == 'candidate'
               for r in claim_relations)


# 7 --------------------------------------------------------------------------

def test_search_reflects_the_merge_immediately(tmp_path):
    """The criterion everything else is judged by: fixed in the background is not fixed."""
    db, _, _ = _seed(tmp_path)
    client = _client(db)

    def knowledge():
        body = client.get('/api/search/knowledge?q=%E9%A6%96%E5%B8%AD%E6%89%A7%E8%A1%8C%E5%AE%98&limit=10').json()
        return {(k['subject_label'], k['object_label'], k['state']) for k in body['knowledge']}

    before = knowledge()
    assert ('苹果', '约翰·特努斯', 'current') in before
    assert ('苹果公司', '蒂姆·库克', 'current') in before

    client.post('/api/integrity/merge', json={'keep_id': 'e-apple', 'drop_id': 'e-apple-inc'})

    after = knowledge()
    assert {subject for subject, _, _ in after} == {'苹果'}
    # The merge moved the fact; the disagreement is now visible rather than gone.
    assert ('苹果', '蒂姆·库克', 'current') in after


# backfill -------------------------------------------------------------------

def test_literals_are_linked_to_existing_entities_and_never_create_one(tmp_path):
    db, ids, _ = _seed(tmp_path)
    client = _client(db)

    scan = client.get('/api/integrity/scan').json()
    proposals = {p['claim_id']: p for p in scan['unlinked_claims']}
    # 乔布斯 and 约翰·特努斯 are both entities in this fixture, so those literals
    # resolve exactly; 蒂姆·库克 is not, and is reported with no target at all.
    assert proposals[ids['is_jobs']]['confidence'] == 'high'
    assert proposals[ids['is_jobs']]['target']['id'] == 'e-jobs'
    assert proposals[ids['ceo_apples']]['target']['id'] == 'e-john'
    # 苹果公司集团 only resembles 苹果公司: offered, never applied by default.
    assert proposals[ids['includes_medium']]['confidence'] == 'medium'
    assert proposals[ids['includes_medium']]['target'] is None
    # 蒂姆·库克 resembles nothing in the wiki, so it is not reported at all — a
    # suggestion list full of dead ends is how people learn to ignore suggestions.
    assert ids['ceo_inc'] not in proposals

    entities_before = client.get('/api/entities?limit=100').json()
    result = client.post('/api/integrity/object-links', json={'min_confidence': 'high'}).json()

    assert result['applied'] == 2
    assert client.get(f"/api/claims/{ids['is_jobs']}").json()['object_id'] == 'e-jobs'
    assert client.get(f"/api/claims/{ids['ceo_apples']}").json()['object_id'] == 'e-john'
    # No entity was created — only ever linked to one that already existed.
    assert len(client.get('/api/entities?limit=100').json()) == len(entities_before)


def test_a_link_proposal_has_three_tiers():
    """high is a lookup, medium is a suggestion, None is "leave it alone"."""
    from app.integrity import link_proposal

    pool = [{'id': 'e1', 'name': '乔布斯', 'status': 'verified', 'types': {'Person'}},
            {'id': 'e2', 'name': '苹果公司集团', 'status': 'verified', 'types': {'Organization'}},
            {'id': 'e9', 'name': '微软公司', 'status': 'archived', 'types': {'Organization'}}]
    resolved = {'乔布斯': ['e1']}
    claim = {'id': 'c1', 'object_text': '乔布斯', 'subject_name': '苹果', 'predicate': 'is'}

    high = link_proposal(claim, resolved, pool)
    assert high['confidence'] == 'high' and high['target']['id'] == 'e1'
    assert high['matched_by'] == 'exact'
    # A longer surface form of a known name: offered, nobody has chosen it yet.
    near = link_proposal({**claim, 'object_text': '苹果公司集团公司'}, resolved, pool)
    assert near['confidence'] == 'medium' and near['target'] is None
    assert near['matched_by'] == 'similar'
    # Nothing resembling it: not reported, because an empty suggestion list is noise.
    assert link_proposal({**claim, 'object_text': '完全无关的东西'}, resolved, pool) is None
    # A merged-away row must not keep proposing the merge that already happened.
    assert link_proposal({**claim, 'object_text': '微软公司'}, resolved, pool) is None


def test_a_link_must_satisfy_the_predicates_declared_range():
    """The registry has the last word: a Person cannot fill an Organization slot."""
    from app.integrity import link_proposal

    pool = [{'id': 'e1', 'name': '苹果公司', 'status': 'verified', 'types': {'Organization'}},
            {'id': 'e2', 'name': '蒂姆·库克', 'status': 'verified', 'types': {'Person'}}]
    resolved = {'苹果公司': ['e1'], '蒂姆·库克': ['e2']}
    claim = {'id': 'c1', 'object_text': '苹果公司', 'subject_name': '苹果', 'predicate': 'has_ceo'}

    # has_ceo declares range: Person — so this pairing is not a candidate at all.
    assert link_proposal(claim, resolved, pool) is None
    assert link_proposal({**claim, 'object_text': '蒂姆·库克'}, resolved, pool)['confidence'] == 'high'


def test_two_entities_claiming_the_same_text_is_never_high():
    """Ambiguity is the one case an automatic write must refuse outright."""
    from app.integrity import link_proposal

    pool = [{'id': 'e1', 'name': '蒂姆·库克', 'status': 'verified', 'types': {'Person'}},
            {'id': 'e2', 'name': '史蒂夫·库克', 'status': 'verified', 'types': {'Person'}}]
    resolved = {'库克': ['e1', 'e2']}
    proposal = link_proposal({'id': 'c1', 'object_text': '库克', 'subject_name': '苹果',
                              'predicate': 'has_ceo'}, resolved, pool)
    assert proposal['confidence'] == 'medium'
    assert proposal['matched_by'] == 'ambiguous'
    assert proposal['target'] is None
    assert {c['id'] for c in proposal['candidates']} == {'e1', 'e2'}


def test_only_exact_matches_are_applied_by_default(tmp_path):
    """L2: a suggestion needs a human, and the default setting does not act on it."""
    db, _, _ = _seed(tmp_path)
    client = _client(db)

    scan = client.get('/api/integrity/scan').json()
    proposals = {p['claim_id']: p for p in scan['unlinked_claims']}
    assert any(p['confidence'] == 'medium' for p in proposals.values()), \
        'the fixture must contain an unconfirmed suggestion for this to mean anything'
    medium = {cid for cid, p in proposals.items() if p['confidence'] == 'medium'}

    result = client.post('/api/integrity/object-links', json={'min_confidence': 'high'}).json()
    assert medium.isdisjoint(result['claim_ids'])
    for claim_id in medium:
        assert client.get(f'/api/claims/{claim_id}').json()['object_id'] is None


def test_the_extraction_run_links_exact_literals_and_audits_them(tmp_path, monkeypatch):
    """L1 runs inside the extraction run — not at start-up, not by a whole-DB scan."""
    db, _, _ = _seed(tmp_path)
    import app.service as service
    from app.llm import _empty

    async def fake_extract(chunks):
        # The fake receives the chunk rows, so it can attribute the claims the way a
        # real extraction does — which is what makes this an end-to-end run rather
        # than a unit test of the linking step.
        chunk_id = chunks[0]['id']
        return {**_empty(), 'claims': [
            {'subject': '苹果', 'predicate': 'is', 'object': '乔布斯', 'source_chunk': chunk_id,
             'content': '苹果 是 乔布斯。', 'evidence_quote': '苹果 是 乔布斯。',
             'claim_type': 'factual', 'polarity': 'positive', 'modality': 'asserted',
             'confidence': 0.9},
            {'subject': '苹果', 'predicate': 'has_ceo', 'object': '蒂姆·库克', 'source_chunk': chunk_id,
             'content': '苹果的 CEO 是蒂姆·库克。', 'evidence_quote': '苹果的 CEO 是蒂姆·库克。',
             'claim_type': 'factual', 'polarity': 'positive', 'modality': 'asserted',
             'confidence': 0.9},
        ]}
    monkeypatch.setattr(service, 'extract', fake_extract)

    doc_id = service.create_document(title='新来源', content='苹果 是 乔布斯，CEO 是蒂姆·库克。',
                                     source_type='note', source_uri=None, metadata={})
    report = asyncio.run(service.index_document(doc_id, use_llm=True))
    assert report['llm'] == 'success'

    # 乔布斯 matched an existing entity exactly. 蒂姆·库克 resembles nothing here, so
    # it is left as a literal rather than guessed into an entity.
    assert report['counts']['object_links'] == 1
    written = {c['object_text']: c for c in _client(db).get('/api/claims?limit=200').json()
               if c['source_document_id'] == doc_id}
    assert written['乔布斯']['object_id'] == 'e-jobs'
    assert written['蒂姆·库克']['object_id'] is None

    # Audit: how the link was made, and under which trace, is answerable later.
    audit = _client(db).get('/api/knowledge/operations?kind=LINK_OBJECT&limit=10').json()
    assert len(audit) == 1
    assert audit[0]['payload']['matched_by'] == 'exact'
    assert audit[0]['payload']['trace_id'] == f'link:{doc_id}'
    assert audit[0]['actor'] == 'system'

    # Idempotent: indexing again cannot link the same claim twice.
    again = asyncio.run(service.index_document(doc_id, use_llm=True))
    assert again['counts']['object_links'] == 0 if again['llm'] == 'success' else True


# --- curation memory ----------------------------------------------------------

def _pair(scan, a, b):
    want = {a, b}
    return [p for p in scan['duplicate_entities']
            if {p['entity_a']['name'], p['entity_b']['name']} == want]


def test_not_same_is_remembered_and_silences_the_pair(tmp_path):
    """The DoD: once refused, a pair must not re-enter the default queue."""
    db, _, _ = _seed(tmp_path)
    client = _client(db)
    assert _pair(client.get('/api/integrity/scan').json(), '苹果', '苹果公司')

    decided = client.post('/api/integrity/curation', json={
        'entity_id_a': 'e-apple', 'entity_id_b': 'e-apple-inc', 'reason': '品牌名与公司名'}).json()
    assert decided['decision'] == 'not_same'
    # Stored in canonical order, so the same pair cannot be recorded twice.
    assert (decided['entity_id_a'], decided['entity_id_b']) == ('e-apple', 'e-apple-inc')

    after = client.get('/api/integrity/scan').json()
    assert _pair(after, '苹果', '苹果公司') == []
    assert after['counts']['hidden_decided'] >= 1


def test_a_curation_decision_survives_a_rename(tmp_path):
    """Keyed on ids, not names — an alias change must not lose the decision."""
    db, _, _ = _seed(tmp_path)
    client = _client(db)
    client.post('/api/integrity/curation',
                json={'entity_id_a': 'e-apple', 'entity_id_b': 'e-apple-inc'})
    client.patch('/api/entities/e-apple-inc', json={'name': '苹果公司集团'})

    assert _pair(client.get('/api/integrity/scan').json(), '苹果', '苹果公司集团') == []
    listed = client.get('/api/integrity/curation').json()
    assert listed[0]['entity_b_name'] == '苹果公司集团'


def test_a_curation_decision_is_revocable(tmp_path):
    """A judgement made once is not permanent truth."""
    db, _, _ = _seed(tmp_path)
    client = _client(db)
    decision = client.post('/api/integrity/curation',
                           json={'entity_id_a': 'e-apple', 'entity_id_b': 'e-apple-inc'}).json()

    assert client.delete(f"/api/integrity/curation/{decision['id']}").json()['revoked'] == 1
    assert _pair(client.get('/api/integrity/scan').json(), '苹果', '苹果公司')
    assert client.delete('/api/integrity/curation/missing').status_code == 404


def test_a_curation_decision_is_not_knowledge(tmp_path):
    """It must not become a claim, a relation, or a predicate in the registry."""
    db, _, _ = _seed(tmp_path)
    client = _client(db)
    claims_before = len(client.get('/api/claims?limit=200').json())

    client.post('/api/integrity/curation',
                json={'entity_id_a': 'e-apple', 'entity_id_b': 'e-apple-inc'})

    assert len(client.get('/api/claims?limit=200').json()) == claims_before
    assert client.get('/api/claim-relations?limit=200').json() == []
    assert 'not_same' not in client.get('/api/ontology/predicates').json()['labels']


def test_not_same_does_not_block_a_later_merge(tmp_path):
    """The pair stops being *suggested*; it does not become unmergeable."""
    db, _, _ = _seed(tmp_path)
    client = _client(db)
    client.post('/api/integrity/curation',
                json={'entity_id_a': 'e-apple', 'entity_id_b': 'e-apple-inc'})
    assert _pair(client.get('/api/integrity/scan').json(), '苹果', '苹果公司') == []

    result = client.post('/api/integrity/merge',
                         json={'keep_id': 'e-apple', 'drop_id': 'e-apple-inc'}).json()
    assert result['merged']['moved_claims'] >= 1


# --- L2: the tier a person has to decide --------------------------------------
#
# The suggestion tier is reported with candidates and **no chosen target**. That is
# only a coherent design if something can act on the candidates, and the batch tool
# cannot: it applies what the registry already settled. Without the confirmation
# below, every suggestion is found, stored, sent to the UI — and then dropped,
# because nothing is allowed to pick. These three tests are that other half.

def test_confirming_a_suggestion_applies_exactly_what_the_user_chose(tmp_path):
    db, ids, _ = _seed(tmp_path)
    client = _client(db)

    scan = client.get('/api/integrity/scan').json()
    suggestion = next(p for p in scan['unlinked_claims'] if p['claim_id'] == ids['includes_medium'])
    assert suggestion['confidence'] == 'medium'
    # The point of the tier: an ambiguous or merely similar match arrives with
    # candidates and *no* target, so no caller can apply it by accident.
    assert suggestion['target'] is None
    assert suggestion['candidates']
    chosen = suggestion['candidates'][0]

    # Looking at it changed nothing.
    assert client.get(f"/api/claims/{ids['includes_medium']}").json()['object_id'] is None

    confirmed = client.post('/api/integrity/object-links/confirm',
                            json={'claim_id': ids['includes_medium'],
                                  'entity_id': chosen['id']}).json()
    assert confirmed['linked'] == 1
    assert client.get(f"/api/claims/{ids['includes_medium']}").json()['object_id'] == chosen['id']

    # A human decision is audited as one — not filed as a system match, which is
    # what the pipeline's own links are.
    audit = client.get('/api/knowledge/operations?kind=LINK_OBJECT&limit=20').json()
    confirmed_rows = [a for a in audit if a['payload']['matched_by'] == 'confirmed']
    assert len(confirmed_rows) == 1
    assert confirmed_rows[0]['actor'] == 'user'

    # And it leaves the queue, which is the feedback the user actually sees.
    after = client.get('/api/integrity/scan').json()
    assert ids['includes_medium'] not in {p['claim_id'] for p in after['unlinked_claims']}


def test_a_confirmation_cannot_break_the_registry_range(tmp_path):
    """The ontology has the last word even when a person asks: has_ceo takes a Person."""
    db, ids, _ = _seed(tmp_path)
    client = _client(db)

    refused = client.post('/api/integrity/object-links/confirm',
                          json={'claim_id': ids['ceo_inc'], 'entity_id': 'e-apple-inc'})
    assert refused.status_code == 422
    assert client.get(f"/api/claims/{ids['ceo_inc']}").json()['object_id'] is None

    # The same slot accepts the right kind of thing.
    accepted = client.post('/api/integrity/object-links/confirm',
                           json={'claim_id': ids['ceo_inc'], 'entity_id': 'e-john'})
    assert accepted.status_code == 200 and accepted.json()['linked'] == 1


def test_a_confirmation_cannot_take_a_link_away_from_an_entity(tmp_path):
    """Confirming twice must not re-point a claim that is already attached."""
    db, ids, _ = _seed(tmp_path)
    client = _client(db)
    client.post('/api/integrity/object-links', json={'min_confidence': 'high'})
    assert client.get(f"/api/claims/{ids['is_jobs']}").json()['object_id'] == 'e-jobs'

    again = client.post('/api/integrity/object-links/confirm',
                        json={'claim_id': ids['is_jobs'], 'entity_id': 'e-john'})
    assert again.status_code == 422
    assert client.get(f"/api/claims/{ids['is_jobs']}").json()['object_id'] == 'e-jobs'


# --- the whole chain ----------------------------------------------------------

def test_the_whole_repair_chain_ends_with_search_and_qa_agreeing(tmp_path):
    """合并 → 新冲突 → 用既有能力处置 → 搜索与 QA 同时反映最终状态。

    Every link is covered on its own above. What is checked here is that they
    *compose*, end to end and against a real database — because "the background is
    fixed but the answer still shows the old state" is the one outcome that makes a
    repair worse than the problem it repaired.
    """
    db, ids, _ = _seed(tmp_path)
    client = _client(db)

    def knowledge():
        body = client.get('/api/search/knowledge?q=%E9%A6%96%E5%B8%AD%E6%89%A7%E8%A1%8C%E5%AE%98'
                          '&limit=10').json()
        return {(k['subject_label'], k['object_label'], k['state']) for k in body['knowledge']}

    def ask(question: str):
        return client.post('/api/ask', json={'question': question}).json()

    # 1. Two subjects, each holding its own CEO: the split that hid a disagreement.
    assert ('苹果', '约翰·特努斯', 'current') in knowledge()
    assert ('苹果公司', '蒂姆·库克', 'current') in knowledge()

    # 2. Merge. One subject for two rows — and the disagreement becomes explicit.
    merged = client.post('/api/integrity/merge',
                         json={'keep_id': 'e-apple', 'drop_id': 'e-apple-inc'}).json()
    assert len(merged['recheck']['conflicts']) == 1
    assert {subject for subject, _, _ in knowledge()} == {'苹果'}

    # 2b. And it lands in the work count, not only in the merge response. This is the
    #     defect the count used to have: the conflict was on the Review page while the
    #     badge stayed silent, so the two disagreed about how much was outstanding.
    assert merged['issues'][0]['kind'] == 'claim_conflict'
    assert merged['issues'][0]['affected_ids']
    assert client.get('/api/review/inbox').json()['groups']['claim_conflicts'] == 1

    # 3. QA refuses rather than choosing a side, which is the honest answer while
    #    two current claims disagree.
    assert ask('苹果现在的 CEO 是谁？')['reason'] == 'ambiguous_multiple_current_claims'

    # 4. Disposal through the machinery that already exists. `supersedes` means the
    #    relation's *target* stops being current, so the row's direction carries the
    #    decision — and the queue states both sides so it can be made knowingly.
    queue = client.get('/api/claim-relations?status=candidate&limit=50').json()
    conflict = next(r for r in queue if r['relationship'] == 'contradicts')
    assert conflict['old_object_text'] == '蒂姆·库克'
    assert conflict['new_object_text'] == '约翰·特努斯'
    client.patch(f"/api/claim-relations/{conflict['id']}",
                 json={'status': 'accepted', 'relationship': 'supersedes'})

    # 5. Both surfaces agree immediately — current state, and the history that
    #    makes the past answerable.
    after = knowledge()
    assert ('苹果', '约翰·特努斯', 'current') in after
    assert ('苹果', '蒂姆·库克', 'historical') in after
    assert ask('苹果现在的 CEO 是谁？')['answer_value'] == '约翰·特努斯'
    assert ask('苹果之前的 CEO 是谁？')['answer_value'] == '蒂姆·库克'

    # 6. The inbox empties as a *consequence* of the decision rather than by
    #    bookkeeping: the target is no longer current, so there is nothing left to
    #    decide. A count that needed a separate "mark as read" would drift from the
    #    knowledge it claims to describe.
    assert client.get('/api/review/inbox').json()['groups']['claim_conflicts'] == 0


# --- maintenance memory: dismissing a suggestion ------------------------------
#
# The entity pair has "not the same" (a curation decision, answerable forever). A
# literal suggestion needs a lighter cousin: "I have seen this, stop showing it",
# which is a preference about a notice and exists in order to be lifted. Without it the
# only options are acting on the suggestion or reading it every single scan.

def _suggestion(client, claim_id: str) -> dict:
    body = client.get('/api/integrity/scan').json()
    return next(p for p in body['unlinked_claims'] if p['claim_id'] == claim_id)


def _dismiss(client, proposal: dict) -> dict:
    return client.post('/api/integrity/suppressions',
                       json={'claim_id': proposal['claim_id'],
                             'object_text': proposal['object_text']}).json()


def test_dismissing_a_suggestion_is_remembered_and_silences_it(tmp_path):
    db, ids, _ = _seed(tmp_path)
    client = _client(db)
    proposal = _suggestion(client, ids['includes_medium'])

    saved = _dismiss(client, proposal)
    assert saved['kind'] == 'object_link' and saved['source_type'] == 'claim'
    assert saved['source_id'] == ids['includes_medium']

    after = client.get('/api/integrity/scan').json()
    assert ids['includes_medium'] not in {p['claim_id'] for p in after['unlinked_claims']}
    # Reported, not silently dropped: a person who dismissed something should be able
    # to see that the system remembered, and how much it is holding back.
    assert after['counts']['hidden_dismissed'] >= 1
    # Ignoring changed nothing about the claim itself.
    assert client.get(f"/api/claims/{ids['includes_medium']}").json()['object_id'] is None


def test_a_dismissal_is_listed_with_context_and_can_be_lifted(tmp_path):
    db, ids, _ = _seed(tmp_path)
    client = _client(db)
    saved = _dismiss(client, _suggestion(client, ids['includes_medium']))

    listed = client.get('/api/integrity/suppressions').json()
    assert len(listed) == 1
    # Enough context to be read back: which claim, about which literal.
    assert listed[0]['subject_name'] == '苹果公司'
    assert listed[0]['object_text'] == '苹果公司集团'

    assert client.delete(f"/api/integrity/suppressions/{saved['id']}").json()['revoked'] == 1
    assert ids['includes_medium'] in {p['claim_id']
                                      for p in client.get('/api/integrity/scan').json()['unlinked_claims']}
    assert client.delete('/api/integrity/suppressions/missing').status_code == 404


def test_a_dismissal_lasts_until_you_say_otherwise(tmp_path):
    """Idempotent, and it never expires on its own — that is what makes it worth clicking."""
    db, ids, _ = _seed(tmp_path)
    client = _client(db)
    proposal = _suggestion(client, ids['includes_medium'])
    first = _dismiss(client, proposal)
    again = _dismiss(client, proposal)
    assert again['id'] == first['id']            # one row, not two
    assert len(client.get('/api/integrity/suppressions').json()) == 1


def test_an_edited_literal_is_a_new_question(tmp_path):
    """The key is the text, so changing the text un-suppresses it.

    A dismissal answers one specific notice. If the claim's free text later changes,
    matching the old dismissal would silently swallow a question nobody has seen.
    """
    db, ids, _ = _seed(tmp_path)
    client = _client(db)
    _dismiss(client, _suggestion(client, ids['includes_medium']))

    conn = db.connect()
    conn.execute('UPDATE claims SET object_text=? WHERE id=?',
                 ('苹果公司集团控股', ids['includes_medium']))
    conn.commit()
    conn.close()

    raised = {p['claim_id'] for p in client.get('/api/integrity/scan').json()['unlinked_claims']}
    assert ids['includes_medium'] in raised


def test_a_dismissal_is_not_knowledge_and_not_a_curation_decision(tmp_path):
    """It records a preference about a notice — nothing about the world."""
    db, ids, _ = _seed(tmp_path)
    client = _client(db)
    claims_before = len(client.get('/api/claims?limit=200').json())
    relations_before = client.get('/api/claim-relations?limit=200').json()

    _dismiss(client, _suggestion(client, ids['includes_medium']))

    assert len(client.get('/api/claims?limit=200').json()) == claims_before
    assert client.get('/api/claim-relations?limit=200').json() == relations_before
    # …and not a curation decision either: 「不是同一个」 is about entities and holds
    # forever; this is about a suggestion and is meant to be lifted.
    assert client.get('/api/integrity/curation').json() == []
    assert 'object_link' not in client.get('/api/ontology/predicates').json()['labels']


# --- the Review Inbox ---------------------------------------------------------

def test_inbox_total_is_the_sum_of_its_groups(tmp_path):
    db, _, _ = _seed(tmp_path)
    body = _client(db).get('/api/review/inbox').json()

    assert body['total'] == sum(body['groups'].values())
    assert body['groups']['entity_duplicates'] == 1
    assert body['groups']['claim_conflicts'] == 0
    # Suggestions are reported beside the total, never inside it: sixteen notices are
    # not sixteen decisions, and counting them would make this a daily work list.
    assert body['maintenance']['object_link_suggestions'] > 0
    assert 'object_link_suggestions' not in body['groups']


def test_inbox_keeps_counting_what_the_review_page_still_lists(tmp_path):
    """The invariant that matters: the badge and the page it opens agree.

    The defect this replaces: claim-level conflicts were listed on Review while the
    sidebar counted only entity/claim/relation candidates, so the number and the page
    disagreed about how much was outstanding.
    """
    from app.repositories import ClaimRepository

    db, _, _ = _seed(tmp_path)
    client = _client(db)
    client.post('/api/integrity/merge', json={'keep_id': 'e-apple', 'drop_id': 'e-apple-inc'})

    body = client.get('/api/review/inbox').json()
    listed = client.get('/api/claim-relations?limit=50').json()
    assert body['groups']['claim_conflicts'] == 1
    assert len([r for r in listed if r['relationship'] == 'contradicts'
                and r['status'] == 'candidate']) == body['groups']['claim_conflicts']
    # And the count is "disposable rows", not "rows in the table".
    assert len(ClaimRepository().pending_decisions(50)) == body['groups']['claim_conflicts']


def test_the_inbox_counts_the_window_the_page_renders(tmp_path):
    """One backlog, one number: the badge and the list it opens must agree.

    Learned by running it against a real wiki: with independent limits the badge
    reported 51 duplicate pairs while the panel listed 20, so the other 31 could not
    be acted on from where the user had been sent. This compares the two *calls*
    rather than trusting two constants to stay equal.
    """
    db, _, _ = _seed(tmp_path)
    client = _client(db)
    panel = client.get('/api/integrity/scan').json()
    queues = client.get('/api/review').json()
    inbox = client.get('/api/review/inbox').json()

    assert inbox['groups']['entity_duplicates'] == panel['counts']['duplicate_entities']
    assert inbox['maintenance']['object_link_suggestions'] == panel['counts']['unlinked_claims']
    assert inbox['groups']['entities'] == len(queues['entities'])
    assert inbox['groups']['claims'] == len(queues['claims'])
    assert inbox['groups']['relations'] == len(queues['relations'])


def test_inbox_drops_rows_nobody_can_act_on(tmp_path):
    """A decision already made must not keep the number up."""
    db, _, _ = _seed(tmp_path)
    client = _client(db)
    client.post('/api/integrity/merge', json={'keep_id': 'e-apple', 'drop_id': 'e-apple-inc'})
    conflict = next(r for r in client.get('/api/claim-relations?status=candidate&limit=50').json()
                    if r['relationship'] == 'contradicts')
    assert client.get('/api/review/inbox').json()['groups']['claim_conflicts'] == 1

    client.patch(f"/api/claim-relations/{conflict['id']}",
                 json={'status': 'accepted', 'relationship': 'supersedes'})

    body = client.get('/api/review/inbox').json()
    assert body['groups']['claim_conflicts'] == 0
    assert body['total'] == sum(body['groups'].values())


# --- post-operation issues, one vocabulary ------------------------------------

def _research_candidate(db, *, subject_id: str, object_text: str) -> str:
    """A pending research candidate that disagrees with what is already current.

    ACCEPT is bounded to research candidates on purpose, so exercising the real path
    means producing one the way the pipeline does: a document with
    ``source_type='research'`` and a candidate claim quoting it.
    """
    from app.service import create_document, write_chunks

    text = f'{subject_id} has_ceo {object_text}.'
    doc_id = create_document(title='研究结论', content=text, source_type='research',
                             source_uri='research:test-issues', metadata={})
    chunk = write_chunks(doc_id, text)[0]
    conn = db.connect()
    cid = str(uuid.uuid4())
    conn.execute(
        '''INSERT INTO claims(id,subject_id,predicate,object_id,object_text,content,context_json,
               claim_type,polarity,modality,confidence,status,created_by,source_document_id,
               source_chunk_id,source_start_offset,source_end_offset,source_quote)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (cid, subject_id, 'has_ceo', None, object_text, text, '{}', 'factual', 'positive',
         'asserted', 0.9, 'candidate', 'llm', doc_id, chunk['id'],
         chunk['start_offset'], chunk['end_offset'], text))
    conn.commit()
    conn.close()
    return cid


def test_accepting_a_candidate_reports_the_conflict_it_creates(tmp_path):
    """ACCEPT is the other way knowledge appears, so it must report in the same shape.

    Otherwise the user learns the warning from one entry point and not from another,
    and 「系统会告诉我它弄坏了什么」 stops being true.
    """
    db, _, _ = _seed(tmp_path)
    client = _client(db)
    candidate = _research_candidate(db, subject_id='e-apple', object_text='蒂姆·库克')

    body = client.post('/api/knowledge/operations',
                       json={'kind': 'ACCEPT', 'payload': {'claim_id': candidate}}).json()

    assert body['issues'], 'accepting a disagreeing statement must report the disagreement'
    issue = body['issues'][0]
    assert issue['kind'] == 'claim_conflict'
    assert issue['severity'] == 'warning'
    assert set(issue['affected_ids']) >= {candidate}
    assert 'has_ceo' not in issue['title']          # never the canonical identifier
    # A merge reports the identical shape, because it is built by the same code.
    merged = client.post('/api/integrity/merge',
                         json={'keep_id': 'e-apple-inc', 'drop_id': 'e-apple'}).json()
    assert merged['issues'][0].keys() == issue.keys()


def test_an_ordinary_write_reports_nothing(tmp_path):
    """The check must not cry wolf: a statement that agrees with the wiki is silent."""
    db, _, _ = _seed(tmp_path)
    client = _client(db)
    conn = db.connect()
    chunk = conn.execute(
        'SELECT id,document_id,start_offset,end_offset FROM chunks LIMIT 1').fetchone()
    conn.close()

    # 苹果 already records 乔布斯; asserting the same value again is a duplicate, and a
    # duplicate is not a problem to fix.
    body = client.post('/api/knowledge/operations', json={
        'kind': 'CREATE', 'payload': {
            'subject': '苹果', 'predicate': 'is', 'object': '乔布斯',
            'content': '苹果 是 乔布斯。',
            'source_document_id': chunk[1], 'source_chunk_id': chunk[0],
            'source_start_offset': chunk[2], 'source_end_offset': chunk[3],
            'source_quote': '苹果 是 乔布斯。'}}).json()
    assert body['issues'] == []

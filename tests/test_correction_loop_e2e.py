"""Phase 5 — the Correction Full Loop, end to end (ADR-015).

    draft -> compile -> candidate retrieval -> supersede plan -> confirm
          -> new Claim + Evidence + Evolution -> current state -> QA -> trace

Both halves matter equally. The happy path must actually work, end to end, with
**no model call at all** (the draft is supplied, the reasoning is deterministic);
and the negative cases — an invented predicate, an unknown predicate, a wrong
domain/range pairing, a duplicate, and an unrelated fact — must be *refused*
rather than absorbed into the knowledge base.
"""
from __future__ import annotations

import importlib
import os
import sys

import pytest

_RELOAD = (
    'app.repositories.base',
    'app.repositories.document_repo',
    'app.repositories.entity_repo',
    'app.repositories.claim_repo',
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
    'app.knowledge',
    'app.claim_relations',
    'app.service',
    'app.domain.claim_state',
    'app.domain.claim_evolution',
    'app.domain.predicate_resolver',
    'app.domain.compiler',
    'app.domain.operations',
    'app.workflows.correction_workflow',
    'app.workflows.ask_workflow',
)


def _db(tmp_path):
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


_SENTENCE = '苹果公司的首席执行官是蒂姆·库克。'


def _seed(db) -> dict:
    """Apple --has_ceo--> Tim Cook, with real provenance, created via CREATE."""
    from app.db import transaction
    from app.domain.operations import OperationRequest, run
    from app.resolution import resolve_or_create_entity
    from app.service import create_document, write_chunks

    doc_id = create_document(title='苹果公司简介', content=_SENTENCE,
                             source_type='note', source_uri=None, metadata={})
    chunk = write_chunks(doc_id, _SENTENCE)[0]
    with transaction() as conn:
        apple = resolve_or_create_entity(conn, name='苹果公司', entity_types=['Organization'],
                                        aliases=['苹果'], description='一家公司', properties={})
        tim = resolve_or_create_entity(conn, name='蒂姆·库克', entity_types=['Person'],
                                       aliases=[], description=None, properties={})
        john = resolve_or_create_entity(conn, name='约翰·特努斯', entity_types=['Person'],
                                        aliases=[], description=None, properties={})
        # A known Location, so a wrong range is *checkable* rather than unknown.
        california = resolve_or_create_entity(conn, name='加利福尼亚', entity_types=['Location'],
                                              aliases=[], description=None, properties={})
        old_id = run(OperationRequest(kind='CREATE', payload={
            'subject': '苹果公司', 'predicate': 'has_ceo', 'object': '蒂姆·库克',
            'content': _SENTENCE,
            'source_document_id': doc_id, 'source_chunk_id': chunk['id'],
            'source_start_offset': chunk['start_offset'],
            'source_end_offset': chunk['end_offset'], 'source_quote': _SENTENCE}),
            conn).affected['claim_id']
    return {'apple': apple, 'tim': tim, 'john': john, 'california': california,
            'doc': doc_id, 'chunk': chunk['id'], 'old': old_id}


def test_e2e_new_ceo_supersedes_and_qa_reflects_it(tmp_path):
    db = _db(tmp_path)
    seed = _seed(db)

    from app.domain.claim_state import is_current
    from app.ontology import claim_registry_version
    from app.repositories.claim_repo import ClaimRepository
    from app.repositories.operation_repo import OperationRepository
    from app.workflows.ask_workflow import try_direct_answer, try_historical_answer
    from app.workflows.correction_workflow import (apply_correction, build_plan,
                                                   intent_from_draft)

    text = '苹果公司的新任 CEO 是约翰·特努斯。'

    # 3-4. The LLM produced only a *draft*; the compiler canonicalises it. The
    #      invented `new_ceo` is not a problem — it is a candidate.
    intent = intent_from_draft(text, {
        'subject_candidate': '苹果公司', 'predicate_candidate': 'new_ceo',
        'object_candidate': '约翰·特努斯', 'temporal_signal': 'new'})
    assert intent is not None
    assert intent.predicate_candidate == 'new_ceo'      # what the model said
    assert intent.predicate == 'has_ceo'                # what the compiler decided
    assert intent.temporal_signal == 'new'

    # 5-6. Candidate retrieval must hit the stored claim *despite* the raw
    #      candidate, and the registry justifies superseding it.
    plan = build_plan(text, intent)
    assert plan.blocked is False
    assert plan.relationship == 'supersedes'
    assert plan.related_claim_id == seed['old']
    assert len(plan.candidates) >= 1
    assert plan.candidates[0]['claim_id'] == seed['old']
    assert plan.apply_supersede is True

    # 7-8. Confirm + apply: a NEW claim, never an UPDATE of the old row.
    result = apply_correction(plan)
    new_id = result['claim_id']
    assert new_id != seed['old']
    assert result['superseded_claim_id'] == seed['old']

    claims = ClaimRepository()
    old, new = claims.get(seed['old']), claims.get(new_id)
    assert old['status'] == 'superseded'                # kept as history
    assert old['object_text'] == '蒂姆·库克'             # content untouched
    assert new['object_text'] == '约翰·特努斯'
    assert new['predicate'] == 'has_ceo'
    assert claims.status_of(new_id) == 'candidate'

    # The decision that drove the supersede is stored *with* the claim, so it can
    # still be explained after the plan is gone. It is recorded as context — never
    # smuggled back into the predicate name, which stays canonical.
    assert new['context']['temporal_signal'] == 'new'
    assert new['context']['predicate_candidate'] == 'new_ceo'

    # 9. State resolver: history kept, exactly one current value.
    rows = claims.related(subject_id=seed['apple'], predicate='has_ceo',
                          exclude_id='', limit=10)
    assert [c['id'] for c in rows if is_current(c)] == [new_id]
    assert [c['id'] for c in rows if not is_current(c)] == [seed['old']]

    # 10. QA reflects the change immediately — and still with 0 LLM.
    now = try_direct_answer('苹果公司现在的 CEO 是谁？')
    assert now is not None and now.status == 'answered'
    assert now.answer_value == '约翰·特努斯'

    # ...while the past stays answerable, from the superseded claim.
    past = try_historical_answer('苹果公司之前的 CEO 是谁？')
    assert past is not None and past.status == 'answered'
    assert past.answer_value == '蒂姆·库克'

    # 11. Trace: new claim -> evolution relation -> old claim -> evidence -> operation.
    relations = claims.relations_for_claim(new_id)
    assert any(r['relationship'] == 'supersedes' and r['target_claim_id'] == seed['old']
               for r in relations)
    assert new['source_document_id'] and new['source_chunk_id'] and new['source_quote']
    operation = OperationRepository().get(result['operation_id'])
    assert operation['kind'] == 'CORRECT'

    # 13. The claim records which ontology was in force when it was compiled.
    conn = db.connect()
    stamped = conn.execute('SELECT ontology_version FROM claims WHERE id=?',
                           (new_id,)).fetchone()['ontology_version']
    invented = conn.execute("SELECT COUNT(*) c FROM claims WHERE predicate='new_ceo'"
                            ).fetchone()['c']
    conn.close()
    assert stamped == claim_registry_version()
    # Negative A: the invented predicate never reached storage.
    assert invented == 0


# --- negative cases -----------------------------------------------------------

def test_negative_unknown_predicate_creates_nothing(tmp_path):
    """B: an unknown predicate is UNRESOLVED — no predicate, no claim."""
    db = _db(tmp_path)
    seed = _seed(db)

    from app.domain.operations import OperationError
    from app.repositories.claim_repo import ClaimRepository
    from app.workflows.correction_workflow import (apply_correction, build_plan,
                                                   intent_from_draft)

    intent = intent_from_draft('苹果公司的合作伙伴是 X。', {
        'subject_candidate': '苹果公司', 'predicate_candidate': 'random_relationship',
        'object_candidate': 'X'})
    assert intent is not None
    assert intent.predicate == ''

    plan = build_plan('苹果公司的合作伙伴是 X。', intent)
    assert plan.blocked is True
    assert plan.relationship == 'unresolved'

    before = ClaimRepository().count()
    with pytest.raises(OperationError):
        apply_correction(plan)
    assert ClaimRepository().count() == before
    assert ClaimRepository().get(seed['old'])['status'] == 'candidate'   # untouched


def test_negative_wrong_domain_range_is_refused(tmp_path):
    """C: Apple has_ceo California — the ontology forbids Organization -> Location."""
    db = _db(tmp_path)
    _seed(db)                       # 加利福尼亚 exists as a Location

    from app.domain.operations import OperationError
    from app.repositories.claim_repo import ClaimRepository
    from app.workflows.correction_workflow import (apply_correction, build_plan,
                                                   intent_from_draft)

    text = '苹果公司的 CEO 是加利福尼亚。'
    intent = intent_from_draft(text, {'subject_candidate': '苹果公司',
                                      'predicate_candidate': 'has_ceo',
                                      'object_candidate': '加利福尼亚'})
    assert intent is not None and intent.predicate == 'has_ceo'

    plan = build_plan(text, intent)
    assert plan.blocked is True
    assert 'domain/range' in plan.summary

    before = ClaimRepository().count()
    with pytest.raises(OperationError):
        apply_correction(plan)
    assert ClaimRepository().count() == before


def test_negative_duplicate_is_recognised(tmp_path):
    """D: re-asserting the same value is a duplicate, not new knowledge.

    Nothing is silently inserted as brand-new, and it is never mistaken for a
    supersede (the value did not change).
    """
    db = _db(tmp_path)
    _seed(db)

    from app.workflows.correction_workflow import build_plan, intent_from_draft

    text = '苹果公司的 CEO 是蒂姆·库克。'
    intent = intent_from_draft(text, {'subject_candidate': '苹果公司',
                                      'predicate_candidate': 'ceo',
                                      'object_candidate': '蒂姆·库克'})
    assert intent is not None and intent.predicate == 'has_ceo'

    plan = build_plan(text, intent)
    assert plan.relationship == 'duplicate'
    assert plan.blocked is False
    assert plan.apply_supersede is False


def test_negative_unrelated_fact_does_not_match_has_ceo(tmp_path):
    """E: a headquarters fact must not be absorbed by the CEO relation."""
    _db(tmp_path)

    from app.ontology import match_claim_predicates
    from app.workflows.correction_workflow import build_plan, intent_from_draft

    text = '苹果公司总部位于加利福尼亚。'
    assert 'has_ceo' not in match_claim_predicates(text)

    intent = intent_from_draft(text, {'subject_candidate': '苹果公司',
                                      'predicate_candidate': 'headquarters_of',
                                      'object_candidate': '加利福尼亚'})
    assert intent is not None
    assert intent.predicate == ''                  # unresolved, not forced into has_ceo
    assert build_plan(text, intent).blocked is True

"""v0.2 end-to-end acceptance: the knowledge lifecycle, not single functions.

Scenario (no LLM, deterministic):

    ingest document
      -> extract claim (OpenAI = 人工智能研究实验室)
      -> detect claim relations
      -> one-sentence correction ("OpenAI = 营利性公司")
      -> CORRECT operation creates a new claim + supersedes the old one
      -> ClaimStateResolver reports the new claim as current, old dropped
      -> Graph shows the new claim, not the superseded one
      -> QA grounding returns the new claim, never the old as current
      -> audit trail records the operation and the previous status

This is the chain that proves "one-sentence correction" is a real knowledge
edit, not a feature bolted on. It exercises Repository -> Domain (operations /
claim_state) -> Workflow -> retrieval / graph, exactly as the architecture says.
"""
import asyncio
import importlib
import os
import sys

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
    'app.resolution',
    'app.knowledge',
    'app.claim_relations',
    'app.service',
    'app.extraction',
    'app.domain.claim_state',
    'app.domain.operations',
    'app.workflows.correction_workflow',
    'app.graph',
    'app.retrieval',
)


def _reload(tmp_path):
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


DOC_TEXT = 'OpenAI 是一家人工智能研究实验室。OpenAI 由 Sam Altman 领导。'
CORRECTION_TEXT = 'OpenAI 是一家营利性公司。'


def _ingest_with_claim(tmp_path, *, subject, predicate, obj, evidence_quote):
    """Mirror the real pipeline (normalize -> persist) without the LLM step."""
    from app.chunking import chunk_text
    from app.db import transaction
    from app.extraction import normalize_extraction
    from app.knowledge import persist_extraction
    from app.claim_relations import detect_claim_relations
    from app.repositories.document_repo import DocumentRepository

    documents = DocumentRepository()
    doc_id = documents.create(title='Intro to OpenAI', content=DOC_TEXT, source_type='note')
    chunks = documents.replace_chunks(
        doc_id, [(c.content, c.index, c.start_offset, c.end_offset) for c in chunk_text(DOC_TEXT)])
    chunk_id = chunks[0]['id']

    extraction = normalize_extraction({
        'entities': [{'name': subject, 'types': ['Organization'], 'aliases': []}],
        'claims': [{
            'subject': subject, 'predicate': predicate, 'object': obj,
            'claim_type': 'definitional', 'polarity': 'positive', 'modality': 'asserted',
            'context': {}, 'confidence': 0.9,
            'source_chunk': chunk_id, 'evidence_quote': evidence_quote,
        }],
        'events': [], 'ideas': [], 'questions': [],
    })
    with transaction() as conn:
        persist_extraction(conn, document_id=doc_id, extraction=extraction)
        detect_claim_relations(conn, document_id=doc_id)
    return doc_id, chunk_id


def test_full_knowledge_lifecycle_end_to_end(tmp_path):
    _reload(tmp_path)
    from app.db import transaction
    from app.domain.claim_state import resolve
    from app.repositories.claim_repo import ClaimRepository
    from app.repositories.entity_repo import EntityRepository
    from app.repositories.operation_repo import OperationRepository
    from app.workflows.correction_workflow import (CorrectionIntent, apply_correction,
                                                   build_plan)
    from app.graph import neighborhood
    from app.retrieval import evidence_hits

    # 1) ingest + extract the original claim
    _, _ = _ingest_with_claim(
        tmp_path, subject='OpenAI', predicate='defined_as',
        obj='人工智能研究实验室', evidence_quote='OpenAI 是一家人工智能研究实验室')
    entity = EntityRepository().by_name('OpenAI')
    openai_id = entity['id']

    # 2) the original claim is current
    claims = ClaimRepository()
    old_claims = claims.related(subject_id=openai_id, predicate='defined_as', exclude_id='', limit=10)
    assert len(old_claims) == 1
    old = old_claims[0]
    assert old['object_text'] == '人工智能研究实验室'
    assert old['status'] == 'candidate'

    # 3) plan a one-sentence correction (read-only)
    intent = CorrectionIntent(text=CORRECTION_TEXT, subject='OpenAI',
                              predicate='defined_as', object='营利性公司')
    plan = build_plan(CORRECTION_TEXT, intent)
    # 'defined_as' is a functional predicate, so a different object is a conflict.
    assert plan.relationship == 'contradicts'
    assert plan.related_claim_id == old['id']

    # 4) apply -> CORRECT operation supersedes the old claim
    result = apply_correction(plan, relationship='supersedes',
                              related_claim_id=old['id'], apply_supersede=True)
    new_id = result['claim_id']

    # 5) lifecycle: old is history, new is current, content untouched
    assert claims.status_of(old['id']) == 'superseded'
    assert claims.get(old['id'])['object_text'] == '人工智能研究实验室'
    new_claim = claims.get(new_id)
    assert new_claim['object_text'] == '营利性公司'
    assert new_claim['status'] == 'candidate'
    # the correction is its own traceable document
    from app.repositories.document_repo import DocumentRepository
    assert DocumentRepository().get(result['document_id'])['source_type'] == 'correction'

    # 6) the single source of truth: current knowledge about OpenAI/defined_as is the new claim
    all_related = claims.related(subject_id=openai_id, predicate='defined_as', exclude_id='', limit=10)
    current = resolve(all_related)
    assert [c['id'] for c in current] == [new_id]
    assert current[0]['object_text'] == '营利性公司'

    # 7) Graph resolves the same way: new present, superseded dropped
    graph = neighborhood(openai_id, depth=1)
    claim_edges = [e for e in graph['edges'] if e.get('kind') == 'claim']
    graph_objs = [e.get('object_text') or e.get('object_name') for e in claim_edges]
    assert '营利性公司' in graph_objs
    assert '人工智能研究实验室' not in graph_objs

    # 8) QA grounding returns the new claim as current, never the old as current
    hits = evidence_hits('OpenAI 是什么公司', limit=8)
    current_objs = set()
    for hit in hits:
        for c in hit.get('claims', []):
            if c.get('predicate') != 'defined_as' or c.get('subject_name') != 'OpenAI':
                continue
            if c.get('lifecycle') == 'superseded' or c.get('status') == 'superseded':
                continue
            current_objs.add(c.get('object_text') or c.get('object_name'))
    assert '营利性公司' in current_objs
    assert '人工智能研究实验室' not in current_objs

    # 9) audit trail: the operation and the evolution relation are traceable
    op = OperationRepository().get(result['operation_id'])
    assert op['kind'] == 'CORRECT'
    relations = claims.relations_for_claim(new_id)
    assert relations[0]['relationship'] == 'supersedes'
    assert result['superseded_claim_id'] == old['id']


def test_correction_is_a_no_op_until_confirmed(tmp_path):
    """Planning must never write; only the applied operation changes knowledge."""
    _reload(tmp_path)
    from app.repositories.claim_repo import ClaimRepository
    from app.repositories.operation_repo import OperationRepository
    from app.workflows.correction_workflow import CorrectionIntent, build_plan

    _ingest_with_claim(tmp_path, subject='OpenAI', predicate='defined_as',
                       obj='人工智能研究实验室', evidence_quote='OpenAI 是一家人工智能研究实验室')

    intent = CorrectionIntent(text=CORRECTION_TEXT, subject='OpenAI',
                              predicate='defined_as', object='营利性公司')
    build_plan(CORRECTION_TEXT, intent)   # pure planning

    assert len(ClaimRepository().list(10)) == 1
    assert OperationRepository().count() == 0


def test_parse_intent_declines_without_a_model(tmp_path):
    _reload(tmp_path)
    from app.workflows.correction_workflow import parse_intent
    assert asyncio.run(parse_intent('OpenAI 是一家营利性公司。')) is None

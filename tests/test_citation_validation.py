"""Citation Validation: deterministic dimensions + injected LLM grounding.

Covers the pure scorer (no DB, no model), the DB-backed currentness/locatability
checks against a temp database, and the API endpoint (which degrades to the
deterministic dimensions when no model is configured).
"""
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
    'app.repositories.operation_repo',
    'app.repositories.event_repo',
    'app.repositories.idea_repo',
    'app.repositories.question_repo',
    'app.repositories.research_repo',
    'app.repositories.run_repo',
    'app.repositories.catalog_repo',
    'app.resolution',
    'app.knowledge',
    'app.domain.citation_validation',
    'app.workflows.citation_validation_workflow',
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


from app.domain.citation_validation import validate_citations


# --------------------------------------------------------------------------- #
# pure layer (no DB, no model)
# --------------------------------------------------------------------------- #
def test_insufficient_evidence_answer_is_fully_covered():
    """ADR-014: "the knowledge base lacks evidence" is a safety stop, not a refusal."""
    r = validate_citations('OpenAI 是做什么的？', '知识库中没有找到与该问题匹配的证据。', [], llm=None)
    assert r.dimensions['coverage'] == 1.0
    assert r.dimensions['support'] is None
    assert 'insufficient_evidence' in r.flags
    assert 'refusal' not in r.flags                  # the two kinds are not synonyms
    assert r.refusal['kind'] == 'insufficient_evidence'
    assert r.overall >= 0.4


def test_an_explicit_refusal_is_flagged_as_such():
    r = validate_citations('OpenAI 是做什么的？', '我无法回答这个问题。', [], llm=None)
    assert r.flags.count('refusal') == 1
    assert 'insufficient_evidence' not in r.flags
    assert r.dimensions['coverage'] == 1.0


def test_a_short_answer_without_citations_is_not_excused():
    """Regression guard (ADR-014): "short" used to mean "refusal" and bought free
    coverage, which let an untraceable answer score as fully covered."""
    r = validate_citations('Apple 的 CEO 是谁？', 'John Ternus。', [], llm=None)
    assert 'refusal' not in r.flags
    assert r.dimensions['coverage'] == 0.2           # untraceable, not a refusal
    assert 'no_citations' in r.flags


def test_inline_citations_match_full_coverage():
    answer = 'OpenAI 是一家实验室 [doc:d1 chunk:k1]。'
    r = validate_citations('OpenAI 是做什么的？', answer,
                           [{'document_id': 'd1', 'chunk_id': 'k1'}], llm=None, conn=None)
    assert r.dimensions['coverage'] == 1.0
    assert 'no_inline_citations' not in r.flags
    assert 'uncited_evidence' not in r.flags


def test_citations_without_inline_markers_flagged():
    answer = 'OpenAI 是一家实验室。'
    r = validate_citations('OpenAI 是做什么的？', answer,
                           [{'document_id': 'd1', 'chunk_id': 'k1'}], llm=None, conn=None)
    assert r.dimensions['coverage'] == 0.45
    assert 'no_inline_citations' in r.flags


def test_llm_grounding_merges_into_support():
    fake_llm = lambda prompt: '{"grounded": true, "rationale": "ok", "claims": [{"claim": "x", "supported": true, "citation": "chunk:k1"}]}'
    r = validate_citations('q', 'OpenAI 是实验室 [doc:d1 chunk:k1]。',
                           [{'document_id': 'd1', 'chunk_id': 'k1'}], llm=fake_llm, conn=None)
    assert r.dimensions['support'] == 1.0
    assert r.grounding is not None
    assert r.grounding['assertions'][0]['supported'] is True


def test_llm_unavailable_degrades_gracefully():
    def boom(prompt):
        raise RuntimeError('no model')
    r = validate_citations('q', 'OpenAI 是实验室 [doc:d1 chunk:k1]。',
                           [{'document_id': 'd1', 'chunk_id': 'k1'}], llm=boom, conn=None)
    assert r.dimensions['support'] is None
    assert 'llm_unavailable' in r.flags


# --------------------------------------------------------------------------- #
# DB-backed dimensions
# --------------------------------------------------------------------------- #
def _seed(tmp_path):
    _reload(tmp_path)
    from app.chunking import chunk_text
    from app.repositories.document_repo import DocumentRepository
    from app.repositories.entity_repo import EntityRepository
    from app.repositories.claim_repo import ClaimRepository
    from app.ontology import normalize_name

    content = 'OpenAI 是一家人工智能研究实验室。它由 Sam 领导。'
    documents = DocumentRepository()
    doc_id = documents.create(title='About OpenAI', content=content, source_type='note')
    chunk = documents.replace_chunks(doc_id, [(c.content, c.index, c.start_offset, c.end_offset)
                                              for c in chunk_text(content)])[0]
    entities = EntityRepository()
    eid = 'e-openai'
    entities.insert(eid, type='Organization', types=['Organization'], name='OpenAI',
                    aliases=[], description='An AI lab', properties={}, normalize=normalize_name)

    ClaimRepository().insert(
        subject_id=eid, predicate='defined_as', object_id=None, object_text='人工智能研究实验室',
        content=None, context={}, claim_type='definitional', polarity='positive', modality='asserted',
        confidence=0.9, status='verified', created_by='llm',
        source_document_id=doc_id, source_chunk_id=chunk['id'],
        source_start_offset=chunk['start_offset'], source_end_offset=chunk['end_offset'],
        source_quote='OpenAI 是一家人工智能研究实验室')
    # A later, contradicting correction already superseded the original assertion.
    ClaimRepository().insert(
        subject_id=eid, predicate='defined_as', object_id=None, object_text='营利性公司',
        content=None, context={}, claim_type='definitional', polarity='positive', modality='asserted',
        confidence=0.95, status='superseded', created_by='llm',
        source_document_id=doc_id, source_chunk_id=chunk['id'],
        source_start_offset=chunk['start_offset'], source_end_offset=chunk['end_offset'],
        source_quote='OpenAI 是一家人工智能研究实验室')
    return chunk['id'], doc_id


def test_locatability_and_currentness_against_db(tmp_path):
    chunk_id, doc_id = _seed(tmp_path)
    from app.db import connect as db_connect
    citations = [{'document_id': doc_id, 'chunk_id': chunk_id}]
    r = validate_citations('OpenAI 是做什么的？', 'OpenAI 是实验室 [doc:%s chunk:%s]。' % (doc_id, chunk_id),
                           citations, llm=None, conn=db_connect())
    assert r.dimensions['locatability'] == 1.0
    # The cited chunk is the source of a superseded claim -> not fully current.
    assert r.dimensions['currentness'] < 1.0
    assert 'stale_source' in r.flags


def test_endpoint_validate(tmp_path):
    chunk_id, doc_id = _seed(tmp_path)
    from fastapi.testclient import TestClient
    import app.main as main
    importlib.reload(main)
    client = TestClient(main.app)

    body = {
        'question': 'OpenAI 是做什么的？',
        'answer': 'OpenAI 是实验室 [doc:%s chunk:%s]。' % (doc_id, chunk_id),
        'citations': [{'document_id': doc_id, 'chunk_id': chunk_id}],
    }
    r = client.post('/api/qa/validate', json=body)
    assert r.status_code == 200
    data = r.json()
    assert set(['locatability', 'coverage', 'currentness', 'support']).issubset(data['dimensions'])
    assert isinstance(data['overall'], (int, float))
    # No model configured in tests -> deterministic-only with llm_unavailable flag.
    assert data['dimensions']['support'] is None
    assert 'llm_unavailable' in data['flags']

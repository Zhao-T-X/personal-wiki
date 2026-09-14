"""KnowledgeQualityScore: deterministic, no LLM, no DB for the pure layer.

Covers both the pure scorer (ClaimQualityInput -> ClaimQuality) and the
DB-backed assembler (score_claim_by_id / review_queue) against a temp database.
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
    'app.repositories.event_repo',
    'app.repositories.idea_repo',
    'app.repositories.question_repo',
    'app.repositories.research_repo',
    'app.repositories.run_repo',
    'app.repositories.catalog_repo',
    'app.resolution',
    'app.knowledge',
    'app.domain.knowledge_quality',
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


from app.domain.knowledge_quality import (ClaimQualityInput, ClaimQuality,
                                           score_claim, suggest_review, review_queue,
                                           score_claim_by_id)


# --------------------------------------------------------------------------- #
# pure layer
# --------------------------------------------------------------------------- #
def _good() -> ClaimQualityInput:
    return ClaimQualityInput(
        claim_id='c1', subject_name='OpenAI', predicate='defined_as',
        claim_type='definitional', polarity='positive', modality='asserted',
        context={}, confidence=0.9, object_text='人工智能研究实验室',
        source_document_id='d1', source_chunk_id='k1',
        source_quote='OpenAI 是一家人工智能研究实验室',
        source_chunk_content='OpenAI 是一家人工智能研究实验室。它由 Sam 领导。',
        subject_entity={'description': 'An AI lab'}, predicate_registered=True,
    )


def _bad() -> ClaimQualityInput:
    return ClaimQualityInput(
        claim_id='c2', subject_name='OpenAI', predicate='ceo_of',
        claim_type='factual', polarity='positive', modality='asserted',
        context={}, confidence=0.2, object_text='Alice',
        source_document_id=None, source_chunk_id=None, source_quote=None,
        source_chunk_content=None, subject_entity=None,
        predicate_registered=False,
    )


def test_pure_good_claim_scores_high():
    q = score_claim(_good())
    assert q.dimensions['predicate'] == 1.0
    assert q.dimensions['evidence'] == 1.0
    assert q.dimensions['quote'] == 1.0
    assert q.dimensions['provenance'] == 1.0
    assert q.overall >= 0.85
    assert q.grade == 'A'
    assert q.flags == []


def test_pure_bad_claim_scores_low_and_flags():
    q = score_claim(_bad())
    assert q.dimensions['predicate'] == 0.0
    assert q.dimensions['evidence'] == 0.0
    assert q.dimensions['provenance'] == 0.0
    assert q.overall < 0.5
    assert 'predicate_unregistered' in q.flags
    assert 'no_evidence' in q.flags
    assert 'low_confidence' in q.flags


def test_quote_not_localized_is_flagged_but_partial():
    d = _good()
    d.source_chunk_content = 'completely unrelated text here'
    q = score_claim(d)
    assert q.dimensions['quote'] == 0.2
    assert 'quote_not_localized' in q.flags


def test_superseded_conflict_is_resolved():
    d = _good()
    d.is_superseded = True
    q = score_claim(d)
    assert q.dimensions['conflict'] == 1.0
    assert 'superseded' in q.flags


def test_live_conflict_penalised():
    d = _good()
    d.conflict_live = True
    q = score_claim(d)
    assert q.dimensions['conflict'] == 0.3
    assert 'unresolved_conflict' in q.flags


def test_suggest_review_uses_threshold_and_critical_flags():
    good = score_claim(_good())
    bad = score_claim(_bad())
    queued = suggest_review([good, bad], threshold=0.7)
    assert bad.claim_id in queued
    assert good.claim_id not in queued


# --------------------------------------------------------------------------- #
# DB-backed assembler
# --------------------------------------------------------------------------- #
def _seed(tmp_path):
    _reload(tmp_path)
    from app.chunking import chunk_text
    from app.db import transaction
    from app.repositories.document_repo import DocumentRepository
    from app.repositories.entity_repo import EntityRepository
    from app.repositories.claim_repo import ClaimRepository
    from app.ontology import normalize_name

    documents = DocumentRepository()
    doc_id = documents.create(title='About OpenAI', content=_good().source_chunk_content,
                              source_type='note')
    chunk = documents.replace_chunks(
        doc_id, [(c.content, c.index, c.start_offset, c.end_offset)
                 for c in chunk_text(_good().source_chunk_content)])[0]
    entities = EntityRepository()
    eid = 'e-openai'
    entities.insert(eid, type='Organization', types=['Organization'], name='OpenAI',
                    aliases=[], description='An AI research lab', properties={},
                    normalize=normalize_name)

    good_id = ClaimRepository().insert(
        subject_id=eid, predicate='defined_as', object_id=None,
        object_text='人工智能研究实验室', content=None, context={},
        claim_type='definitional', polarity='positive', modality='asserted',
        confidence=0.9, status='candidate', created_by='llm',
        source_document_id=doc_id, source_chunk_id=chunk['id'],
        source_start_offset=chunk['start_offset'], source_end_offset=chunk['end_offset'],
        source_quote='OpenAI 是一家人工智能研究实验室')

    bad_id = ClaimRepository().insert(
        subject_id=eid, predicate='ceo_of', object_id=None,
        object_text='Alice', content=None, context={},
        claim_type='factual', polarity='positive', modality='asserted',
        confidence=0.2, status='candidate', created_by='llm',
        source_document_id=doc_id, source_chunk_id=chunk['id'],
        source_start_offset=chunk['start_offset'], source_end_offset=chunk['end_offset'],
        source_quote=None)
    return good_id, bad_id


def test_score_claim_by_id_high(tmp_path):
    good_id, _ = _seed(tmp_path)
    q = score_claim_by_id(good_id)
    assert q is not None
    assert q.grade in ('A', 'B')
    assert q.overall >= 0.8
    assert 'predicate_unregistered' not in q.flags


def test_score_claim_by_id_low_and_review_queue(tmp_path):
    good_id, bad_id = _seed(tmp_path)
    q = score_claim_by_id(bad_id)
    assert q is not None
    assert q.overall < 0.5
    assert 'predicate_unregistered' in q.flags

    queued = review_queue(limit=20, threshold=0.7)
    ids = [item['claim_id'] for item in queued]
    assert bad_id in ids
    assert good_id not in ids


def test_quality_endpoint(tmp_path):
    good_id, bad_id = _seed(tmp_path)
    from fastapi.testclient import TestClient
    import app.main as main
    importlib.reload(main)
    client = TestClient(main.app)

    r = client.get(f'/api/knowledge/claims/{bad_id}/quality')
    assert r.status_code == 200
    body = r.json()
    assert body['claim_id'] == bad_id
    assert body['overall'] < 0.5
    assert 'predicate_unregistered' in body['flags']

    r2 = client.get('/api/knowledge/quality/review?threshold=0.7')
    assert r2.status_code == 200
    ids = [item['claim_id'] for item in r2.json()]
    assert bad_id in ids
    assert good_id not in ids

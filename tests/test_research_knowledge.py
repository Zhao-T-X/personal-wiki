"""Research ends in knowledge — compiled, proposed, and accepted on purpose.

The chain this file pins down, end to end:

    Research findings -> document -> (compiler) -> candidate claim
                     -> [采纳] -> ACCEPT operation -> knowledge

Two boundaries decide the shape.

**Nothing writes a claim from prose.** Findings become a *document* and take the same
extraction path every other document takes, so a research candidate is compiled like
any other claim and can claim no more than its evidence supports. The alternative —
writing claims out of a conclusion — would be the one place in the product where
prose turned into knowledge without being checked.

**A candidate is a proposal, not knowledge.** It stays ``candidate`` until a human
accepts it, and accepting is an audited operation that moves a lifecycle status and
writes no content. So 「这条从哪来」 and 「谁接受的」 both have answers in storage.
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

QUESTION = '苹果公司的 CEO 变化'
FINDINGS = '苹果公司的首席执行官是约翰·特努斯。'


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


def _client(db):
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def _task(db, *, findings: str | None = FINDINGS, question: str = QUESTION) -> str:
    """A completed research task, as one looks after a run."""
    from app.repositories.research_repo import ResearchRepository
    task_id = ResearchRepository().create(question_id=None, question_text=question)
    if findings is not None:
        ResearchRepository().set_status(task_id, 'completed', findings)
    return task_id


def _fake_extraction(monkeypatch, claims: list[dict]):
    """Stand in for the model: the pipeline around it stays real."""
    import app.service as service
    from app.llm import _empty

    async def fake_extract(chunks):
        chunk_id = chunks[0]['id']
        return {**_empty(),
                'entities': [{'name': '苹果公司', 'types': ['Organization']}],
                'claims': [{**c, 'source_chunk': chunk_id} for c in claims]}

    monkeypatch.setattr(service, 'extract', fake_extract)


# --- proposing ----------------------------------------------------------------

def test_findings_are_compiled_into_pending_candidates(tmp_path, monkeypatch):
    db = _reload_db(tmp_path)
    client = _client(db)
    _fake_extraction(monkeypatch, [{
        'subject': '苹果公司', 'predicate': 'has_ceo', 'object': '约翰·特努斯',
        'content': FINDINGS, 'evidence_quote': FINDINGS, 'claim_type': 'factual',
        'polarity': 'positive', 'modality': 'asserted', 'confidence': 0.9}])
    task_id = _task(db)

    body = client.post(f'/api/research/{task_id}/candidates').json()

    assert body['knowledge'], 'a conclusion that states a fact must propose it'
    candidate = body['knowledge'][0]
    assert candidate['state'] == 'candidate'
    assert candidate['subject_label'] == '苹果公司'
    assert candidate['predicate_label'] == '首席执行官'
    assert candidate['object_label'] == '约翰·特努斯'
    # Grounded: a quote, and a document to look it up in.
    assert candidate['evidence_preview']
    assert candidate['evidence']['document_id'] == body['document_id']


def test_a_candidate_traces_back_to_the_research_that_proposed_it(tmp_path, monkeypatch):
    db = _reload_db(tmp_path)
    client = _client(db)
    _fake_extraction(monkeypatch, [{
        'subject': '苹果公司', 'predicate': 'has_ceo', 'object': '约翰·特努斯',
        'content': FINDINGS, 'evidence_quote': FINDINGS, 'claim_type': 'factual',
        'polarity': 'positive', 'modality': 'asserted', 'confidence': 0.9}])
    task_id = _task(db)

    body = client.post(f'/api/research/{task_id}/candidates').json()
    document = client.get(f"/api/documents/{body['document_id']}").json()
    claim = client.get(f"/api/claims/{body['knowledge'][0]['claim_id']}").json()

    # findings -> document (linked by origin) -> claim (linked by source document)
    assert document['source_type'] == 'research'
    assert document['source_uri'] == f'research:{task_id}'
    assert document['content'] == FINDINGS
    assert claim['source_document_id'] == body['document_id']


def test_proposing_twice_updates_one_document_instead_of_piling_up_copies(tmp_path, monkeypatch):
    db = _reload_db(tmp_path)
    client = _client(db)
    _fake_extraction(monkeypatch, [{
        'subject': '苹果公司', 'predicate': 'has_ceo', 'object': '约翰·特努斯',
        'content': FINDINGS, 'evidence_quote': FINDINGS, 'claim_type': 'factual',
        'polarity': 'positive', 'modality': 'asserted', 'confidence': 0.9}])
    task_id = _task(db)

    first = client.post(f'/api/research/{task_id}/candidates').json()
    second = client.post(f'/api/research/{task_id}/candidates').json()

    assert first['document_id'] == second['document_id']


def test_a_task_with_no_findings_cannot_propose_anything(tmp_path):
    """No source, no candidate: a conclusion is the only thing worth compiling."""
    db = _reload_db(tmp_path)
    client = _client(db)
    task_id = _task(db, findings=None)

    assert client.post(f'/api/research/{task_id}/candidates').status_code == 422
    assert client.get(f'/api/research/{task_id}').json()['knowledge'] == []


def test_an_unknown_task_is_a_404(tmp_path):
    db = _reload_db(tmp_path)
    client = _client(db)
    assert client.get('/api/research/missing').status_code == 404
    assert client.post('/api/research/missing/candidates').status_code == 422


# --- adopting -----------------------------------------------------------------

def _proposed(tmp_path, monkeypatch):
    db = _reload_db(tmp_path)
    client = _client(db)
    _fake_extraction(monkeypatch, [{
        'subject': '苹果公司', 'predicate': 'has_ceo', 'object': '约翰·特努斯',
        'content': FINDINGS, 'evidence_quote': FINDINGS, 'claim_type': 'factual',
        'polarity': 'positive', 'modality': 'asserted', 'confidence': 0.9}])
    task_id = _task(db)
    body = client.post(f'/api/research/{task_id}/candidates').json()
    return db, client, task_id, body


def test_adopting_is_an_audited_operation_that_only_moves_the_lifecycle(tmp_path, monkeypatch):
    db, client, task_id, body = _proposed(tmp_path, monkeypatch)
    claim_id = body['knowledge'][0]['claim_id']

    result = client.post('/api/knowledge/operations',
                         json={'kind': 'ACCEPT', 'payload': {'claim_id': claim_id}}).json()

    affected = result['affected']
    assert affected['claim_id'] == claim_id
    assert affected['status'] == 'verified' and affected['previous_status'] == 'candidate'
    # The adoption itself carries the link back to the research that proposed it.
    assert affected['research_task_id'] == task_id
    # It is knowledge now, so it is no longer a pending proposal…
    assert client.get(f'/api/research/{task_id}').json()['knowledge'] == []
    # …and it is knowledge *as proposed*: nothing was rewritten on the way in.
    claim = client.get(f'/api/claims/{claim_id}').json()
    assert claim['status'] == 'verified'
    assert claim['content'] == FINDINGS
    # The adoption itself is on the record.
    audit = client.get('/api/knowledge/operations?kind=ACCEPT&limit=5').json()
    assert audit and audit[0]['payload']['claim_id'] == claim_id


def test_adopting_never_retires_knowledge_behind_the_users_back(tmp_path, monkeypatch):
    """ACCEPT is not a supersede: accepting a proposal must not archive a fact.

    The disagreement is detected by the machinery that already exists — the research
    document goes through extraction like any other, so its claim is compared against
    what the wiki held and the conflict is recorded as a *candidate* relation for a
    human, not resolved by whoever happened to adopt it.
    """
    db = _reload_db(tmp_path)
    client = _client(db)
    from app.service import create_document, index_document

    # The wiki already says Tim Cook, established before the research ran.
    older = '苹果公司的首席执行官是蒂姆·库克。'
    _fake_extraction(monkeypatch, [{
        'subject': '苹果公司', 'predicate': 'has_ceo', 'object': '蒂姆·库克',
        'content': older, 'evidence_quote': older, 'claim_type': 'factual',
        'polarity': 'positive', 'modality': 'asserted', 'confidence': 0.9}])
    doc_id = create_document(title='旧公告', content=older, source_type='note',
                             source_uri=None, metadata={})
    asyncio.run(index_document(doc_id, use_llm=True))
    existing = client.get('/api/claims?limit=50').json()[0]['id']
    # Established knowledge, verified the ordinary way — ACCEPT is reserved for research
    # candidates, so it is deliberately not the operation used here.
    client.patch(f'/api/knowledge/claim/{existing}/status', json={'status': 'verified'})

    # Now research proposes the replacement value.
    _fake_extraction(monkeypatch, [{
        'subject': '苹果公司', 'predicate': 'has_ceo', 'object': '约翰·特努斯',
        'content': FINDINGS, 'evidence_quote': FINDINGS, 'claim_type': 'factual',
        'polarity': 'positive', 'modality': 'asserted', 'confidence': 0.9}])
    task_id = _task(db)
    body = client.post(f'/api/research/{task_id}/candidates').json()
    new_id = body['knowledge'][0]['claim_id']
    client.post('/api/knowledge/operations', json={'kind': 'ACCEPT', 'payload': {'claim_id': new_id}})

    # Two current values now: neither was quietly retired, and the conflict is on
    # record as a candidate — the same row an import would have written.
    assert client.get(f'/api/claims/{existing}').json()['status'] == 'verified'
    relations = client.get(f'/api/claims/{new_id}/relations').json()['relations']
    assert any(r['relationship'] == 'contradicts' and r['status'] == 'candidate'
               for r in relations)


def test_research_candidates_carry_their_origin_and_their_word(tmp_path, monkeypatch):
    """「研究候选」 is display semantics decided here, not by whichever page renders it."""
    db, client, _task_id, body = _proposed(tmp_path, monkeypatch)
    candidate = body['knowledge'][0]

    assert candidate['origin'] == 'research_candidate'
    assert candidate['status_label'] == '研究候选'
    assert candidate['state'] == 'candidate'


def test_accept_refuses_a_claim_that_did_not_come_from_research(tmp_path, monkeypatch):
    """ACCEPT is a user intent for research proposals, not a general promote button."""
    db, client, _task_id, _body = _proposed(tmp_path, monkeypatch)
    from app.service import create_document, index_document

    note = '随手记：苹果公司的首席执行官是约翰·特努斯。'
    _fake_extraction(monkeypatch, [{
        'subject': '苹果公司', 'predicate': 'has_ceo', 'object': '约翰·特努斯',
        'content': note, 'evidence_quote': note, 'claim_type': 'factual',
        'polarity': 'positive', 'modality': 'asserted', 'confidence': 0.9}])
    doc_id = create_document(title='笔记', content=note, source_type='note',
                             source_uri=None, metadata={})
    asyncio.run(index_document(doc_id, use_llm=True))
    other = next(c['id'] for c in client.get('/api/claims?limit=50').json()
                 if c['source_document_id'] == doc_id)

    response = client.post('/api/knowledge/operations',
                           json={'kind': 'ACCEPT', 'payload': {'claim_id': other}})

    assert response.status_code == 422
    assert 'research' in response.json()['detail']


def test_accept_refuses_a_claim_that_is_already_settled(tmp_path, monkeypatch):
    db, client, _task_id, body = _proposed(tmp_path, monkeypatch)
    payload = {'kind': 'ACCEPT', 'payload': {'claim_id': body['knowledge'][0]['claim_id']}}

    assert client.post('/api/knowledge/operations', json=payload).status_code == 200
    second = client.post('/api/knowledge/operations', json=payload)

    assert second.status_code == 422 and 'proposed claim' in second.json()['detail']


def test_an_unknown_status_cannot_be_adopted_into(tmp_path, monkeypatch):
    db, client, _task_id, body = _proposed(tmp_path, monkeypatch)
    response = client.post('/api/knowledge/operations', json={
        'kind': 'ACCEPT',
        'payload': {'claim_id': body['knowledge'][0]['claim_id'], 'status': 'legendary'}})
    assert response.status_code == 422

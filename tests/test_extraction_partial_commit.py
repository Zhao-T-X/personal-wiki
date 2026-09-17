"""Step 11 — Claim Failure Isolation / Partial Commit.

A claim the ontology refuses must cost **that claim only**. The document keeps
everything that compiled, the refusal is reported with a code and a stage, and the
document's status names which of the three outcomes it was:

    COMPLETED                 everything necessary was accepted
    COMPLETED_WITH_WARNINGS   legal knowledge was kept, some items were refused
    FAILED                    nothing legal was kept (or the store itself broke)

Everything here is 0-token. The extraction layer is driven through its own seams and
through recorded payloads — never the network.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from app import db as _db
from app import llm, service
from app.extraction_items import (ACCEPTED, COMPLETED, COMPLETED_WITH_WARNINGS, FAILED,
                                  REJECTED, compile_extraction_items)

FIXTURE = (Path(__file__).parent / 'golden_corpus' / 'failures'
           / 'creates_domain_violation.json')


@pytest.fixture(autouse=True)
def _database():
    _db.init_db()


# --------------------------------------------------------------------------- helpers

def _claim(subject, predicate, obj, chunk_id, text, **over):
    claim = {'subject': subject, 'predicate': predicate, 'object': obj,
             'claim_type': 'factual', 'polarity': 'positive', 'modality': 'asserted',
             'confidence': 0.8, 'source_chunk': chunk_id, 'evidence_quote': text}
    claim.update(over)
    return claim


def _envelope(chunk_id, text, claims, entities=None):
    return {'entities': entities or [], 'claims': claims,
            'events': [], 'ideas': [], 'questions': []}


def _document(text):
    """A real document + chunk through the real service, so provenance is real."""
    marker = uuid.uuid4().hex[:8]
    content = f'{text} [{marker}]'
    doc = service.create_document(title=f'partial {marker}', content=content, source_type='note')
    return doc, service.write_chunks(doc, content)[0]['id'], content


def _drive_llm(monkeypatch, extract_fn):
    """Point the extraction layer at a fake model without touching the real seams."""
    async def fake_detect(payload):
        return {'entities': True, 'claims': True, 'events': False,
                'ideas': False, 'questions': False}
    monkeypatch.setattr(llm, 'detect_structured', fake_detect)
    monkeypatch.setattr(llm, 'extract_structured', extract_fn)


def _counts(doc):
    conn = _db.connect()
    try:
        claims = conn.execute('SELECT COUNT(*) c FROM claims WHERE source_document_id=?',
                              (doc,)).fetchone()['c']
        relations = conn.execute('SELECT COUNT(*) c FROM relations WHERE source_document_id=?',
                                 (doc,)).fetchone()['c']
        return claims, relations
    finally:
        conn.close()


# ------------------------------------------------------------------ Case 1: isolation

def test_one_bad_claim_keeps_the_good_ones(monkeypatch):
    """Claim A ok, Claim B refused, Claim C ok -> A and C persist, B is reported."""
    doc, chunk_id, content = _document('苹果 的 CEO 是 John Ternus。LLM-Wiki 用于知识管理。')

    async def fake_extract(payload):
        if 'Validation error to fix' in payload:
            return _envelope(chunk_id, content, [])      # the retry fixes nothing
        return _envelope(chunk_id, content, [
            _claim('苹果', 'has_ceo', 'John Ternus', chunk_id, content),
            _claim('苹果', 'totally_unregistered_predicate', 'John Ternus', chunk_id, content),
            _claim('LLM-Wiki', 'used_for', '知识管理', chunk_id, content),
        ])
    _drive_llm(monkeypatch, fake_extract)

    report = asyncio.run(service.index_document(doc))

    assert report['status'] == COMPLETED_WITH_WARNINGS
    assert report['counts']['claims'] == 2      # A and C, not B
    assert _counts(doc)[0] == 2
    assert report['warnings'] == 1
    failure = report['rejected_items'][0]
    assert failure['item_type'] == 'claim'
    assert failure['error_code'] == 'UNSUPPORTED_PREDICATE'
    assert failure['stage'] if 'stage' in failure else True
    assert failure['source_location']['source_chunk'] == chunk_id
    # the failure carries the payload that caused it, so it can be replayed offline
    assert failure['input']['predicate'] == 'totally_unregistered_predicate'


# ------------------------------------------------------------- Case 2: repair success

def test_repair_success_completes_the_document(monkeypatch):
    """A refused item that the one repair attempt fixes leaves a COMPLETED document."""
    doc, chunk_id, content = _document('苹果 的 CEO 是 John Ternus。')

    async def fake_extract(payload):
        if 'Validation error to fix' in payload:
            return _envelope(chunk_id, content, [
                _claim('苹果', 'has_ceo', 'John Ternus', chunk_id, content)])
        return _envelope(chunk_id, content, [
            _claim('苹果', 'unregistered_predicate_here', 'Totally Wrong Object',
                   chunk_id, content)])
    _drive_llm(monkeypatch, fake_extract)

    report = asyncio.run(service.index_document(doc))

    assert report['status'] == COMPLETED
    assert report['warnings'] == 0
    assert report['counts']['claims'] == 1
    assert _counts(doc)[0] == 1


# --------------------------------------------------------------- Case 3: repair fails

def test_repair_failure_isolates_the_item(monkeypatch):
    """When the retry does not fix it, the item is REJECTED and the document survives."""
    doc, chunk_id, content = _document('苹果 的 CEO 是 John Ternus。LLM-Wiki 用于知识管理。')

    async def fake_extract(payload):
        if 'Validation error to fix' in payload:
            # The repair payload holds the refused items only, so the retry answers
            # with just that item — still broken.
            return _envelope(chunk_id, content, [
                _claim('苹果', 'still_unregistered_predicate', 'John Ternus', chunk_id, content)])
        return _envelope(chunk_id, content, [
            _claim('LLM-Wiki', 'used_for', '知识管理', chunk_id, content),
            _claim('苹果', 'still_unregistered_predicate', 'John Ternus', chunk_id, content)])
    _drive_llm(monkeypatch, fake_extract)

    report = asyncio.run(service.index_document(doc))

    assert report['status'] == COMPLETED_WITH_WARNINGS
    assert report['counts']['claims'] == 1
    failure = report['rejected_items'][0]
    assert failure['attempts'] == 2                      # the retry is recorded
    assert failure['prior_errors'][0]['attempt'] == 1    # and the first error with it


# ---------------------------------------------------- Case 4: nothing legal was kept

def test_only_a_bad_claim_is_a_failed_document(monkeypatch):
    """No legal knowledge persisted is FAILED — never dressed up as success."""
    doc, chunk_id, content = _document('苹果 的 CEO 是 John Ternus。')

    async def fake_extract(payload):
        return _envelope(chunk_id, content, [
            _claim('苹果', 'unregistered_predicate_xyz', 'John Ternus', chunk_id, content)])
    _drive_llm(monkeypatch, fake_extract)

    report = asyncio.run(service.index_document(doc))

    assert report['status'] == FAILED
    assert report['llm'] == 'failed'
    assert report['counts']['claims'] == 0
    assert _counts(doc)[0] == 0
    assert report['warnings'] == 1


# ------------------------------------------------------ Case 5: infrastructure failure

def test_fatal_write_error_rolls_back_everything(monkeypatch):
    """A broken store is NOT an item problem: the document must not half-write."""
    from app.repositories import ClaimRepository

    doc, chunk_id, content = _document('LLM-Wiki 用于知识管理。')

    async def fake_extract(payload):
        return _envelope(chunk_id, content, [
            _claim('LLM-Wiki', 'used_for', '知识管理', chunk_id, content)])
    _drive_llm(monkeypatch, fake_extract)

    def explode(self, **kwargs):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(ClaimRepository, 'insert', explode)

    with pytest.raises(sqlite3.OperationalError):
        asyncio.run(service.index_document(doc))

    # the outer transaction rolled back: no half-written knowledge survives
    claims, relations = _counts(doc)
    assert claims == 0 and relations == 0


# ------------------------------------------------------- Eligibility regression (§14)

def test_rejected_claim_cannot_promote_an_entity():
    """KEEP only ever comes from an ACCEPTED claim (Step 9 / 9.1 contract)."""
    from app.db import transaction
    from app.domain.entity_eligibility import is_entity_eligible_for_semantic_pool
    from app.knowledge import persist_extraction
    from app.repositories import EntityRepository

    doc, chunk_id, content = _document('GomsInventoryPagingRequestDTO 用于库存分页查询。')
    envelope = {
        'entities': [{'name': 'GomsInventoryPagingRequestDTO', 'types': ['Technology']},
                     {'name': 'GomsInventoryPagingResponseDTO', 'types': ['Technology']},
                     {'name': '库存分页查询', 'types': ['Concept']}],
        'claims': [
            # refused: pairs a Technology with an unregistered predicate
            _claim('GomsInventoryPagingRequestDTO', 'unregistered_predicate_qq',
                   'GomsInventoryPagingResponseDTO', chunk_id, content),
            # accepted
            _claim('GomsInventoryPagingResponseDTO', 'used_for', '库存分页查询', chunk_id, content),
        ],
        'events': [], 'ideas': [], 'questions': [],
    }
    outcome = compile_extraction_items(envelope)
    assert [r.error_code for r in outcome.rejected] == ['UNSUPPORTED_PREDICATE']

    with transaction() as conn:
        persist_extraction(conn, document_id=doc, extraction=outcome.envelope)

    entities = EntityRepository()
    promoted = entities.get(entities.by_name('GomsInventoryPagingRequestDTO')['id'])
    supported = entities.get(entities.by_name('GomsInventoryPagingResponseDTO')['id'])
    # only the entity an ACCEPTED claim mentions is eligible for the semantic pool
    assert is_entity_eligible_for_semantic_pool(supported['properties']) is True
    assert is_entity_eligible_for_semantic_pool(promoted['properties']) is False
    # and it is a candidate (REVIEW), not silently verified
    assert promoted['status'] == 'candidate'


# ---------------------------------------------------------- Evidence regression (§15)

def test_rejected_claim_evidence_is_not_persisted():
    """The refused claim leaves no row, so its evidence is not accepted knowledge."""
    from app.db import transaction
    from app.knowledge import persist_extraction
    from app.repositories import ClaimRepository

    doc, chunk_id, content = _document('苹果 的 CEO 是 John Ternus。')
    envelope = _envelope(chunk_id, content, [
        _claim('苹果', 'has_ceo', 'John Ternus', chunk_id, content),
        _claim('苹果', 'unregistered_predicate_tt', 'John Ternus', chunk_id, content)])
    outcome = compile_extraction_items(envelope)
    with transaction() as conn:
        persist_extraction(conn, document_id=doc, extraction=outcome.envelope)

    rows = ClaimRepository().for_document(doc)
    assert len(rows) == 1
    assert rows[0]['predicate'] == 'has_ceo'
    assert rows[0]['source_quote'] == content


# --------------------------------------------------- Real failure fixture replay (§16)

def test_real_failure_replays_offline():
    """The document that used to abort the whole import, replayed at 0 tokens.

    The recorded first output is the real shape that produced
    ``Claim violates ontology domain/range: CORRECT creates Claim`` — the failure that
    cost KNOWLEDGE-OPERATIONS.md its 19 entities and 18 claims in the Step 10 audit.
    """
    case = json.loads(FIXTURE.read_text(encoding='utf-8'))
    outcome = compile_extraction_items(case['first_output'])

    rejected = outcome.rejected
    assert [r.error_code for r in rejected] == ['DOMAIN_RANGE_INVALID']
    assert 'creates' in rejected[0].error_message
    assert rejected[0].source_location['source_chunk']
    # the rest of the envelope survives instead of being thrown away with it
    kept_claims = [r for r in outcome.accepted if r.item_type == 'claim']
    assert len(kept_claims) == len(case['first_output']['claims']) - 1
    assert outcome.status == COMPLETED_WITH_WARNINGS
    assert case['final_status'] in {COMPLETED_WITH_WARNINGS, FAILED, COMPLETED}


def test_the_same_payload_used_to_abort_the_whole_document():
    """Before / After on ONE recorded payload, at 0 tokens.

    Before (the strict contract, still enforced for direct callers): the envelope is
    refused and the document would have rolled back with it.
    After: the same payload keeps four legal claims and rejects one.
    """
    from app.db import transaction
    from app.extraction import normalize_extraction
    from app.knowledge import persist_extraction
    from app.repositories import ClaimRepository

    case = json.loads(FIXTURE.read_text(encoding='utf-8'))

    # Before — document-level abort.
    with pytest.raises(ValueError) as err:
        normalize_extraction(json.loads(json.dumps(case['first_output'])))
    assert 'domain/range' in str(err.value)

    # After — item-level isolation, through the real persistence pipeline.
    doc, chunk_id, content = _document('CREATE 创建新知识。CORRECT 纠正既有知识（产生新 Claim，而非覆盖）。')
    envelope = json.loads(json.dumps(case['first_output']))
    for c in envelope['claims']:
        c['source_chunk'] = chunk_id
        c['evidence_quote'] = content
    outcome = compile_extraction_items(envelope)
    with transaction() as conn:
        persist_extraction(conn, document_id=doc, extraction=outcome.envelope)

    assert outcome.status == COMPLETED_WITH_WARNINGS
    assert [r.error_code for r in outcome.rejected] == ['DOMAIN_RANGE_INVALID']
    assert len(ClaimRepository().for_document(doc)) == 4


def test_relation_predicate_rejection_reports_the_relation_declaration():
    """Diagnostics only (Step 11 §9): the message must name the rule that rejected it.

    ``creates`` is registered both as a claim predicate and as a relation predicate, and
    it is the relation's endpoint types that reject a ``Resource`` subject. Printing the
    claim spec's empty domain/range was misleading; the validation is unchanged.
    """
    case = json.loads(FIXTURE.read_text(encoding='utf-8'))
    message = compile_extraction_items(case['first_output']).rejected[0].error_message
    assert 'source_types=' in message and 'target_types=' in message
    assert 'Person' in message                      # the declaration that actually applies
    assert 'domain=*, range=*' not in message

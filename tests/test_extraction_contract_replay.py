"""Replay tests for the extraction contract — three layers, zero LLM calls.

Level 1: the recorded/expected LLM extraction (a fixture, not a live call).
Level 2: what ``KnowledgeCompiler`` / ``normalize_extraction`` must produce.
Level 3: what persistence must store (eligibility, predicate).

Running the whole file costs 0 tokens; that is the point (Step 10 test infrastructure).
"""
import json
import uuid
from pathlib import Path

import pytest

from app.extraction import normalize_extraction


def test_llm_calls_are_forbidden_by_default():
    """The guard: under the default test mode a real extraction attempt raises, so an
    accidentally-live test fails loudly instead of quietly spending money."""
    import asyncio
    from app import llm
    with pytest.raises(AssertionError):
        asyncio.run(llm.extract([{'id': 'c1', 'content': '苹果 CEO 是 John Ternus。'}]))

_CASES = json.loads(
    (Path(__file__).parent / 'fixtures' / 'extraction_cases' / 'micro_cases.json')
    .read_text(encoding='utf-8'))['cases']


@pytest.mark.parametrize('case', _CASES, ids=[c['id'] for c in _CASES])
def test_replay_llm_to_compiler(case):
    """Level 2: feed the fixed LLM output through the compiler."""
    if case.get('expect_raise'):
        with pytest.raises(ValueError):
            normalize_extraction(case['llm_extraction'])
        return
    normalized = normalize_extraction(case['llm_extraction'])
    claim = normalized['claims'][0]
    for key, expected in case['expect']['compiler'].items():
        assert claim[key] == expected, (case['id'], key, claim.get(key), expected)


def test_replay_llm_to_persistence_is_offline():
    """Level 3: the accepted envelope reaches SQLite with the right eligibility."""
    case = next(c for c in _CASES if c['id'] == 'ceo_predicate_resolution')
    marker = uuid.uuid4().hex[:8]
    content = case['text'] + f' [{marker}]'
    envelope = json.loads(json.dumps(case['llm_extraction']))
    for c in envelope['claims']:
        c['evidence_quote'] = case['text']            # must be a substring of the chunk

    from app import service
    from app.db import transaction
    from app.domain.entity_eligibility import is_entity_eligible_for_semantic_pool
    from app.knowledge import persist_extraction
    from app.repositories import ClaimRepository, EntityRepository

    doc = service.create_document(title=f'replay {marker}', content=content, source_type='note')
    chunk = service.write_chunks(doc, content)[0]
    for c in envelope['claims']:
        c['source_chunk'] = chunk['id']
    normalized = normalize_extraction(envelope)
    with transaction() as conn:
        persist_extraction(conn, document_id=doc, extraction=normalized)

    # predicate resolved by the Registry, not invented
    claim = next(c for c in ClaimRepository().by_ids(
        [r['id'] for r in ClaimRepository().for_document(doc)]) if c['predicate'] == 'has_ceo')
    assert claim['predicate'] == 'has_ceo'
    # claim-backed entities are KEEP (eligible for the semantic pool)
    apple = EntityRepository().by_name('苹果')
    assert apple is not None
    assert is_entity_eligible_for_semantic_pool(EntityRepository().get(apple['id'])['properties']) is True

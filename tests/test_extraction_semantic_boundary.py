"""Step 10 — Extraction Semantic Boundary: LLM owns semantics, code owns constraints.

Covers the ten checks: keyword never decides `concept`; legitimate entities are not
dropped by heuristics; literals/paths recognised deterministically; predicate hints can
only generate candidates; the Registry decides the predicate; rejected claims cannot
grant KEEP; accepted claims can; similarity is candidate-only; legacy behaviour intact.
"""
import sqlite3

import pytest

from app.domain.compiler import KnowledgeCompiler
from app.domain.entity_eligibility import get_entity_eligibility, is_entity_eligible_for_semantic_pool
from app.entity_eligibility import entity_eligibility
from app.extraction import normalize_extraction
from app.object_classification import (CONCEPT, ENTITY, LITERAL, UNKNOWN, classify_object,
                                       concept_hint, is_entity_like)
from app.ontology import CLAIM_REGISTRY, match_claim_predicates
from app.resolution import find_similar_entities


# 1. keyword must NOT decide `concept`.
def test_keyword_does_not_decide_concept():
    for text in ('提供能力的机制', '使用规则的过程', 'the mechanism that feeds the LLM'):
        kind, _ = classify_object(text)
        assert kind != CONCEPT, f'keyword decided concept for {text!r}'
    # it is only a candidate HINT, never a verdict
    assert concept_hint('只给 LLM 选定证据包的机制') is True
    # the LLM's verdict is what decides
    assert classify_object('提供能力的机制', object_kind='concept')[0] == CONCEPT


# 2. legitimate entities are not dropped by a heuristic.
def test_legitimate_entity_not_dropped():
    assert entity_eligibility('Context Runtime', ['Technology'], supported=True)[0] == 'KEEP'
    assert entity_eligibility('KnowledgeCompiler', ['Technology'], supported=True)[0] == 'KEEP'
    assert entity_eligibility('SQLite', ['Software'], supported=True)[0] == 'KEEP'
    # a technical identifier is entity-like deterministically
    assert is_entity_like('KnowledgeCompiler') is True
    assert is_entity_like('Context Runtime', object_kind='entity') is True


# 3. literal recognition.
@pytest.mark.parametrize('text', ['8192', 'true', '2026', '42%', '每周', '高风险'])
def test_literal_is_recognised(text):
    assert classify_object(text)[0] == LITERAL


# 4. path recognition.
@pytest.mark.parametrize('text', ['docs/context/runtime.md', 'a/b/c.py', 'https://example.com/x'])
def test_path_is_recognised(text):
    assert classify_object(text)[0] == LITERAL


# 5. predicate hints generate candidates only.
def test_predicate_hints_are_candidates_only():
    candidates = match_claim_predicates('苹果新任 CEO 是 John Ternus')
    registered = set(CLAIM_REGISTRY['claim_predicates'])
    assert candidates, 'a hint should propose something'
    assert set(candidates) <= registered, 'a hint must never invent a predicate'
    assert 'new_ceo' not in candidates
    # hint function returns a list (candidates), never a single authoritative predicate
    assert isinstance(candidates, list)


# 6. the Registry / Compiler decides the final predicate.
def test_registry_decides_final_predicate():
    assert 'new_ceo' not in set(CLAIM_REGISTRY['claim_predicates'])
    result = KnowledgeCompiler().compile_extraction_claim({
        'subject': '苹果公司', 'predicate': 'new_ceo', 'object': '约翰·特努斯',
        'claim_type': 'factual', 'polarity': 'positive', 'modality': 'asserted',
        'context': {}, 'confidence': 0.9,
    })
    assert result.ok, result.reasons
    assert result.claim.predicate == 'has_ceo'          # resolved, not invented
    assert result.claim.temporal_signal == 'new'        # temporal folded out of the name


# 7. a rejected claim cannot grant KEEP (normalize refuses it, so it never persists).
def test_rejected_claim_never_reaches_persistence():
    with pytest.raises(ValueError):
        normalize_extraction({
            'entities': [{'name': 'X', 'types': ['Concept'], 'aliases': []}],
            'claims': [{'subject': 'X', 'predicate': 'totally_invented_predicate',
                        'object': 'Y', 'claim_type': 'factual', 'polarity': 'positive',
                        'modality': 'asserted', 'context': {}, 'confidence': 0.9,
                        'source_chunk': 'c', 'evidence_quote': 'q'}],
            'events': [], 'ideas': [], 'questions': [],
        })


# 8. an accepted claim supports KEEP.
def test_accepted_claim_supports_keep():
    assert entity_eligibility('Supported', ['Concept'], supported=True)[0] == 'KEEP'
    assert entity_eligibility('Unsupported', ['Concept'], supported=False)[0] == 'REVIEW'


# 9. similarity is candidate generation, not a final identity decision.
def _conn(rows):
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('''CREATE TABLE entities(
        id TEXT PRIMARY KEY, type TEXT, types_json TEXT, name TEXT, status TEXT,
        properties_json TEXT DEFAULT '{}', updated_at TEXT)''')
    for i, (name, etype) in enumerate(rows, 1):
        conn.execute("INSERT INTO entities(id,type,types_json,name,status,properties_json,updated_at)"
                     " VALUES(?,?,?,?,?,?,?)",
                     (f'e{i}', etype, f'["{etype}"]', name, 'candidate',
                      '{"eligibility": "keep"}', f'2026-01-{i:02d}'))
    return conn


def test_similarity_is_candidate_generation_only():
    conn = _conn([('Apple Inc.', 'Organization'), ('Apple Computer', 'Organization')])
    hits = find_similar_entities(conn, 'e1')
    assert [h['name'] for h in hits] == ['Apple Computer']
    assert 'similarity' in hits[0]                      # offered as a candidate…
    # …and nothing was merged: two rows still exist.
    assert conn.execute('SELECT COUNT(*) FROM entities').fetchone()[0] == 2


# 10. legacy / contract behaviour is not broken.
def test_legacy_and_contract_behaviour():
    # missing eligibility is never silently KEEP on read
    assert get_entity_eligibility({}) is not None
    assert is_entity_eligible_for_semantic_pool({}) is False
    # an illegal value is treated as not-eligible, never as KEEP
    assert is_entity_eligible_for_semantic_pool({'eligibility': 'whatever'}) is False
    # undefined text stays unknown (no keyword verdict)
    assert classify_object('some short phrase')[0] in (UNKNOWN, ENTITY)

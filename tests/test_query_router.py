"""Query Router (task §19/§20/§21/§22; ADR-010/ADR-013).

Routing is deterministic, so it is fully unit-testable and must never depend on
a model: the same signals always produce the same route. The interesting cases
are the *boundaries* — when a question looks like a fact lookup but is not one,
it must NOT be routed to the 0-LLM path.
"""
from __future__ import annotations

from app.domain.query_router import (EVIDENCE_SYNTHESIS, FACT_LOOKUP, RESEARCH,
                                     ROUTES, STRUCTURED_REASONING, QuerySignals,
                                     classify, has_multi_hop_intent,
                                     has_temporal_intent)


def _signals(question: str, **kw) -> QuerySignals:
    return QuerySignals(question=question, **kw)


def test_fact_lookup_needs_one_subject_one_predicate_and_a_claim():
    plan = classify(_signals('苹果公司的 CEO 是谁？', subject='苹果公司',
                             predicates=('is',), direct_claim_available=True))
    assert plan.route == FACT_LOOKUP
    assert plan.llm_expected is False          # the whole point: no model needed


def test_two_predicates_are_not_a_fact_lookup():
    """Ambiguity must not be answered as if it were certain."""
    plan = classify(_signals('苹果公司怎么样？', subject='苹果公司',
                             predicates=('is', 'improves'), direct_claim_available=True))
    assert plan.route != FACT_LOOKUP
    assert plan.llm_expected is True


def test_no_current_claim_falls_back_to_synthesis():
    plan = classify(_signals('苹果公司的 CEO 是谁？', subject='苹果公司',
                             predicates=('is',), direct_claim_available=False))
    assert plan.route == EVIDENCE_SYNTHESIS


def test_no_subject_falls_back_to_synthesis():
    plan = classify(_signals('什么是 RAG？', predicates=('defined_as',),
                             direct_claim_available=True))
    assert plan.route != FACT_LOOKUP


def test_research_markers_take_priority():
    """Even with a perfect direct-lookup shape, a foresight question is research."""
    plan = classify(_signals('预测苹果公司未来的 CEO 接班风险', subject='苹果公司',
                             predicates=('is',), direct_claim_available=True))
    assert plan.route == RESEARCH


def test_multi_hop_routes_to_structured_reasoning():
    plan = classify(_signals('苹果公司的 CEO 的母校是哪里？', subject='苹果公司',
                             predicates=('is',), direct_claim_available=True,
                             multi_hop=True))
    assert plan.route == STRUCTURED_REASONING


def test_temporal_question_routes_to_structured_reasoning():
    """'who WAS the CEO' is claim evolution, not the current value (§21)."""
    signals = _signals('苹果公司上一任 CEO 是谁？', subject='苹果公司',
                       predicates=('is',), direct_claim_available=True)
    assert classify(signals).route == STRUCTURED_REASONING


def test_temporal_intent_is_detected_from_the_question():
    assert has_temporal_intent('苹果公司前任 CEO 是谁？')
    assert has_temporal_intent('Who was the CEO of Apple?')
    assert not has_temporal_intent('苹果公司的 CEO 是谁？')


def test_multi_hop_intent_is_detected():
    # Two possessive hops => a second lookup is needed.
    assert has_multi_hop_intent('苹果公司的 CEO 的母校是哪里？')
    # A single hop is still a direct lookup.
    assert not has_multi_hop_intent('苹果公司的 CEO 是谁？')


def test_synthesis_markers_route_to_synthesis():
    plan = classify(_signals('为什么 RAG 能减少幻觉？'))
    assert plan.route == EVIDENCE_SYNTHESIS


def test_default_route_is_synthesis():
    plan = classify(_signals('RAG 是什么'))
    assert plan.route == EVIDENCE_SYNTHESIS


def test_plan_is_serialisable_and_explains_itself():
    plan = classify(_signals('苹果公司的 CEO 是谁？', subject='苹果公司',
                             predicates=('is',), direct_claim_available=True))
    payload = plan.to_dict()
    assert payload['route'] == FACT_LOOKUP
    assert payload['llm_expected'] is False
    assert payload['reasons'] and isinstance(payload['reasons'], list)
    assert set(ROUTES) >= {payload['route']}


def test_routing_is_deterministic():
    signals = _signals('苹果公司的 CEO 是谁？', subject='苹果公司',
                       predicates=('is',), direct_claim_available=True)
    assert classify(signals).to_dict() == classify(signals).to_dict()

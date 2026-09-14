"""Knowledge Quality Gate (§14/§15): deterministic evidence beats the LLM.

The point of these tests is not that the arithmetic works, it is that the gate
*refuses to be talked into accepting a claim* — a confident model cannot
overrule missing evidence, and an ontology violation cannot be laundered by a
high score. Pure functions, no DB, no service.
"""
import pytest

from app.domain.quality_gate import (
    ACCEPT, ACCEPT_THRESHOLD, DETERMINISTIC_SIGNALS, DETERMINISTIC_WEIGHT_TOTAL,
    LLM_CONFIDENCE_WEIGHT, MIN_DETERMINISTIC_WEIGHT, REJECT, REVIEW,
    REVIEW_THRESHOLD, TOTAL_WEIGHT, WEIGHTS, QualitySignals,
    evaluate, single_signal_outweighs_confidence,
)


def _all_true(**overrides) -> QualitySignals:
    base = dict(ontology_valid=True, schema_complete=True, entity_resolved=True,
                evidence_found=True, quote_exact=True, no_contradiction=True)
    base.update(overrides)
    return QualitySignals(**base)


# --------------------------------------------------------------------------- #
# happy path — full deterministic agreement is accepted, with or without a model
# --------------------------------------------------------------------------- #
def test_all_deterministic_true_no_llm_confidence_is_accepted():
    result = evaluate(_all_true())
    assert result.verdict == ACCEPT
    assert result.score == pytest.approx(0.95)          # 95/100 deterministic
    assert result.score >= ACCEPT_THRESHOLD
    assert 'llm_confidence' not in result.contributions


def test_low_self_reported_confidence_does_not_veto_deterministic_evidence():
    # A model hedging at 0.0 must not drag a fully-grounded claim below accept.
    result = evaluate(_all_true(llm_confidence=0.0))
    assert result.verdict == ACCEPT
    assert result.contributions['llm_confidence'] == 0.0
    assert result.score == pytest.approx(0.95)


# --------------------------------------------------------------------------- #
# the anti-hallucination rule: confidence cannot substitute for evidence
# --------------------------------------------------------------------------- #
def test_high_confidence_without_evidence_is_not_accepted():
    result = evaluate(_all_true(evidence_found=False, quote_exact=False,
                                llm_confidence=1.0))
    assert result.verdict != ACCEPT
    assert result.verdict == REVIEW                       # best it may reach
    assert result.contributions['llm_confidence'] == LLM_CONFIDENCE_WEIGHT


def test_ontology_violation_is_rejected_even_with_perfect_score():
    result = evaluate(_all_true(ontology_valid=False, llm_confidence=1.0))
    assert result.verdict == REJECT
    # Everything else is perfect, so the score alone would have accepted it.
    assert result.score >= ACCEPT_THRESHOLD


# --------------------------------------------------------------------------- #
# the model is a weak signal — proven against the weight table itself
# --------------------------------------------------------------------------- #
def test_deterministic_weights_outweigh_the_model():
    assert sum(WEIGHTS[name] for name in DETERMINISTIC_SIGNALS) == 95
    assert DETERMINISTIC_WEIGHT_TOTAL == 95
    assert DETERMINISTIC_WEIGHT_TOTAL > LLM_CONFIDENCE_WEIGHT
    assert LLM_CONFIDENCE_WEIGHT == 5


def test_any_single_deterministic_signal_beats_llm_confidence():
    assert MIN_DETERMINISTIC_WEIGHT > LLM_CONFIDENCE_WEIGHT
    for name in DETERMINISTIC_SIGNALS:
        assert WEIGHTS[name] > LLM_CONFIDENCE_WEIGHT, name
    assert single_signal_outweighs_confidence() is True
    assert TOTAL_WEIGHT == 100


# --------------------------------------------------------------------------- #
# contributions are the raw point values the weights declare
# --------------------------------------------------------------------------- #
def test_contributions_match_weights_when_all_true():
    result = evaluate(_all_true(llm_confidence=0.5))
    for name in DETERMINISTIC_SIGNALS:
        assert result.contributions[name] == float(WEIGHTS[name])
    assert result.contributions['llm_confidence'] == pytest.approx(2.5)


def test_contributions_zero_out_failed_signals():
    result = evaluate(_all_true(schema_complete=False, entity_resolved=False))
    assert result.contributions['schema_complete'] == 0.0
    assert result.contributions['entity_resolved'] == 0.0
    assert result.contributions['evidence_found'] == 20.0


def test_llm_confidence_is_clamped_to_unit_interval():
    high = evaluate(_all_true(llm_confidence=2.0))
    low = evaluate(_all_true(llm_confidence=-1.0))
    assert high.contributions['llm_confidence'] == pytest.approx(5.0)
    assert low.contributions['llm_confidence'] == pytest.approx(0.0)


def test_thresholds_and_reject_band():
    assert ACCEPT_THRESHOLD == 0.80 and REVIEW_THRESHOLD == 0.50
    # ontology_valid failing is already a hard reject; also check the numeric band.
    weak = evaluate(QualitySignals(ontology_valid=True, schema_complete=False,
                                   entity_resolved=False, evidence_found=False,
                                   quote_exact=False, no_contradiction=False))
    assert weak.verdict == REJECT
    assert weak.score < REVIEW_THRESHOLD

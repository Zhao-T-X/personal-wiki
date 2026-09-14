"""Knowledge Quality Gate — a deterministic decision, not an LLM opinion.

This module deliberately does **not** ask a model whether a piece of knowledge
is good. Every signal it reads is a fact the runtime already knows how to check:
is the predicate/type registered, is the extraction schema complete, did entity
resolution land, is there a source-grounded quote, does the quote localize, is
there an open contradiction. Each one is a closed question with a yes/no answer.

Why this matters — the weighting is the whole point:

    The task brief (§14/§15) is explicit that **deterministic evidence must
    carry more weight than the model's self-reported confidence**. A language
    model is exactly the component that is most fluent while being most wrong,
    so its own ``confidence`` number is the *weakest* signal in the gate, never
    a tie-breaker and never an override. Concretely:

      * each deterministic signal is worth 10–20 points;
      * ``llm_confidence`` is worth at most 5 points (and 0 if not supplied);
      * therefore a *single* deterministic signal out-weighs the model's entire
        self-assessment (see :func:`single_signal_outweighs_confidence`).

Hard rules (enforced independently of the numeric score):

    1. ``ontology_valid is False`` → ``reject``. An ontology violation is not a
       quality opinion; it is an illegal claim and can never be accepted.
    2. ``evidence_found is False`` → can never be ``accept`` (capped at review).
       Ungrounded prose may be *plausible*, but plausible is not knowledge.

Pure logic: standard library only, no IO, no database, no framework, no LLM
(docs/adr/ADR-011; enforced by tests/test_architecture.py).
"""
from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# Weights — the single source of truth. Six deterministic signals plus one
# advisory signal. They sum to 100 points.
#
# Per the brief (§15) these values are fixed and must not drift: combining them
# with a different table would silently change what the gate accepts.
# --------------------------------------------------------------------------- #
WEIGHTS: dict[str, int] = {
    'ontology_valid': 20,     # predicate/entity types are registered
    'schema_complete': 15,    # required extraction fields all present
    'entity_resolved': 15,    # subject/object resolve to entities
    'evidence_found': 20,     # a source chunk + quote back the claim
    'quote_exact': 15,        # the quote actually localizes in the chunk
    'no_contradiction': 10,   # no unresolved contradiction with current knowledge
}

# The model's opinion. Capped, and the only signal that may be absent entirely.
LLM_CONFIDENCE_WEIGHT = 5

DETERMINISTIC_SIGNALS: tuple[str, ...] = tuple(WEIGHTS)
DETERMINISTIC_WEIGHT_TOTAL = sum(WEIGHTS.values())
TOTAL_WEIGHT = DETERMINISTIC_WEIGHT_TOTAL + LLM_CONFIDENCE_WEIGHT

# The smallest deterministic signal. Exported so the "determinism beats the
# model" claim is checked in code, not merely asserted in a comment.
MIN_DETERMINISTIC_WEIGHT = min(WEIGHTS.values())

# Verdict thresholds, applied to the normalised 0..1 score.
ACCEPT_THRESHOLD = 0.80
REVIEW_THRESHOLD = 0.50

ACCEPT = 'accept'
REVIEW = 'review'
REJECT = 'reject'


@dataclass(frozen=True)
class QualitySignals:
    """The inputs to a gate decision. Every field is already-known evidence.

    ``llm_confidence`` is the *only* model-supplied value and the only one that
    may be ``None`` (meaning "not supplied" — which still lets a fully-grounded
    claim pass, because determinism is what we trust).
    """

    ontology_valid: bool
    schema_complete: bool
    entity_resolved: bool
    evidence_found: bool
    quote_exact: bool
    no_contradiction: bool
    llm_confidence: float | None = None


@dataclass(frozen=True)
class QualityAssessment:
    """The gate's verdict, plus *why* — so a human reviewer can audit it.

    ``contributions`` holds each signal's **raw point value** (what it added
    before the final division into the 0..1 ``score``); ``reasons`` explains the
    verdict in plain language.
    """

    score: float
    verdict: str
    contributions: dict[str, float]
    reasons: list[str]


def _clamp(value: float, low: float, high: float) -> float:
    if value < low:
        return low
    if value > high:
        return high
    return value


def single_signal_outweighs_confidence() -> bool:
    """Prove the design invariant: any deterministic signal > the model's word.

    Returns ``True`` iff the *weakest* deterministic signal is worth strictly
    more than the model's self-reported confidence at full strength. Exported as
    a function (next to :data:`MIN_DETERMINISTIC_WEIGHT`) so a regression in the
    weight table fails loudly instead of quietly re-weighting the model.
    """
    return MIN_DETERMINISTIC_WEIGHT > LLM_CONFIDENCE_WEIGHT


def _criteria(signals: QualitySignals) -> list[str]:
    """Which deterministic checks failed, in weight-descending order."""
    failed = [name for name in DETERMINISTIC_SIGNALS if not getattr(signals, name)]
    failed.sort(key=lambda name: WEIGHTS[name], reverse=True)
    return failed


def _verdict(signals: QualitySignals, score: float) -> str:
    # Hard rule 1: an ontology violation is rejected no matter how good the rest
    # looks — a high score must never launder an illegal claim.
    if not signals.ontology_valid:
        return REJECT

    if score >= ACCEPT_THRESHOLD:
        base = ACCEPT
    elif score >= REVIEW_THRESHOLD:
        base = REVIEW
    else:
        base = REJECT

    # Hard rule 2: no grounded evidence means never auto-accept.
    if base == ACCEPT and not signals.evidence_found:
        return REVIEW
    return base


def evaluate(signals: QualitySignals) -> QualityAssessment:
    """Turn evidence signals into a single ``accept`` / ``review`` / ``reject``.

    Deterministic signals contribute their full weight when true and zero when
    false. ``llm_confidence`` (when supplied) contributes
    ``LLM_CONFIDENCE_WEIGHT * clamp(value, 0, 1)`` — it can nudge a score but,
    being worth at most 5/100, can never on its own move a verdict.
    """
    contributions: dict[str, float] = {}
    for name in DETERMINISTIC_SIGNALS:
        contributions[name] = float(WEIGHTS[name]) if getattr(signals, name) else 0.0

    if signals.llm_confidence is not None:
        contributions['llm_confidence'] = (
            LLM_CONFIDENCE_WEIGHT * _clamp(float(signals.llm_confidence), 0.0, 1.0)
        )

    score = round(sum(contributions.values()) / TOTAL_WEIGHT, 4)
    verdict = _verdict(signals, score)

    reasons: list[str] = []
    if not signals.ontology_valid:
        reasons.append(
            'ontology_violation: the claim uses an unregistered predicate or '
            'type — rejected regardless of score.')
    if not signals.evidence_found:
        reasons.append(
            'no_grounded_evidence: a claim without a source-grounded quote can '
            'never be auto-accepted.')
    for name in _criteria(signals):
        if name in ('ontology_valid', 'evidence_found'):
            continue
        reasons.append(f'missing_signal: {name} failed (weight {WEIGHTS[name]}).')
    if signals.llm_confidence is not None:
        reasons.append(
            f'llm_confidence={signals.llm_confidence!r} is advisory only '
            f'(weight {LLM_CONFIDENCE_WEIGHT}/{TOTAL_WEIGHT}); deterministic '
            'signals are 10-20 points each.')
    reasons.append(
        f'score {score:.4f} vs accept>={ACCEPT_THRESHOLD}/review>={REVIEW_THRESHOLD} '
        f'-> {verdict}.')

    return QualityAssessment(score=score, verdict=verdict,
                             contributions=contributions, reasons=reasons)

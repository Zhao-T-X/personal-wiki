"""Claim Evolution judgement — registry-driven and deterministic.

"Does this new statement *replace* the stored one, or merely sit beside it?"

The answer must never be a guess, and it must never be a hardcoded predicate
list (§31). It is decided from ontology facts the registry already declares, plus
the temporal signal the draft carried:

    functional (single-valued)  +  temporal / supersedable  +  a change signal
        -> SUPERSEDE is justified

Everything else keeps the conservative verdict from ``claim_relations`` (a plain
contradiction goes to a human). Superseding **never deletes** anything: it moves
the older claim's lifecycle status, so history stays answerable (ADR-005).

Pure logic: no database, no framework, no LLM (ADR-011).
"""
from __future__ import annotations

from ..ontology import claim_predicate_spec

# Signals that assert a *change* — a new value holds now. Past-tense signals are
# deliberately absent: "the former CEO was X" replaces nothing, it reports history.
CHANGE_SIGNALS: frozenset[str] = frozenset({
    'new', 'current', 'next', 'successor', 'incoming', 'designated', 'now',
})


def is_evolution_signal(temporal_signal: str | None) -> bool:
    """True when the temporal signal asserts that something changed."""
    return str(temporal_signal or '').strip().casefold() in CHANGE_SIGNALS


def supersede_recommended(relationship: str, *, predicate: str,
                          temporal_signal: str | None) -> bool:
    """Whether a single-valued conflict should be planned as SUPERSEDE.

    All three conditions must hold, and each comes from somewhere with authority:
    the planner found a conflict for a *single-valued* predicate, the registry
    says the relation *evolves*, and the statement itself signalled a change.
    """
    if relationship != 'contradicts':
        return False
    spec = claim_predicate_spec(predicate)
    if spec is None or not (spec.functional and spec.temporal):
        return False
    if spec.evolution not in (None, 'supersedable'):
        return False
    return is_evolution_signal(temporal_signal)


def evolution_reason(*, predicate: str, temporal_signal: str | None) -> str:
    """A human-readable justification, shown with the plan before confirmation."""
    spec = claim_predicate_spec(predicate)
    label = (spec.label if spec and spec.label else predicate)
    return (f'「{label}」是单值且可演进的关系（registry: functional + temporal），'
            f'新陈述带有变更信号「{temporal_signal}」，因此建议取代既有断言；'
            '旧 Claim 会保留为历史，不会被删除。')

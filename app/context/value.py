"""Context value scoring.

The runtime prefers the content that carries the most information per token:

    score   = 0.35*relevance + 0.25*information_gain + 0.15*freshness
              + 0.15*evidence_strength + 0.10*task_priority
    utility = score / max(estimated_tokens, 1)

Deliberately a fixed linear model - no learned optimiser. It only has to rank
candidates consistently and be explainable in the Context Inspector.
"""
from __future__ import annotations

from .items import ContextItem

WEIGHTS = {
    'relevance': 0.35,
    'information_gain': 0.25,
    'freshness': 0.15,
    'evidence_strength': 0.15,
    'priority': 0.10,
}


def score(item: ContextItem) -> float:
    """Weighted value of an item, independent of its size (0..1)."""
    total = (
        WEIGHTS['relevance'] * item.relevance
        + WEIGHTS['information_gain'] * item.information_gain
        + WEIGHTS['freshness'] * item.freshness
        + WEIGHTS['evidence_strength'] * item.evidence_strength
        + WEIGHTS['priority'] * item.priority
    )
    return round(total, 6)


def utility(item: ContextItem) -> float:
    """Value per token: what makes a small evidence quote beat a whole chunk."""
    return score(item) / max(item.estimated_tokens, 1)


def utility_per_1k(item: ContextItem) -> float:
    """Utility scaled to a readable number for the UI (value per 1k tokens)."""
    return round(utility(item) * 1000, 4)


def rank(items: list[ContextItem]) -> list[ContextItem]:
    """Order items by required-first, then value per token, then size."""
    return sorted(
        items,
        key=lambda i: (not i.required, -utility(i), i.estimated_tokens),
    )

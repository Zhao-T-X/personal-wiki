"""Legacy predicate migration — deciding what a stored predicate *is*, without touching anything.

Claims written before the ontology gate existed can carry a predicate the registry
never declared (``new_ceo``) or one it declares only as an alias (``ceo``). Neither
is neutral:

* **Leaving them splits one meaning across several terms.** ``related(predicate=…)``
  matches on the stored string, so a claim recorded as ``ceo`` is *invisible* to a
  lookup for ``has_ceo``. Search, conflict detection, claim evolution and the
  correction planner all silently stop seeing those rows.
* **Deleting them destroys the point of the design.** Evidence, relations and the
  audit trail are what let a claim explain itself. A migration that erased rows to
  tidy the vocabulary would be a far worse defect than the one it fixed.

So the fix is to move each claim onto the predicate it always meant, and to record
the move in the audit trail. This module only *decides*; the caller writes.

Two rules it will not break:

1. **It never rewrites a predicate the registry already declares.** A migration that
   could touch valid knowledge would be a knowledge edit in disguise. Registered
   values are skipped before any resolution happens.
2. **A legacy value that maps to nothing stays ``needs_review``.** It is reported,
   never guessed at — the same legal failure exit a live write gets.

Pure logic: no database, no framework, no LLM (docs/adr/ADR-011).
"""
from __future__ import annotations

from dataclasses import dataclass

from ..ontology import CLAIM_PREDICATES, normalize_predicate
from .predicate_resolver import resolve_predicate

MIGRATE = 'migrate'
NEEDS_REVIEW = 'needs_review'


@dataclass(frozen=True)
class PredicateMigration:
    """What should happen to every claim recorded under one legacy predicate."""

    legacy: str
    claims: int
    canonical: str | None
    temporal_signal: str | None
    confidence: float
    source: str
    action: str
    reason: str

    @property
    def migratable(self) -> bool:
        return self.action == MIGRATE and self.canonical is not None

    def to_dict(self) -> dict:
        return {
            'legacy': self.legacy,
            'claims': self.claims,
            'canonical': self.canonical,
            'temporal_signal': self.temporal_signal,
            'confidence': self.confidence,
            'source': self.source,
            'action': self.action,
            'reason': self.reason,
        }


def plan_migrations(census: dict[str, int]) -> list[PredicateMigration]:
    """Turn a ``{stored_predicate: claim_count}`` census into migration decisions.

    Registration is checked against the registry itself, so the day a predicate is
    added to ``schemas/`` its claims drop out of the plan with no code change here.
    Resolution is the *same* ``PredicateResolver`` the write path uses — a migration
    must not have a second opinion about what ``new_ceo`` means.
    """
    plans: list[PredicateMigration] = []
    for legacy, count in census.items():
        normalized = normalize_predicate(legacy)
        if normalized in CLAIM_PREDICATES:
            continue                       # already canonical — not a migration
        resolution = resolve_predicate(normalized)
        if not resolution.resolved or not resolution.predicate:
            plans.append(PredicateMigration(
                legacy=legacy, claims=count, canonical=None,
                temporal_signal=resolution.temporal_signal, confidence=0.0,
                source=resolution.source, action=NEEDS_REVIEW, reason=resolution.reason))
            continue
        plans.append(PredicateMigration(
            legacy=legacy, claims=count, canonical=resolution.predicate,
            temporal_signal=resolution.temporal_signal, confidence=resolution.confidence,
            source=resolution.source, action=MIGRATE, reason=resolution.reason))
    # Migratable first, biggest first, then alphabetically: the report is read top-down.
    plans.sort(key=lambda p: (p.action != MIGRATE, -p.claims, p.legacy))
    return plans

"""Entity Eligibility — the canonical boundary for "may this Entity enter downstream
semantic processing?" (Step 9.1).

One vocabulary, one implementation. Every consumer that needs a *qualified* Entity
(Duplicate Detection, Object Linking, Entity Resolution, …) asks this module instead of
parsing ``properties['eligibility']`` itself, so the boundary cannot drift per call site.

Semantics:

* ``KEEP``   — the Entity has met the bar to enter the downstream *semantic pool*:
  identity plus the minimum knowledge support. It is **not** a claim of factual truth,
  and not permanence — only of eligibility.
* ``REVIEW`` — still a candidate / awaiting a human judgement.

Compatibility policy (deliberate):

* only ``keep`` / ``review`` are legal values;
* a **missing** value (legacy rows written before this contract) is treated as
  ``REVIEW`` — never silently promoted to ``KEEP``;
* so the Semantic Pool is ``KEEP`` only, while the Review Queue is ``REVIEW`` **plus**
  the legacy (unlabelled) rows, which keeps historical maintenance possible.

This is Domain: pure, deterministic, no database, no framework, no LLM.
"""
from __future__ import annotations

from enum import Enum

ELIGIBILITY_KEY = 'eligibility'


class EntityEligibility(str, Enum):
    KEEP = 'keep'
    REVIEW = 'review'


_LEGAL = frozenset({EntityEligibility.KEEP.value, EntityEligibility.REVIEW.value})


def get_entity_eligibility(properties) -> EntityEligibility:
    """The eligibility of an Entity from its ``properties``.

    Missing / unknown / malformed is ``REVIEW``: the boundary never defaults open.
    """
    if not isinstance(properties, dict):
        return EntityEligibility.REVIEW
    value = properties.get(ELIGIBILITY_KEY)
    if value == EntityEligibility.KEEP.value:
        return EntityEligibility.KEEP
    return EntityEligibility.REVIEW


def is_entity_eligible_for_semantic_pool(properties) -> bool:
    """May this Entity enter a semantic-pool consumer (resolution / duplicate / linking)?"""
    return get_entity_eligibility(properties) is EntityEligibility.KEEP


def should_enter_entity_review(properties) -> bool:
    """Does this Entity belong in the human Review Queue (REVIEW or unlabelled legacy)?"""
    return get_entity_eligibility(properties) is EntityEligibility.REVIEW


def validate_entity_eligibility(properties) -> EntityEligibility:
    """Strict reader: raise when eligibility is absent or illegal.

    Used where a value is *required* (writes, invariant checks) rather than tolerated,
    so a caller cannot persist an unlabelled Entity by accident.
    """
    if not isinstance(properties, dict):
        raise ValueError('properties must be a dict')
    value = properties.get(ELIGIBILITY_KEY)
    if value is None:
        raise ValueError('eligibility is required')
    if value not in _LEGAL:
        raise ValueError(f'illegal eligibility value: {value!r}')
    return EntityEligibility(value)


def eligibility_from_verdict(verdict: str) -> EntityEligibility:
    """Map an extraction verdict (``app.entity_eligibility``) onto the persisted value.

    The extraction gate emits upper-case ``KEEP`` / ``DROP`` / ``REVIEW``; only ``KEEP``
    survives as eligible (a DROP entity is never persisted at all, so it never reaches
    here in practice).
    """
    return (EntityEligibility.KEEP if str(verdict).strip().upper() == 'KEEP'
            else EntityEligibility.REVIEW)


def with_eligibility(properties, value: EntityEligibility | str) -> dict:
    """Return a copy of ``properties`` carrying the given eligibility."""
    out = dict(properties or {}) if isinstance(properties, dict) else {}
    out[ELIGIBILITY_KEY] = EntityEligibility(value).value
    return out

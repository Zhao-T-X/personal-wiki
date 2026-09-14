"""Predicate resolution: the only door from a candidate to a canonical predicate.

An LLM proposes; it does not decide. This module is the deterministic second
half of that split:

    raw candidate ("new_ceo", "optimises", "has_ceo", ...)
        -> split temporal/evolution signal out of the name
        -> match against the closed registry (exact, then alias)
        -> RESOLVED / AMBIGUOUS / UNRESOLVED

Three outcomes, none of them optional:

* ``resolved``   - exactly one registered predicate; a CanonicalClaim may exist.
* ``ambiguous``  - several registered predicates compete; a human/LLM must pick.
* ``unresolved`` - nothing in the registry matches. This is a *legal* result:
                   the caller keeps the candidate for review and creates no
                   knowledge. It is never permission to invent a predicate.

Resolution deliberately never invents a mapping. ``new`` + ``ceo`` splits into
``temporal_signal=new`` and base ``ceo``, and then ``ceo`` matches the registry
alias of ``has_ceo`` — so the candidate compiles to
``predicate=has_ceo, temporal_signal=new``. ``optimises`` maps to ``improves``
because the registry lists it as an alias. Something like ``head_of_company`` ->
``has_ceo`` is *not* performed here: with no alias and no registered base it
stays UNRESOLVED.

Pure logic: no database, no framework, no LLM (docs/adr/ADR-011).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..ontology import (CLAIM_PREDICATES, match_claim_predicates, normalize_predicate,
                        resolve_claim_predicate)

RESOLVED = 'resolved'
AMBIGUOUS = 'ambiguous'
UNRESOLVED = 'unresolved'

# Temporal / evolution vocabulary. These words describe *when* a relation holds
# (or *whether* it still holds), never *which* relation it is. Encoding them
# into a predicate is what turns one relation into a family of polluting terms
# (has_ceo / current_ceo / former_ceo / next_ceo). They belong in
# ``temporal_signal``, not in the predicate name.
TEMPORAL_SIGNALS: frozenset[str] = frozenset({
    'new', 'current', 'former', 'previous', 'prior', 'existing',
    'next', 'future', 'upcoming', 'incoming', 'outgoing', 'successor',
    'predecessor', 'past', 'interim', 'acting', 'designated',
})

_TEMPORAL_ZH_PREFIX: tuple[tuple[str, str], ...] = (
    ('新任', 'new'), ('现任', 'current'), ('前任', 'former'), ('已有', 'existing'),
    ('下一任', 'next'), ('即将', 'next'), ('未来', 'future'), ('候任', 'designated'),
    ('代理', 'acting'), ('临时', 'interim'),
)
_TEMPORAL_ZH_SUFFIX: tuple[tuple[str, str], ...] = (
    ('之前', 'former'), ('以前', 'former'), ('现任', 'current'), ('新任', 'new'),
)

_SPLIT = re.compile(r'[^a-zA-Z0-9]+')


@dataclass(frozen=True)
class PredicateResolution:
    """The verdict for one candidate predicate, and the evidence behind it."""

    status: str
    candidate: str
    predicate: str | None
    source: str
    confidence: float
    temporal_signal: str | None
    candidates: tuple[str, ...]
    reason: str

    @property
    def resolved(self) -> bool:
        return self.status == RESOLVED

    def to_dict(self) -> dict:
        return {
            'status': self.status,
            'candidate': self.candidate,
            'predicate': self.predicate,
            'source': self.source,
            'confidence': self.confidence,
            'temporal_signal': self.temporal_signal,
            'candidates': list(self.candidates),
            'reason': self.reason,
        }


def split_temporal_signal(value: str) -> tuple[str, str | None]:
    """Separate a temporal/evolution signal from a candidate predicate.

    ``new_ceo`` -> ``('ceo', 'new')``; ``ceo_new`` -> ``('ceo', 'new')``;
    ``新任CEO`` -> ``('CEO', 'new')``; ``improves`` -> ``('improves', None)``.
    The split is mechanical and lossless: nothing is discarded, the signal is
    moved to where it can be modelled as time rather than vocabulary.
    """
    raw = str(value or '').strip()
    if not raw:
        return '', None
    for prefix, signal in _TEMPORAL_ZH_PREFIX:
        if raw.startswith(prefix) and len(raw) > len(prefix):
            return raw[len(prefix):].strip(), signal
    for suffix, signal in _TEMPORAL_ZH_SUFFIX:
        if raw.endswith(suffix) and len(raw) > len(suffix):
            return raw[: -len(suffix)].strip(), signal
    normalized = normalize_predicate(raw)
    parts = [p for p in normalized.split('_') if p]
    if len(parts) > 1:
        if parts[0] in TEMPORAL_SIGNALS:
            return '_'.join(parts[1:]), parts[0]
        if parts[-1] in TEMPORAL_SIGNALS:
            return '_'.join(parts[:-1]), parts[-1]
    return normalized, None


def resolve_predicate(candidate: str | None, *, text: str = '', limit: int = 5) -> PredicateResolution:
    """Resolve one candidate predicate against the closed registry.

    ``text`` is surrounding context. It is used **only** to offer suggestions on
    an unresolved candidate - suggestions are never auto-applied, so a lucky
    keyword in the source text can never smuggle an invented predicate into
    canonical knowledge.
    """
    raw = str(candidate or '').strip()
    if not raw:
        return PredicateResolution(
            status=UNRESOLVED, candidate='', predicate=None, source='none',
            confidence=0.0, temporal_signal=None, candidates=(),
            reason='empty_predicate_candidate')

    normalized = normalize_predicate(raw)

    # 1. Already a registered predicate (case/format already folded).
    if normalized in CLAIM_PREDICATES:
        return PredicateResolution(
            status=RESOLVED, candidate=raw, predicate=normalized, source='exact',
            confidence=1.0, temporal_signal=None, candidates=(normalized,),
            reason='registered_value')

    # 1b. A registry-declared alias ("CEO" -> has_ceo). Aliases are ontology data
    #     (design time), so this maps without inventing anything.
    aliased = resolve_claim_predicate(normalized)
    if aliased:
        return PredicateResolution(
            status=RESOLVED, candidate=raw, predicate=aliased, source='registry_alias',
            confidence=0.95, temporal_signal=None, candidates=(aliased,),
            reason='registry_alias_match')

    # 2. Temporal signal split out, then exact/alias match on the base — this is
    #    the "new_ceo -> has_ceo + temporal_signal=new" path.
    base, signal = split_temporal_signal(raw)
    base_normalized = normalize_predicate(base)
    base_registered = (base_normalized if base_normalized in CLAIM_PREDICATES
                       else resolve_claim_predicate(base_normalized))
    if base_registered:
        return PredicateResolution(
            status=RESOLVED, candidate=raw, predicate=base_registered,
            source='temporal_split' if signal else 'normalized',
            confidence=0.97 if signal else 0.95, temporal_signal=signal,
            candidates=(base_registered,),
            reason='temporal_signal_separated' if signal else 'normalized_value')

    # 3. Registry alias / synonym of the candidate itself (never of free text).
    for probe in dict.fromkeys([base_normalized, normalized]):
        if not probe:
            continue
        subset = match_claim_predicates(probe, limit=limit)
        if len(subset) == 1:
            return PredicateResolution(
                status=RESOLVED, candidate=raw, predicate=subset[0], source='alias',
                confidence=0.8, temporal_signal=signal, candidates=tuple(subset),
                reason='single_registry_alias')
        if len(subset) > 1:
            return PredicateResolution(
                status=AMBIGUOUS, candidate=raw, predicate=None, source='alias',
                confidence=0.0, temporal_signal=signal, candidates=tuple(subset),
                reason='multiple_registry_candidates')

    # 4. Legal failure exit. Context may suggest candidates for a human, but the
    #    candidate itself is not knowledge and nothing is created from it.
    suggestions = tuple(match_claim_predicates(text, limit=limit)) if text else ()
    return PredicateResolution(
        status=UNRESOLVED, candidate=raw, predicate=None, source='none',
        confidence=0.0, temporal_signal=signal, candidates=suggestions,
        reason='no_registry_match')

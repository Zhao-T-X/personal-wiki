"""ONTOLOGY MUTATION POLICY — the ontology is closed at runtime.

This module is the single, system-level statement of a rule that must never be
restated per-agent: **the language of knowledge is fixed, and no runtime path
may extend it.**

The LLM understands knowledge; it does not define the vocabulary of knowledge.
Agents may therefore *select* from the registry and may *propose* a candidate,
but a value that is not registered can never become persisted knowledge. That
guarantee cannot live in a prompt, because a prompt is guidance, not a
constraint — it lives here, and every layer downstream (Schema, Resolver,
Domain, Operation, Repository) is expected to enforce it.

The eight rules, verbatim:

1. Predicate Registry is closed at runtime.
2. LLM cannot create predicates.
3. Agents cannot create predicates.
4. Workflows cannot create predicates.
5. Repository cannot create predicates.
6. Only explicit ontology maintenance may modify the registry.
7. Runtime knowledge generation may only reference existing predicates.
8. Unresolved predicates never become persisted knowledge.

Consequently ``new_ceo``, ``former_ceo``, ``current_ceo``, ``future_ceo`` and
``next_ceo`` are not smaller problems to be merged later — they are the exact
failure this policy exists to prevent.

Pure logic: no database, no framework, no LLM (docs/adr/ADR-002, ADR-011).
"""
from __future__ import annotations

from ..ontology import (
    CLAIM_PREDICATES,
    CLAIM_TYPES,
    ENTITY_TYPES,
    EVENT_TYPES,
    MODALITIES,
    POLARITIES,
    QUESTION_TYPES,
    RELATION_TYPES,
)

ONTOLOGY_MUTATION_POLICY: tuple[str, ...] = (
    'Predicate Registry is closed at runtime.',
    'LLM cannot create predicates.',
    'Agents cannot create predicates.',
    'Workflows cannot create predicates.',
    'Repository cannot create predicates.',
    'Only explicit ontology maintenance may modify the registry.',
    'Runtime knowledge generation may only reference existing predicates.',
    'Unresolved predicates never become persisted knowledge.',
)

# The only surface through which the ontology may legitimately change. Editing
# these files is an explicit maintenance act (rule 6), reviewed like code; no
# runtime path is allowed to write ontology values.
ONTOLOGY_WRITE_SURFACE: tuple[str, ...] = ('schemas/*-registry.json',)

_KINDS: dict[str, frozenset[str]] = {
    'entity_type': frozenset(ENTITY_TYPES),
    'claim_predicate': frozenset(CLAIM_PREDICATES),
    'relation_predicate': frozenset(RELATION_TYPES),
    'event_type': frozenset(EVENT_TYPES),
    'question_type': frozenset(QUESTION_TYPES),
    'claim_type': frozenset(CLAIM_TYPES),
    'polarity': frozenset(POLARITIES),
    'modality': frozenset(MODALITIES),
}


class OntologyViolation(ValueError):
    """A runtime value attempted to extend or escape the closed ontology.

    Raised at the boundary where an unregistered value would otherwise have
    been accepted, so the failure is a *rejection*, not a silent new term.
    """


def ontology_kinds() -> tuple[str, ...]:
    """The controlled vocabularies this policy governs."""
    return tuple(_KINDS)


def ontology_values(kind: str) -> frozenset[str]:
    """Every registered value of ``kind`` — the only legal values there are."""
    try:
        return _KINDS[kind]
    except KeyError as exc:
        raise OntologyViolation(f'Unknown ontology kind: {kind}') from exc


def is_registered(kind: str, value: str) -> bool:
    """True when ``value`` already exists in the registry for ``kind``."""
    values = _KINDS.get(kind)
    return bool(values) and str(value).strip() in values


def require_registered(kind: str, value: str) -> str:
    """Return ``value`` when registered, otherwise refuse to extend the ontology.

    This is the enforcement point for rules 1-5 and 7: callers get a clean
    rejection instead of the ability to mint a new term.
    """
    if kind not in _KINDS:
        raise OntologyViolation(f'Unknown ontology kind: {kind}')
    text = str(value).strip()
    if text not in _KINDS[kind]:
        raise OntologyViolation(
            f'"{text}" is not a registered {kind}; the ontology is closed at runtime '
            f'(ONTOLOGY MUTATION POLICY rules 1-5).')
    return text


def policy_text() -> str:
    """Render the policy for prompts, docs and trace output."""
    rules = '\n'.join(f'{i}. {rule}' for i, rule in enumerate(ONTOLOGY_MUTATION_POLICY, 1))
    return f'ONTOLOGY MUTATION POLICY\n{rules}'

"""The single definition of "current knowledge".

Search, Graph, Knowledge QA, Review, Correction and Research all ask *this*
module what the wiki currently asserts. There must not be a second opinion
anywhere — otherwise QA and Graph can disagree about which claim is current.

Pure logic: it takes claim dictionaries and returns filtered/annotated ones. It
performs no IO, so it is trivially testable and cannot drift from the database.
"""
from __future__ import annotations

SUPERSEDED = 'superseded'


def is_current(claim: dict) -> bool:
    """A superseded claim is history, not current knowledge."""
    return claim.get('status') != SUPERSEDED


def current(claims: list[dict]) -> list[dict]:
    return [c for c in claims if is_current(c)]


def history(claims: list[dict]) -> list[dict]:
    """Superseded claims — kept forever so "what was true" stays answerable."""
    return [c for c in claims if not is_current(c)]


def resolve(claims: list[dict]) -> list[dict]:
    """Prefer current knowledge; never delete history.

    - A superseded claim whose ``(subject, predicate)`` is covered by a current
      claim *in the same result set* is dropped: "who is the CEO" must never be
      answered from a statement that was replaced.
    - A superseded claim with nothing current covering it is kept and flagged
      ``lifecycle='superseded'``: "who *was* the CEO" still needs its evidence.

    Note on scope: this coverage rule is about *alternatives present in the
    result set*. It is deliberately separate from
    ``claim_relations.FUNCTIONAL_PREDICATES``, which is a much narrower set used
    for conflict detection (contradicts vs coexists) — not for currentness.
    """
    if not claims:
        return claims
    covered = {(c.get('subject_name'), c.get('predicate')) for c in claims if is_current(c)}
    out: list[dict] = []
    for claim in claims:
        if is_current(claim):
            out.append(claim)
        elif (claim.get('subject_name'), claim.get('predicate')) in covered:
            continue
        else:
            out.append({**claim, 'lifecycle': SUPERSEDED})
    return out


def select_current(claims: list[dict]) -> list[dict]:
    """Convenience for callers that only want the current rows (no history)."""
    return [c for c in resolve(claims) if is_current(c)]

"""Knowledge evolution, ordered — the chain one fact sits in.

A claim is never overwritten (ADR-005). "Tim Cook is CEO" becoming "John Ternus is
CEO" writes a *new* claim plus an accepted ``supersedes`` relation pointing at the
older one, which keeps its own evidence and its own history. So the history of a
fact is a directed path in that relation graph — and answering "what did this used
to say, and when did that stop being true?" is a walk, not a query.

That walk lives here, apart from storage, because it is a statement about
knowledge rather than about SQL: a chain has an order, an oldest member, a current
member, and — if the data is malformed — a cycle. Deciding those is pure logic.

Pure module: no database, no framework, no LLM (docs/adr/ADR-011).
"""
from __future__ import annotations

from dataclasses import dataclass

# An edge names the *newer* claim and the one it replaced.
SOURCE_KEY = 'source_claim_id'
TARGET_KEY = 'target_claim_id'


@dataclass(frozen=True)
class ClaimHistory:
    """One fact's evolution, oldest first."""

    ordered_ids: list[str]
    current_id: str
    cycles: bool

    @property
    def superseded_count(self) -> int:
        """How many earlier statements this fact has moved through."""
        return max(0, len(self.ordered_ids) - 1)


def order_chain(edges: list[dict], seed_id: str) -> ClaimHistory:
    """Order the supersede chain containing ``seed_id``, oldest first.

    ``edges`` pairs each newer claim with the one it replaced, accepted only. The
    seed may be any member of the chain — the newest, the oldest, or one in the
    middle — because a caller reaching history from a search result has no idea
    where in the timeline it just landed.

    The walk is bounded by the edge count, so malformed data (a chain that loops
    back on itself) terminates and is *reported* rather than hung on or silently
    truncated: an impossible history is a fact about the data worth surfacing.
    """
    supersedes: dict[str, str] = {}    # newer -> older
    replaced_by: dict[str, str] = {}   # older -> newer
    for edge in edges:
        newer, older = edge.get(SOURCE_KEY), edge.get(TARGET_KEY)
        if not newer or not older or newer == older:
            continue
        supersedes.setdefault(newer, older)
        replaced_by.setdefault(older, newer)

    if seed_id not in supersedes and seed_id not in replaced_by:
        return ClaimHistory([seed_id], seed_id, False)

    # Back to the oldest ancestor…
    oldest, seen, cycles = seed_id, {seed_id}, False
    while oldest in supersedes:
        previous = supersedes[oldest]
        if previous in seen:
            cycles = True
            break
        seen.add(previous)
        oldest = previous

    # …then forward to today.
    ordered, cursor = [oldest], oldest
    while cursor in replaced_by:
        following = replaced_by[cursor]
        if following in ordered:
            cycles = True
            break
        ordered.append(following)
        cursor = following

    return ClaimHistory(ordered, ordered[-1], cycles)

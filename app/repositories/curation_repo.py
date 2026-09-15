"""Curation decisions — what a human has decided about the wiki's own bookkeeping.

Deliberately *not* knowledge. "苹果 and 苹果公司 are not the same entity" is not a
fact about the world; it is a decision about how this wiki files things. Making it a
Claim would put it in the knowledge graph, a Relation would put it in the ontology,
and a predicate like ``not_same_as`` would put it in the registry — all three would
let a curation choice leak into answers about the world.

So it lives here, with the shape its use demands: a *pair* (symmetric, so the two ids
are stored in canonical order and A↔B is B↔A), keyed on ids rather than names (so
renaming an entity later cannot lose the decision), revocable (a judgement can be
wrong, and this one is not permanent truth), and carrying a reason, an author and a
trace id for when someone asks why a pair stopped being suggested.
"""
from __future__ import annotations

import uuid

from .base import Repository, row, rows

# The only decision the first version makes. The vocabulary is expected to grow
# (likely_same, merge_approved) — see the note on the table in app/db.py for why it
# is validated here instead of by a CHECK constraint.
NOT_SAME = 'not_same'


def canonical_pair(entity_id_a: str, entity_id_b: str) -> tuple[str, str]:
    """The two ids in one stable order, so a pair is one row whichever way it arrives.

    Pure and total: sorting is what makes "already decided" a lookup rather than a
    two-directional search, and it has to happen in exactly one place.
    """
    a, b = str(entity_id_a or ''), str(entity_id_b or '')
    return (a, b) if a <= b else (b, a)


class CurationRepository(Repository):
    def decide(self, *, entity_id_a: str, entity_id_b: str, decision: str = NOT_SAME,
               reason: str | None = None, created_by: str = 'user',
               trace_id: str | None = None) -> dict:
        """Record a decision about a pair. Idempotent: deciding twice is not an error."""
        if not entity_id_a or not entity_id_b or entity_id_a == entity_id_b:
            raise ValueError('A curation decision needs two different entities')
        a, b = canonical_pair(entity_id_a, entity_id_b)
        decision_id = str(uuid.uuid4())
        with self.write() as conn:
            conn.execute(
                'INSERT OR IGNORE INTO entity_curation_decisions'
                '(id,entity_id_a,entity_id_b,decision,reason,created_by,trace_id) '
                'VALUES(?,?,?,?,?,?,?)',
                (decision_id, a, b, decision, reason, created_by, trace_id))
        return self.for_pair(a, b, decision) or {}

    def for_pair(self, entity_id_a: str, entity_id_b: str,
                 decision: str = NOT_SAME) -> dict | None:
        a, b = canonical_pair(entity_id_a, entity_id_b)
        with self.read() as conn:
            return row(conn.execute(
                'SELECT * FROM entity_curation_decisions WHERE entity_id_a=? AND entity_id_b=? '
                'AND decision=?', (a, b, decision)))

    def has(self, entity_id_a: str, entity_id_b: str, decision: str = NOT_SAME) -> bool:
        return self.for_pair(entity_id_a, entity_id_b, decision) is not None

    def decided_pairs(self, decision: str = NOT_SAME) -> set[tuple[str, str]]:
        """Every pair already decided, for a batch filter.

        One query for the whole set: an integrity scan must be able to drop decided
        pairs without asking once per candidate pair.
        """
        with self.read() as conn:
            return {(r['entity_id_a'], r['entity_id_b']) for r in rows(conn.execute(
                'SELECT entity_id_a, entity_id_b FROM entity_curation_decisions WHERE decision=?',
                (decision,)))}

    def list(self, limit: int = 100) -> list[dict]:
        """Decisions with both names, so a person can see and undo what was decided."""
        with self.read() as conn:
            return rows(conn.execute(
                '''SELECT d.*,a.name entity_a_name,b.name entity_b_name
                   FROM entity_curation_decisions d
                   JOIN entities a ON a.id=d.entity_id_a
                   JOIN entities b ON b.id=d.entity_id_b
                   ORDER BY d.created_at DESC LIMIT ?''', (max(1, limit),)))

    def revoke(self, decision_id: str) -> int:
        """Forget a decision. A judgement made once is not permanent truth."""
        with self.write() as conn:
            return conn.execute('DELETE FROM entity_curation_decisions WHERE id=?',
                                (decision_id,)).rowcount

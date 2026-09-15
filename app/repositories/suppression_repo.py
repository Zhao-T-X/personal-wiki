"""Maintenance suppressions — "I have seen this suggestion, stop reminding me".

Not knowledge: the row says nothing about the world. And deliberately not a curation
decision either (``curation_repo``). ``苹果 ≠ 苹果公司`` is a judgement about how this
wiki files things and is answerable indefinitely; "I am not acting on this object-link
suggestion" is an *operational preference about a notice*, and it is expected to be
taken back. One table for both would give the second the weight of the first.

The identity is ``(kind, source_type, source_id, candidate_key)`` — structured rather
than a rendered sentence, so:
* a rescan recognises the same suggestion as already seen (that is the whole point);
* a literal that was edited in the meantime does not match, and comes back as a *new*
  question instead of staying silently suppressed.
"""
from __future__ import annotations

import uuid

from .base import Repository, row, rows

# The only kind the first version records. The vocabulary is expected to grow with the
# detectors, which is why it is not a CHECK constraint — see the note on the table in
# app/db.py: SQLite cannot widen a CHECK without rebuilding, and a rule that silently
# fails to apply to an existing database is worse than no rule.
OBJECT_LINK = 'object_link'
KINDS = (OBJECT_LINK,)

# What a suppression is *about*. Kept explicit so a future kind can point at a
# different object without overloading ``source_id``.
CLAIM = 'claim'


class SuppressionRepository(Repository):
    def suppress(self, *, kind: str, source_id: str, candidate_key: str,
                 source_type: str = CLAIM, reason: str | None = None,
                 created_by: str = 'user', trace_id: str | None = None) -> dict:
        """Record that a suggestion has been seen and dismissed. Idempotent.

        Suppressing twice is not an error: the second click means the same thing as
        the first, and the unique key is what makes that a no-op rather than a
        duplicate row.
        """
        if not kind or not source_id or not candidate_key:
            raise ValueError('A suppression needs a kind, a source and a candidate key')
        suppression_id = str(uuid.uuid4())
        with self.write() as conn:
            conn.execute(
                'INSERT OR IGNORE INTO maintenance_suppressions'
                '(id,kind,source_type,source_id,candidate_key,reason,created_by,trace_id) '
                'VALUES(?,?,?,?,?,?,?,?)',
                (suppression_id, kind, source_type, source_id, candidate_key,
                 reason, created_by, trace_id))
        return self.for_key(kind, source_id, candidate_key, source_type) or {}

    def for_key(self, kind: str, source_id: str, candidate_key: str,
                source_type: str = CLAIM) -> dict | None:
        """The suppression for one source, not one literal — this is the *probe*.

        It answers "may I offer this suggestion again?", so it must be a lookup on the
        key rather than a scan of the table's contents.
        """
        with self.read() as conn:
            return row(conn.execute(
                'SELECT * FROM maintenance_suppressions WHERE kind=? AND source_type=? '
                'AND source_id=? AND candidate_key=?',
                (kind, source_type, source_id, candidate_key)))

    def has(self, kind: str, source_id: str, candidate_key: str,
            source_type: str = CLAIM) -> bool:
        return self.for_key(kind, source_id, candidate_key, source_type) is not None

    def suppressed_keys(self, kind: str, source_type: str = CLAIM) -> set[tuple[str, str]]:
        """Every suppressed ``(source_id, candidate_key)`` pair, for a batch filter.

        One query for the whole set: a scan drops dozens of dismissed suggestions and
        must not ask once per suggestion. Same discipline as the curation memory.
        """
        with self.read() as conn:
            return {(r['source_id'], r['candidate_key']) for r in rows(conn.execute(
                'SELECT source_id, candidate_key FROM maintenance_suppressions '
                'WHERE kind=? AND source_type=?', (kind, source_type)))}

    def list(self, limit: int = 100) -> list[dict]:
        """Suppressions with enough context to be read back and lifted.

        The literal is read from the claim rather than stored twice: the text a person
        dismissed is the text that is still on the claim, and copying it here would let
        the two drift.
        """
        with self.read() as conn:
            return rows(conn.execute(
                '''SELECT s.*,c.predicate,c.object_text,c.status claim_status,
                          e.name subject_name
                   FROM maintenance_suppressions s
                   LEFT JOIN claims c ON c.id=s.source_id AND s.source_type='claim'
                   LEFT JOIN entities e ON e.id=c.subject_id
                   ORDER BY s.created_at DESC LIMIT ?''', (max(1, limit),)))

    def revoke(self, suppression_id: str) -> int:
        """Lift a suppression. The suggestion becomes an ordinary candidate again."""
        with self.write() as conn:
            return conn.execute('DELETE FROM maintenance_suppressions WHERE id=?',
                                (suppression_id,)).rowcount

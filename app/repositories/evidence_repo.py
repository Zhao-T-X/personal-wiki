"""Evidence / provenance access.

Evidence is currently stored inline on each knowledge object (``source_document_id``
/ ``source_chunk_id`` / ``source_start_offset`` / ``source_end_offset`` /
``source_quote``). This repository centralises that access today; when Evidence is
promoted to its own domain object (docs/adr/ADR-006) only this class changes.

It is a read model: it never decides what evidence *means*.
"""
from __future__ import annotations

from .base import Repository, row, rows


class EvidenceRepository(Repository):
    def provenance(self, chunk_id: str) -> dict | None:
        """The exact source span a chunk occupies in its document."""
        with self.read() as conn:
            return row(conn.execute(
                'SELECT id,document_id,content,start_offset,end_offset FROM chunks WHERE id=?', (chunk_id,)))

    def for_claim(self, claim_id: str) -> dict | None:
        with self.read() as conn:
            return row(conn.execute(
                '''SELECT source_document_id,source_chunk_id,source_start_offset,
                          source_end_offset,source_quote FROM claims WHERE id=?''', (claim_id,)))

    def for_document(self, document_id: str, limit: int = 100) -> list[dict]:
        """Every quoted span extracted from a document, with its subject."""
        with self.read() as conn:
            return rows(conn.execute(
                '''SELECT c.id claim_id,c.source_chunk_id,c.source_start_offset,c.source_end_offset,
                          c.source_quote,s.name subject_name,c.predicate
                   FROM claims c JOIN entities s ON s.id=c.subject_id
                   WHERE c.source_document_id=? AND c.source_quote IS NOT NULL
                   ORDER BY c.created_at DESC LIMIT ?''', (document_id, limit)))

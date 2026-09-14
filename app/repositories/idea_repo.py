"""Ideas."""
from __future__ import annotations

import uuid

from .base import Repository, rows

_COLUMNS = 'id,content,status,confidence,source_document_id,source_quote,created_at'


class IdeaRepository(Repository):
    def list(self, limit: int, offset: int, status: str | None = None) -> list[dict]:
        sql = f'SELECT {_COLUMNS} FROM ideas'
        params: list = []
        if status:
            sql += ' WHERE status=?'; params.append(status)
        sql += ' ORDER BY created_at DESC LIMIT ? OFFSET ?'; params.extend([limit, offset])
        with self.read() as conn:
            return rows(conn.execute(sql, params))

    def for_documents(self, document_ids: list[str], limit: int = 50) -> list[dict]:
        if not document_ids:
            return []
        q = ','.join('?' * len(document_ids))
        with self.read() as conn:
            return rows(conn.execute(
                f'SELECT * FROM ideas WHERE source_document_id IN ({q}) '
                'ORDER BY created_at DESC LIMIT ?', (*document_ids, limit)))

    def insert(self, *, content: str, status: str, confidence: float | None = None,
               source_document_id: str | None = None, source_chunk_id: str | None = None,
               source_start_offset: int | None = None, source_end_offset: int | None = None,
               source_quote: str | None = None) -> str:
        idea_id = str(uuid.uuid4())
        with self.write() as conn:
            conn.execute(
                '''INSERT INTO ideas(id,content,status,confidence,source_document_id,source_chunk_id,
                       source_start_offset,source_end_offset,source_quote)
                   VALUES(?,?,?,?,?,?,?,?,?)''',
                (idea_id, content, status, confidence, source_document_id, source_chunk_id,
                 source_start_offset, source_end_offset, source_quote))
        return idea_id

    def delete_for_document(self, document_id: str) -> None:
        with self.write() as conn:
            conn.execute('DELETE FROM ideas WHERE source_document_id=?', (document_id,))

    def delete_for_chunks(self, chunk_ids: list[str]) -> int:
        """Delete ideas sourced from any of ``chunk_ids`` (incremental §32); empty list is a no-op."""
        ids = list(chunk_ids)
        if not ids:
            return 0
        marks = ','.join('?' for _ in ids)
        with self.write() as conn:
            return conn.execute(f'DELETE FROM ideas WHERE source_chunk_id IN ({marks})', ids).rowcount

    def set_status(self, idea_id: str, status: str) -> int:
        with self.write() as conn:
            return conn.execute('UPDATE ideas SET status=? WHERE id=?', (status, idea_id)).rowcount

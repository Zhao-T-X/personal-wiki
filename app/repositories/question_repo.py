"""Questions."""
from __future__ import annotations

import uuid

from .base import Repository, one, rows

_COLUMNS = 'id,content,status,source_document_id,source_quote,created_at'


class QuestionRepository(Repository):
    def list(self, limit: int, offset: int, status: str | None = None) -> list[dict]:
        sql = f'SELECT {_COLUMNS} FROM questions'
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
                f'SELECT {_COLUMNS} FROM questions WHERE source_document_id IN ({q}) '
                'ORDER BY created_at DESC LIMIT ?', (*document_ids, limit)))

    def insert(self, *, content: str, status: str, source_document_id: str | None = None,
               source_chunk_id: str | None = None, source_start_offset: int | None = None,
               source_end_offset: int | None = None, source_quote: str | None = None) -> str:
        question_id = str(uuid.uuid4())
        with self.write() as conn:
            conn.execute(
                '''INSERT INTO questions(id,content,status,source_document_id,source_chunk_id,
                       source_start_offset,source_end_offset,source_quote)
                   VALUES(?,?,?,?,?,?,?,?)''',
                (question_id, content, status, source_document_id, source_chunk_id,
                 source_start_offset, source_end_offset, source_quote))
        return question_id

    def delete_for_document(self, document_id: str) -> None:
        with self.write() as conn:
            conn.execute('DELETE FROM questions WHERE source_document_id=?', (document_id,))

    def open_count(self) -> int:
        with self.read() as conn:
            return one(conn.execute("SELECT COUNT(*) c FROM questions WHERE status='open'"), 'c')

    def set_status(self, question_id: str, status: str) -> int:
        with self.write() as conn:
            return conn.execute('UPDATE questions SET status=? WHERE id=?', (status, question_id)).rowcount

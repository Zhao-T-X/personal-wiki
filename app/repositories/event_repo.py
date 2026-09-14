"""Events."""
from __future__ import annotations

import uuid

from ..db import dumps, loads
from .base import Repository, rows

_COLUMNS = ('id,event_type,description,participants_json,time_json,location,status,confidence,'
            'source_document_id,source_quote,created_at')


def _decode(item: dict) -> dict:
    item['participants'] = loads(item.pop('participants_json', '[]') or '[]', [])
    item['time'] = loads(item.pop('time_json', '{}') or '{}', {})
    return item


class EventRepository(Repository):
    def list(self, limit: int, offset: int, status: str | None = None) -> list[dict]:
        sql = f'SELECT {_COLUMNS} FROM events'
        params: list = []
        if status:
            sql += ' WHERE status=?'; params.append(status)
        sql += ' ORDER BY created_at DESC LIMIT ? OFFSET ?'; params.extend([limit, offset])
        with self.read() as conn:
            return [_decode(r) for r in rows(conn.execute(sql, params))]

    def for_documents(self, document_ids: list[str], limit: int = 50) -> list[dict]:
        if not document_ids:
            return []
        q = ','.join('?' * len(document_ids))
        with self.read() as conn:
            return rows(conn.execute(
                f'SELECT * FROM events WHERE source_document_id IN ({q}) '
                'ORDER BY created_at DESC LIMIT ?', (*document_ids, limit)))

    def insert(self, *, event_type: str, description: str, participants: list, time: dict,
               location: str | None, status: str, confidence: float | None = None,
               source_document_id: str | None = None, source_chunk_id: str | None = None,
               source_start_offset: int | None = None, source_end_offset: int | None = None,
               source_quote: str | None = None) -> str:
        event_id = str(uuid.uuid4())
        with self.write() as conn:
            conn.execute(
                '''INSERT INTO events(id,event_type,description,participants_json,time_json,location,status,
                       confidence,source_document_id,source_chunk_id,source_start_offset,source_end_offset,source_quote)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (event_id, event_type, description, dumps(participants or []), dumps(time or {}),
                 location, status, confidence, source_document_id, source_chunk_id,
                 source_start_offset, source_end_offset, source_quote))
        return event_id

    def delete_for_document(self, document_id: str) -> None:
        with self.write() as conn:
            conn.execute('DELETE FROM events WHERE source_document_id=?', (document_id,))

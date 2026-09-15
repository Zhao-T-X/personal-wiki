"""Entity-to-entity relations (derived deterministically from claims)."""
from __future__ import annotations

import uuid

from ..db import dumps, loads
from .base import Repository, one, rows


class RelationRepository(Repository):
    def list(self, limit: int, status: str | None = None) -> list[dict]:
        sql = ('SELECT r.*,a.name source_name,b.name target_name FROM relations r '
               'JOIN entities a ON a.id=r.source_id JOIN entities b ON b.id=r.target_id')
        params: list = []
        if status:
            sql += ' WHERE r.status=?'; params.append(status)
        sql += ' ORDER BY r.created_at DESC LIMIT ?'; params.append(limit)
        with self.read() as conn:
            out = rows(conn.execute(sql, params))
        for item in out:
            item['context'] = loads(item.pop('context_json', '{}') or '{}', {})
        return out

    def for_entity(self, entity_id: str) -> list[dict]:
        with self.read() as conn:
            return rows(conn.execute(
                '''SELECT r.*,a.name source_name,b.name target_name FROM relations r
                   JOIN entities a ON a.id=r.source_id JOIN entities b ON b.id=r.target_id
                   WHERE r.source_id=? OR r.target_id=? ORDER BY r.created_at DESC''',
                (entity_id, entity_id)))

    def repoint_entity(self, from_id: str, to_id: str) -> int:
        """Move every relation endpoint from one entity onto another.

        The counterpart of ``ClaimRepository.repoint_entity``: both sides of a merge
        have to move together, or a relation would end up pointing at an entity that
        no longer carries the claims that justified it.
        """
        with self.write() as conn:
            moved = conn.execute('UPDATE relations SET source_id=? WHERE source_id=?',
                                 (to_id, from_id)).rowcount
            moved += conn.execute('UPDATE relations SET target_id=? WHERE target_id=?',
                                  (to_id, from_id)).rowcount
            return moved

    def for_documents(self, document_ids: list[str], limit: int = 50) -> list[dict]:
        if not document_ids:
            return []
        q = ','.join('?' * len(document_ids))
        with self.read() as conn:
            return rows(conn.execute(
                f'SELECT * FROM relations WHERE source_document_id IN ({q}) ORDER BY created_at DESC LIMIT ?',
                (*document_ids, limit)))

    def candidates(self, limit: int) -> list[dict]:
        with self.read() as conn:
            return rows(conn.execute(
                '''SELECT r.id,r.predicate,r.confidence,r.status,r.source_document_id,
                          a.name source_name,b.name target_name
                   FROM relations r JOIN entities a ON a.id=r.source_id JOIN entities b ON b.id=r.target_id
                   WHERE r.status='candidate' ORDER BY r.created_at DESC LIMIT ?''', (limit,)))

    def incident(self, entity_ids: list[str], limit: int) -> list[dict]:
        """Relations incident to any of ``entity_ids`` (graph BFS step)."""
        if not entity_ids:
            return []
        qmarks = ','.join('?' for _ in entity_ids)
        with self.read() as conn:
            return rows(conn.execute(
                f'''SELECT r.id,r.source_id,r.target_id,r.predicate,r.confidence,r.status,
                           a.name source_name,b.name target_name,r.source_document_id,r.source_chunk_id
                    FROM relations r JOIN entities a ON a.id=r.source_id JOIN entities b ON b.id=r.target_id
                    WHERE r.source_id IN ({qmarks}) OR r.target_id IN ({qmarks})
                    LIMIT ?''', (*entity_ids, *entity_ids, limit)))

    def global_edges(self, limit: int) -> list[dict]:
        with self.read() as conn:
            return rows(conn.execute(
                "SELECT r.id,r.source_id,r.target_id,r.predicate,r.confidence,r.status,r.source_document_id,"
                "r.source_chunk_id,a.name source_name,b.name target_name FROM relations r "
                "JOIN entities a ON a.id=r.source_id JOIN entities b ON b.id=r.target_id "
                "ORDER BY r.created_at DESC LIMIT ?", (limit,)))

    def exists(self, *, source_id: str, predicate: str, target_id: str,
               document_id: str, chunk_id: str) -> bool:
        with self.read() as conn:
            return conn.execute(
                'SELECT id FROM relations WHERE source_id=? AND predicate=? AND target_id=? '
                'AND source_document_id=? AND source_chunk_id=?',
                (source_id, predicate, target_id, document_id, chunk_id)).fetchone() is not None

    def insert(self, *, source_id: str, predicate: str, target_id: str, context: dict,
               confidence: float | None, status: str, created_by: str, source_document_id: str,
               source_chunk_id: str, source_start_offset: int, source_end_offset: int,
               source_quote: str | None) -> str:
        relation_id = str(uuid.uuid4())
        with self.write() as conn:
            conn.execute(
                '''INSERT INTO relations(id,source_id,predicate,target_id,context_json,confidence,status,
                       created_by,source_document_id,source_chunk_id,source_start_offset,source_end_offset,source_quote)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (relation_id, source_id, predicate, target_id, dumps(context or {}), confidence, status,
                 created_by, source_document_id, source_chunk_id, source_start_offset, source_end_offset,
                 source_quote))
        return relation_id

    def delete_for_document(self, document_id: str) -> None:
        with self.write() as conn:
            conn.execute('DELETE FROM relations WHERE source_document_id=?', (document_id,))

    def delete_for_chunks(self, chunk_ids: list[str]) -> int:
        """Delete relations sourced from any of ``chunk_ids`` (incremental §32); empty list is a no-op."""
        ids = list(chunk_ids)
        if not ids:
            return 0
        marks = ','.join('?' for _ in ids)
        with self.write() as conn:
            return conn.execute(f'DELETE FROM relations WHERE source_chunk_id IN ({marks})', ids).rowcount

    def set_status(self, relation_id: str, status: str) -> int:
        with self.write() as conn:
            return conn.execute('UPDATE relations SET status=? WHERE id=?', (status, relation_id)).rowcount

    def count(self) -> int:
        with self.read() as conn:
            return one(conn.execute('SELECT COUNT(*) c FROM relations'), 'c')

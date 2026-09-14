"""Knowledge Operations audit trail."""
from __future__ import annotations

from ..db import dumps, loads
from .base import Repository, one, row, rows


class OperationRepository(Repository):
    def record(self, *, op_id: str, kind: str, actor: str, status: str,
               reason: str, payload: dict, result: dict) -> None:
        with self.write() as conn:
            conn.execute(
                'INSERT INTO knowledge_operations(id,kind,actor,status,reason,payload_json,result_json) '
                'VALUES(?,?,?,?,?,?,?)',
                (op_id, kind, actor, status, reason or '', dumps(payload), dumps(result)))

    def list(self, limit: int, kind: str | None = None) -> list[dict]:
        sql = 'SELECT * FROM knowledge_operations'
        params: list = []
        if kind:
            sql += ' WHERE kind=?'; params.append(kind)
        sql += ' ORDER BY created_at DESC LIMIT ?'; params.append(limit)
        with self.read() as conn:
            out = rows(conn.execute(sql, params))
        for item in out:
            item['payload'] = loads(item.pop('payload_json', '{}') or '{}', {})
            item['result'] = loads(item.pop('result_json', '{}') or '{}', {})
        return out

    def get(self, op_id: str) -> dict | None:
        with self.read() as conn:
            item = row(conn.execute('SELECT * FROM knowledge_operations WHERE id=?', (op_id,)))
        if item is None:
            return None
        item['payload'] = loads(item.pop('payload_json', '{}') or '{}', {})
        item['result'] = loads(item.pop('result_json', '{}') or '{}', {})
        return item

    def count(self) -> int:
        with self.read() as conn:
            return one(conn.execute('SELECT COUNT(*) c FROM knowledge_operations'), 'c')

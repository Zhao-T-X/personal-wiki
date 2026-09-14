"""LLM run/step records (read side; writes stay in app/runlog.py)."""
from __future__ import annotations

from ..db import loads
from .base import Repository, one, row, rows


class RunRepository(Repository):
    def list(self, limit: int, task_type: str | None = None, status: str | None = None) -> list[dict]:
        sql = 'SELECT r.*,d.title document_title FROM llm_runs r LEFT JOIN documents d ON d.id=r.document_id'
        params: list = []
        cond: list[str] = []
        if task_type:
            cond.append('r.task_type=?'); params.append(task_type)
        if status:
            cond.append('r.status=?'); params.append(status)
        if cond:
            sql += ' WHERE ' + ' AND '.join(cond)
        sql += ' ORDER BY r.created_at DESC LIMIT ?'; params.append(limit)
        with self.read() as conn:
            out = rows(conn.execute(sql, params))
        for item in out:
            item['summary'] = loads(item.pop('summary_json', '{}') or '{}', {})
        return out

    def get(self, run_id: str) -> dict | None:
        with self.read() as conn:
            run = row(conn.execute(
                'SELECT r.*,d.title document_title FROM llm_runs r '
                'LEFT JOIN documents d ON d.id=r.document_id WHERE r.id=?', (run_id,)))
            if run is None:
                return None
            steps = rows(conn.execute(
                'SELECT * FROM llm_run_steps WHERE run_id=? ORDER BY step_index', (run_id,)))
        run['summary'] = loads(run.pop('summary_json', '{}') or '{}', {})
        run['steps'] = steps
        return run

    def status(self, run_id: str) -> str | None:
        with self.read() as conn:
            return one(conn.execute('SELECT status FROM llm_runs WHERE id=?', (run_id,)), 'status')

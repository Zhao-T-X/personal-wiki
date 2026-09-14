"""Research tasks."""
from __future__ import annotations

import uuid

from .base import Repository, rows


class ResearchRepository(Repository):
    def list(self, limit: int) -> list[dict]:
        with self.read() as conn:
            return rows(conn.execute(
                'SELECT * FROM research_tasks ORDER BY created_at DESC LIMIT ?', (limit,)))

    def get(self, task_id: str) -> dict | None:
        with self.read() as conn:
            from .base import row
            return row(conn.execute('SELECT * FROM research_tasks WHERE id=?', (task_id,)))

    def create(self, *, question_id: str | None, question_text: str) -> str:
        task_id = str(uuid.uuid4())
        with self.write() as conn:
            conn.execute('INSERT INTO research_tasks(id,question_id,question_text,status) VALUES(?,?,?,?)',
                         (task_id, question_id, question_text, 'open'))
        return task_id

    def set_status(self, task_id: str, status: str, findings: str | None = None) -> None:
        with self.write() as conn:
            conn.execute('UPDATE research_tasks SET status=?,findings=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                         (status, findings, task_id))

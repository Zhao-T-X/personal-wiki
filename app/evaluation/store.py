"""Persistence for evaluation runs (backend wiring only).

This module is *allowed* to talk to the database. It must stay free of direct
FastAPI / AgentScope / sqlite3 / ``app.llm`` imports — it only goes through
:mod:`app.db`. The pure harness (``*_eval.py``, ``metrics.py``) must never
import this module.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..db import transaction, dumps, loads


def save_run(suite: str, summary: dict, report: dict) -> str:
    """Persist one evaluation run and return its ``run_id``."""
    import uuid
    run_id = uuid.uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat()
    with transaction() as conn:
        conn.execute(
            'INSERT INTO evaluation_runs (id, suite, created_at, summary, report) '
            'VALUES (?, ?, ?, ?, ?)',
            (run_id, suite, created_at, dumps(summary), dumps(report)),
        )
    return run_id


def list_runs(limit: int = 20) -> list[dict]:
    """Newest-first list of runs as ``{run_id, suite, created_at, summary}``."""
    limit = max(1, min(int(limit), 200))
    with transaction() as conn:
        rows = conn.execute(
            'SELECT id, suite, created_at, summary FROM evaluation_runs '
            'ORDER BY created_at DESC, id DESC LIMIT ?',
            (limit,),
        ).fetchall()
    return [
        {
            'run_id': row['id'],
            'suite': row['suite'],
            'created_at': row['created_at'],
            'summary': loads(row['summary'], {}),
        }
        for row in rows
    ]


def get_run(run_id: str) -> dict | None:
    """Fetch one run as ``{run_id, suite, created_at, summary, report}`` or None."""
    with transaction() as conn:
        row = conn.execute(
            'SELECT id, suite, created_at, summary, report FROM evaluation_runs '
            'WHERE id = ?',
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        'run_id': row['id'],
        'suite': row['suite'],
        'created_at': row['created_at'],
        'summary': loads(row['summary'], {}),
        'report': loads(row['report'], {}),
    }

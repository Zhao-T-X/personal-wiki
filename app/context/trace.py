"""Persist Context Traces to SQLite.

Every LLM call should leave behind what it was given: the budget, the actual
token cost, the sections and why they were loaded. This is the audit trail that
makes "which agent wastes tokens?" answerable.

All writes are best effort - a failed trace must never break an LLM call.
"""
from __future__ import annotations

import json
import sys
import uuid

from ..db import transaction


def _warn(exc: Exception) -> None:
    print(f'[context.trace] {type(exc).__name__}: {exc}', file=sys.stderr)


def _current_run_id() -> str | None:
    try:
        from ..runlog import current_run
        run = current_run()
        return run.id if run is not None else None
    except Exception:
        return None


def record_context(context) -> str | None:
    """Insert one context_run row plus its sections. Returns the context run id."""
    context_run_id = str(uuid.uuid4())
    try:
        run_id = _current_run_id()
        trace = context.to_trace()
        with transaction() as conn:
            conn.execute(
                '''INSERT INTO context_runs(id, run_id, agent_name, budget_tokens, actual_tokens,
                                             trimmed_tokens, efficiency, over_budget, optimizations_json,
                                             withheld_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (context_run_id, run_id, trace['agent'], trace['budget_tokens'], trace['actual_tokens'],
                 trace['trimmed_tokens'], trace['efficiency'], 1 if trace['over_budget'] else 0,
                 json.dumps(trace['optimizations'], ensure_ascii=False),
                 json.dumps(trace.get('withheld', []), ensure_ascii=False)),
            )
            for index, section in enumerate(trace['sections']):
                conn.execute(
                    '''INSERT INTO context_sections(id, context_run_id, section_index, name, policy,
                                                     source, reason, tokens, chars, trimmed)
                       VALUES(?,?,?,?,?,?,?,?,?,?)''',
                    (str(uuid.uuid4()), context_run_id, index, section['name'],
                     section['policy'], section['source'], section['reason'], section['tokens'],
                     section['chars'], 1 if section['trimmed'] else 0),
                )
        return context_run_id
    except Exception as exc:
        _warn(exc)
        return None


def list_context_runs(limit: int = 50, agent_name: str | None = None) -> list[dict]:
    """Recent Context Traces, newest first."""
    from ..db import connect
    sql = 'SELECT * FROM context_runs'
    params: list = []
    if agent_name:
        sql += ' WHERE agent_name=?'
        params.append(agent_name)
    sql += ' ORDER BY created_at DESC LIMIT ?'
    params.append(max(1, min(limit, 200)))
    conn = connect()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [
            dict(r) | {
                'optimizations': json.loads(r['optimizations_json'] or '[]'),
                'withheld': json.loads(r['withheld_json'] or '[]'),
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_context_run(context_run_id: str) -> dict | None:
    """One Context Trace with its sections."""
    from ..db import connect
    conn = connect()
    try:
        row = conn.execute('SELECT * FROM context_runs WHERE id=?', (context_run_id,)).fetchone()
        if row is None:
            return None
        sections = conn.execute(
            'SELECT * FROM context_sections WHERE context_run_id=? ORDER BY section_index',
            (context_run_id,),
        ).fetchall()
        item = dict(row)
        item['optimizations'] = json.loads(item.pop('optimizations_json') or '[]')
        item['withheld'] = json.loads(item.pop('withheld_json', '[]') or '[]')
        item['sections'] = [dict(s) for s in sections]
        return item
    finally:
        conn.close()

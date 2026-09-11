"""Run/step execution records for every LLM-triggered task.

A *run* is one user-visible task (document extraction, knowledge-base ask,
agent conversation); a *step* is one actual model call inside it. Recording
must never break the main flow, so every write is best-effort and failures
are only reported to stderr.
"""
from __future__ import annotations

import contextvars
import json
import sys
import time
import uuid
from typing import Any

from .config import runtime

_current_run: contextvars.ContextVar = contextvars.ContextVar('llm_current_run', default=None)

# Runs the user asked to cancel. Cancellation is cooperative: long pipelines
# (e.g. batch extraction) poll is_cancelled() between batches.
_cancelled: set[str] = set()


def current_run():
    """The run opened by the enclosing ``record_run`` block, if any."""
    return _current_run.get()


def request_cancel(run_id: str) -> None:
    _cancelled.add(run_id)


def is_cancelled(run_id: str | None) -> bool:
    return bool(run_id) and run_id in _cancelled


def clear_cancel(run_id: str) -> None:
    _cancelled.discard(run_id)


def _warn(exc: Exception) -> None:
    print(f'[runlog] write failed: {type(exc).__name__}: {exc}', file=sys.stderr)


def _connect():
    # Imported lazily so module reloads in tests rebind the active database.
    from .db import connect
    return connect()


def _take_agent_usage() -> dict | None:
    """Usage handed over by the agent layer, if any (see app/agent_usage.py)."""
    try:
        from .agent_usage import take
        return take()
    except Exception:
        return None


class Step:
    """One model call. Use as a sync or async context manager."""

    def __init__(self, run_id: str, step_index: int, name: str, input_summary: str | None):
        self.run_id = run_id
        self.step_index = step_index
        self.name = name
        self.input_summary = input_summary
        self.output: str | None = None
        # Provider-reported cost of this model call; set by the caller.
        self.usage: dict | None = None
        self._t0 = 0.0

    def set_usage(self, usage: dict | None) -> None:
        """Attach provider usage ({'prompt_tokens','completion_tokens','usage_source'})."""
        self.usage = usage

    def __enter__(self) -> 'Step':
        self._t0 = time.perf_counter()
        self.usage = None
        _take_agent_usage()  # start each step with a clean usage channel
        try:
            conn = _connect()
            conn.execute(
                'INSERT INTO llm_run_steps(id,run_id,step_index,name,status,input_summary) VALUES(?,?,?,?,?,?)',
                (str(uuid.uuid4()), self.run_id, self.step_index, self.name, 'started', self.input_summary))
            conn.commit(); conn.close()
        except Exception as exc:
            _warn(exc)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        duration = int((time.perf_counter() - self._t0) * 1000)
        status = 'failed' if exc_type is not None else 'success'
        usage = self.usage or _take_agent_usage() or {}
        try:
            conn = _connect()
            conn.execute(
                'UPDATE llm_run_steps SET status=?,output_text=?,error_message=?,duration_ms=?,'
                'prompt_tokens=?,completion_tokens=?,usage_source=? WHERE run_id=? AND step_index=?',
                (status, self.output, str(exc) if exc else None, duration,
                 usage.get('prompt_tokens'), usage.get('completion_tokens'),
                 usage.get('usage_source'), self.run_id, self.step_index))
            conn.commit(); conn.close()
        except Exception as e:
            _warn(e)
        return False

    async def __aenter__(self) -> 'Step':
        return self.__enter__()

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return self.__exit__(exc_type, exc, tb)


class Run:
    """One user-visible task. Use as a sync or async context manager."""

    def __init__(self, task_type: str, *, document_id: str | None = None,
                 agent_role: str | None = None, model: str | None = None):
        self.id = str(uuid.uuid4())
        self.task_type = task_type
        self.document_id = document_id
        self.agent_role = agent_role
        self.model = model or runtime().get('openai_model')
        self.summary: dict[str, Any] = {}
        self._t0 = 0.0
        self._token = None

    def __enter__(self) -> 'Run':
        self._t0 = time.perf_counter()
        try:
            conn = _connect()
            conn.execute(
                'INSERT INTO llm_runs(id,task_type,document_id,agent_role,model,status) VALUES(?,?,?,?,?,?)',
                (self.id, self.task_type, self.document_id, self.agent_role, self.model, 'started'))
            conn.commit(); conn.close()
        except Exception as exc:
            _warn(exc)
        self._token = _current_run.set(self)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        _current_run.reset(self._token)
        clear_cancel(self.id)
        duration = int((time.perf_counter() - self._t0) * 1000)
        status = 'success' if exc_type is None else 'failed'
        step_count = 0
        prompt_tokens = 0
        completion_tokens = 0
        try:
            conn = _connect()
            row = conn.execute(
                "SELECT SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) c,"
                " COALESCE(SUM(prompt_tokens),0) p,"
                " COALESCE(SUM(completion_tokens),0) o"
                " FROM llm_run_steps WHERE run_id=?",
                (self.id,)).fetchone()
            step_count = row['c']
            prompt_tokens = row['p']
            completion_tokens = row['o']
            conn.close()
        except Exception as e:
            _warn(e)
        try:
            conn = _connect()
            conn.execute(
                'UPDATE llm_runs SET status=?,step_count=?,summary_json=?,error_message=?,duration_ms=?,'
                'prompt_tokens=?,completion_tokens=?,finished_at=CURRENT_TIMESTAMP WHERE id=?',
                (status, step_count, json.dumps(self.summary, ensure_ascii=False),
                 str(exc) if exc else None, duration, prompt_tokens, completion_tokens, self.id))
            conn.commit(); conn.close()
        except Exception as e:
            _warn(e)
        return False

    async def __aenter__(self) -> 'Run':
        return self.__enter__()

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return self.__exit__(exc_type, exc, tb)

    def step(self, name: str, input_summary: str | None = None) -> Step:
        try:
            conn = _connect()
            row = conn.execute(
                'SELECT COALESCE(MAX(step_index),-1) m FROM llm_run_steps WHERE run_id=?',
                (self.id,)).fetchone()
            conn.close()
            index = row['m'] + 1
        except Exception:
            index = 0
        return Step(self.id, index, name, input_summary)


def record_run(task_type: str, *, document_id: str | None = None,
               agent_role: str | None = None, model: str | None = None) -> Run:
    return Run(task_type, document_id=document_id, agent_role=agent_role, model=model)

# Unified LLM Run Records Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record every LLM-triggered task (document extraction, knowledge-base ask, agent conversation) as a `llm_runs` row with per-model-call `llm_run_steps` rows, exposed via `/api/runs*` and a UI "Runs" view, replacing `extraction_runs`.

**Architecture:** A standalone recorder module `app/runlog.py` provides `Run`/`Step` context managers (sync + async) that write SQLite directly and never propagate their own failures. The current run is shared with inner call sites through `contextvars`, so `llm.extract` can attach per-batch steps without signature changes.

**Tech Stack:** Python 3 / FastAPI / stdlib sqlite3 / AgentScope 2.x / vanilla JS single-file UI.

**Spec:** `docs/superpowers/specs/2026-09-10-llm-run-records-design.md`

## Global Constraints

- **Not a git repository — skip every commit step.** Each task ends with "run the task tests" instead.
- Test DB isolation: every test must call the local `_reload_db(tmp_path)` helper (pattern from `tests/test_pipeline.py`) which sets `DATABASE_PATH`/`SETTINGS_PATH` env vars and reloads `app.config`/`app.db` plus DB-bound modules. Never touch `./data/wiki.db`.
- runlog must NEVER break the main flow: every DB write inside `app/runlog.py` is wrapped in try/except that only prints `[runlog] write failed: …` to stderr.
- `llm_runs.status` / `llm_run_steps.status` ∈ `('started','success','failed')` (enforced by SQL CHECK).
- `step_count` = number of `success` steps, written when the run finishes.
- `output_text` is stored verbatim (no truncation); list API must NOT return it, only `GET /api/runs/{id}`.
- Python style in this repo: compact, minimal docstrings, `from __future__ import annotations`, 4-space indent.

## File Structure

- Modify `app/db.py` — add `llm_runs` + `llm_run_steps` to `SCHEMA`, `DROP TABLE IF EXISTS extraction_runs`.
- Create `app/runlog.py` — `Run`, `Step`, `record_run()`, `current_run()` (contextvars).
- Modify `app/llm.py` — `_extract_one()` helper attaches steps; `answer()` wraps itself in a run.
- Modify `app/agents/base.py` — `run_agent()` wraps itself in a run.
- Modify `app/service.py` — `index_document()` opens the `extract` run; old `extraction_runs` writes removed.
- Modify `app/main.py` — `/api/runs`, `/api/runs/{id}`; delete `/api/extractions`; stats table swap.
- Modify `web/index.html` — nav button, `renderRuns()` / `showRun()`.
- Modify `sql/schema.v0.1.sql`, `docs/PROCESSING-PIPELINE.md` — documentation copies.
- Create `tests/test_runlog.py` — schema, unit, and e2e tests.

---

### Task 1: Schema — `llm_runs` + `llm_run_steps`, drop `extraction_runs`

**Files:**
- Modify: `app/db.py` (inside `SCHEMA`, after the `agent_prompt_versions` block, before `chunk_embeddings`)
- Modify: `sql/schema.v0.1.sql`
- Test: `tests/test_runlog.py` (create)

**Interfaces:**
- Produces: tables `llm_runs(id, task_type, document_id, agent_role, model, status, step_count, summary_json, error_message, duration_ms, created_at, finished_at)` and `llm_run_steps(id, run_id, step_index, name, status, input_summary, output_text, error_message, duration_ms, created_at)`. All later tasks write through raw SQL against these columns.

- [ ] **Step 1: Write the failing test**

Create `tests/test_runlog.py`:

```python
import json

import pytest


def _reload_db(tmp_path):
    import os
    os.environ['DATABASE_PATH'] = str(tmp_path / 'test.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import importlib
    import sys
    import app.config as config
    import app.db as db
    importlib.reload(config); importlib.reload(db)
    for name in ('app.runlog', 'app.service', 'app.knowledge', 'app.resolution',
                 'app.retrieval', 'app.graph', 'app.importer', 'app.embeddings'):
        module = sys.modules.get(name)
        if module is not None:
            importlib.reload(module)
    db.init_db()
    return db


def test_schema_has_run_tables(tmp_path):
    db = _reload_db(tmp_path)
    conn = db.connect()
    names = {r['name'] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert 'llm_runs' in names and 'llm_run_steps' in names
    assert 'extraction_runs' not in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_runlog.py::test_schema_has_run_tables -v`
Expected: FAIL — `assert 'llm_runs' in names`

- [ ] **Step 3: Add the tables to `app/db.py::SCHEMA`**

In `app/db.py`, insert between the `agent_prompt_versions` index and `CREATE TABLE IF NOT EXISTS chunk_embeddings`:

```sql
CREATE TABLE IF NOT EXISTS llm_runs (
  id TEXT PRIMARY KEY,
  task_type TEXT NOT NULL CHECK(task_type IN ('extract','ask','agent')),
  document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
  agent_role TEXT,
  model TEXT,
  status TEXT NOT NULL CHECK(status IN ('started','success','failed')),
  step_count INTEGER NOT NULL DEFAULT 0,
  summary_json TEXT NOT NULL DEFAULT '{}',
  error_message TEXT,
  duration_ms INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_llm_runs_created ON llm_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_runs_type ON llm_runs(task_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_runs_document ON llm_runs(document_id);

CREATE TABLE IF NOT EXISTS llm_run_steps (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES llm_runs(id) ON DELETE CASCADE,
  step_index INTEGER NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('started','success','failed')),
  input_summary TEXT,
  output_text TEXT,
  error_message TEXT,
  duration_ms INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(run_id, step_index)
);
CREATE INDEX IF NOT EXISTS idx_llm_run_steps_run ON llm_run_steps(run_id, step_index);

DROP TABLE IF EXISTS extraction_runs;
```

- [ ] **Step 4: Update the schema doc copy**

Replace the whole content of `sql/schema.v0.1.sql` with:

```sql
-- LLM-Wiki canonical SQLite schema (v0.1 + llm run records).
-- The executable copy is app/db.py::SCHEMA.
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

-- v0.2: extraction_runs was replaced by llm_runs + llm_run_steps
-- (app/db.py::SCHEMA drops it on init).
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_runlog.py::test_schema_has_run_tables -v`
Expected: PASS

---

### Task 2: Recorder module `app/runlog.py`

**Files:**
- Create: `app/runlog.py`
- Test: `tests/test_runlog.py` (append)

**Interfaces:**
- Consumes: `app.db.connect`, `app.config.runtime`.
- Produces (used by Tasks 3–5):
  - `record_run(task_type: str, *, document_id: str | None = None, agent_role: str | None = None, model: str | None = None) -> Run`
  - `Run` context manager (sync + async): sets `.summary: dict` before exit; `run.step(name: str, input_summary: str | None = None) -> Step`
  - `Step` context manager (sync + async) with `.output: str | None`
  - `current_run() -> Run | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runlog.py`:

```python
def test_run_success_records_steps_and_summary(tmp_path):
    db = _reload_db(tmp_path)
    from app.runlog import record_run

    with record_run('ask', agent_role='ask') as run:
        with run.step('answer', input_summary='question text') as step:
            step.output = 'the answer'
        run.summary = {'answer_chars': 10}

    conn = db.connect()
    run_row = conn.execute('SELECT * FROM llm_runs').fetchone()
    steps = conn.execute('SELECT * FROM llm_run_steps ORDER BY step_index').fetchall()
    conn.close()
    assert run_row['status'] == 'success'
    assert run_row['task_type'] == 'ask'
    assert run_row['duration_ms'] is not None
    assert run_row['finished_at'] is not None
    assert json.loads(run_row['summary_json']) == {'answer_chars': 10}
    assert run_row['step_count'] == 1
    assert len(steps) == 1
    assert steps[0]['status'] == 'success'
    assert steps[0]['output_text'] == 'the answer'
    assert steps[0]['step_index'] == 0


def test_run_failure_records_error_and_reraises(tmp_path):
    db = _reload_db(tmp_path)
    from app.runlog import record_run

    with pytest.raises(ValueError):
        with record_run('extract', document_id=None, agent_role='extractor') as run:
            with run.step('extract_batch', input_summary='batch') as step:
                step.output = 'partial'
                raise ValueError('boom')

    conn = db.connect()
    run_row = conn.execute('SELECT * FROM llm_runs').fetchone()
    step = conn.execute('SELECT * FROM llm_run_steps').fetchone()
    conn.close()
    assert run_row['status'] == 'failed'
    assert 'boom' in run_row['error_message']
    assert step['status'] == 'failed'
    assert 'boom' in step['error_message']
    assert step['output_text'] == 'partial'
    assert run_row['step_count'] == 0  # only success steps count


def test_step_indices_increment(tmp_path):
    db = _reload_db(tmp_path)
    from app.runlog import record_run

    with record_run('extract', agent_role='extractor') as run:
        with run.step('extract_batch', input_summary='b1') as s1:
            s1.output = '{}'
        with run.step('extract_repair', input_summary='b1 retry') as s2:
            s2.output = '{}'

    conn = db.connect()
    idx = [r['step_index'] for r in conn.execute('SELECT step_index FROM llm_run_steps ORDER BY step_index')]
    names = [r['name'] for r in conn.execute('SELECT name FROM llm_run_steps ORDER BY step_index')]
    conn.close()
    assert idx == [0, 1]
    assert names == ['extract_batch', 'extract_repair']


def test_async_run_context(tmp_path):
    import asyncio
    db = _reload_db(tmp_path)
    from app.runlog import record_run

    async def main():
        async with record_run('agent', agent_role='personal') as run:
            async with run.step('agent_reply', input_summary='hello') as step:
                step.output = 'hi'
            run.summary = {'answer_chars': 2}

    asyncio.run(main())
    conn = db.connect()
    run_row = conn.execute('SELECT * FROM llm_runs').fetchone()
    conn.close()
    assert run_row['status'] == 'success'
    assert run_row['agent_role'] == 'personal'
    assert run_row['step_count'] == 1


def test_runlog_failure_does_not_break_main_flow(tmp_path):
    db = _reload_db(tmp_path)
    conn = db.connect()
    conn.execute('DROP TABLE llm_run_steps')
    conn.commit()
    conn.close()
    from app.runlog import record_run

    with record_run('ask') as run:
        with run.step('answer') as step:  # INSERT will fail and must be swallowed
            step.output = 'ok'

    conn = db.connect()
    run_row = conn.execute('SELECT status, step_count FROM llm_runs').fetchone()
    conn.close()
    assert run_row['status'] == 'success'
    assert run_row['step_count'] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runlog.py -v -k "success or failure or indices or async_run or does_not_break"`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.runlog'` (or ImportError on import)

- [ ] **Step 3: Create `app/runlog.py`**

```python
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


def current_run():
    """The run opened by the enclosing ``record_run`` block, if any."""
    return _current_run.get()


def _warn(exc: Exception) -> None:
    print(f'[runlog] write failed: {type(exc).__name__}: {exc}', file=sys.stderr)


def _connect():
    # Imported lazily so module reloads in tests rebind the active database.
    from .db import connect
    return connect()


class Step:
    """One model call. Use as a sync or async context manager."""

    def __init__(self, run_id: str, step_index: int, name: str, input_summary: str | None):
        self.run_id = run_id
        self.step_index = step_index
        self.name = name
        self.input_summary = input_summary
        self.output: str | None = None
        self._t0 = 0.0

    def __enter__(self) -> 'Step':
        self._t0 = time.perf_counter()
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
        try:
            conn = _connect()
            conn.execute(
                'UPDATE llm_run_steps SET status=?,output_text=?,error_message=?,duration_ms=? WHERE run_id=? AND step_index=?',
                (status, self.output, str(exc) if exc else None, duration, self.run_id, self.step_index))
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
        duration = int((time.perf_counter() - self._t0) * 1000)
        status = 'success' if exc_type is None else 'failed'
        try:
            conn = _connect()
            step_count = 0
            if exc_type is None:
                row = conn.execute(
                    "SELECT COUNT(*) c FROM llm_run_steps WHERE run_id=? AND status='success'",
                    (self.id,)).fetchone()
                step_count = row['c']
            conn.execute(
                'UPDATE llm_runs SET status=?,step_count=?,summary_json=?,error_message=?,duration_ms=?,finished_at=CURRENT_TIMESTAMP WHERE id=?',
                (status, step_count, json.dumps(self.summary, ensure_ascii=False),
                 str(exc) if exc else None, duration, self.id))
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_runlog.py -v`
Expected: all 6 tests PASS

---

### Task 3: Wire steps into `app/llm.py`

**Files:**
- Modify: `app/llm.py`

**Interfaces:**
- Consumes: `app.runlog.current_run`, `app.runlog.record_run` (Task 2).
- Produces: `extract(chunks: list[dict]) -> dict` (signature unchanged) now records `extract_batch` / `extract_repair` steps; `answer(question: str, evidence_pack: str) -> str` (signature unchanged) records an `ask` run + `answer` step.

- [ ] **Step 1: Update imports and add `_extract_one`**

In `app/llm.py`, replace the import block and add the helper after `_repair_instruction`:

```python
from __future__ import annotations
import json
from .config import runtime
from .agents.extraction_agent import extract_structured
from .extraction import normalize_extraction
from .runlog import current_run, record_run
```

```python
async def _extract_one(payload: str, step_name: str, input_summary: str) -> dict:
    """Run one extraction pass, recording it as a step of the current run."""
    run = current_run()
    if run is None:
        return await extract_structured(payload)
    with run.step(step_name, input_summary=input_summary) as step:
        data = await extract_structured(payload)
        step.output = json.dumps(data, ensure_ascii=False)
        return data
```

- [ ] **Step 2: Rewrite `extract` to use the helper**

Replace the body of `extract` (keep the docstring):

```python
async def extract(chunks: list[dict]) -> dict:
    results = []
    batch_size = max(1, int(runtime()['llm_batch_chunks']))
    batches = [chunks[i:i + batch_size] for i in range(0, len(chunks), batch_size)]
    for n, batch in enumerate(batches, 1):
        payload = '\n\n'.join(f"[CHUNK {c['id']}]\n{c['content']}" for c in batch)
        chars = sum(len(c['content']) for c in batch)
        data = await _extract_one(
            payload, 'extract_batch',
            f'batch {n}/{len(batches)} · {len(batch)} chunks · {chars} chars')
        try:
            results.append(normalize_extraction(data))
        except ValueError:
            repaired = await _extract_one(
                _repair_instruction(data), 'extract_repair',
                f'batch {n}/{len(batches)} · retry after validation failure')
            results.append(normalize_extraction(repaired))
    return _merge(results)
```

- [ ] **Step 3: Wrap `answer` in a run**

Replace `answer`:

```python
def answer(question: str, evidence_pack: str) -> str:
    with record_run('ask', agent_role='ask') as run:
        with run.step('answer', input_summary=question[:200]) as step:
            client = _client()
            response = client.chat.completions.create(
                model=runtime()['openai_model'], temperature=0.15,
                messages=[
                    {'role': 'system', 'content': 'You are a careful personal knowledge-base assistant. Use only the supplied evidence. Cite sources inline exactly as [doc:ID chunk:ID]. Do not fabricate. Note conflicts and uncertainty.'},
                    {'role': 'user', 'content': f'Question:\n{question}\n\nEvidence:\n{evidence_pack}'},
                ],
            )
            content = response.choices[0].message.content or ''
            step.output = content
            run.summary = {'question': question[:200], 'answer_chars': len(content)}
            return content
```

Note (spec deviation, accepted): the spec's `evidence_count` is not recordable because `answer()` receives the evidence already formatted as one string; `answer_chars` + `question` are recorded instead.

- [ ] **Step 4: Verify nothing broke**

Run: `pytest tests/ -v`
Expected: PASS (no new tests in this task; existing suite must stay green)

---

### Task 4: Wire the `agent` run into `agents/base.py`

**Files:**
- Modify: `app/agents/base.py` (`run_agent` only)

**Interfaces:**
- Consumes: `record_run` (Task 2).
- Produces: `run_agent(agent, message) -> str` unchanged in signature; records `agent` run with `agent_role=agent.name` and one `agent_reply` step.

- [ ] **Step 1: Rewrite `run_agent`**

Replace the whole `run_agent` function:

```python
async def run_agent(agent: Agent, message: str) -> str:
    from ..runlog import record_run
    with record_run('agent', agent_role=agent.name) as run:
        with run.step('agent_reply', input_summary=message[:200]) as step:
            result = await agent.reply(UserMsg(name="user", content=message))
            text = None
            if hasattr(result, "get_text_content"):
                text = result.get_text_content()
            if not text:
                content = getattr(result, "content", result)
                text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
            step.output = text
            run.summary = {'answer_chars': len(text)}
            return text
```

(`record_run` is imported inside the function to keep the module-import graph unchanged for tests that reload DB-bound modules.)

- [ ] **Step 2: Verify nothing broke**

Run: `pytest tests/ -v`
Expected: PASS

---

### Task 5: `extract` run in `service.index_document` + docs

**Files:**
- Modify: `app/service.py`
- Modify: `docs/PROCESSING-PIPELINE.md:33`
- Test: `tests/test_runlog.py` (append)

**Interfaces:**
- Consumes: `record_run` (Task 2), `current_run` via `llm.extract` (Task 3).
- Produces: `index_document(document_id, *, use_llm=True)` unchanged signature/return; `use_llm=True` now records an `extract` run with `summary = {counts…, 'chunks': n}`.

- [ ] **Step 1: Write the failing e2e test**

Append to `tests/test_runlog.py`:

```python
def test_index_document_records_extract_run(tmp_path, monkeypatch):
    import asyncio
    db = _reload_db(tmp_path)
    import app.llm as llm
    from app.service import create_document, index_document

    async def fake_extract_structured(payload):
        return {'entities': [{'name': 'RAG', 'types': ['Concept']}],
                'claims': [], 'events': [], 'ideas': [], 'questions': []}

    monkeypatch.setattr(llm, 'extract_structured', fake_extract_structured)
    doc_id = create_document(title='T', content='RAG helps.',
                             source_type='note', source_uri=None, metadata={})
    result = asyncio.run(index_document(doc_id, use_llm=True))

    assert result['llm'] == 'success'
    conn = db.connect()
    run_row = conn.execute("SELECT * FROM llm_runs WHERE task_type='extract'").fetchone()
    steps = conn.execute('SELECT * FROM llm_run_steps').fetchall()
    conn.close()
    assert run_row['status'] == 'success'
    assert run_row['document_id'] == doc_id
    assert run_row['agent_role'] == 'extractor'
    assert run_row['step_count'] == 1
    summary = json.loads(run_row['summary_json'])
    assert summary['entities'] == 1 and summary['chunks'] == 1
    assert len(steps) == 1 and steps[0]['name'] == 'extract_batch'
    assert json.loads(steps[0]['output_text'])['entities'][0]['name'] == 'RAG'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_runlog.py::test_index_document_records_extract_run -v`
Expected: FAIL — `assert run_row is not None` fails because `index_document` does not open a run yet (the fetchone returns None).

- [ ] **Step 3: Rewrite `index_document` in `app/service.py`**

Replace the whole function (imports at top stay; `import time` may be removed if now unused — it is):

```python
async def index_document(document_id: str, *, use_llm=True):
    from .db import connect
    from .runlog import record_run
    conn=connect(); doc=conn.execute('SELECT * FROM documents WHERE id=?',(document_id,)).fetchone(); conn.close()
    if not doc: raise KeyError(document_id)
    clear_derived(document_id)
    chunks=write_chunks(document_id, doc['content'])
    if not use_llm:
        embedded=False
        if runtime()['auto_embed']:
            try:
                await asyncio.to_thread(embed_document, document_id)
                embedded=True
            except RuntimeError:
                pass
        return {'document_id':document_id,'chunks':len(chunks),'llm':'skipped','embedded':embedded}
    async with record_run('extract', document_id=document_id, agent_role='extractor') as run:
        result=normalize_extraction(await extract(chunks))
        with transaction() as conn:
            counts=persist_extraction(conn,document_id=document_id,extraction=result)
        run.summary={**counts,'chunks':len(chunks)}
        embedded=False
        if runtime()['auto_embed']:
            try:
                await asyncio.to_thread(embed_document, document_id)
                embedded=True
            except RuntimeError:
                pass
        return {'document_id':document_id,'chunks':len(chunks),'llm':'success','counts':counts,'embedded':embedded}
```

Also remove `time` from the top-of-file import line if nothing else in the module uses it:

```python
import asyncio, hashlib, uuid
```

The old `extraction_runs` INSERT/UPDATE statements and the `try/except` that wrote `status='failed'` are deleted — runlog owns success/failure bookkeeping now, and exceptions still propagate to the route handler.

- [ ] **Step 4: Update the pipeline doc**

In `docs/PROCESSING-PIPELINE.md`, change line 33 from:

```
6. Record an `extraction_runs` audit record.
```

to:

```
6. Record an `llm_runs` audit row (plus one `llm_run_steps` row per model call, including the raw model output).
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_runlog.py -v && pytest tests/ -v`
Expected: all PASS

---

### Task 6: API — `/api/runs`, delete `/api/extractions`, stats swap

**Files:**
- Modify: `app/main.py`

**Interfaces:**
- Consumes: tables from Task 1.
- Produces:
  - `GET /api/runs?task_type=&status=&limit=` → `[run dict]` with `summary` parsed and `document_title` joined; no steps.
  - `GET /api/runs/{run_id}` → run dict + `steps: [step dict]` (includes `output_text`).

- [ ] **Step 1: Replace the extractions endpoint**

In `app/main.py`, delete:

```python
@app.get('/api/extractions')
def extraction_runs(limit:int=50):
    conn=connect(); rows=conn.execute('SELECT * FROM extraction_runs ORDER BY created_at DESC LIMIT ?',(max(1,min(limit,200)),)).fetchall(); conn.close(); return [dict(r) for r in rows]
```

and put in its place:

```python
@app.get('/api/runs')
def list_runs(limit:int=50, task_type:str|None=None, status:str|None=None):
    limit=max(1,min(limit,200))
    sql='SELECT r.*,d.title document_title FROM llm_runs r LEFT JOIN documents d ON d.id=r.document_id'
    params=[]
    cond=[]
    if task_type: cond.append('r.task_type=?'); params.append(task_type)
    if status: cond.append('r.status=?'); params.append(status)
    if cond: sql+=' WHERE '+' AND '.join(cond)
    sql+=' ORDER BY r.created_at DESC LIMIT ?'; params.append(limit)
    conn=connect(); rows=conn.execute(sql,params).fetchall(); conn.close()
    return [dict(r)|{'summary':loads(r.pop('summary_json'),{})} for r in rows]

@app.get('/api/runs/{run_id}')
def get_run(run_id:str):
    conn=connect()
    run=conn.execute('SELECT r.*,d.title document_title FROM llm_runs r LEFT JOIN documents d ON d.id=r.document_id WHERE r.id=?',(run_id,)).fetchone()
    if not run: conn.close(); raise HTTPException(404,'Run not found')
    steps=conn.execute('SELECT * FROM llm_run_steps WHERE run_id=? ORDER BY step_index',(run_id,)).fetchall()
    conn.close()
    item=dict(run); item['summary']=loads(item.pop('summary_json'),{}); item['steps']=[dict(s) for s in steps]
    return item
```

- [ ] **Step 2: Swap the stats table name**

In `app/main.py::stats`, change:

```python
    conn = connect(); tables = ['documents','chunks','entities','claims','relations','ideas','questions','events','chunk_embeddings','extraction_runs']
```

to:

```python
    conn = connect(); tables = ['documents','chunks','entities','claims','relations','ideas','questions','events','chunk_embeddings','llm_runs','llm_run_steps']
```

- [ ] **Step 3: Smoke-check the API**

Run: `pytest tests/ -v`
Expected: PASS

Run the server manually (`run.bat` or `python -m uvicorn app.main:app --port 8000`) and verify:
- `GET http://127.0.0.1:8000/api/runs` → `[]` (or seeded rows)
- `GET http://127.0.0.1:8000/api/extractions` → `{"detail":"Not Found"}`

---

### Task 7: UI — Runs view

**Files:**
- Modify: `web/index.html`

**Interfaces:**
- Consumes: `/api/runs`, `/api/runs/{id}` (Task 6).

- [ ] **Step 1: Add the nav button**

In `web/index.html` line 18, insert before the settings button:

```html
<button data-view="runs">🧾 Runs</button>
```

- [ ] **Step 2: Register the view**

In the `render()` function (line 50), append to the chain of ifs:

```javascript
if(state.view==='runs')renderRuns();
```

- [ ] **Step 3: Add the render functions**

Insert before `function drawGraph(g){` (line 94):

```javascript
async function renderRuns(){
  app.innerHTML=`<div class="card"><div class="row"><h1 class="title">Task Runs</h1><button onclick="renderRuns()">Refresh</button></div><div class="row" style="margin:10px 0"><select id="runType" onchange="renderRuns()"><option value="">All types</option><option value="extract">extract</option><option value="ask">ask</option><option value="agent">agent</option></select></div><div id="runList" class="list"></div><div id="runDetail"></div></div>`;
  const t=document.getElementById('runType').value;
  try{
    const runs=await api('/api/runs?limit=50'+(t?'&task_type='+t:''));
    document.getElementById('runList').innerHTML=runs.map(r=>`<div class="result" style="cursor:pointer" onclick="showRun('${r.id}')"><div class="row"><strong>${esc(r.task_type)}</strong><span class="pill ${r.status==='failed'?'':'status'}">${esc(r.status)}</span><span class="small muted">${esc(r.document_title||r.agent_role||'')}</span></div><div class="small muted">${esc(r.created_at)} · ${r.step_count} steps · ${r.duration_ms??'—'} ms${r.error_message?' · '+esc(r.error_message.slice(0,120)):''}</div></div>`).join('')||'<div class="empty">No runs yet</div>';
  }catch(e){document.getElementById('runList').innerHTML=`<div class="card"><strong>Error</strong><div class="pre">${esc(e.message)}</div></div>`}
}
async function showRun(id){
  document.getElementById('runDetail').innerHTML='<div class="card">Loading…</div>';
  try{
    const r=await api('/api/runs/'+id);
    document.getElementById('runDetail').innerHTML=`<div class="card"><h3>Run detail</h3><div class="sub">${esc(r.task_type)} · ${esc(r.status)} · ${esc(r.model||'')} · ${r.duration_ms??'—'} ms</div><div class="pre mono" style="margin-top:8px">${esc(JSON.stringify(r.summary,null,2))}</div>${(r.steps||[]).map(s=>`<div class="result"><div class="row"><strong>${esc(s.name)}</strong><span class="pill ${s.status==='failed'?'':'status'}">${esc(s.status)}</span><span class="small muted">${s.duration_ms??'—'} ms</span></div><div class="small muted">${esc(s.input_summary||'')}</div>${s.error_message?`<div class="pre" style="color:#b91c1c">${esc(s.error_message)}</div>`:''}<details style="margin-top:6px"><summary>LLM output</summary><div class="pre mono">${esc(s.output_text||'(none)')}</div></details></div>`).join('')||'<div class="muted">No steps</div>'}</div>`;
  }catch(e){document.getElementById('runDetail').innerHTML=`<div class="card"><strong>Error</strong><div class="pre">${esc(e.message)}</div></div>`}
}
```

- [ ] **Step 4: Manual UI check**

Start the server, open `http://127.0.0.1:8000/`, click "🧾 Runs": list renders (empty state ok), type filter works, clicking a row renders the detail card with steps and collapsible output.

---

### Task 8: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Full test suite**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 2: Restart the server** (it was started without `--reload`)

Kill the current uvicorn process, then start it again with the usual command (`run.bat` / `python -m uvicorn app.main:app --port 8000`).

- [ ] **Step 3: End-to-end acceptance**

1. Index a document from the UI ("Index with LLM") → open Runs → `extract` run with `extract_batch` steps containing raw JSON output.
2. Ask a question in the Ask view → `ask` run with the `answer` step.
3. Run one Agent conversation → `agent` run with `agent_reply` step.
4. `GET /api/stats` shows `llm_runs` / `llm_run_steps` counts; `GET /api/extractions` returns 404.

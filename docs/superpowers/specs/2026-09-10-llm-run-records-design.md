# Design: 统一 LLM 任务执行记录（llm_runs + llm_run_steps）

- Date: 2026-09-10
- Status: Approved
- Scope: 记录所有触发 LLM 的任务（文档抽取 / 知识库问答 / Agent 对话），任务级 run + 调用级 step 两层。

## 已确认的决策

1. **覆盖范围**：统一 LLM 任务记录，覆盖 `extract`、`ask`、`agent` 三类入口，一套 API + 一个统一列表页。
2. **粒度**：两层。`llm_runs`（任务）+ `llm_run_steps`（每次真正打模型的调用）。列表看任务汇总，展开看每次 LLM 调用。
3. **step 内容（标准档）**：状态、耗时、输入摘要、LLM 原始输出全文、错误信息。不记 token 用量与工具调用轨迹。
4. **旧表处置**：直接替换。删除 `extraction_runs` 表与 `GET /api/extractions`，只留新的一套；历史抽取记录随 DROP 丢失（用户已接受）。

## 背景

此前只有 `extraction_runs` 一张表：只记文档级汇总（计数/耗时/错误），不记 LLM 原始返回；`llm.extract` 按 `llm_batch_chunks` 分批调用 AgentScope agent、每批可能因校验失败触发一次 repair 重试，这些调用级信息完全不可见；问答（`llm.answer`）与 Agent 对话（`agents/base.run_agent`）无任何记录。

## 数据模型（app/db.py::SCHEMA）

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

- `document_id`：抽取任务填；问答/对话为空。删文档级联删其 run 历史（与旧表一致）。
- `agent_role`：`extractor` / `personal` / `knowledge` / `research` / `curator` / `review`；问答为 `ask`。
- `summary_json`：
  - extract：`{"entities":n,"claims":n,"relations":n,"ideas":n,"questions":n,"events":n,"chunks":n}`
  - ask：`{"question":"…前200字","answer_chars":n,"evidence_count":n}`
  - agent：`{"answer_chars":n}`
- `output_text` 不做截断；列表接口不返回该字段，仅详情接口返回。

## 记录器模块 app/runlog.py（新文件）

```python
# 同步与异步调用点通用：Run 同时实现 __enter__/__exit__ 与 __aenter__/__aexit__
with record_run('ask', agent_role='ask') as run: ...
async with record_run('extract', document_id=doc_id, agent_role='extractor') as run: ...

with run.step('extract_batch', input_summary='chunk 1/3 · 4 chunks · 6120 chars') as step:
    step.output = json.dumps(data, ensure_ascii=False)
```

- `record_run(task_type, *, document_id=None, agent_role=None, model=None)`：
  - 进入：INSERT `started` 行；退出（正常）：UPDATE `success` + `duration_ms` + `finished_at` + summary；退出（异常）：UPDATE `failed` + `error_message`，异常照常向上抛。
  - 以 `contextvars.ContextVar` 暴露 `current_run()`，供内层调用点（`llm.extract`）在不改签名的情况下挂 step。
  - `step_count` 由成功的 step 数在 run 收尾时回填。
- `run.step(name, input_summary=None)`：INSERT `started` step；正常退出写 `success` + `output_text` + `duration_ms`；异常写 `failed` + `error_message` 并照常抛出。
- **可靠性红线**：runlog 自身的所有写库操作包 try/except，失败仅向 stderr 打印警告，绝不影响主流程。
- 同步场景说明：`/api/ask` 是同步 endpoint（FastAPI 线程池线程），`llm.answer` 为同步函数；`contextvar` 与 SQLite 连接都在同线程内创建与使用，无跨线程问题。

## 接入点

| 位置 | run | step |
|---|---|---|
| `service.index_document`（use_llm=True） | `record_run('extract', document_id=…, agent_role='extractor', model=…)`，收尾 summary=counts+chunks | — |
| `llm.extract` | 复用 `current_run()` | 每批 `extract_batch`（output=normalize 前的原始 JSON）；校验失败重试记 `extract_repair` |
| `llm.answer` | `record_run('ask', agent_role='ask', model=…)`，summary 含 answer_chars | `answer`，output=回答全文 |
| `agents/base.run_agent` | `record_run('agent', agent_role=agent.name, model=…)` | `agent_reply`，output=最终文本 |

不重复记录：`extract_structured` 直接调用 `agent.reply`，不经过 `base.run_agent`。`/api/agent/ask` 路由不变，记录发生在 `base.run_agent`。

## API（app/main.py）

- `GET /api/runs?task_type=&status=&limit=`：run 列表（不含 steps），LEFT JOIN `documents.title` 作为 `document_title`。
- `GET /api/runs/{run_id}`：run + `steps` 数组（含 `output_text`）。
- 删除 `GET /api/extractions`。
- `GET /api/stats`：`extraction_runs` → `llm_runs`。

## UI（web/index.html）

- nav 增加 `<button data-view="runs">🧾 Runs</button>`，`render()` 增加 `renderRuns()` 分支。
- `renderRuns()`：列表行 = 时间 · 类型 · 目标（文档标题/问题摘要/Agent 角色）· 状态 · 步数 · 耗时；点击行 `GET /api/runs/{id}` 展开 steps（名称、状态、耗时、input_summary；`output_text` 用 `<details>` 折叠显示）。

## 测试（tests/test_runlog.py，新增）

1. 成功 run：`started` → `success`，`duration_ms`、`summary_json`、`step_count` 正确。
2. 异常 run：status=`failed`、`error_message` 非空，异常向上抛。
3. step 明细：多 step 顺序 `step_index` 递增，`output_text` 落库，与 `step_count` 一致。
4. runlog 自身故障不传播：模拟写库异常（如临时指向无效表）不影响被包裹函数正常返回。
5. 端到端：`index_document`（mock LLM）产生 extract run + steps。

## 文档与迁移

- `docs/PROCESSING-PIPELINE.md`：把 "Record an `extraction_runs` audit record." 更新为 `llm_runs` / `llm_run_steps` 两层记录。
- `sql/schema.v0.1.sql`：与 `app/db.py::SCHEMA` 保持一致（该文件仅是说明副本）。
- `init_db()` 现有 SCHEMA 重放机制直接完成迁移与 DROP，无需额外迁移函数。

## 验收

1. `pytest` 全绿。
2. 重启服务：索引一篇文档 → run+steps 有数据（含原始 JSON 输出）；问答一次 → ask run；Agent 对话一次 → agent run。
3. `/api/runs` 列表与详情接口、UI Runs 页可用；`/api/extractions` 返回 404。

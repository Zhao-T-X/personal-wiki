# Frontend Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-file vanilla UI with a Vue 3 + Vite + TypeScript workspace implementing the approved prototype (`docs/prototype/llm-wiki-full-ui-v3-qa.html`), 14 pages, drawer-first interactions, plus 6 small backend endpoints.

**Architecture:** New `frontend/` Vite app builds to `web/dist`; FastAPI serves `web/dist` when present and falls back to the legacy `web/index.html`. Visual baseline is the prototype's CSS token system ported verbatim. All pages consume the existing `/api` plus 6 small new endpoints.

**Tech Stack:** Vue 3 `<script setup>` + TypeScript, Vite 6, Vue Router 4, Pinia 2, Cytoscape.js, markdown-it; backend FastAPI + stdlib sqlite3.

**Spec:** `docs/superpowers/specs/2026-09-10-frontend-redesign-design.md`
**Visual reference (source of truth):** `docs/prototype/llm-wiki-full-ui-v3-qa.html`

## Global Constraints

- Not a git repository — skip every commit step; each task ends with its verification command.
- Visual tokens come from the prototype `:root` block verbatim (`--bg:#f7f9fe;--blue:#5b7cff;--violet:#8b67f7;--radius:18px` etc.); do not invent new colors. Light theme only.
- No fake data anywhere: pages render only what the API returns; empty states use the `.empty` dashed style.
- Cut per user decision: Workflows page, notification center, avatar/user system.
- Backend additions follow `app/main.py` compact one-line style; all list endpoints cap `limit=max(1,min(limit,200))`.
- `frontend/node_modules` must never be copied/served/deployed; only `web/dist` is served.
- Existing pytest suite must stay green after backend changes.

## File Structure

```
app/main.py                    (modify: 6 endpoints + dist serving)
tests/test_frontend_api.py     (create: backend endpoint tests)
frontend/
  package.json  vite.config.ts  tsconfig.json  index.html
  src/main.ts  src/App.vue
  src/styles/tokens.css        (prototype tokens + component classes)
  src/api/client.ts  src/api/types.ts
  src/stores/app.ts
  src/router/index.ts
  src/components/ (AppDrawer.vue, AppModal.vue, AppToast.vue, KpiCard.vue,
                   StatusTag.vue, DataTable.vue, EmptyState.vue, MarkdownView.vue,
                   PageHead.vue, SegTabs.vue)
  src/views/ (DashboardView.vue, KnowledgeView.vue, QaView.vue, EntitiesView.vue,
              GraphView.vue, EventsView.vue, IdeasView.vue, QuestionsView.vue,
              EvidenceView.vue, TasksView.vue, LogsView.vue, PromptsView.vue,
              SettingsView.vue, DatabaseView.vue)
```

---

### Task 1: Backend — 6 new endpoints + tests

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_frontend_api.py` (create)

**Interfaces (produced):**
- `GET /api/events?limit=` → `[{id,event_type,description,time_json,status,confidence,source_document_id,source_quote,created_at}]`
- `GET /api/ideas?status=&limit=` → `[{id,content,status,confidence,source_document_id,source_quote,created_at}]`
- `GET /api/questions?status=&limit=` → `[{id,content,question_type,status,source_document_id,source_quote,created_at}]`
- `GET /api/stats/timeseries?days=14` → `[{date,documents,entities,claims}]` ascending
- `GET /api/database/integrity` → `{"integrity":"ok","size_mb":float,"page_count":int}`
- `GET /api/documents` rows gain `chunk_count:int`, `last_run_status:str|None`, `last_run_at:str|None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_frontend_api.py`:

```python
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


def test_events_ideas_questions_endpoints(tmp_path):
    db = _reload_db(tmp_path)
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    conn = db.connect()
    conn.execute("INSERT INTO events(id,event_type,description,status) VALUES('e1','release','R1','completed')")
    conn.execute("INSERT INTO ideas(id,content,status,source_document_id,source_chunk_id,source_start_offset,source_end_offset) VALUES('i1','idea text','candidate','doc','ch',0,1)")
    conn.execute("INSERT INTO questions(id,content,status,source_document_id,source_chunk_id,source_start_offset,source_end_offset) VALUES('q1','question text','open','doc','ch',0,1)")
    conn.commit(); conn.close()
    ev = client.get('/api/events').json()
    ideas = client.get('/api/ideas').json()
    qs = client.get('/api/questions').json()
    assert ev[0]['id'] == 'e1' and ev[0]['event_type'] == 'release'
    assert ideas[0]['content'] == 'idea text' and ideas[0]['status'] == 'candidate'
    assert qs[0]['content'] == 'question text'
    assert client.get('/api/ideas?status=accepted').json() == []


def test_timeseries_and_integrity(tmp_path):
    db = _reload_db(tmp_path)
    from app.service import create_document
    create_document(title='T', content='c', source_type='note', source_uri=None, metadata={})
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    ts = client.get('/api/stats/timeseries?days=7').json()
    assert len(ts) == 7 and ts[-1]['documents'] >= 1
    assert ts[0]['date'] < ts[-1]['date']
    ig = client.get('/api/database/integrity').json()
    assert ig['integrity'] == 'ok' and ig['size_mb'] >= 0


def test_documents_list_aggregates(tmp_path):
    db = _reload_db(tmp_path)
    import asyncio
    from app.service import create_document, write_chunks
    doc_id = create_document(title='D', content='hello world data', source_type='note', source_uri=None, metadata={})
    write_chunks(doc_id, 'hello world data')
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    docs = client.get('/api/documents').json()
    row = next(d for d in docs if d['id'] == doc_id)
    assert row['chunk_count'] >= 1
    assert 'last_run_status' in row
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_frontend_api.py -v`
Expected: FAIL — 404 on `/api/events` etc.

- [ ] **Step 3: Implement in `app/main.py`**

Add after the existing `/api/documents` list endpoint (replace it):

```python
@app.get('/api/documents')
def list_docs(limit:int=100):
    limit=max(1,min(limit,500))
    conn=connect()
    rows=conn.execute('''SELECT d.id,d.title,d.source_type,d.source_uri,d.created_at,d.updated_at,
        (SELECT COUNT(*) FROM chunks c WHERE c.document_id=d.id) chunk_count,
        (SELECT status FROM llm_runs r WHERE r.document_id=d.id ORDER BY r.created_at DESC LIMIT 1) last_run_status,
        (SELECT created_at FROM llm_runs r WHERE r.document_id=d.id ORDER BY r.created_at DESC LIMIT 1) last_run_at
        FROM documents d ORDER BY d.updated_at DESC LIMIT ?''',(limit,)).fetchall(); conn.close()
    return [dict(r) for r in rows]

@app.get('/api/events')
def events_list(limit:int=100,status:str|None=None):
    sql='SELECT id,event_type,description,participants_json,time_json,location,status,confidence,source_document_id,source_quote,created_at FROM events'; params=[]
    if status: sql+=' WHERE status=?'; params.append(status)
    sql+=' ORDER BY created_at DESC LIMIT ?'; params.append(max(1,min(limit,200)))
    conn=connect(); rows=conn.execute(sql,params).fetchall(); conn.close()
    return [dict(r)|{'participants':loads(r['participants_json'],[]),'time':loads(r['time_json'],{})} for r in rows]

@app.get('/api/ideas')
def ideas_list(limit:int=100,status:str|None=None):
    sql='SELECT id,content,status,confidence,source_document_id,source_quote,created_at FROM ideas'; params=[]
    if status: sql+=' WHERE status=?'; params.append(status)
    sql+=' ORDER BY created_at DESC LIMIT ?'; params.append(max(1,min(limit,200)))
    conn=connect(); rows=conn.execute(sql,params).fetchall(); conn.close(); return [dict(r) for r in rows]

@app.get('/api/questions')
def questions_list(limit:int=100,status:str|None=None):
    sql='SELECT id,content,question_type,status,confidence,source_document_id,source_quote,created_at FROM questions'; params=[]
    if status: sql+=' WHERE status=?'; params.append(status)
    sql+=' ORDER BY created_at DESC LIMIT ?'; params.append(max(1,min(limit,200)))
    conn=connect(); rows=conn.execute(sql,params).fetchall(); conn.close(); return [dict(r) for r in rows]

@app.get('/api/stats/timeseries')
def stats_timeseries(days:int=14):
    days=max(1,min(days,90))
    conn=connect()
    def series(table):
        rows=conn.execute(f"SELECT date(created_at) d,COUNT(*) c FROM {table} WHERE created_at>=date('now',?) GROUP BY date(created_at)",(f'-{days} days',)).fetchall()
        return {r['d']:r['c'] for r in rows}
    docs,ents,clms=series('documents'),series('entities'),series('claims'); conn.close()
    out=[]; from datetime import date,timedelta
    for i in range(days-1,-1,-1):
        d=str(date.today()-timedelta(days=i))
        out.append({'date':d,'documents':docs.get(d,0),'entities':ents.get(d,0),'claims':clms.get(d,0)})
    return out

@app.get('/api/database/integrity')
def database_integrity():
    conn=connect()
    integrity=conn.execute('PRAGMA integrity_check').fetchone()[0]
    page_count=conn.execute('PRAGMA page_count').fetchone()[0]
    page_size=conn.execute('PRAGMA page_size').fetchone()[0]
    conn.close()
    return {'integrity':integrity,'page_count':page_count,'size_mb':round(page_count*page_size/1048576,1)}
```

- [ ] **Step 4: Run tests + full suite**

Run: `pytest tests/test_frontend_api.py -v && pytest tests/ -q`
Expected: all PASS (existing suite stays green)

---

### Task 2: Vite app scaffold + tokens + API layer

**Files:**
- Create: `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/index.html`, `src/main.ts`, `src/App.vue`, `src/styles/tokens.css`, `src/api/client.ts`, `src/api/types.ts`, `src/stores/app.ts`, `src/router/index.ts`

**Interfaces (produced):**
- `api/client.ts`: `export async function api<T>(path: string, opt?: RequestInit): Promise<T>` (JSON parse + error detail extraction, same semantics as legacy UI), plus `export const endpoints = { ... }` typed helpers per route used by views.
- `stores/app.ts`: Pinia store `useAppStore` — `health`, `searchTerm`, `toast(msg)`.
- Router paths: `/`, `/knowledge`, `/qa`, `/entities`, `/graph`, `/events`, `/ideas`, `/questions`, `/evidence`, `/tasks`, `/logs`, `/prompts`, `/settings`, `/database`.

- [ ] **Step 1: Scaffold**

Run in workspace root:

```
cd e:\test_project\llm-wiki-agentscope-v0.5.2-sqlite-prompts
npm create vite@latest frontend -- --template vue-ts
cd frontend && npm install && npm install vue-router@4 pinia cytoscape markdown-it && npm install -D @types/markdown-it
```

- [ ] **Step 2: `vite.config.ts`** — build outDir + proxy:

```ts
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  build: { outDir: '../web/dist', emptyOutDir: true },
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
})
```

- [ ] **Step 3: Port tokens** — `src/styles/tokens.css` receives the prototype's `:root` variables and shared component classes (`.card`, `.kpi`, `.table`, `.tag`, `.btn`, `.field`, `.seg`, `.drawer`, `.modal`, `.toast`, `.empty`, `.timeline`, `.kv`, `.listitem`, `.log` …) copied from `docs/prototype/llm-wiki-full-ui-v3-qa.html` lines 8–57, minus `.nav` sidebar specifics which live in `App.vue` scoped styles, minus media-query references to `.workflow`/avatar classes.

- [ ] **Step 4: API layer** — `src/api/client.ts`:

```ts
export async function api<T = any>(path: string, opt?: RequestInit): Promise<T> {
  const r = await fetch(path, opt)
  const text = await r.text()
  let j: any = null
  try { j = text ? JSON.parse(text) : null } catch { j = null }
  if (!r.ok) {
    const detail = (j && typeof j === 'object' && (j.detail || j.message)) || text || `HTTP ${r.status}`
    throw new Error(String(detail))
  }
  if (j === null && text) throw new Error(`Non-JSON response: ${text.slice(0, 300)}`)
  return j as T
}
export const post = (p: string, body?: unknown) =>
  api(p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
export const put = (p: string, body: unknown) =>
  api(p, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
export const patch = (p: string, body: unknown) =>
  api(p, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
```

`src/api/types.ts` defines interfaces for DocumentRow, Entity, Claim, Relation, Run, RunStep, EventRow, IdeaRow, QuestionRow, Settings, Health, TimeseriesPoint — fields exactly matching Task 1 responses and existing endpoints.

- [ ] **Step 5: Shell** — `src/App.vue` renders the prototype sidebar (nav groups 概览/知识空间/智能体空间/系统 minus Workflows; pill badges only when real counts exist) + topbar (search box → `router.push({path:'/knowledge', query:{q}})`, settings icon; no bell, no avatar) + `<RouterView>` inside `.content`. Sidebar footer status card binds real `/api/health` (SQLite WAL line from `database_path`, AgentScope from `llm_configured`, version from `version`). `src/stores/app.ts`:

```ts
import { defineStore } from 'pinia'
import { api } from '../api/client'
export const useAppStore = defineStore('app', {
  state: () => ({ health: null as null | Record<string, any>, toastMsg: '', searchTerm: '' }),
  actions: {
    async loadHealth() { try { this.health = await api('/api/health') } catch { this.health = null } },
    toast(msg: string) { this.toastMsg = msg; setTimeout(() => (this.toastMsg = ''), 1800) },
  },
})
```

- [ ] **Step 6: Verify**

Run: `cd frontend && npm run build`
Expected: build succeeds, `web/dist/index.html` exists (views may be stubs returning empty layout — real views come in Tasks 3–7).

---

### Task 3: Shared components

**Files (create):** `src/components/{KpiCard,StatusTag,DataTable,AppDrawer,AppModal,AppToast,EmptyState,MarkdownView,PageHead,SegTabs}.vue`

**Interfaces (produced):**
- `KpiCard.vue`: props `{label:string; value:string|number; trend?:string; warn?:boolean}`
- `StatusTag.vue`: props `{status:string}` → maps status → tag color class (`green`: success/verified/processed/completed/valid/grounded; `amber`: candidate/open/queued/review/partial; `red`: failed/invalid/missing; `blue`: running/processing/started; default gray)
- `DataTable.vue`: props `{columns:{key,label}[]; rows:any[]}`; emits `row-click(row)`; slot for cell overrides; built-in client-side filter via prop `filter:string`
- `AppDrawer.vue`: props `{open:boolean; title:string}`; emits `close`; default slot
- `AppModal.vue`: props `{open:boolean; title:string; subtitle?:string}`; emits `close`
- `AppToast.vue`: reads `useAppStore().toastMsg`
- `EmptyState.vue`: props `{text:string}`
- `MarkdownView.vue`: props `{content:string}` → markdown-it render with `html:false`
- `PageHead.vue`: props `{title:string; subtitle?:string}`; slot `actions`
- `SegTabs.vue`: props `{options:string[]; modelValue:string}`; emits `update:modelValue`

- [ ] **Step 1: Implement all 10 components** — each is a small SFC using the prototype's CSS classes (`card kpi`, `tag`, `table`, `drawer`, `modal`, `toast`, `empty`, `timeline`, `kv`, `listitem`) moved into the shared `tokens.css` so components stay thin.
- [ ] **Step 2: Verify** — `npm run build` passes.

---

### Task 4: Views — 概览 + 知识空间 (6 pages)

**Files (create):** `DashboardView.vue`, `KnowledgeView.vue`, `QaView.vue`, `EntitiesView.vue`, `GraphView.vue`, `EventsView.vue`, `IdeasView.vue`, `QuestionsView.vue`, `EvidenceView.vue`

- [ ] **DashboardView** — KPI row from `/api/stats` (Documents+Chunks → 知识对象, Entities, Claims, today's runs via `/api/runs?limit=200` client filter); growth chart = inline SVG polyline built from `/api/stats/timeseries?days=14` (documents + claims lines, legend like prototype); 待处理 card = candidate counts from `/api/stats` (entities/claims candidate) linking to `/review`… (no review page in new IA → link to `/entities` with query `status=candidate`); 最近知识流 table = `/api/documents?limit=8` + `/api/runs?limit=8` merged by time; row click → document drawer.
- [ ] **KnowledgeView** — SegTabs `Documents|Chunks|Claims|Runs`; Documents table from `GET /api/documents` (columns Document/类型/Chunks/最后抽取/状态 `last_run_status`), toolbar filter + type select; row click → drawer with markdown-rendered content (`GET /api/documents/{id}`), actions: Index with LLM (`POST /api/documents/{id}/index`), Build chunks only (`/index/local`), Embeddings (`/embed`), Delete (`DELETE`), chunks list (`GET /api/documents/{id}/chunks` collapsed `<details>`); Chunks tab = prompt for doc selection then same chunks call; Claims tab = `GET /api/claims?limit=100`; Runs tab = `GET /api/runs?limit=50`. 新建/导入 modal → `POST /api/documents` / `POST /api/documents/import` (FormData, no JSON header).
- [ ] **QaView** — question textarea + 发送 → `POST /api/ask`; answer card (`grounded` tag, answer text, evidence quote blocks from `evidence[]` with title/chunk/content); citations list; 最近问答 table from `/api/runs?task_type=ask&limit=10` (question from `summary.question`, duration, status); row click → run drawer.
- [ ] **EntitiesView** — prototype `.detail` layout: left table from `/api/entities?limit=200` (filter + type select + status select), right detail card loads `GET /api/entities/{id}` (hero icon = first letter, `.kv` fields, aliases, claims list, description edit → `PATCH /api/entities/{id}`); status change buttons (verify/reject) → `PATCH /api/knowledge/entity/{id}/status`; 新建实体 modal → (no POST /api/entities endpoint exists — create via extraction only; modal posts nothing, show hint) — implement modal as read-only note "实体由抽取创建" unless backend adds endpoint. Graph preview button → `/graph?focus={id}`.
- [ ] **GraphView** — toolbar: focus entity input, depth select (1–3), predicate filter; fetch `/api/graph` (or `/api/entities/{id}/graph?depth=` when focused); Cytoscape container `.graph` height 500px; node color by entity type via token palette (Technology #5b7cff, Concept #8a69e9, Dataset #45c49a, Software #62c6e4, Method #8b67f7, fallback #f1a456); edge label = predicate; tap node → right drawer with `GET /api/entities/{id}` summary + relations; zoom/pan enabled, `fit` on load.
- [ ] **EventsView** — KPIs from `GET /api/events?limit=200` (total, by type counts); timeline (`.timeline/.event`) sorted by `time.start` desc; filter chips by `event_type`/`status`; row → drawer with quote + source doc link.
- [ ] **IdeasView** — three columns candidate/accepted/implemented (+archived collapsed) from `GET /api/ideas`; card actions: accept/implement/reject via `PATCH /api/knowledge/idea/{id}/status`; 记录想法 modal → no POST endpoint — omit create button (read-only board), note in subtitle.
- [ ] **QuestionsView** — table from `GET /api/questions` (seg filter by status); status change via `PATCH /api/knowledge/question/{id}/status`; row → drawer with quote/source.
- [ ] **EvidenceView** — table from `GET /api/claims?limit=200` (Knowledge Object = `subject → predicate → object_text||object_name`, Document = `source_document_id`, Chunk, Quote, Offsets `[start,end)`, 校验 tag = green when `source_quote` non-empty else red missing); filter by validity; row click → drawer with full quote + doc link.

- [ ] **Verify:** `npm run build` passes; `npm run dev` + walk pages against running backend (each shows real data or clean empty state).

---

### Task 5: Views — 智能体空间 + 系统 (5 pages)

**Files (create):** `TasksView.vue`, `LogsView.vue`, `PromptsView.vue`, `SettingsView.vue`, `DatabaseView.vue`

- [ ] **TasksView** — KPIs from `/api/runs?limit=200` (running = `started`, failed, success today, total); table 时间/类型/目标/状态/步数/耗时/错误摘要; filter seg by task_type + status select; row → drawer = run detail (summary JSON pretty, steps list with StatusTag + duration + input_summary, `output_text` in `<details>` with copy button).
- [ ] **LogsView** — Runtime Trace panel styled like prototype `.log`: pick a run (latest first from `/api/runs?limit=50`, type filter) → `GET /api/runs/{id}`; render steps as monospace trace lines: `[hh:mm:ss] OK|ERROR name` + indented `input/output/duration` lines, colors `.ok/.warn/.err` by step status; right column 最近调用 = runs list.
- [ ] **PromptsView** — prototype `.prompt-layout`: left agent menu from `GET /api/agent/prompts` (6 roles, icon, name+description); center: version line + seg tabs (Prompt/版本历史) + dark `textarea.promptbox` bound to `custom_prompt`; actions 恢复默认 (`POST /api/agent/prompts/{role}/reset`), 预览 (`GET /api/agent/prompts/{role}` → `effective_prompt_preview` in `<details>`), 保存 (`PUT /api/agent/prompts/{role}` `{custom_prompt,note:'edited in UI'}`); right: version history list from profile `history[]` + 恢复此版本 (`POST .../restore` `{version}`) + Skills list (`GET /api/agent/roles` read-only).
- [ ] **SettingsView** — prototype `.settings` layout: left nav (基础配置 / LLM / 检索与Embedding / 数据) as anchor groups; form fields from `GET /api/settings` (base_url, model, embedding model, dims, API key password field with `openai_api_key_configured` placeholder, database_path, llm_batch_chunks, max_search_results); switches for `auto_embed`, `agentscope_enabled`; 保存 → `PUT /api/settings` (omit empty api key); 测试连接 → `/api/health` result in `.log` box.
- [ ] **DatabaseView** — KPIs from `GET /api/database/integrity` (size_mb) + `/api/stats` (tables count, total rows); Schema Overview table from `/api/stats` (Table/Rows/Status OK); 完整性检查 button → integrity endpoint shows result tag; no backup button (no backend).

- [ ] **Verify:** `npm run build` passes; full click-through against backend.

---

### Task 6: FastAPI serves dist + final acceptance

**Files:**
- Modify: `app/main.py` (bottom, replacing static mount block)

- [ ] **Step 1: Serve dist with fallback**

Replace:

```python
app.mount('/static',StaticFiles(directory='web'),name='static')
@app.get('/')
def index(): return FileResponse('web/index.html')
```

with:

```python
from pathlib import Path as _Path
_DIST = _Path('web/dist')

@app.get('/')
def index():
    if (_DIST / 'index.html').exists(): return FileResponse(_DIST / 'index.html')
    return FileResponse('web/index.html')

if (_DIST / 'assets').exists():
    app.mount('/assets', StaticFiles(directory=str(_DIST / 'assets')), name='assets')
app.mount('/static', StaticFiles(directory='web'), name='static')
```

(The `if` runs at import; after `npm run build` the server must be restarted. Legacy UI remains reachable as fallback when dist is absent.)

- [ ] **Step 2: Full verification**

```
pytest tests/ -q              # all green
cd frontend && npm run build  # zero errors
```

Restart uvicorn. Acceptance checklist:
1. `/` renders the new light-theme workspace; all 14 nav items navigate without full reload.
2. Dashboard KPIs/curve show real numbers; 最近知识流 rows open drawers.
3. Knowledge: create note → appears; Index with LLM → run appears in Tasks & Logs with steps; doc drawer shows markdown + chunks.
4. QA: ask → grounded answer card with evidence; 最近问答 row opens run drawer.
5. Graph: zoom/pan/drag; node tap opens entity drawer; type colors match palette.
6. Events/Ideas/Questions/Evidence render real rows; idea/question status changes persist.
7. Prompts: edit + save + version history + restore + preview all work.
8. Settings save persists (`data/settings.json`); health card in sidebar reflects state.
9. Delete `web/dist`, restart server → legacy UI served (fallback intact). Rebuild dist afterwards.

# LLM-Wiki — local-first, LLM-native personal knowledge base

> 一个基于 SQLite 的本地优先、LLM 原生的个人知识库：从原始文档抽取结构化知识（实体 / 断言 / 关系 / 事件 / 想法 / 问题），以证据溯源驱动检索与问答，并用 **AgentScope 2.x** 多智能体 + **Context Runtime**（渐进式上下文加载）作为推理与执行层。
>
> 版本：`app/__init__.py` 中 `__version__ = "0.1.0"`。

---

## 目录

- [特性概览](#特性概览)
- [架构与数据模型](#架构与数据模型)
- [项目结构](#项目结构)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
  - [本地运行](#本地运行)
  - [Docker](#docker)
  - [Windows](#windows)
- [配置](#配置)
- [Web 界面](#web-界面)
- [AgentScope 多智能体](#agentscope-多智能体)
- [Context Runtime（渐进式上下文加载）](#context-runtime渐进式上下文加载)
- [API 速查](#api-速查)
- [数据库 Schema](#数据库-schema)
- [知识标准与抽取流程](#知识标准与抽取流程)
- [Agent 提示词与 Skills](#agent-提示词与-skills)
- [开发、测试与脚本](#开发测试与脚本)
- [备份与迁移](#备份与迁移)
- [文档索引](#文档索引)

---

## 特性概览

- **原始层（Raw）**：原样保留 Markdown / TXT / HTML 文档及其精确 chunk 偏移量，结构化输出永不替换原文。
- **知识层（Knowledge）**：从 chunk 批量抽取候选实体、断言（claim）、关系、事件、想法、问题，并携带来源溯源（source document / chunk / offset / quote）。
- **演化层（Evolution）**：新知识永不覆盖旧知识。新 Claim 与已有 Claim 的关系（`duplicate` / `coexists` / `supersedes` / `contradicts`）显式建模；"当前知识"由 Claim 状态与关系派生得出，历史与证据永久保留。判定确定性优先，LLM 仅在用户主动请求时给出建议。见 `docs/standards/14-CLAIM-EVOLUTION-STANDARD.md`。
- **检索层（Retrieval）**：SQLite **FTS5** 全文检索 + 可选 OpenAI embedding（NumPy 余弦相似度回退）。混合排序使用 **倒数排名融合（RRF）**。
- **推理层（Reasoning）**：证据溯源的问答，回答带文档 / chunk 引用与证据等级（Evidence Escalation L1–L5）。
- **图谱层（Graph）**：实体邻域（可设深度）与全局知识图谱 API。
- **多智能体层（AgentScope 2.x）**：单入口 `PersonalAgent` 拆分为 5 个角色（Personal / Knowledge / Research / Curator / Review），外加独立 `ExtractionAgent`。
- **Context Runtime**：token 计量、预算、规划、编译、Trace、Agent 间 Task Packet、Context Cache（版本失效）、多轮 `conversation_summaries` 持久化、Web 端 Context 工作台与 Context Inspector。
- **确定性优先**：ontology / predicate registry / normalization / offset 校验 / 关系方向 / 去重 等一律由程序完成，LLM 只做语义判断。

---

## 架构与数据模型

数据流（原文永不被结构化输出替换）：

```text
Document (source of truth)
    ↓ chunking (exact offsets)
Chunk (exact offsets)
    ├── FTS5 (lexical)
    └── embedding (optional, float32 blob)
    ↓ LLM batched extraction (structured JSON)
    → JSON Schema validation
    → predicate normalization
    → entity resolution (exact / alias / fuzzy)
    → source chunk validation
    → optional exact evidence quote localization
    → candidate persistence (status = candidate)
    ↓ 用户审核 / Agent 辅助
Entity / Claim / Relation / Idea / Question / Event
    ↓
Hybrid Retrieval / Graph / RAG / Multi-agent
```

**上下文分层（Context Runtime）**：

| 层 | 内容 | 例 |
|---|---|---|
| Static | Bootstrap / Identity / Core rules | Agent 身份、grounding、tool 原则、output contract |
| Dynamic | Task / Knowledge / Evidence / History | 当前问题、相关实体摘要、命中的证据、最近对话 |
| On-Demand | References / Registry subset / Examples | 按任务特征惰性加载的 `references/*.md`、2–5 个 predicate 候选 |

四种加载策略：`LOAD`（必用）/ `SUMMARIZE`（先压缩）/ `RETRIEVE_LATER`（只留 ID，后续工具取）/ `NEVER_LOAD`（程序处理或与任务无关，如 SQLite schema、UI 配置）。

---

## 项目结构

```text
.
├── app/                      # FastAPI 后端
│   ├── main.py               # 路由 / API 入口
│   ├── db.py                 # SQLite schema + 迁移 + 连接
│   ├── config.py             # 设置（环境变量 + data/settings.json）
│   ├── ontology.py           # 实体类型 / 断言 / 关系 registry + normalization + registry_version()
│   ├── extraction.py         # LLM 抽取 + 结构化输出校验
│   ├── chunking.py / importer.py / normalization.py / resolution.py
│   ├── retrieval.py          # FTS5 + embedding 混合检索、证据命中、升级
│   ├── embeddings.py         # OpenAI embedding + NumPy 余弦
│   ├── graph.py / knowledge.py / llm.py / service.py / runlog.py
│   ├── prompt_profiles.py    # Agent 提示词 Profile（含版本历史）
│   ├── skills.py             # Skill 加载
│   ├── agents/               # 6 个 Agent（base/personal/knowledge/research/curator/review/extraction）
│   ├── context/              # Context Runtime：budget/planner/compiler/manager/packet/cache/trace/history/providers
│   ├── tools/                # Agent 工具：knowledge_tools / skill_tools / compression（工具返回压缩）
│   ├── runtime/              # TaskContext / features_for
│   └── workflows/            # agent_workflow.run_agent / run_research_pipeline
├── frontend/                 # Vue 3 + TypeScript + Vite 单页应用
│   └── src/{views,components,stores,api,router}
├── schemas/                  # 抽取 JSON Schema + registry（claim-predicate / relation-predicate / entity-type / normalization）
├── skills/                   # Agent 技能（knowledge-curation / knowledge-extraction）+ references
├── docs/                     # 设计文档 / 知识标准 / superpowers 规格
├── scripts/                  # init_db / export_json / migrate_v01 / seed_demo / smoke_test / check_css
├── sql/                      # schema.v0.1.sql
├── tests/                    # pytest 套件（24 个文件）
├── web/                      # 后端直接托管的前端静态入口（index.html）
├── requirements.txt / Makefile / Dockerfile / run.sh / run.bat / pytest.ini
```

---

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python ≥ 3.11, FastAPI, Uvicorn, Pydantic v2 |
| 存储 | SQLite（WAL 模式，FTS5 虚拟表） |
| LLM | OpenAI-compatible 接口（AgentScope 2.x 执行） |
| 向量 | fastembed / numpy（余弦），可选 OpenAI embedding |
| 校验 | jsonschema |
| 前端 | Vue 3 `<script setup>`, TypeScript, Vite, Pinia, vue-router, cytoscape |
| 测试 | pytest + httpx |

依赖见 `requirements.txt`：

```text
fastapi>=0.115  uvicorn[standard]>=0.30  pydantic>=2.8  python-dotenv>=1.0
openai>=1.40  jsonschema>=4.23  numpy>=2.0  fastembed>=0.7  python-multipart>=0.0.9
pytest>=8.0  httpx>=0.27  agentscope>=2.0
```

---

## 快速开始

### 本地运行

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # 填写 OPENAI_API_KEY（可选）
python scripts/init_db.py
uvicorn app.main:app --reload
```

打开 `http://127.0.0.1:8000`。

> **无需 API Key 也能用**：笔记创建、文件导入、分块、FTS 检索、图谱 / 审核、Agent 提示词管理均可离线工作。只有 LLM 抽取、回答与 embedding 需要配置 OpenAI-compatible 端点。

### Docker

```bash
docker build -t llm-wiki .
docker run -p 8000:8000 -v "$(pwd)/data:/app/data" llm-wiki
```

`Dockerfile` 基于 `python:3.13-slim`，构建时执行 `scripts/init_db.py`，容器内数据库默认写入 `/app/data/wiki.db`（建议挂载卷持久化）。

### Windows

```bat
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python scripts\init_db.py
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

也可直接运行 `run.sh`（Linux/macOS）或 `run.bat`（Windows）一键初始化并启动。

---

## 配置

配置来源优先级：环境变量 → `data/settings.json`（运行时可经 `PUT /api/settings` 修改）→ `app/config.py` 默认值。

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATABASE_PATH` | `./data/wiki.db` | SQLite 文件路径 |
| `OPENAI_API_KEY` | 空 | OpenAI-compatible API Key（留空则 LLM 功能不可用） |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | 兼容端点（可用任意 OpenAI-like 服务） |
| `OPENAI_MODEL` | `gpt-4.1-mini` | 对话 / 抽取模型 |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | embedding 模型 |
| `EMBEDDING_DIMS` | `1536` | embedding 维度 |
| `LLM_BATCH_CHUNKS` | `8` | 单次抽取批量 chunk 数 |
| `MAX_SEARCH_RESULTS` | `50` | 检索返回上限 |
| `AUTO_EMBED` | `false` | 索引时是否自动 embedding |
| `AGENTSCOPE_ENABLED` | `true` | 是否启用 AgentScope 多智能体（`Settings → Enable AgentScope`） |
| `agent_context_budgets` | `{}` | 每 Agent token 软预算（如 `{"KnowledgeAgent": 2500}`），空则用 `AGENT_BUDGETS` |
| `tool_quote_chars` | `240` | 工具返回引用截断上限（P2b Tool Result Compression） |
| `tool_entity_claims` | `8` | 单实体卡 claim 数上限 |
| `tool_graph_nodes` | `25` | 图谱返回节点上限 |
| `tool_batch_size` | `8` | 批量工具一次取实体数上限 |

---

## Web 界面

Vue 3 单页应用（`frontend/`），构建产物由后端直接托管（`web/dist` 优先，`web/index.html` 兜底）。主要功能页：

- **知识库 / 文档**：导入、分块、LLM 索引、本地索引、embedding、chunks 浏览、导出。
- **实体 / 断言 / 关系 / 事件 / 想法 / 问题**：浏览、CRUD、状态流转（candidate → verified / rejected / archived）、冲突检测。
- **图谱**：cytoscape 可视化实体邻域与全局关系。
- **检索 / 问答**：证据溯源问答（`/api/ask`），显示引用、证据等级与上下文统计。
- **研究**：创建研究任务，触发研究流水线（`KnowledgeAgent` 采集 → `TaskPacket` → `ResearchAgent` 综合）。
- **Agent 工作台**：
  - **Context** 标签页：Efficiency / Cache-Hit KPI、Per-Agent 账本、Token-per-Run、Context Runs 列表（7/14/30 天窗口、可切全部 Agent）。
  - **Context Inspector** 抽屉：预算 / 实际 / Efficiency / Trimmed / 关联 Run，分区「名称·策略·为什么加载·成本·来源」+ 刻意未加载项 + 优化动作。
  - **Prompts** 页：custom prompt token 用量条 vs 建议上限、健康 / 偏长状态、迁入 Skill 建议、分区成本、刻意未加载项；版本历史回滚 / 重置。
- **设置**：模型、embedding、预算、AgentScope 开关、数据库路径切换（即时生效）。

前端开发：`cd frontend && npm install && npm run dev`（Vite 热更新）。构建：`npm run build`。

---

## AgentScope 多智能体

AgentScope 2.x 作为执行层。内置 Agent 不直连 SQLite，而是调用 LLM-Wiki 的搜索 / 实体 / 图谱工具。

| 角色 | 职责 |
|---|---|
| `PersonalAgent` | 通用入口与个人助手（默认 `role=auto` 路由） |
| `KnowledgeAgent` | 知识检索、实体、关系、证据 |
| `ResearchAgent` | 多步研究、比较与综合 |
| `CuratorAgent` | 去重、冲突与知识质量建议 |
| `ReviewAgent` | 候选知识人工审核辅助 |
| `ExtractionAgent` | 文档知识抽取（独立流程） |

- `POST /api/agent/ask`：`role=auto|personal|knowledge|research|curator|review`，可选 `conversation_id` 实现多轮（持久化于 `conversation_summaries`）。
- `GET /api/agent/roles`：列出可用角色。
- `POST /api/agent/ask` 返回 `framework: "AgentScope 2.x"`。

> AgentScope 2.x 需 Python ≥ 3.11；可在 **Settings → Enable AgentScope** 关闭。

---

## Context Runtime（渐进式上下文加载）

设计目标：把每次 LLM 调用从「Prompt + Everything」升级为 **Progressive Context Loading**——进入 Context 的每个 token 都应有明确的任务价值。完整规格见 `docs/superpowers/specs/2026-09-11-context-runtime-design.md`。

### 已交付分期

| 期 | 内容 |
|---|---|
| **P1** | token 计量基座；`ContextItem / Manager / Planner / Compiler / Provider / Budget / Trace / TaskPacket`；`context_runs` + `context_sections` + `task_packets` 落库；抽取 Agent 去重复注入；Prompt token 预算 API；真实 provider 用量采集 |
| **P2a** | Evidence Escalation L1–L5、Retrieval Dedup、Knowledge Summary；QA 链路切换，prompt 实测约 −80% |
| **P2b** | Tool Result Compression（`app/tools/compression.py`：工具返回一律「决策卡」——quote 卡 / 实体卡 / 去描述图，截断必带 `truncated` + `hint`，id 全保留可下钻；search −82% / get_entity −50% / graph −96%）；Batch Tools（`get_entities` 一次取多实体卡） |
| **P2c** | Skill / Reference cost metadata（`REFERENCE_LOAD_WHEN`，默认形状任务零加载）；Registry Subset（`ontology.match_claim_predicates` 确定性 matcher）；History Compression（`compress_history`：最近 4 条原文 + 旧消息确定性摘要，单轮任务零成本） |
| **P3** | `conversation_summaries` 持久化（滚动 summary + 近窗 ≤4 条原文，每轮一次读一次 upsert）；**Task Packet 调用链**（`run_research_pipeline`：KnowledgeAgent 采集 → `TaskPacket` 落库 → ResearchAgent 以 `TYPE_TASK` 必选项消费 `context_block()`，知识步骤失败自动降级 packet-less）；**Context Cache + 版本失效**（复合哈希键绑定 prompt / skill / reference / registry / schema / history 版本，任一变化即新键） |
| **P4** | Agent 工作台 Context 标签页、Context Inspector 抽屉、Prompts 页 Context Token 面板；Context Cache 端点失败自动降级 |

### Context Cache（版本失效）

- 缓存键 = 提示内容 + skill / reference 版本 + `ontology.registry_version()`（对 `schemas/*.json` 取 SHA1 指纹，前 12 位）+ 历史 / 包状态的复合哈希。
- **失效按构造成立**：任一 schema / registry / skill 版本变化 → 指纹变化 → 产生新键，旧条目自然失效，无需显式失效逻辑。
- `GET /api/context/cache` 观测命中率（`rows = misses`，`Σhits = 复用`）；命中重放 miss 时账本（标记 `cached:true`），不产生重复 trace 行。

### Task Packet（Agent 间传状态不传上下文）

```json
{ "task_id": "", "goal": "", "context_summary": "", "known_facts": [],
  "open_questions": [], "constraints": [], "required_actions": [],
  "entity_ids": [], "claim_ids": [], "evidence_ids": [], "document_ids": [],
  "required_output": {} }
```

研究流水线中，KnowledgeAgent 的采集结果封装为 `TaskPacket` 落库，ResearchAgent 仅消费其 `context_block()`（id 计数不带载荷），实现 Agent 间「传状态不传完整历史 / 上下文」。

---

## API 速查

所有路由前缀 `/api`。以下为常用端点（完整清单见 `app/main.py`）：

**系统 / 配置**

- `GET /api/health` — 健康与模型配置
- `GET|PUT /api/settings` — 读取 / 修改设置（仅允许字段）
- `GET /api/stats` — 各表计数
- `GET /api/stats/timeseries?days=` — 文档 / 实体 / 断言时间序列
- `GET /api/database/integrity` — PRAGMA integrity_check + 大小

**文档 / 分块 / 索引**

- `GET|POST /api/documents` — 列表 / 创建
- `GET|PUT|DELETE /api/documents/{id}`
- `POST /api/documents/import` — 上传文件导入（multipart）
- `POST /api/documents/{id}/index` — LLM 索引；`/index/local` 仅本地（无 LLM）
- `POST /api/documents/{id}/embed` — 生成 embedding；`POST /api/embeddings/backfill` 全量回填
- `GET /api/documents/{id}/chunks` — 精确偏移 chunks

**知识对象**

- `GET /api/entities`（`?status&type&q`）、`GET /api/entities/{id}`、`GET /api/entities/{id}/object`、`GET /api/entities/{id}/graph?depth=`
- `POST /api/entities`、`PATCH /api/entities/{id}`
- `GET /api/claims`、`GET /api/claims/{id}`
- `GET /api/claims/{id}/relations` — 该 Claim 的演化关系（双向）
- `GET /api/claim-relations`（`?status=candidate`）— 待确认的知识变化
- `PATCH /api/claim-relations/{id}` — 确认 / 忽略 / 撤销；`relationship=supersedes` 是唯一让旧 Claim 停止当前状态的路径
- `POST /api/claim-relations/{id}/analyze` — 按需让 LLM 给出语义判断建议（不自动调用）
- `GET /api/relations`、`GET /api/ideas`、`POST /api/ideas`、`GET /api/questions`、`POST /api/questions`、`GET /api/events`、`POST /api/events`
- `GET /api/graph`、`GET /api/conflicts`、`GET /api/knowledge/health`、`GET /api/review`
- `PATCH /api/knowledge/{kind}/{id}/status` — `kind ∈ entity|claim|relation|idea|question`

**检索 / 问答 / 研究**

- `GET|POST /api/search`（`?q&limit&semantic`）
- `POST /api/ask` — 证据溯源问答（经 Context Runtime：dedup → escalation → plan → compile → answer），返回 `answer / citations / evidence / context`
- `GET|POST /api/research`、`POST /api/research/{task_id}/run`（研究流水线，返回 `findings / agent / packet_id`）

**运行记录 / 取消**

- `GET /api/runs`（`?task_type&status`）、`GET /api/runs/{run_id}`、`POST /api/runs/{run_id}/cancel`

**Agent / 提示词 / Skills**

- `GET /api/agent/roles`、`POST /api/agent/ask`（`message / role / conversation_id`）
- `GET /api/agent/prompts`、`GET|PUT /api/agent/prompts/{role}`、`POST /api/agent/prompts/{role}/restore`、`POST /api/agent/prompts/{role}/reset`
- `GET /api/skills`、`GET /api/skills/{name}`

**Context Runtime 可观测性**

- `GET /api/context/cache` — Cache 命中率
- `GET /api/context/runs`（`?agent`）、`GET /api/context/runs/{id}` — Context Trace
- `GET /api/context/budgets` — 各 Agent 预算
- `GET /api/context/metrics?days=` — token 计量（计划 / 实际 / trimmed / over-budget / provider 实测）

**导出**

- `GET /api/export` — 全部知识表 JSON 快照

---

## 数据库 Schema

存储于单一 SQLite 文件（默认 `data/wiki.db`，WAL 模式）。核心表（DDL 见 `app/db.py`）：

- `documents` — 原文（content_hash 唯一，防重复）
- `chunks` — 精确偏移分块；`documents_fts` FTS5 虚拟表（unicode61）+ 触发器同步
- `entities` / `entity_aliases` — 实体与别名（归一化索引）
- `claims` — 断言（subject / predicate / object，含 polarity / modality / claim_type / 溯源偏移 / 引用）
- `relations` — 知识图谱关系（entity → entity，source / predicate / target，含溯源）
- `claim_relations` — Claim → Claim 的演化关系（`duplicate` / `coexists` / `supersedes` / `contradicts`），两端 `ON DELETE CASCADE`；`target_previous_status` 支持精确撤销
- `ideas` / `questions` / `events` — 想法 / 问题 / 事件
- `research_tasks` — 研究任务
- `agent_prompt_profiles` / `agent_prompt_versions` — Agent 提示词 Profile 与版本历史
- `llm_runs` / `llm_run_steps` — 运行记录（含 prompt / completion token 与 usage_source）
- `chunk_embeddings` — float32 embedding blob
- **Context Runtime 表**：`context_runs` / `context_sections` / `task_packets` / `conversation_summaries` / `context_cache`

`additive migration helper`（`db.py::_ensure_token_columns` + 实体 / 声明 / 来源列迁移）保证旧库原地升级，新表 `IF NOT EXISTS` 幂等。

---

## 知识标准与抽取流程

LLM 输出不盲目信任，抽取流水线：

```text
chunk batch
  → structured JSON candidate
  → JSON Schema validation
  → predicate normalization
  → exact / alias / fuzzy entity resolution
  → source chunk validation
  → optional exact evidence quote localization
  → candidate persistence (status = candidate)
```

知识对象初始为 `candidate`，可在 UI 提升为 `verified` 或拒绝。规范性标准见 `docs/standards/`：

- `00-ONTOLOGY-OVERVIEW` — 整体知识模型与边界
- `01-ENTITY-TYPE-STANDARD` — 14 类实体与多类型规则
- `02-CLAIM-STANDARD` / `03-CLAIM-PREDICATE-REGISTRY` — 断言语义与受控谓词
- `04-RELATION-PREDICATE-REGISTRY` — 知识图谱受控谓词
- `05-CLAIM-RELATION-NORMALIZATION` — 确定性 Claim → Relation 转换
- `06-EVENT` / `07-IDEA` / `08-QUESTION` / `09-EVIDENCE-PROVENANCE` — 各对象标准
- `10-EXTRACTION-V2-STANDARD` — LLM 抽取契约与职责分离
- `11-NORMALIZATION-ENGINE-STANDARD` — 确定性归一化 / 实体解析 / Claim→Relation
- `12-IMPLEMENTATION-MAP` — 标准 → 运行时模块映射
- `13-AGENT-PROMPT-SKILL-STANDARD` — Agent 提示词与 Skill 标准
- `14-CLAIM-EVOLUTION-STANDARD` — Claim 演化与冲突处理：永不覆盖、关系建模（duplicate / coexists / supersedes / contradicts）、Current Knowledge 派生、与现有架构的对照

registry 与 normalization 规则 **不进 prompt**，仅做程序校验（Deterministic logic first, LLM reasoning second）。

---

## Agent 提示词与 Skills

- 每个角色有 **不可变 Core Contract** + 可选本地 **Skill**（含 references）+ 可编辑 **custom prompt**，存储于 `agent_prompt_profiles` / `agent_prompt_versions`（保留最近版本用于回滚 / 重置；首次初始化时一次性迁移旧 JSON Profile）。
- 行为可在 Web UI **Prompts** 页配置；custom prompt token 超阈值时提示「建议把 N tokens 移入 Skill」。
- Skill 结构（`skills/<name>/`）：
  - `SKILL.md` — Purpose / When to use / Core workflow / Required references / Prohibitions
  - `references/*.md` — 详细规范，按任务特征（features）惰性加载，带 cost metadata（`lazy` / `estimated_tokens` / `load_when`）
- 架构详见 `docs/standards/13-AGENT-PROMPT-SKILL-STANDARD.md`。

---

## 开发、测试与脚本

```bash
# 安装
pip install -r requirements.txt

# 初始化数据库
python scripts/init_db.py

# 运行测试（全量 150+ 通过，24 个测试文件）
pytest -q

# Makefile 快捷方式
make install   # pip install -r requirements.txt
make db        # python scripts/init_db.py
make run       # uvicorn app.main:app --host 127.0.0.1 --port 8000
make test      # pytest -q
make export    # python scripts/export_json.py
```

辅助脚本（`scripts/`）：

- `init_db.py` — 建库 / 迁移
- `export_json.py` — 导出可移植 JSON 快照
- `migrate_v01.py` — v0.1 数据迁移
- `seed_demo.py` — 灌入演示数据
- `smoke_test.py` — 冒烟测试（无 LLM 也能跑核心路径）
- `check_css.py` — 前端 CSS 一致性检查

测试覆盖：chunk 偏移、FTS、实体解析、溯源、检索回退、JSON Schema、Context Budget / Trace、Skill 惰性加载、Registry Subset、History Compression、Tool Compression、Evidence Escalation、conversation_summaries、context_cache、task_packet_chain 等。

---

## 备份与迁移

- 规范存储为 `data/wiki.db`（SQLite）。停止应用后复制该文件，或使用 SQLite backup API 即可完整备份。
- 可移植 JSON 快照：`python scripts/export_json.py`（导出 `documents / chunks / entities / claims / relations / ideas / questions / events`）。
- 旧库通过 `db.py` 内建的 additive migration 自动原地升级，无需手动迁移步骤。

---

## 文档索引

- `docs/TECHNICAL-DESIGN.md` / `TECHNICAL-DESIGN.docx` — 技术设计
- `docs/PROCESSING-PIPELINE.md` / `ORIGINAL-PROCESSING-FLOW.md` — 处理流水线
- `docs/IMPLEMENTATION-PLAN.md` / `ORIGINAL-IMPLEMENTATION-PLAN.md` — 实现计划
- `docs/API-EXAMPLES.md` — API 示例
- `docs/README-IMPLEMENTATION.md` — 实现说明
- `docs/standards/` — 知识标准（见上）
- `docs/superpowers/specs/2026-09-11-context-runtime-design.md` — Context Runtime 设计规格（P1–P4 验收）
- `docs/superpowers/specs/` — 其他设计规格（frontend-redesign / llm-run-records / pkos-redesign）
- `frontend/README.md` — 前端脚手架说明

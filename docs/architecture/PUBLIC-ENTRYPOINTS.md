# PUBLIC-ENTRYPOINTS — 公共入口（Facade）

> 状态：**TARGET**。当前 v0.1 **尚无 Facade**，能力分散在 `app/main.py` 路由、`app/service.py`、`app/knowledge.py`、`app/retrieval.py`。本文定义目标入口，作为重构与审查基准。

## 1. 五个公共入口

系统对外提供且**仅提供**以下五个 Facade：

```text
KnowledgeFacade
SearchFacade
OperationFacade
OntologyFacade
WorkflowFacade
```

```text
API / Agent / CLI / MCP / Scheduler
        ↓（只能经 Facade）
   五个 Facade
        ↓
     Domain / Repository
```

---

## 2. KnowledgeFacade

负责**读取/表达知识状态**，不做修改。

```text
Entity
Claim
Relation
Evidence
History（Claim Evolution 历史）
Current Knowledge（经 ClaimStateResolver 的当前视图）
```

典型能力：`get_entity(id)`、`claims_for(subject)`、`current_claims(subject, predicate)`、`history(claim_id)`、`evidence_for(claim_id)`。

**CURRENT 对应**：`app/main.py` 的 `/api/entities*`、`/api/claims*`、`/api/relations*`；`app/retrieval.py::evidence_hits` / `entity_summaries`。

---

## 3. SearchFacade

负责**所有检索**，是 QA / Research / Agent 检索的唯一来源。

```text
Lexical Search
Semantic Search
Hybrid Search（RRF）
Entity Search
Claim Search
Evidence Retrieval
```

**CURRENT 对应**：`app/retrieval.py`（`lexical_search` / `search` / `evidence_hits`）、`app/embeddings.py::semantic_search`。

**红线**：Retrieval 层不得自行推导 Current Knowledge，必须调用 `ClaimStateResolver`（见 [ADR-005](../adr/ADR-005-CLAIM-EVOLUTION.md)）。

---

## 4. OperationFacade

负责**所有知识变更**，是唯一允许修改知识状态的入口。

```text
CREATE
CORRECT
MERGE
SPLIT
SUPERSEDE
CONTRADICT
ARCHIVE
RESTORE
```

每个操作必须：有输入契约、有 domain validation、有执行结果、有 audit record、可追踪、必要时可回滚。

详见 [KNOWLEDGE-OPERATIONS.md](./KNOWLEDGE-OPERATIONS.md)。

---

## 5. OntologyFacade

负责**本体与注册表**的权威查询与校验。

```text
Entity Type
Predicate（Claim / Relation）
Relation Type
Normalization
Registry
Schema Version（registry_version）
```

**CURRENT 对应**：`app/ontology.py`（`canonical_entity_type` / `match_claim_predicates` / `relation_spec` / `registry_version`）。

---

## 6. WorkflowFacade

负责**用例编排**（Application 层）。

```text
Ingest
Extract
Ask
Research
Review
Correct
```

**CURRENT 对应**：`app/service.py::create_document` / `index_document`、`app/workflows/agent_workflow.py::run_agent` / `run_research_pipeline`。

---

## 7. 访问规则（红线）

五个 Facade 是**唯一公共入口**。以下均被禁止：

```text
API      → ClaimRepository        ❌ 绕过 Facade
API      → SQLite                 ❌ 跨层
Agent    → SQLite                 ❌ 跨层
Agent    → Repository             ❌ 绕过 Facade
Frontend → 内部模块               ❌ 只能打 API
Scheduler→ Repository             ❌ 必须经 WorkflowFacade
```

正确的两条路径：

```text
API    → KnowledgeFacade → Repository   ✅
Agent  → KnowledgeFacade → Repository   ✅
```

## 8. 为什么不直接开更多 endpoint

每新增一个「特殊 endpoint」，就等于在 Presentation 层新增一份未被 Facade 约束的业务逻辑，最终 `main.py` 会重新变成事实上的业务层（这正是 CURRENT 的问题）。因此：

> **新能力优先扩展 Facade，而不是新增绕过 Facade 的 endpoint。**

## 9. 迁移提示

Facade 的建立采用「Stabilize → Abstract → Migrate → Delete」策略：先让 Facade 内部直接复用现有实现，再逐一把调用方从旧路径切到 Facade，最后删除旧路径。见 [MIGRATION-PLAN.md](./MIGRATION-PLAN.md)。

# Architecture Gap (Phase 0 · CURRENT vs TARGET)

> 本文档基于 `docs/architecture/PUBLIC-ENTRYPOINTS.md`（状态 TARGET，定义 5 个 Facade）与 `docs/architecture/MIGRATION-PLAN.md`（迁移纪律）的**目标架构**，对照 `CURRENT-ARCHITECTURE.md` 记录的真实代码，建立可量化差异矩阵。
>
> TARGET 原文均来自上述两文件（标注 file:line）。CURRENT 现状均来自真实代码（标注 file:line）。
>
> 生成日期：2026-09-28。仅盘点，未重构。

---

## 0. TARGET 架构（来自 PUBLIC-ENTRYPOINTS.md / MIGRATION-PLAN.md）

- `PUBLIC-ENTRYPOINTS.md:3`：当前 v0.1 **尚无 Facade**，能力分散于 `main.py` 路由、`service.py`、`knowledge.py`、`retrieval.py`；本文定义目标入口作为重构/审查基准。
- `PUBLIC-ENTRYPOINTS.md:7-15`：系统对外**仅提供 5 个 Facade** —— `KnowledgeFacade` / `SearchFacade` / `OperationFacade` / `OntologyFacade` / `WorkflowFacade`；调用链 `API/Agent/CLI/MCP/Scheduler → 5 Facade → Domain/Repository`。
- `PUBLIC-ENTRYPOINTS.md:57`：路由组织 TARGET 为「Router 拆分（resource 维度）」，main.py 不应再是业务层。
- `MIGRATION-PLAN.md:121`：双轨期纪律——新旧并存时新代码必须走 Facade；旧路径标记 `# legacy`。
- 迁移总目标（`MIGRATION-PLAN.md:20`）：架构基本冻结（Phase 1–4 落地），质量工程主线完成。

### 5 Facade 目标职责（PUBLIC-ENTRYPOINTS.md 原文摘录）

| Facade | 职责边界 | TARGET file:line |
|---|---|---|
| KnowledgeFacade | 负责**读取/表达知识状态**，不做修改。含 Entity/Claim/Relation/Evidence/History/Current Knowledge。`current_claims(subject, predicate)` | :27-42 |
| SearchFacade | 负责**所有检索**，是 QA/Research/Agent 检索唯一来源。含 Lexical/Semantic/Hybrid/Entity/Claim/Evidence Retrieval。**红线**：Retrieval 层不得自行推导 Current Knowledge，必须调用 `ClaimStateResolver` | :46-61 |
| OperationFacade | 负责**所有知识变更**，是唯一允许修改知识状态的入口。8 类操作 CREATE/CORRECT/MERGE/SPLIT/SUPERSEDE/CONTRADICT/ARCHIVE/RESTORE | :65-82 |
| OntologyFacade | 负责**本体与注册表**权威查询与校验。含 Entity Type/Predicate/Relation Type/Normalization/Registry/Schema Version | :86-99 |
| WorkflowFacade | 负责**用例编排**（Application 层）。含 Ingest/Extract/Ask/Research/Review/Correct | :103-116 |

---

## 1. CURRENT / TARGET 差异矩阵（Task 0.3）

| 能力 | CURRENT（真实代码位置） | TARGET | 差异 | 优先级 |
|---|---|---|---|---|
| **Knowledge（读）** | `main.py` 路由直读各 repo（get_claim:197、entity_object:415、document_knowledge:781、claims:975、relations:979、entities:820），无统一读 Facade | KnowledgeFacade | **存在**：无 Facade，路由直读 | P0 |
| **Search** | `retrieval.py`（非 service.py）被 handler 直调（api_search:575、api_search_knowledge:584） | SearchFacade | **存在**：检索逻辑在 retrieval.py，handler 直调，无 Facade 收口 | P0 |
| **Operation（写）** | 仅 `POST /api/knowledge/operations`(main.py:398) 与 `corrections/apply`(correction_workflow.py:527) 进 Operation；3 处知识变更绕过（见 §3） | OperationFacade | **存在**：写入口分散，未统一；operation 既无 Application 也无 Workflow 层 | P0 |
| **Ontology** | `ontology.py` 被各 handler 直接 `canonical_entity_type / normalize_name / claim_predicate_spec`（update_entity:834、create_entity:861、ontology_predicates:762） | OntologyFacade | **存在**：本体访问分散于 handler | P1 |
| **Workflow** | `service.py`（Ingest/Extract/候选）+ `workflows/*`（仅 ask/correction/agent/citation_validation）；Review/Operation/Research 编排在 handler 或 service 内联 | WorkflowFacade | **存在**：编排无统一 Application owner | P1 |
| **Application Layer** | 无 `app/application/`；`service.py` 与 `retrieval.py` 是事实上的"半 Application"但不在目标命名下、覆盖范围不全 | `app/application/*`（Knowledge/Search/Operation/Workflow/Ontology） | **存在**：层缺失 | P0（Phase 1 新建） |
| **Read Model** | `readmodels/knowledge_view.py`（被 QA/Search/Research 共用）+ `readmodels/integrity_issue.py`；无 review/search/research/history | 统一 Read Model（KnowledgeView/ReviewInboxView/SearchResultView/ResearchCandidateView/ClaimHistoryView） | **部分存在**：Knowledge 已收敛，Review/Search/Research/History 无统一 owner | P0/P1（Phase 2） |
| **current knowledge 语义** | `domain/claim_state.py` 是唯一真源；`retrieval._current_claims` 是转调它的 shim（retrieval.py:229-237） | 全部经由 `ClaimStateResolver` | **已收敛**：单源已建立，shim 待删除 | P1（Phase 4） |
| **main.py 角色** | 业务编排中心（104 路由，QA/Search/Review/Operation 直编或直调 domain/repo） | 仅 resource 维度 Router（Presentation） | **存在**：越层 | P0（Phase 1） |

---

## 2. 各差异项代码位置（验收要求：每个差异项有代码位置）

| 差异项 | CURRENT 代码位置 | TARGET 落点 |
|---|---|---|
| KnowledgeFacade 缺失 | main.py:197/415/781/975/979/820 直读 repo | app/application/knowledge.py |
| SearchFacade 缺失 | main.py:575/580/584 → retrieval.py:131/159 | app/application/search.py |
| OperationFacade 已收口 | main.py:386-412（仅此入口）+ correction_workflow.py:527；原旁路 main.py:285-314 / 649 / 1013-1029 已收敛至 application/operation.py（kind-aware ARCHIVE/RESTORE/ACCEPT、RELATION_RESOLVE、MERGE_ENTITY） | app/application/operation.py |
| OntologyFacade 缺失 | main.py:834/861/762 → ontology.py | app/application/ontology.py |
| WorkflowFacade 缺失 | 编排散落：main.py:1102(ask)、:1000(review)、service.py:71(index) | app/application/workflow.py |
| main.py 越层 | 同 §1 八链"handler 直接多步编排"列 | 全部改为 `request → validate → application → response` |
| Read Model 不全 | 缺 readmodels/review.py、search.py、research.py、history.py；main.py:1000 独立统计 | 新建并收口 |

---

## 3. 知识变更绕过 Operation 的入口（Phase 3 必改清单）

| API | 动作 | 是否改变知识 | CURRENT 机制 | TARGET 机制 | 代码位置 |
|---|---|---|---|---|---|
| `POST /api/knowledge/corrections/apply` | CORRECT | 是 | Operation（✅ 正确） | Operation | correction_workflow.py:527 |
| `POST /api/knowledge/operations` | CREATE/CORRECT/MERGE/... | 是 | Operation（✅ 正确） | Operation | main.py:398 |
| `PATCH /api/knowledge/{kind}/{id}/status` | status（archive/restore/accept） | 是 | ~~Repository 直写~~ → **Operation（ARCHIVE/RESTORE/ACCEPT，kind-aware 覆盖 entity/relation/idea/question）** | Operation | application/operation.py → domain.operations |
| `PATCH /api/claim-relations/{id}` | relation 状态/类型 | 是 | ~~Repository 直写~~ → **Operation（RELATION_RESOLVE）** | Relation Operation | application/operation.py → domain.operations |
| `POST /api/integrity/merge` | MERGE | 是 | ~~`integrity.merge_entities` 直写~~ → **Operation（MERGE_ENTITY，在调用方事务内执行 integrity 核心）** | Operation（MERGE） | application/operation.py → domain.operations |
| `POST /api/documents/{id}/index` | 抽取落库 | 是 | `persist_extraction` + 各 repo.insert；仅 LINK_OBJECT 记审计 | 抽取经 Operation 或明确登记为 EXTRACTION 权威入口 | service.py:134；integrity.py:262-270 |
| 直接实体/想法/问题/事件创建 | CREATE | 是 | `EntityRepository.insert` 等 | 经 Operation（CREATE）或登记为 SYSTEM MIGRATION | main.py:861/876/881/886 |

> 原则（来自总计划 Phase 3）：任何改变"当前知识状态"的 API 必须进 Operation；特殊情况（READ ONLY / SYSTEM MIGRATION / INTERNAL MAINTENANCE）必须显式登记，不得模糊放行。

---

## 4. Legacy / Shim / Compat 分布（Phase 4 输入，Task 4.1 前置）

> 全仓 `app/`（排除 `__pycache__`）真实命中。注意：`deprecated/TODO/FIXME/XXX/HACK` 均为 0 处。技术债以 `legacy` / `shim` / `compat` 命名与数据迁移形式存在。

### 4.1 明确的 shim（待删除）

| 代码 | 原因 | 唯一真源 | 删除条件 | 文件:行 |
|---|---|---|---|---|
| `retrieval._current_claims` | 转调 Domain 的薄封装，"只为早于 Domain 模块的调用方存在" | `domain.claim_state.resolve` | 所有调用方改用 `domain.claim_state.resolve` 后删除 | retrieval.py:229-237 |

### 4.2 Legacy 数据/兼容层（多为一次性迁移，启动期执行）

| 代码 | 含义 | 文件:行 |
|---|---|---|
| `db._migrate_legacy_entities` / `_migrate_legacy_claims` | 旧表结构在线迁移（启动期调用，db.py:691-692） | db.py:465-484 / 486-497 / 691-692 |
| `db.legacy_alter_table` | 重建表保留外键；修复 questions_old 残留外键 | db.py:556-584 |
| `ontology.LEGACY_ENTITY_TYPES` | 旧实体类型字符串 → 规范类型映射 | ontology.py:19/98-99 |
| `knowledge.py` "Legacy explicit relations are ignored" | 旧显式 relations 已忽略 | knowledge.py:273 |
| `predicate_migration.py` | 旧谓词 → 本体谓词归一，残留进 needs_review | domain/predicate_migration.py:1/23/41/78-94 |
| `entity_eligibility.py` legacy rows | 旧行（无 eligibility 字段）按 REVIEW | domain/entity_eligibility.py:18/59；entity_eligibility.py（顶层）:112/116 |
| `resolution.py` legacy rows | 旧行不进冲突检测器 | resolution.py:186 |
| `extraction._legacy_to_v2` | v1→v2 抽取 schema 兼容适配器（无损） | extraction.py:78/106/257；extraction_items.py:31/185 |
| `prompt_profiles._legacy_path / _load_legacy_store` | 旧 JSON 提示词 → DB 迁移，迁移后改名 `.migrated` | prompt_profiles.py:47/51/64/71-102 |
| `relation_repo / claim_repo` legacy rows | 旧 claim/relation 谓词归一/不参与自动解析 | claim_repo.py:616；relation_repo 相关；entity_repo.py:111 |

### 4.3 Compatibility 层（非临时债，属设计性兼容）

| 代码 | 含义 | 文件:行 |
|---|---|---|
| `config.py` Backward-compatible 常量 | `DATABASE_PATH`/`OPENAI_*` 等仍导出待迁移后删 | config.py:111 |
| `context/items.py` Section 别名 | 旧名 `Section` → `ContextItem` 兼容别名 | context/items.py:123-124 |
| "OpenAI-compatible" | 普通 API 描述，非债 | embeddings.py:42/93；llm.py:100 等 |

> 说明：`operations.py` 的 `old_id` 是领域操作中"新 claim 指向旧 claim"的**正式语义字段**（duplicate/supersede/contradicts），不是技术债 `old_` 命名，不计入 legacy。

---

## 5. 本阶段（Phase 0）量化勾选

- [x] API 全量清单：104 路由，逐条标注写? / 走 Operation?
- [x] 8 条核心调用链：逐条画至 SQLite，标 MISSING
- [x] CURRENT/TARGET 差异矩阵：5 Facade + Application + Read Model + main.py 角色，共 9 项，含优先级
- [x] 每个差异项有代码位置（file:line）
- [x] Legacy/Shim/Compat 清单（Phase 4 输入）：1 个明确 shim、10+ 处 legacy 迁移、3 处 compat

### 下一步
- Phase 1：新建 `app/application/`（Knowledge/Search/Operation 优先），main.py 降级为 Router。
- Phase 2：补全 Read Model（ReviewInboxView 为首要，消除 main.py:1000 第二统计源）。
- Phase 3：§3 清单的写入口统一进 Operation。
- Phase 4：§4 的 shim 与 legacy 清理，建立 LEGACY-REGISTRY.md。
- Phase 5：Correction Golden Path E2E + 前端核心链路回归。

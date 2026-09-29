# CURRENT Architecture (Phase 0 事实地图)

> 本文档是**事实记录**，不是设计文档。所有结论来自对真实代码的逐文件核对（`app/main.py` 全文 1–1307 行，`app/service.py`、`app/workflows/*`、`app/domain/*`、`app/repositories/*`、`app/retrieval.py`、`app/integrity.py`、`app/review.py`、`app/claim_relations.py`）。
>
> 原则：不重构、不拆文件、不新建 Facade。仅记录"现在到底是什么样"。
>
> 验证基线（本阶段未运行，留待 Phase 5）：`pytest -q`、`npm --prefix frontend run build`。
>
> 生成日期：2026-09-28。

---

## 0. 分层现状（CURRENT 实际存在什么层）

| 层 | 现状 |
|---|---|
| Presentation / HTTP | `app/main.py`（`app=FastAPI(...)`，main.py:36），**全仓唯一路由注册处**。无 `app/api/` 目录，无 `APIRouter`，无 `include_router`。共 **104 个路由**（含 1 个 `GET /` 静态首页）。 |
| Application | **没有统一 Application 层**。`app/service.py` 只覆盖文档创建/索引/删除/embed + 研究候选（7 个函数）。其余业务能力无 Application owner。 |
| Workflow | `app/workflows/` 下只有 4 个：`ask_workflow.py`、`correction_workflow.py`、`agent_workflow.py`、`citation_validation_workflow.py`。**无 operation_workflow / extraction_workflow / review_workflow / research_workflow 的"应用编排"层**。 |
| Domain | `app/domain/` 存在且较完整：`operations.py`（Operation 执行框架）、`claim_state.py`（current knowledge 唯一真源）、`compiler.py`、`query_router.py`、`knowledge_quality.py`、`predicate_resolver.py`、`refusal.py`、`ontology_policy.py` 等。 |
| Read Model | `app/readmodels/` 已存在且**部分落地**：`knowledge_view.py`（`best_per_statement` 等）、`integrity_issue.py`。无 `review.py` / `search.py` / `research.py` / `history.py`。 |
| Repository | `app/repositories/` 存在且完整：claim / entity / relation / operation / document / evidence / catalog / curation / research / question / idea / event / run / suppression repo。 |
| SQLite | 通过 `app/db.py`（`db.transaction` / `db.conn`）统一访问。 |

---

## 1. API 全量清单（Task 0.1）

> "写?" 列：写 = CREATE/UPDATE/DELETE/状态变更；读 = 纯 SELECT。"走 Operation?" 列：是否经由 `domain/operations.run` / `OperationRequest` 执行框架。

| Method | Path | Handler | 行号 | 实际调用（Repo / Service / Workflow） | 写? | 走 Operation? | 备注 |
|---|---|---|---|---|---|---|---|
| GET | /api/agent/prompts | agent_prompts | 42 | Service(prompt_profiles) | 读 | 否 | |
| PUT | /api/agent/prompts/{role} | update_agent_prompt | 60 | Service(prompt_profiles) | 写 | 否 | |
| POST | /api/agent/prompts/{role}/restore | restore_agent_prompt | 72 | Service(prompt_profiles) | 写 | 否 | |
| POST | /api/agent/prompts/{role}/reset | reset_agent_prompt | 80 | Service(prompt_profiles) | 写 | 否 | |
| GET | /api/agent/roles | agent_roles | 88 | 静态返回 | 读 | 否 | |
| GET | /api/health | health | 102 | config | 读 | 否 | |
| GET | /api/settings | get_settings_api | 106 | config | 读 | 否 | |
| PUT | /api/settings | update_settings_api | 114 | config.save_settings; db.init_db | 写 | 否 | 改设置并重建库 |
| POST | /api/agent/ask | agent_ask | 137 | Workflow(agent_workflow.run_agent) | 委派 | 委派 | 写/operation 取决于所选 agent |
| POST | /api/documents | create_doc | 158 | Service.create_document | 写 | 否 | |
| POST | /api/documents/import | import_file | 164 | importer; create_doc() | 写 | 否 | 未用 `importer.read_file`（importer.py:6 未接入 HTTP 链路） |
| GET | /api/documents | list_docs | 171 | DocumentRepository().list | 读 | 否 | |
| GET | /api/events | events_list | 177 | EventRepository().list | 读 | 否 | |
| GET | /api/ideas | ideas_list | 181 | IdeaRepository().list | 读 | 否 | |
| GET | /api/questions | questions_list | 185 | QuestionRepository().list | 读 | 否 | |
| GET | /api/stats/timeseries | stats_timeseries | 189 | CatalogRepository().timeseries | 读 | 否 | |
| GET | /api/database/integrity | database_integrity | 193 | CatalogRepository().integrity | 读 | 否 | |
| GET | /api/claims/{claim_id} | get_claim | 197 | ClaimRepository().get | 读 | 否 | |
| GET | /api/claims/{claim_id}/relations | claim_relations_for | 203 | ClaimRepository().relations_for_claim | 读 | 否 | |
| GET | /api/claims/{claim_id}/history | claim_history | 213 | ClaimRepository + domain.claim_history + ontology | 读 | 否 | |
| GET | /api/claim-relations | claim_relations_queue | 260 | ClaimRepository().relation_queue | 读 | 否 | |
| POST | /api/claim-relations/{relation_id}/analyze | analyze_claim_relation | 265 | Service(claim_relations.analyze_relation) | 读 | 否 | 标注 writes nothing |
| PATCH | /api/claim-relations/{relation_id} | update_claim_relation | 285 | ClaimRepository.update_relation/set_status | 写 | **否** | ⚠ 直接仓储改状态，绕过 operation |
| POST | /api/knowledge/corrections | correction_plan | 317 | Workflow(correction_workflow.parse_intent/build_plan/verify_new_claim) | 读 | 否 | 标注 writes nothing |
| POST | /api/knowledge/corrections/apply | correction_apply | 336 | Workflow(apply_correction→run(OperationRequest kind=CORRECT)) | 写 | **是** | correction_workflow.py:527 |
| GET | /api/knowledge/claims/{claim_id}/quality | claim_quality | 359 | domain.knowledge_quality | 读 | 否 | |
| GET | /api/knowledge/quality/review | quality_review | 369 | domain.knowledge_quality.review_queue | 读 | 否 | |
| GET | /api/knowledge/operations | knowledge_operations | 376 | OperationRepository().list | 读 | 否 | 仅审计读取 |
| GET | /api/knowledge/operations/kinds | knowledge_operation_kinds | 381 | domain.operations.registered_operations | 读 | 否 | |
| POST | /api/knowledge/operations | knowledge_operation | 386 | domain.operations.run(OperationRequest) | 写 | **是** | 主 operation 入口（main.py:398） |
| GET | /api/entities/{entity_id}/object | entity_object | 415 | Entity/Claim/Relation/Event/Idea/Question repos | 读 | 否 | |
| GET | /api/conflicts | conflicts_list | 436 | ClaimRepository().conflicts_rows | 读 | 否 | |
| GET | /api/knowledge/health | knowledge_health | 449 | Entity/Claim/Relation/Question repos | 读 | 否 | |
| GET | /api/skills | skills_list | 462 | pathlib 读文件 | 读 | 否 | |
| GET | /api/skills/{name} | skill_detail | 474 | pathlib 读文件 | 读 | 否 | |
| GET | /api/documents/{doc_id} | get_doc | 481 | DocumentRepository().get | 读 | 否 | |
| POST | /api/documents/{doc_id}/index | index_doc | 487 | Service.index_document(use_llm=True) | 写 | 否 | persist_extraction + 仓储；仅 LINK_OBJECT 记审计 |
| GET | /api/experiments/corpus | experiment_corpus | 498 | experiments.load_corpus | 读 | 否 | |
| POST | /api/experiments/run | experiment_run | 510 | Service.create_document + index_document | 写 | 否 | |
| POST | /api/experiments/run-corpus | experiment_run_corpus | 525 | experiments.run_experiment | 写 | 否 | |
| GET | /api/experiments/snapshots | experiment_snapshots | 535 | RunRepository + experiments | 读 | 否 | |
| GET | /api/experiments/compare | experiment_compare | 548 | RunRepository + experiments | 读 | 否 | |
| POST | /api/documents/{doc_id}/index/local | index_local | 558 | Service.index_document(use_llm=False) | 写 | 否 | |
| POST | /api/documents/{doc_id}/embed | embed_doc | 563 | Service.embed_document | 写 | 否 | |
| POST | /api/embeddings/backfill | embeddings_backfill | 569 | embeddings.backfill_embeddings | 写 | 否 | |
| GET | /api/search | api_search | 575 | retrieval.search | 读 | 否 | |
| POST | /api/search | post_search | 580 | retrieval.search | 读 | 否 | |
| GET | /api/search/knowledge | api_search_knowledge | 584 | retrieval.search_knowledge | 读 | 否 | 经 readmodels.knowledge_view |
| POST | /api/onebox/intent | onebox_intent | 597 | Service(intent.classify) | 读 | 否 | 无模型调用 |
| GET | /api/integrity/scan | integrity_scan | 619 | Service(integrity.scan) | 读 | 否 | |
| GET | /api/integrity/merge-impact | integrity_merge_impact | 637 | Service(integrity.merge_impact) | 读 | 否 | dry-run |
| POST | /api/integrity/merge | integrity_merge | 649 | Service(integrity.merge_entities)→仓储 repoint | 写 | **否** | ⚠ 直接 repoint_entity，绕过 operation |
| GET | /api/integrity/curation | curation_decisions | 663 | Service(integrity.curation_decisions) | 读 | 否 | |
| POST | /api/integrity/curation | curation_decide | 674 | Service(integrity.record_not_same) | 写 | 否 | |
| DELETE | /api/integrity/curation/{id} | curation_revoke | 684 | Service(integrity.revoke_curation) | 写 | 否 | |
| POST | /api/integrity/object-links | integrity_object_links | 692 | Service(integrity.apply_object_links) | 写 | 否 | 仅记审计行 |
| POST | /api/integrity/object-links/confirm | integrity_confirm_object_link | 699 | Service(integrity.confirm_object_link) | 写 | 否 | |
| GET | /api/integrity/suppressions | integrity_suppressions | 712 | Service(integrity.dismissals) | 读 | 否 | |
| POST | /api/integrity/suppressions | integrity_suppress | 723 | Service(integrity.dismiss_suggestion) | 写 | 否 | |
| DELETE | /api/integrity/suppressions/{id} | integrity_revoke_suppression | 733 | Service(integrity.revoke_dismissal) | 写 | 否 | |
| GET | /api/documents/{doc_id}/chunks | document_chunks | 741 | DocumentRepository().chunks | 读 | 否 | |
| GET | /api/ontology/predicates | ontology_predicates | 762 | ontology.CLAIM_PREDICATES | 读 | 否 | |
| GET | /api/documents/{doc_id}/knowledge | document_knowledge | 781 | Document/Claim repos + ontology | 读 | 否 | |
| GET | /api/entities | entities | 820 | EntityRepository().list | 读 | 否 | |
| GET | /api/entities/{entity_id} | entity | 826 | EntityRepository().get/count_claims_for | 读 | 否 | |
| PATCH | /api/entities/{entity_id} | update_entity | 834 | EntityRepository().update | 写 | 否 | |
| POST | /api/entities | create_entity | 861 | EntityRepository().insert | 写 | 否 | |
| POST | /api/ideas | create_idea | 876 | IdeaRepository().insert | 写 | 否 | |
| POST | /api/questions | create_question | 881 | QuestionRepository().insert | 写 | 否 | |
| POST | /api/events | create_event | 886 | EventRepository().insert | 写 | 否 | |
| GET | /api/research | research_list | 893 | ResearchRepository().list | 读 | 否 | |
| GET | /api/research/{task_id} | research_detail | 897 | ResearchRepository().get; Service.research_candidates | 读 | 否 | |
| POST | /api/research/{task_id}/candidates | research_propose_candidates | 916 | Service.propose_research_candidates→index_document | 写 | 否 | 经 index_document 直接仓储 |
| POST | /api/research | research_create | 931 | ResearchRepository().create | 写 | 否 | |
| POST | /api/research/{task_id}/run | research_run | 936 | ResearchRepository + Workflow(agent_workflow.run_research_pipeline) | 写 | 否 | handler 直接 set_status |
| POST | /api/runs/{run_id}/cancel | cancel_run | 952 | runlog + RunRepository | 写 | 否 | |
| GET | /api/entities/{entity_id}/graph | entity_graph | 961 | graph.neighborhood | 读 | 否 | |
| GET | /api/entities/{entity_id}/duplicates | entity_duplicates | 967 | resolution + EntityRepository | 读 | 否 | |
| GET | /api/claims | claims | 975 | ClaimRepository().list | 读 | 否 | |
| GET | /api/relations | relations | 979 | RelationRepository().list | 读 | 否 | |
| GET | /api/review/inbox | review_inbox | 983 | Service(review.inbox) | 读 | 否 | |
| GET | /api/review | review | 1000 | Entity/Claim/Relation repos candidates | 读 | 否 | handler 内联编排 |
| PATCH | /api/knowledge/{kind}/{item_id}/status | update_status | 1013 | Entity/Claim/Relation/Idea/Question repos set_status | 写 | **否** | ⚠ 直接改状态，绕过 operation |
| GET | /api/runs | list_runs | 1032 | RunRepository().list | 读 | 否 | |
| GET | /api/runs/{run_id} | get_run | 1036 | RunRepository().get | 读 | 否 | |
| GET | /api/context/cache | context_cache_stats | 1042 | context.cache | 读 | 否 | |
| GET | /api/context/runs | context_runs | 1049 | context.list_context_runs | 读 | 否 | |
| GET | /api/context/runs/{id} | context_run_detail | 1054 | context.get_context_run | 读 | 否 | |
| GET | /api/context/budgets | context_budgets | 1061 | context.AGENT_BUDGETS | 读 | 否 | |
| GET | /api/context/metrics | context_metrics | 1066 | CatalogRepository().context_metrics | 读 | 否 | |
| POST | /api/ask | ask | 1102 | runlog + retrieval + query_router + ask_workflow + llm.answer | 读 | 否 | 检索+生成，无写 |
| POST | /api/qa/validate | qa_validate | 1192 | Workflow(citation_validation_workflow) | 读 | 否 | 校验 |
| PUT | /api/documents/{doc_id} | update_doc | 1206 | DocumentRepository().update | 写 | 否 | |
| DELETE | /api/documents/{doc_id} | delete_doc | 1218 | Service.delete_document | 写 | 否 | |
| GET | /api/graph | global_graph | 1224 | Catalog/Relation repos | 读 | 否 | |
| GET | /api/stats | stats | 1232 | CatalogRepository().stats | 读 | 否 | |
| GET | /api/export | export_all | 1236 | CatalogRepository().export_all | 读 | 否 | |
| POST | /api/eval/run | eval_run | 1245 | evaluation.runners | 写 | 否 | |
| GET | /api/eval/runs | eval_runs | 1254 | evaluation.store | 读 | 否 | |
| GET | /api/eval/runs/{id} | eval_run_detail | 1260 | evaluation.store | 读 | 否 | |
| GET | /api/eval/baseline | eval_baseline | 1268 | evaluation.baseline | 读 | 否 | |
| POST | /api/eval/baseline/pin | eval_baseline_pin | 1284 | evaluation.store + baseline | 写 | 否 | |
| GET | / | index | 1304 | FileResponse | 读 | 否 | 非 API |

### 1.1 关键事实：真正走 Operation 执行框架的接口只有 2 个

- `POST /api/knowledge/operations`（main.py:392, :398）→ `domain.operations.run`
- `POST /api/knowledge/corrections/apply`（correction_workflow.py:527，`run(OperationRequest(kind='CORRECT'))`）

### 1.2 绕过 Operation 框架的"知识变更"写接口（Phase 3 重点）

| 接口 | 行号 | 直接调用的仓储/服务 | 绕过点 |
|---|---|---|---|
| `PATCH /api/claim-relations/{id}` | main.py:285-314 | `ClaimRepository.update_relation / set_status` | 未进 Operation |
| `POST /api/integrity/merge` | main.py:649；integrity.py:498-536 | `integrity.merge_entities` → 仓储 `repoint_entity/update` | 未进 Operation |
| `PATCH /api/knowledge/{kind}/{item_id}/status` | main.py:1013-1029 | `Entity/Claim/Relation/Idea/Question repos set_status` | 未进 Operation |
| 文档抽取 `POST /api/documents/{doc_id}/index` | service.py:134 | `knowledge.persist_extraction` → 各 repo.insert | 仅 `LINK_OBJECT` 记审计行（integrity.py:262-270），不走 Operation 执行框架 |

---

## 2. 动态 import / inline orchestration 清单（Task 1.4 输入）

`main.py` 内大量"函数内部 `from .xxx import ...`"仅为规避循环依赖。逐处（节选最关键）：

| 行号 | 函数 | 动态 import |
|---|---|---|
| 392 | knowledge_operation | `from .domain.operations import OperationError, OperationRequest, run` |
| 398 | knowledge_operation | `from .review import issues_for_claims` |
| 306-313 区 | update_claim_relation | `from .claim_relations import RELATIONSHIPS` |
| 324 | correction_plan | `from .workflows.correction_workflow import build_plan, parse_intent, verify_new_claim` |
| 339 | correction_apply | `from .workflows.correction_workflow import apply_correction, ...` |
| 571-735 | integrity_*（11 处） | `from .integrity import scan / merge_entities / curation_* / apply_object_links / dismissals / ...` |
| 909-942 | research_* | `from .service import research_candidates / propose_research_candidates`；`from .workflows.agent_workflow import run_research_pipeline` |
| 1115-1124 | ask | `from .runlog / .retrieval / .domain.query_router / .workflows.ask_workflow / .context / .context.providers / .runtime` |

> 结论：这些动态 import 是"模块顶层 A 依赖 B，B 又依赖 A"的典型症状。`main.py` 同时是路由、编排者、和多个模块的依赖根。Phase 1 不应简单全部移到顶部，而应先确定依赖方向（Application 层是稳定的中间层，顶层模块 → Application → Domain/Repo），再决定哪些 import 应下沉到新建的 `app/application/*`，哪些可上提到顶层。

---

## 3. 8 条核心真实调用链（Task 0.2）

层级图约定：`HTTP → Handler(main.py) → Application/Service → Workflow → Domain → Repository → SQLite`。MISSING = 当前代码不存在该层（不补）。

### 链 1：文档导入
```
HTTP  POST /api/documents/import              # main.py:164 import_file
  └─ Handler import_file
       ├─ file.read()                          # main.py:167（未用 importer.read_file，importer.py:6 未接入）
       └─ create_doc(...)                      # main.py:159 → service.create_document
            └─ Service.create_document         # service.py:19
                 └─ DocumentRepository().create → SQLite
HTTP  POST /api/documents/{id}/index          # main.py:487 index_doc
  └─ Handler → Service.index_document         # service.py:71（编排者）
       ├─ chunk_text                           # app/chunking.py
       ├─ DocumentRepository sync_chunks
       ├─ llm.extract                          # llm.py:240
       ├─ knowledge.persist_extraction         # service.py:134 → knowledge.py:141 → 各 repo.insert → SQLite
       ├─ claim_relations.detect_claim_relations
       └─ embeddings.embed_document
```
**结论：经 service（service.index_document 内联编排）。MISSING：独立 Extraction 应用层（抽取内联于 index_document）；importer.read_file 未接入 HTTP 链路。**

### 链 2：Extraction
```
HTTP  POST /api/documents/{id}/index (use_llm=True)  # main.py:487
  └─ Service.index_document                          # service.py:71（编排者）
       ├─ llm.extract                                # llm.py:240
       ├─ extraction_items.compile_extraction_items  # extraction_items.py:176
       └─ knowledge.persist_extraction               # knowledge.py:141 → Claim/Entity/Event/Relation/Idea/Question repos → SQLite
```
**结论：经 service（内联）。MISSING：独立 extraction Application/Workflow 层。**

### 链 3：Knowledge QA
```
HTTP  POST /api/ask                          # main.py:1102
  └─ Handler ask —— 直接多步编排
       ├─ plan_question / try_direct_answer / try_historical_answer  # ask_workflow.py
       ├─ evidence_hits(req.question)        # retrieval.py:272 → search (lexical+semantic)
       │    └─ ClaimRepository.for_chunk / for_document_pack → SQLite
       ├─ COMPILER/PLANNER.compile           # context/
       ├─ llm.answer                         # llm.py:312
       └─ supporting_knowledge(cited_claims) # ask_workflow.py:87 → readmodels.knowledge_view.best_per_statement → SQLite
```
**结论：handler 直接编排（部分经 ask_workflow 路由/直答，LLM 生成在 handler 内）；无 Application/Service 层。MISSING：统一 QA Application。**

### 链 4：Correction
```
Plan:
HTTP  POST /api/knowledge/corrections         # main.py:317 correction_plan
  └─ Workflow correction_workflow.parse_intent / build_plan / verify_new_claim  # 只读
Apply:
HTTP  POST /api/knowledge/corrections/apply   # main.py:336 correction_apply
  └─ Workflow.apply_correction                # correction_workflow.py:468
       ├─ DocumentRepository().create + replace_chunks   # 纠正文档落库
       └─ run(OperationRequest(kind='CORRECT', ...))      # correction_workflow.py:527
            └─ Domain domain/operations.run   # operations.py:297
                 ├─ _correct → ClaimRepository.insert / insert_relation / set_status(superseded)
                 └─ OperationRepository.record → SQLite
```
**结论：经 workflow + domain/operations（CORRECT）。无 Application 层，但已正确进 Operation。** 写回经 Claim + Operation repos。

### 链 5：Operation
```
HTTP  POST /api/knowledge/operations          # main.py:386 knowledge_operation
  └─ Handler → with transaction(): run(OperationRequest(kind, payload), conn)  # main.py:398
       └─ Domain domain/operations.run       # operations.py:297
            ├─ _HANDLERS: _create / _correct / _supersede / _merge / _accept / _archive / _restore / _duplicate / _contradict
            ├─ ClaimRepository / EntityRepository / RelationRepository / DocumentRepository
            └─ OperationRepository.record → SQLite
```
**结论：handler 直调 domain/operations.run。MISSING：Application 层；MISSING：operation_workflow（workflows/ 无 operation 文件）。**

### 链 6：Review
```
Inbox (pending 统计):
HTTP  GET /api/review/inbox                   # main.py:983 review_inbox
  └─ Service review.inbox()                   # review.py:52
       ├─ integrity.scan(duplicate/unlinked)  # integrity.py:322
       ├─ ClaimRepository.pending_decisions
       ├─ EntityRepository.candidates(_QUEUE_WINDOW)
       ├─ ClaimRepository.candidates
       └─ RelationRepository.candidates → SQLite
Raw 列表:
HTTP  GET /api/review                         # main.py:1000 review
  └─ Handler —— 直接内联调 Entity/Claim/Relation repos candidates → SQLite
Accept/Reject (状态变更):
PATCH /api/knowledge/{kind}/{item_id}/status  # main.py:1013 update_status
  └─ 各 repo set_status → SQLite（⚠ 绕过 Operation）
PATCH /api/claim-relations/{id}               # main.py:285 update_claim_relation
  └─ ClaimRepository.update_relation/set_status → SQLite（⚠ 绕过 Operation）
```
**结论：inbox 经 review.py 模块汇总；raw 列表 handler 内联；accept/reject 各 repo 直写。Review 数量**有**一个汇总结算点（review.inbox + review.py 各 candidates），但 `get /api/review`（main.py:1000）又独立调一次 candidates，相当于第二处统计逻辑。MISSING：统一 ReviewInboxView（readmodels/review.py 不存在）。**

### 链 7：Research
```
Create:   POST /api/research                  # main.py:931 → ResearchRepository().create → SQLite
Run:      POST /api/research/{id}/run         # 936 → ResearchRepository.set_status + Workflow(agent_workflow.run_research_pipeline) → agents/* → SQLite
Candidates:POST /api/research/{id}/candidates # 916 → Service.propose_research_candidates → index_document(use_llm=True)（同链 1/2）→ SQLite
Detail:   GET  /api/research/{id}             # 897 → Service.research_candidates → ClaimRepository.candidates_for_document → readmodels.knowledge_view → SQLite
```
**结论：run 经 workflow（agent_workflow）；candidates 经 service（index_document）。create/findings 落库 handler 直调 ResearchRepository。MISSING：统一 ResearchView（readmodels/research.py 不存在）。**

### 链 8：Search
```
HTTP  GET/POST /api/search                    # main.py:575/580 → retrieval.search        # retrieval.py:131
  ├─ lexical_search → DocumentRepository.search_fts/search_like
  └─ embeddings.semantic_search
HTTP  GET /api/search/knowledge               # main.py:584 → retrieval.search_knowledge  # retrieval.py:159
  ├─ search(...)
  ├─ ClaimRepository.claims_for_chunks
  ├─ EntityRepository.aliases_for
  └─ readmodels.knowledge_view.best_per_statement
```
**结论：handler 直调 retrieval（无 Application、无 Workflow）。`retrieval.py` 充当检索 Application/服务角色，但**不在** `service.py` 中。MISSING：SearchFacade/Application 统一收口。**

### 3.1 八链层级归属汇总

| 链 | Handler | Application/Service | Workflow | Domain | Repo→SQLite | handler 是否直接多步编排 |
|---|---|---|---|---|---|---|
| 1 导入 | main.py:164/487 | **service.index_document** | — | — | Document/Claim/Entity | 否（service 内联） |
| 2 抽取 | main.py:487 | **service.index_document** | — | — | knowledge.persist_extraction | 否（service 内联） |
| 3 QA | main.py:1102 | — | ask_workflow(部分) | query_router | retrieval→Document/Claim/Entity | **是** |
| 4 纠正 | main.py:317/336 | — | **correction_workflow** | operations.run | Claim/Operation | 否（workflow 编排） |
| 5 操作 | main.py:386 | — | — (MISSING) | **operations.run** | Claim/Entity/Operation | **是（直调 domain）** |
| 6 审核 | main.py:983/1000/1013 | review.py(非 service) | — | — | Entity/Claim/Relation | **是（review 端点内联 + status 直写）** |
| 7 研究 | main.py:931-936 | service(仅 candidates) | **agent_workflow** | — | Research/Document/Claim | 部分 |
| 8 检索 | main.py:575/584 | retrieval.py(非 service.py) | — | — | Document/Claim/Entity/embeddings | **是（直调 retrieval）** |

---

## 4. 总体结论（无歧义）

1. **`main.py` 过重且是事实上的业务编排中心**：104 个路由全在其中；QA、Search、Review、Operation 四类核心链路由 handler 直接多步编排或直调 domain/repo。
2. **Application 层缺失**：仅 `service.py`（文档 + 研究候选）与 `retrieval.py`（检索）是事实上的"半 Application"，二者都不在计划中的 `app/application/` 名下，且未覆盖 Knowledge/Operation/Ontology/Workflow Facade 范围。
3. **Operation 覆盖不全**：真正进入 Operation 执行框架的只有 2 个接口；3 处知识变更（claim-relations 改、integrity/merge、status PATCH）与抽取落库均绕过 Operation。
4. **Read Model 初具雏形但不完整**：`knowledge_view.py` 已被 QA/Search/Research 共用；`integrity_issue.py` 存在；但 Review/Search/Research/History 无统一 Read Model，且 `get /api/review`（main.py:1000）独立统计 pending，形成第二来源。
5. **current knowledge 已收敛到单源**：`domain/claim_state.py` 是唯一真源，`retrieval._current_claims` 是转调它的 shim（见 ARCHITECTURE-GAP.md）。
6. **无"永久 TODO / deprecated 标记"**：全仓 grep `deprecated/TODO/FIXME/XXX/HACK` 均为 0 处；技术债以"legacy 数据迁移"与"compat 兼容层"形式存在（见 ARCHITECTURE-GAP.md）。

---

## 5. Phase 0 验收对照

- [x] API 全量清单完成（104 路由，含逐条写?/走 Operation? 判定）
- [x] 8 条核心调用链完成（含 MISSING 标注）
- [x] CURRENT/TARGET 差异矩阵（见 ARCHITECTURE-GAP.md）
- [x] 每一个差异项都有代码位置（file:line）
- [x] 未出现"应该是 / 大概是 / 可能走"等描述（全部基于真实代码）

> 本阶段未改动任何代码。下一步：Phase 1（Application 层落地）。

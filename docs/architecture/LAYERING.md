# LAYERING — 分层与依赖规则

> 状态：**TARGET 规则 + CURRENT 模块归属**。当前模块归属是「现状」，不是「合规」。

## 1. 层定义

| 层 | 允许依赖 | 禁止依赖 |
|---|---|---|
| Presentation | Application, Domain | Infrastructure（直接） |
| Application | Domain | Presentation；Infrastructure 细节 |
| Domain | 无（纯逻辑） | Presentation, Application, 框架, 驱动 |
| Infrastructure | 无（被上层调用） | Application, Presentation（不得反向回调） |

## 2. 依赖规则（可审查）

```text
Rule L1  依赖只能向下，不能向上。
Rule L2  禁止跨层跳跃（Presentation 不得直接调 Infrastructure）。
Rule L3  Domain 不得 import 任何框架或数据库驱动。
Rule L4  Infrastructure 不得 import Application / Presentation。
Rule L5  同层模块之间通过公共接口交互，不得互相 reach into 私有实现。
```

## 3. 当前模块归属（CURRENT，事实）

> 这是为了迁移而记录的现状。标 ⚠️ 的表示该模块当前**跨层**，是迁移重点。

| 层（目标） | 当前模块 | 备注 |
|---|---|---|
| Presentation | `app/main.py` | FastAPI 路由；**路由内直接写 SQL**，是最大跨层点 ⚠️ |
| Presentation | `frontend/`, `web/` | Vue SPA 与静态入口 |
| Application | `app/workflows/agent_workflow.py` | `run_agent` / `run_research_pipeline`，最接近 Workflow 用例 |
| Application | `app/service.py` | `create_document` / `index_document` / `embed_document`：编排 + 直接事务 ⚠️ |
| Domain（部分） | `app/ontology.py` | 类型/谓词 registry 与校验（含 `registry_version()`） |
| Domain（部分） | `app/normalization.py` | Claim → Relation 确定性派生 |
| Domain（部分） | `app/resolution.py` | 实体解析（exact / alias / fuzzy） |
| Domain（部分） | `app/claim_relations.py` | duplicate / coexists / supersedes / contradicts 判定 |
| Domain + Persistence ⚠️ | `app/knowledge.py` | `persist_extraction` 同时做 Domain 校验与 INSERT |
| Domain（散落）⚠️ | `app/retrieval.py::_current_claims` | Claim 生命周期解析内联在检索层 |
| Infrastructure | `app/db.py` | SQLite schema / 迁移 / 连接 |
| Infrastructure | `app/llm.py`, `app/embeddings.py` | LLM / Embedding 调用 |
| Infrastructure | `app/context/` | Context Runtime（执行基础设施） |
| Infrastructure | `app/runlog.py` | 运行记录（best-effort 落库） |
| Adapter | `app/agents/`, `app/tools/` | AgentScope 适配与工具 |
| Presentation | `app/importer.py` | 文件类型适配（边界） |

## 4. 已知分层缺口（CURRENT）

1. **`app/main.py` 承担了 Domain 与 Infrastructure 的职责**：路由函数内直接 `connect()` + 拼 SQL + 组装领域结果。这是「无 Facade」的直接后果。
2. **`app/knowledge.py` 混合 Domain 校验与持久化**：`persist_extraction` 接收 `conn`，既是领域操作又是数据访问。
3. **Claim 状态解析不统一**：`retrieval.py::_current_claims` 内联实现；未来 Correction / Review / Graph 若各自实现，必然产生不一致。
4. **`app/service.py` 直接管理事务**：用例层越权持有持久化细节。

这些缺口对应的迁移动作见 [MIGRATION-PLAN.md](./MIGRATION-PLAN.md)。

## 5. 目标依赖图

```text
main.py (Presentation)
   └── workflows / facades (Application)
          └── KnowledgeFacade / SearchFacade / OperationFacade / OntologyFacade
                 └── Domain (rules, resolver, operations)
                        └── Repository (persistence)
                               └── db.py (SQLite)
```

- `agents/` 与 `tools/` 位于 Application 与 Presentation 之间：它们是**适配器**，只能调用 Facade。
- `context/` 被 Application 使用，不给 Domain 使用。

## 6. 审查方法

对任一改动，检查 import：

```text
Domain 代码里出现 import fastapi / agentscope / sqlite3 / vue  → 违规
main.py 里出现业务判定（if predicate in ...）                 → 违规（应下沉 Domain）
Repository 里出现「决定谁 current」                            → 违规（应属 Domain）
```

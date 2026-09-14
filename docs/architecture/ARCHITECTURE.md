# ARCHITECTURE

> 状态：**TARGET 为主，CURRENT 见每节标注**。当前 v0.1 代码只部分符合本图。

## 1. 四层模型

```text
                    ┌─────────────────────┐
                    │   Presentation      │
                    │ API / UI / CLI/MCP  │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │   Application       │
                    │ Use Cases / Workflow│
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │      Domain         │
                    │ Knowledge / Rules    │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │  Infrastructure     │
                    │ DB / LLM / Embedding │
                    └─────────────────────┘
```

依赖方向：

```text
Presentation → Application → Domain → Infrastructure
```

**依赖只能向下。** 任何向上依赖、跨层跳跃都是违规。

---

## 2. 各层职责

| 层 | 职责 | 不得包含 |
|---|---|---|
| **Presentation** | HTTP 路由、请求/响应模型、UI、CLI/MCP 适配 | 业务规则、SQL |
| **Application** | 用例编排（Workflow）：ingest / extract / ask / research / review / correct | 领域判定的实现细节 |
| **Domain** | 知识模型、不变量、状态解析、操作（Operation）、校验规则 | FastAPI、AgentScope、Vue、SQLite API |
| **Infrastructure** | SQLite 持久化、LLM 调用、Embedding、缓存 | 业务规则 |

---

## 3. Domain 的硬约束

Domain 层：

```text
不能依赖 FastAPI
不能依赖 AgentScope
不能依赖 Vue
不能依赖 SQLite API
```

理由：Domain 是系统里最稳定、最需要被反复测试的部分。它一旦绑定了框架或数据库驱动，就无法独立验证，也会被框架版本牵制。

> **CURRENT**：当前还没有独立的 Domain 包。领域逻辑散落在 `app/ontology.py`（类型/谓词校验）、`app/normalization.py`（关系派生）、`app/resolution.py`（实体解析）、`app/claim_relations.py`（断言关系）、`app/knowledge.py`（持久化抽取结果）。抽取结果落库 `app/knowledge.py::persist_extraction` 目前**同时承担 Domain 校验与持久化**，是分层最容易混淆的模块。
>
> **TARGET**：Domain 成为纯逻辑层（不 import `sqlite3`），持久化下沉到 Repository，由 Facade 组合。

---

## 4. 一次典型调用的目标路径

以「回答问题」为例：

```text
POST /api/ask                       (Presentation)
   ↓
WorkflowFacade.ask()                (Application)
   ↓
SearchFacade.evidence()             (Domain/入口)
   ↓
ClaimStateResolver.current()        (Domain)
   ↓
Repository / providers              (Infrastructure)
```

以「导入并抽取文档」为例：

```text
POST /api/documents/{id}/index      (Presentation)
   ↓
WorkflowFacade.ingest()             (Application)
   ↓
ExtractionAgent (LLM) → 结构化候选   (Infrastructure: LLM)
   ↓
Domain validation + normalization   (Domain)
   ↓
OperationFacade.create_claims()     (Domain)
   ↓
Repository                          (Infrastructure)
```

**LLM 永远出现在 Domain 之前的一段**：LLM 产出的是「候选」，只有经过 Parse → Schema Validation → Normalization → Domain Validation 之后才成为知识。

---

## 5. 与 Context Runtime 的关系

Context Runtime（`app/context/`：budget / planner / compiler / cache / trace / TaskPacket）属于 **执行基础设施**，不属于 Knowledge Domain。

- 它决定「哪些 token 进入某次 LLM 调用」，不决定「知识是什么」。
- 因此 Context Runtime 可以依赖 Domain 概念（如 Claim / Evidence 的摘要），但 Domain 不得反向依赖 Context Runtime。

详见 [ADR-008](../adr/ADR-008-CONTEXT-RUNTIME-BOUNDARY.md)。

---

## 6. 与 AgentScope 的关系

AgentScope 是 **执行框架（Runtime Adapter）**，不是领域框架。角色（Personal / Knowledge / Research / Curator / Review / Extraction）通过工具与 Facade 访问系统能力，**不直连 SQLite、不实现业务规则**，未来可整体替换。

详见 [ADR-007](../adr/ADR-007-AGENTSCOPE-AS-RUNTIME-ADAPTER.md)。

---

## 7. 架构红线速查

```text
API      → Facade → Repository      ✅
Agent    → Facade → Repository      ✅
API      → ClaimRepository          ❌ 绕过 Facade
Agent    → SQLite                   ❌ 跨层
Domain   → FastAPI / agentscope     ❌ 反向依赖
Repository 内出现业务规则            ❌ 职责混淆
LLM 直接决定 current/superseded      ❌ 状态由程序定
```

具体审查清单见 [docs/development/CODING-STANDARD.md](../development/CODING-STANDARD.md)。

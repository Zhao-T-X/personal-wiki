# MODULE-BOUNDARIES — 模块边界

> 状态：**目标边界** + **CURRENT 现状**。定义模块之间「谁能调谁、传什么、禁什么」。

## 1. 模块边界总览

```text
app/main.py            路由（Presentation）
app/workflows/         用例编排（Application）
app/agents/            AgentScope 适配
app/tools/             Agent 工具（只读 + 压缩）
app/context/           Context Runtime（执行基础设施）
app/ontology.py        本体/注册表（Domain）
app/normalization.py   关系派生（Domain）
app/resolution.py      实体解析（Domain）
app/claim_relations.py 断言关系 / Evolution（Domain）
app/knowledge.py       抽取结果落库（Domain + Persistence，待拆分）
app/service.py         用例 + 事务（Application，待拆分）
app/retrieval.py       检索 + current 过滤（待拆分）
app/db.py              SQLite（Infrastructure）
app/llm.py/embeddings  LLM/Embedding（Infrastructure）
app/runlog.py          运行记录（Infrastructure）
```

## 2. 调用规则

| 调用方 | 允许调用 | 禁止调用 |
|---|---|---|
| `main.py` | workflows / facades | 直接 `connect()` + 业务分支（目标） |
| `workflows/` | facades / domain | 直接 SQL |
| `agents/` | Facade / tools | 直连 SQLite / 实现领域规则 |
| `tools/` | Facade / retrieval | 直连 SQLite（目标） |
| Domain 模块 | 互相调用（同层） | fastapi / agentscope / sqlite3 |
| `db.py` | 被调用 | 回调上层、含业务规则 |

## 3. 数据传递边界

- **跨模块传领域对象/字典，不传 `sqlite3.Connection`**（目标）。
  - CURRENT：`persist_extraction(conn, ...)`、`detect_claim_relations(conn, ...)` 都接收连接——这是「Domain 与 Persistence 耦合」的具体表现，迁移时改为 Repository 内部持有连接。
- **不要跨模块 reach 私有函数**（`_` 前缀）：
  - CURRENT：`retrieval.py` 内部 `_current_claims` 被同文件使用，暂无跨模块私有调用；但生命周期逻辑需要提升为公共 `ClaimStateResolver`。
- **模块内部字段变更不得外泄**：JSON 列（`*_json`）的序列化/反序列化由模块内部负责，通过 `app/db.py::dumps/loads` 统一。

## 4. 允许的依赖方向（目标）

```text
main ─► workflows ─► facades ─► domain ─► repository ─► db
agents ─► facades
tools  ─► facades
context ─► (被 workflows/agents 使用；不依赖 domain 实现细节)
```

- `context/` 可依赖「领域概念的摘要」（如 Claim 文本），但 Domain 不得依赖 `context/`。
- `agents/` / `tools/` 是适配器：位于 Presentation 与 Application 之间，**只能下行**到 Facade。

## 5. 边界违规的典型信号

```text
main.py 里出现 SQL 字符串                  → 边界违规（SQL 应在 Repository）
domain 模块 import fastapi/agentscope      → 反向依赖
retrieval.py 里出现「是否 current」判定     → 规则重复（应调 Resolver）
agents/ 里出现 predicate 合法性判断         → Agent 承担了 Domain 规则
db.py 里出现业务 if 分支                    → Repository 越权
```

## 6. 与 Context Runtime 的边界

Context Runtime 的职责是「为一次 LLM 调用装配上下文」，边界如下：

- **输入**：TaskContext（agent / task_type / goal / features）、Provider 集合、配置预算。
- **输出**：CompiledContext（system / context / user / trace 账本）。
- **不做**：不决定知识正确性，不写知识表（trace 表除外），不裁决 current。

详见 [../architecture/ARCHITECTURE.md](../architecture/ARCHITECTURE.md) 第 5 节与 [ADR-008](../adr/ADR-008-CONTEXT-RUNTIME-BOUNDARY.md)。

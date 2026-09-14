# ADR — Architecture Decision Records

本目录记录 LLM-Wiki 的**架构决策**。

ADR 不记录「怎么写代码」（那属于 [docs/development/](../development/README.md)），而是记录：

> **为什么这样设计。**

ADR 是**追加式、不可改写**的：决策变了不修改旧 ADR，而是新增一条并把旧的标记为 `Superseded by ADR-xxx`。

---

## 状态标记

| Status | 含义 |
|---|---|
| `Proposed` | 提议中，未定 |
| `Accepted` | 已采纳（架构层面的决定） |
| `Superseded by ADR-xxx` | 被后续 ADR 取代 |
| `Deprecated` | 不再适用 |

> 注意区分 **ADR 状态**（决策是否采纳）与 **实现状态**（CURRENT / PARTIAL / TARGET）。每条 ADR 都会注明实现的当前进度。

---

## 索引

| ADR | 决策 | 实现状态 |
|---|---|---|
| [ADR-001](./ADR-001-SQLITE-AS-PRIMARY-STORAGE.md) | SQLite 作为主存储 | CURRENT |
| [ADR-002](./ADR-002-LAYERED-ARCHITECTURE.md) | 四层架构与向下依赖 | PARTIAL |
| [ADR-003](./ADR-003-PUBLIC-FACADE-ENTRYPOINTS.md) | 五个公共 Facade 入口 | TARGET |
| [ADR-004](./ADR-004-DOMAIN-AND-REPOSITORY-SEPARATION.md) | Domain 与 Repository 分离 | TARGET |
| [ADR-005](./ADR-005-CLAIM-EVOLUTION.md) | Claim Evolution 与统一状态裁决 | PARTIAL |
| [ADR-006](./ADR-006-EVIDENCE-AS-FIRST-CLASS-DOMAIN.md) | Evidence 一等领域对象 | PARTIAL |
| [ADR-007](./ADR-007-AGENTSCOPE-AS-RUNTIME-ADAPTER.md) | AgentScope 作为运行时适配器 | CURRENT |
| [ADR-008](./ADR-008-CONTEXT-RUNTIME-BOUNDARY.md) | Context Runtime 边界 | CURRENT |
| [ADR-009](./ADR-009-KNOWLEDGE-OPERATIONS.md) | 知识变更统一经 Operation | TARGET |
| [ADR-010](./ADR-010-DETERMINISTIC-FIRST.md) | 确定性优先于 LLM | CURRENT |

---

## 模板

新增 ADR 请复制以下骨架：

```markdown
# ADR-0XX — <决策标题>

- Status: Proposed | Accepted | Superseded by ADR-xxx
- Date: YYYY-MM-DD
- Implementation status: CURRENT | PARTIAL | TARGET
- Related: <链接>

## Context
为什么需要做这个决定？约束与背景。

## Decision
决定了什么。用精确、可执行的措辞。

## Consequences
### Positive
### Negative / Trade-offs

## Current vs Target
当前实现现状与目标差距。

## References
代码 / 文档位置。
```

---

## 何时需要 ADR

- 改变分层、入口、依赖方向；
- 改变领域模型或不变量；
- 引入/替换存储、执行框架、检索策略等长期基础设施；
- 改变对外契约（API 语义、错误模型）。

日常 bugfix、局部重构、模板调整**不需要** ADR。

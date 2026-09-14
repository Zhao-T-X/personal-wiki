# ADR-004 — Domain / Repository Separation

- Status: Accepted
- Date: 2026-09-14
- Implementation status: **TARGET**
- Related: [ADR-002](./ADR-002-LAYERED-ARCHITECTURE.md), [ADR-009](./ADR-009-KNOWLEDGE-OPERATIONS.md)

## Context

在 v0.1 中，领域规则与持久化常常写在同一个函数里。最典型的是 `app/knowledge.py::persist_extraction`：它同时

- 做领域校验（provenance 是否属于本文档、关系端点类型是否合法、关系是否重复）；
- 执行持久化（多条 `INSERT`）。

并且它接收一个 `sqlite3.Connection`。这带来两个问题：

1. 领域规则无法脱离数据库测试；
2. 持久层与领域层的边界模糊，改 schema 会牵动业务判定。

## Decision

严格区分两层职责：

```text
Repository = Persistence   （只读写，不含业务规则）
Domain     = Business Rules（纯逻辑，不知道 SQL / 连接）
```

规则：

1. **Domain 不接收 `Connection`**；跨模块传递的是领域对象/字典。
2. **Repository 不包含业务分支**（不出现「谁是 current / 类型是否合法」这类判断）。
3. 事务由 Application / Repository 拥有；Domain 只被调用。

## Consequences

### Positive

- 领域规则可纯函数单测（不建库）。
- 换存储实现不影响领域。
- 「规则」集中，避免多处复制（呼应「One business rule, one implementation」）。

### Negative / Trade-offs

- 需要引入 Repository 层并在 Facade 中组合，迁移期代码量上升。
- 过度抽象风险：Repository 应贴近用例，不做「万能 DAO」。

## Current vs Target

- **CURRENT（事实）**：
  - `knowledge.py::persist_extraction(conn, ...)`、`claim_relations.py::detect_claim_relations(conn, ...)` 接收连接 —— Domain 与持久化耦合。
  - `service.py` 直接 `with transaction() as conn:` 编排。
  - `main.py` 路由内直接 SQL。
- **TARGET**：
  - Domain：`ontology` / `normalization` / `resolution` / `claim_relations` / `ClaimStateResolver` 为纯逻辑。
  - Repository：`DocumentRepository` / `EntityRepository` / `ClaimRepository` / `EvidenceRepository` 等。
  - Facade 组合二者，Application 拥有事务。
- **MIGRATION**：Phase 2 拆分 `persist_extraction`（[../architecture/MIGRATION-PLAN.md](../architecture/MIGRATION-PLAN.md)）。

## References

- [../development/MODULE-BOUNDARIES.md](../development/MODULE-BOUNDARIES.md)
- `app/knowledge.py`, `app/service.py`

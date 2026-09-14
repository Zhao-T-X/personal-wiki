# ADR-002 — Layered Architecture

- Status: Accepted
- Date: 2026-09-14
- Implementation status: **PARTIAL**（分层思想已存在，但未物理分层）
- Related: [ADR-003](./ADR-003-PUBLIC-FACADE-ENTRYPOINTS.md), [ADR-004](./ADR-004-DOMAIN-AND-REPOSITORY-SEPARATION.md)

## Context

v0.1 的功能实现集中在 `app/main.py`（路由 + 裸 SQL）与 `app/knowledge.py`（领域校验与持久化混合）。随着 Agent、Context Runtime、检索等能力增长，若继续把逻辑堆在路由层，将无法审查、无法复用、无法独立测试。

## Decision

采用四层架构，**依赖只能向下**：

```text
Presentation → Application → Domain → Infrastructure
```

各层职责与硬约束见 [../architecture/ARCHITECTURE.md](../architecture/ARCHITECTURE.md)。

关键规定：

1. **Domain 不得依赖** FastAPI / AgentScope / Vue / SQLite API。
2. 禁止跨层跳跃（Presentation 不直接调 Infrastructure）。
3. 禁止反向依赖（Infrastructure 不回调 Application/Presentation）。

## Consequences

### Positive

- 领域规则可脱离框架与数据库独立测试。
- 框架（AgentScope / FastAPI）可替换，不影响领域。
- 审查有客观依据（import 检查即可发现违规）。

### Negative / Trade-offs

- 迁移期存在双轨（旧路径与新层并存），需要纪律（见迁移计划）。
- 对现有代码是一次结构手术，成本高、风险集中在 `main.py` 与 `knowledge.py`。

## Current vs Target

- **CURRENT（事实）**：逻辑分布在 `main.py`（路由 + SQL + 组装）、`service.py`（编排 + 事务）、`knowledge.py`（Domain 校验 + INSERT）。没有独立 Domain 包；Domain 逻辑分散在 `ontology.py` / `normalization.py` / `resolution.py` / `claim_relations.py`。
- **TARGET**：Domain 成为纯逻辑层（不 import `sqlite3`），持久化下沉 Repository，由 Facade 组合。
- **MIGRATION**：见 [../architecture/MIGRATION-PLAN.md](../architecture/MIGRATION-PLAN.md) Phase 1–2。

## References

- [../architecture/ARCHITECTURE.md](../architecture/ARCHITECTURE.md)
- [../architecture/LAYERING.md](../architecture/LAYERING.md)
- [../development/MODULE-BOUNDARIES.md](../development/MODULE-BOUNDARIES.md)

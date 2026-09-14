# ADR-003 — Public Facade Entry Points

- Status: Accepted
- Date: 2026-09-14
- Implementation status: **TARGET**
- Related: [ADR-002](./ADR-002-LAYERED-ARCHITECTURE.md), [ADR-004](./ADR-004-DOMAIN-AND-REPOSITORY-SEPARATION.md)

## Context

当前所有外部调用（API / Agent / 前端）都直接访问内部函数与数据库：`main.py` 路由内直接 `connect()` + 拼 SQL，Agent 通过工具直接读表。这意味着：

- 同一能力有多个入口，行为可能漂移；
- 没有稳定的公共契约，重构内部实现会波及全仓；
- 未来新增 CLI / MCP / Scheduler 会再次复制一套访问逻辑。

## Decision

系统对外**只提供五个公共入口（Facade）**：

```text
KnowledgeFacade   读知识：Entity / Claim / Relation / Evidence / History / Current
SearchFacade      检索：lexical / semantic / hybrid / entity / claim / evidence
OperationFacade   变更：CREATE / CORRECT / MERGE / SPLIT / SUPERSEDE / CONTRADICT / ARCHIVE / RESTORE
OntologyFacade    本体：Entity Type / Predicate / Relation Type / Normalization / Registry / Schema Version
WorkflowFacade    用例：Ingest / Extract / Ask / Research / Review / Correct
```

并确立红线：

> **业务代码不得绕过 Facade 直接访问 Repository。**

```text
API   → KnowledgeFacade → Repository   ✅
Agent → KnowledgeFacade → Repository   ✅
API   → ClaimRepository                ❌
Agent → SQLite                         ❌
```

## Consequences

### Positive

- 唯一稳定的对外契约，内部可自由重构。
- 权限 / 审计 / 校验可在 Facade 统一施加。
- 新前端（CLI / MCP / Scheduler）复用同一入口。

### Negative / Trade-offs

- Facade 需要精心设计，粒度过粗会成为瓶颈、过细则泄漏实现。
- 迁移期新旧路径并存，必须防止「新功能又走旧路径」。

## Current vs Target

- **CURRENT**：无 Facade。能力分散在 `main.py` 路由、`service.py`、`knowledge.py`、`retrieval.py`。
- **TARGET**：五个 Facade 就位，外部一律经 Facade。
- **MIGRATION**：Facade 先做薄封装复用现有实现，再逐步切换调用方（[../architecture/MIGRATION-PLAN.md](../architecture/MIGRATION-PLAN.md) Phase 1）。

## References

- [../architecture/PUBLIC-ENTRYPOINTS.md](../architecture/PUBLIC-ENTRYPOINTS.md)
- [../development/MODULE-BOUNDARIES.md](../development/MODULE-BOUNDARIES.md)

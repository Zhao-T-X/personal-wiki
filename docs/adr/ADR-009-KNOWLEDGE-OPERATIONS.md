# ADR-009 — Knowledge Operations

- Status: Accepted
- Date: 2026-09-14
- Implementation status: **PARTIAL**（八类 Operation + 审计已落地于 `app/domain/operations.py`；旧 status PATCH 待收敛）
- Related: [ADR-005](./ADR-005-CLAIM-EVOLUTION.md), [ADR-004](./ADR-004-DOMAIN-AND-REPOSITORY-SEPARATION.md)

## Context

v0.1 对知识的变更主要是「抽取落库（新增）」与「粗粒度状态 PATCH」。缺少对**修正、合并、拆分、取代、归档、恢复**的一等建模：

- 修正只能靠直接 `UPDATE`（会覆盖历史，违反 [ADR-005](./ADR-005-CLAIM-EVOLUTION.md)）；
- 合并 / 拆分没有统一入口与审计；
- 状态 PATCH 无法表达「为什么改、改了什么、能否回滚」。

## Decision

> **所有知识修改必须通过 Operation。**

定义八类操作：

```text
CREATE / CORRECT / MERGE / SPLIT / SUPERSEDE / CONTRADICT / ARCHIVE / RESTORE
```

每个 Operation 必须满足：

```text
1. 有明确输入（input contract）
2. 有 domain validation
3. 有执行结果（result contract）
4. 有 audit record
5. 可追踪
6. 必要时可回滚
```

配套规定：

- **铁律**：用户修改知识**不得直接 UPDATE 覆盖历史**；更正必须产生**新 Claim**并经 `supersedes` / `contradicts` 关联。
- Operation 是**注册式扩展点**：新增操作靠注册，而不是在中心函数加 `elif`。
- Operation 是知识变更的**唯一**入口，对外经 `OperationFacade`（可暴露为 `POST /api/knowledge/operations` 等）。

## Consequences

### Positive

- 每一次知识变更都可审计、可追踪、可回滚。
- 修正 / 合并 / 取代有统一语义，前端与 Agent 行为一致。
- 与 Claim Evolution 无缝衔接（取代 = 一个 Operation）。

### Negative / Trade-offs

- 需要新建 Operation 抽象层、审计记录与测试，前期成本较高。
- 现有直接写入路径（`persist_extraction`、状态 PATCH）需逐步收敛进 Operation。

## Current vs Target

- **CURRENT**：仅 `CREATE`（`app/knowledge.py::persist_extraction`）与 `PATCH /api/knowledge/{kind}/{id}/status`。`supersedes` 仅作为 `claim_relations` 关系被记录，无统一操作入口；无 CORRECT / MERGE / SPLIT。
- **TARGET**：OperationFacade + 注册式八操作 + 审计记录。
- **MIGRATION**：Phase 3（[../architecture/MIGRATION-PLAN.md](../architecture/MIGRATION-PLAN.md)），前置不变量测试（`Correction 必须产生 Operation`、`Operation 失败必须 rollback`）。

## References

- [../architecture/KNOWLEDGE-OPERATIONS.md](../architecture/KNOWLEDGE-OPERATIONS.md)
- [../architecture/EXTENSION-POINTS.md](../architecture/EXTENSION-POINTS.md)
- `app/knowledge.py`, `app/main.py::update_status`

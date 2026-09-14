# ADR-005 — Claim Evolution

- Status: Accepted
- Date: 2026-09-14
- Implementation status: **PARTIAL**
- Related: [ADR-006](./ADR-006-EVIDENCE-AS-FIRST-CLASS-DOMAIN.md), [ADR-009](./ADR-009-KNOWLEDGE-OPERATIONS.md)

## Context

知识会随时间变化：公司的 CEO 会换、API 会废弃、事实会被更正。若用 `UPDATE` 覆盖旧断言，就会丢失：

- 历史证据与原文溯源；
- 「为什么改」的审计线索；
- 回答「*谁曾经是* CEO」的能力。

## Decision

**知识永不静默覆盖历史。** 新 Claim 与既有 Claim 通过显式关系建模：

```text
duplicate     同一陈述再次出现（可自动 link evidence）
coexists      同一 subject+predicate、不同 object，可同时成立
contradicts   不能同时为真、且无从判断谁当前（交人工 review）
supersedes    新 Claim 取代旧 Claim
```

关键规则：

1. **`supersedes` 是让旧 Claim 停止 current 的唯一关系。**
2. **`supersedes` 永不自动推断** —— 只有当证据文本明确说明「取代」时才成立；单值谓词对象变化 → `contradicts` + `review`（由人裁决）。False certainty 比诚实的提问更糟。
3. **`contradicts` 不自动 supersede。**
4. **Current Knowledge 不得由 Retrieval 层自行推导**；统一由 `ClaimStateResolver` 裁决：
   ```python
   is_current() / get_current() / get_history() / resolve()
   ```
5. 所有读取 current 的地方（Retrieval / Graph / QA / Correction / Review）必须调用**同一个** Resolver。

## Consequences

### Positive

- 历史、证据、时间线全部保留，可回答「曾经是什么」。
- 冲突显式暴露给人，而不是被静默覆盖或错误排序。
- current 视图是**推导结果**，不是被覆盖的存储状态。

### Negative / Trade-offs

- 读取必须经过 Resolver，多一层逻辑；遗漏调用会导致读到历史。
- 需要在审核队列中处理 `contradicts`，增加人工成本（这是刻意的）。

## Current vs Target

- **CURRENT**：
  - 判定与写入在 `app/claim_relations.py`（`compare_claim` / `detect_claim_relations`，落 `claim_relations` 表，`supersedes` 不在确定性规则中自动产生）。
  - `index_document` 在同一事务内调用 `detect_claim_relations`。
  - **current 过滤内联**在 `app/retrieval.py::_current_claims`（superseded 且被 current 覆盖则丢弃，否则保留并标 `lifecycle`）——这是唯一实现，但位于检索层，无法被 Graph / Correction / Review 复用。
- **TARGET**：抽出 `ClaimStateResolver` 到 Domain，全部读取路径统一调用。
- **进展**：`app/domain/claim_state.py` 已建立，`retrieval`（Search/QA）与 `graph` 已统一调用；Review / Correction 亦经同一入口。
- **MIGRATION**：Phase 3（[../architecture/MIGRATION-PLAN.md](../architecture/MIGRATION-PLAN.md)），迁移前先补不变量测试（`superseded 不能 current`、`contradicts 不自动 supersede`）。

## References

- `app/claim_relations.py`
- `app/retrieval.py::_current_claims`
- `docs/standards/02-CLAIM-STANDARD.md`
- [../architecture/KNOWLEDGE-OPERATIONS.md](../architecture/KNOWLEDGE-OPERATIONS.md) §5

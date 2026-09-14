# ADR-006 — Evidence as First-Class Domain Object

- Status: Accepted
- Date: 2026-09-14
- Implementation status: **PARTIAL**
- Related: [ADR-005](./ADR-005-CLAIM-EVOLUTION.md), [ADR-004](./ADR-004-DOMAIN-AND-REPOSITORY-SEPARATION.md)

## Context

项目的根本承诺是：**原文永不被结构化知识覆盖，一切断言可回溯到原文偏移与引用。** 当前证据以**内联列**形式挂在知识对象上：

```text
claims.source_document_id
claims.source_chunk_id
claims.source_start_offset
claims.source_end_offset
claims.source_quote
relations / events / ideas / questions 同构
```

这带来两个限制：

1. **一个 Claim 只能有一个来源** —— 无法表达「多个来源共同支持同一断言」。
2. 证据无法被独立引用、独立演化，也难以支持「一处证据支撑多条断言」。

## Decision

把 **Evidence 正式升级为一等领域对象**：

```text
Evidence
    ↓
Document → Chunk → Offset → Quote
```

建模方向：

```text
Claim
 ├── Evidence
 ├── Evidence
 └── Evidence
```

规则：

1. Claim **不再「拥有一个 source」**，而是关联一到多个 Evidence。
2. Evidence 必须指向存在的 Chunk（不变量）。
3. quote 优先定位到精确 `start/end offset`；无法精确时标记 `imprecise`，但不丢失（回落整块 span）。
4. 同一 Evidence 可被多条断言引用。

## Consequences

### Positive

- 支持多证据、多来源、交叉验证。
- 证据可独立检索与统计（例如「哪些断言只有单一且不精确的证据」）。
- 为「一个来源支撑多断言」和证据复用铺路。

### Negative / Trade-offs

- 需要新表与新迁移，且要兼容现有内联列（迁移期双写/回填）。
- 读取断言时需要 join 证据，查询复杂度上升。

## Current vs Target

- **CURRENT**：证据为内联列；quote 定位在 `app/knowledge.py::_locate_quote`（exact → 空白压缩 → unicode 归一 → 逐行模糊 ≥0.85 → 整块回落，`localized=False` 计数为 `imprecise_quotes`）。标准映射见 `docs/standards/09-EVIDENCE-PROVENANCE-STANDARD.md`。
- **TARGET**：独立 Evidence 表/实体，`1 Claim ↔ N Evidence`；内联列保留为兼容读或迁移后移除。
- **MIGRATION**：Phase 3（[../architecture/MIGRATION-PLAN.md](../architecture/MIGRATION-PLAN.md)）。迁移前置不变量测试：`Evidence 不能指向不存在的 Chunk`。

## References

- `app/knowledge.py::_source` / `_locate_quote` / `persist_extraction`
- `app/db.py`（claims / relations / events / ideas / questions 的 source_* 列）
- `docs/standards/09-EVIDENCE-PROVENANCE-STANDARD.md`
- [../architecture/DOMAIN-MODEL.md](../architecture/DOMAIN-MODEL.md) §2 Evidence

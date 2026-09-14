# ADR-010 — Deterministic First

- Status: Accepted
- Date: 2026-09-14
- Implementation status: **CURRENT**
- Related: [ADR-005](./ADR-005-CLAIM-EVOLUTION.md), [ADR-009](./ADR-009-KNOWLEDGE-OPERATIONS.md)

## Context

LLM 擅长语义判断，但在**可确定性计算**的事情上不稳定：schema / 枚举 / 类型校验、重复检测、偏移校验、关系方向、去重。

如果把这些交给 LLM：

- 同样输入可能得到不同结果，无法回归测试；
- 错误无法定位（是模型错还是规则错？）；
- 为「保险」不断往 context 里加规则，导致 prompt 膨胀（正是 Context Runtime 要解决的问题之一）。

## Decision

> **能用程序确定的事情，不交给 LLM。**

```text
Deterministic
      >
LLM
```

明确归属：

| 由程序（Deterministic）决定 | 由 LLM 判断 |
|---|---|
| schema / 枚举 / 类型校验 | 语义抽取（实体、断言、关系候选） |
| 谓词归一化与合法性 | 语义分类（claim_type 等建议） |
| 实体解析（exact / alias / fuzzy） | 语义摘要 |
| 关系方向与端点类型约束 | 语义排序建议 |
| 偏移 / quote 定位 | 语义解释 |
| 去重、证据升级级别 | — |
| 关系判定（duplicate/coexists/… 的结构部分） | 「两段不同措辞是否同义」这类**按需**语义判断 |
| **Claim 生命周期（current/superseded）** | —（**永不由 LLM 决定**） |

配套：`docs/standards/12-IMPLEMENTATION-MAP.md` 已确立「LLM 负责语义抽取，应用负责 registry 强制、实体解析、归一化、关系派生、证据校验、去重、生命周期」。

## Consequences

### Positive

- 可重复、可回归、可测试；错误可定位到具体规则。
- 减少对 prompt 中「规则文本」的依赖，控制上下文成本。
- 安全：状态变更不依赖模型幻觉。

### Negative / Trade-offs

- 需要维护 registry 与规则代码；语义边界情况需显式设计而非「丢给模型」。
- 对确实需要语义的部分（如同义判定）保留 LLM，但必须是**建议**而非**决定**。

## Current vs Target

- **CURRENT**：已广泛实践。示例：
  - `app/ontology.py`（registry 校验 / 归一化 / `match_claim_predicates` 确定性候选）
  - `app/resolution.py`（实体解析）
  - `app/normalization.py`（关系确定性派生，**不信任 LLM 输出的 graph relation**）
  - `app/claim_relations.py`（结构判定确定性；`supersedes` 永不自动推断；语义判断 `analyze_relation` 按需、且仅为建议）
  - `app/knowledge.py::_locate_quote`（确定性定位阶梯）
  - `app/retrieval.py`（去重、证据升级）
- **TARGET**：继续保持，并在 `ClaimStateResolver` 中把生命周期裁决完整收归确定性程序。

## References

- `docs/standards/12-IMPLEMENTATION-MAP.md`
- `app/ontology.py`, `app/resolution.py`, `app/normalization.py`, `app/claim_relations.py`, `app/retrieval.py`
- [../development/CODING-STANDARD.md](../development/CODING-STANDARD.md) §3（LLM 编程规范）

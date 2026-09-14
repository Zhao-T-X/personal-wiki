# ADR-014 — Refusal Semantics: one definition, layered detectors

- Status: Accepted
- Date: 2026-09-14
- Implementation status: **CURRENT**
- Related: [ADR-013](./ADR-013-QUERY-ROUTING-AND-ZERO-LLM-LOOKUP.md), [ADR-010](./ADR-010-DETERMINISTIC-FIRST.md), [ADR-011](./ADR-011-KNOWLEDGE-COMPILATION-PIPELINE.md)

## Context

「这条答案算不算拒答」曾有两套实现：

* `domain/citation_validation.py`：宽松启发式 —— **无引用且长度 < 80 即当作拒答**；
* `evaluation/qa_eval.py`：严格标记契约 —— 必须带显式拒答短语。

两者对同一条答案可能给出相反结论。而拒答语义会一路影响 Golden Dataset、Quality Gate
与回归指标：一旦不一致，指标异常时无法判断是 Correction 错、QA 错、Citation 错，还是
evaluator 错。

更糟的是那条启发式的**业务含义是错的**：

```text
“苹果目前的 CEO 是 John Ternus。”      （正常回答，只是没引用）
旧行为：len < 80 且无引用  ->  拒答      ->  coverage 直接给 1.0
```

也就是说：**一个忘了引用的短回答，反而因为被当成拒答而免除了引用要求。**

## Decision

统一的是**定义**，不是**检测策略**：

```text
        RefusalKind               语义契约（唯一，domain/refusal.py）
             ↑
   classify_refusal()             结构化线索 → 标记词表
             ↑
   ┌─────────┴──────────┐
citation_validation      qa_eval
 (detector='runtime')   (detector='evaluation')
```

任何一种答案恰好归属于四类之一：

| kind | 含义 | 是不是安全停机 |
|---|---|---|
| `REFUSAL` | 拒绝回答（针对**回答者**的声明） | ✅ |
| `INSUFFICIENT_EVIDENCE` | 明确说明知识库没有足够依据（针对**数据**的声明） | ✅ |
| `NON_REFUSAL` | 给出了实质回答 | ❌ |
| `UNKNOWN` | 无法可靠判断（如空答案） | ❌ |

三条关键判定：

1. **`INSUFFICIENT_EVIDENCE ≠ REFUSAL`。** 前者正是 §23 想要的**高质量安全行为**，
   把它当成拒答会惩罚系统最值得鼓励的那条路径。二者只在「断言了没有」这一点上相同
   （`is_safety_stop`），语义上必须分开。数据层声明优先于回答者层声明
   （「我无法回答，因为知识库中没有足够证据」→ `INSUFFICIENT_EVIDENCE`）。
2. **没有引用不等于拒答。** 无引用的正常回答是 `NON_REFUSAL` + 引用不完整，交给
   Citation Validation 单独扣分，不再冒充拒答。因此 `coverage` 的豁免条件从
   「看起来短」收紧为**只有安全停机才豁免**。
3. **空答案不是拒答，是 `UNKNOWN`。** 它既没拒绝也没回答；把「没信号」当作拒答也是猜。

消费方保留各自的**策略**（运行时只想知道「这条答案是否需要引用」；评测需要的是数据集
契约），但「它是什么」永远只有一个答案，并在 `detector` 字段里标明是谁判的——这样将来
出现分歧可以归因到检测器，而不是归因到定义。

## Consequences

### Positive

- 运行时与评测对同一条答案给出**同一个 kind**，指标异常可直接归因。
- 短回答不再因为「短」而免除引用要求；`/api/qa/validate` 的 coverage 变得更诚实。
- `report.refusal` / `case.refusal_kind` 暴露语义判决，为 §37 看板留好接口。
- 空答案、知识不足、明确拒答、带 caveat 的正常回答，四种情形都有确定且可测的判据。

### Negative / Trade-offs

- **一处端点可见的变化**：`/api/qa/validate` 对「知识库中没有证据」这条回答，flags 由
  `refusal` 改为 `insufficient_evidence`（并新增 `refusal` 字段，评分不变）。按 kind 消费
  的 UI 需要跟进。
- 标记词表是**词面匹配**：`我无法确认这一点，但根据现有知识 X 是 CEO` 这类「边解释边回答」
  会被判为 `REFUSAL`。这是检测精度问题，不是定义问题——分层设计允许将来替换更精确的检测器。
- 词表需要维护；两种语言的表达覆盖不全时会落到 `NON_REFUSAL`（默认不作为拒答），
  偏保守，符合「宁可算作回答也不要错杀」。

## Current vs Target

- **CURRENT**
  - `app/domain/refusal.py`：`RefusalKind`、`RefusalDecision`（含 `detector` / `markers`）、
    `classify_refusal`，以及两份互不重叠的标记词表。
  - `domain/citation_validation.py`：`_is_refusal` 删除，改用共享判决；
    `coverage` 仅对 `is_safety_stop` 豁免；`CitationReport.refusal` 暴露判决。
  - `evaluation/qa_eval.py`：布尔 `is_refusal` 换成 `refusal_kind` / `is_safety_stop` /
    `refusal`；幻觉率定义为「无证据却 assert」即 `NON_REFUSAL`，`UNKNOWN` 不计入。
  - `tests/test_refusal_semantics.py`：5 个验收场景 + 结构线索 + 两消费方一致性。
- **TARGET**
  - 引入更精确的检测器（结构化线索 / 小模型辅助），但**必须**映射回同一 `RefusalKind`。
  - 把 `is_safety_stop` 推广到 AnswerValidation 的其他维度（groundedness / relevance）。

## References

- `app/domain/refusal.py`, `app/domain/citation_validation.py`, `app/evaluation/qa_eval.py`
- `app/main.py`（`_DIRECT_REFUSALS` 文案必须满足该契约）
- `tests/test_refusal_semantics.py`, `tests/test_citation_validation.py`,
  `tests/test_qa_evaluation.py`, `tests/test_ask_direct_api.py`
- 任务书 §23、§24、§37

# ADR-013 — Query Routing and Zero-LLM Fact Lookup

- Status: Accepted
- Date: 2026-09-14
- Implementation status: **PARTIAL**
- Related: [ADR-010](./ADR-010-DETERMINISTIC-FIRST.md), [ADR-011](./ADR-011-KNOWLEDGE-COMPILATION-PIPELINE.md), [ADR-012](./ADR-012-TWO-STAGE-EXTRACTION.md)

## Context

原来的问答是「一个问题 → 检索 → 塞进上下文 → 让模型读一遍再复述」。这对开放问题是对的，
但对**知识库里本来就有确定答案**的问题是一种浪费，而且模型复述时可能引入幻觉：

```text
问：苹果公司的 CEO 是谁？
知识库：已存 Claim「苹果公司 — CEO — 约翰·特努斯」，附 chunk 与 quote
旧做法：检索 → 组装上下文 → LLM 复述        （慢、贵、可能说错）
```

任务书 §19–§23 的要求：

```text
简单问题 → 0 LLM
中等问题 → 少量 LLM
复杂研究 → 多步骤 LLM
```

并且 §23 明确：知识库没有可靠依据时，宁可说 **NO_SUFFICIENT_EVIDENCE**，
也不要生成一个「看起来合理」的答案。

## Decision

问答先**路由**，再回答。路由是确定性的（ADR-010），四种去向：

```text
Question -> Query Router ┬ FACT_LOOKUP          0 LLM（唯一主语+唯一谓词+已有当前 Claim）
                         ├ STRUCTURED_REASONING  时序（上一任/以前）或多跳（A 的 B 的 C）
                         ├ EVIDENCE_SYNTHESIS    常规检索 + 生成
                         └ RESEARCH              开放性/前瞻性问题
```

配套四条规则：

1. **FACT_LOOKUP 必须是「唯一」的。** 需要唯一主语 + **恰好一个**已注册谓词候选 +
   存在当前 Claim。多个谓词竞争说明问题有歧义——那不是事实直查，不能靠猜一个谓词
   给出一个看起来确定的答案。
2. **直查不改写。** 0-LLM 的答案就是已存 Claim 的原文（`content` 或
   `subject predicate object`），不做润色。润色就是生成，而生成正是我们要避免的那一步。
3. **拒答优于猜测。** FACT_LOOKUP 命中后，只有以下情形返回 `NO_SUFFICIENT_EVIDENCE`：

   | reason | 含义 | 端点行为 |
   |---|---|---|
   | `ambiguous_multiple_current_claims` | 同一 `(subject, predicate)` 有多条当前 Claim | **拒答** |
   | `no_current_claim` | 只有 superseded 记录，没有当前值 | **拒答** |
   | `no_object_value` | 有当前 Claim 但没有 object | 回退检索+生成 |

   冲突与过时都是**知识状态**，不是可以四舍五入的噪声；而无 object 的当前 Claim，
   其答案可能以散文形式存在于文档里，才值得回退。
4. **「该谓词有直查路径」≠「有当前值」。** `direct_claim_available` 判定的是
   「存在非 rejected/archived 的 Claim 行」（含 superseded），而不是「存在当前 Claim」。
   这是刻意的：若按后者判定，superseded-only 会被直接路由到 EVIDENCE_SYNTHESIS 回退给模型——
   而检索会**保留**「无当前覆盖的 superseded claim」，模型就可能把**过时值当成现值**回答。
   先承认「这里有结构化知识，但它不是当前的」，才谈得上诚实拒答。
5. **拒答文案必须满足统一契约。** 拒答时 `citations` 必须为空，且文案必须包含
   `app/evaluation/qa_eval.py::REFUSAL_MARKERS` 中的标记；否则评测会把它计为
   unknown-answer hallucination。

`NO_SUFFICIENT_EVIDENCE` 是一等结果：拒答时 `citations` 必须为空、答案必须带显式拒答标记，
并且**不记录任何 model step**（`step_count = 0`），这样 §37 的 LLM 调用统计才是诚实的。

## Consequences

### Positive

- 简单事实查询 **0 次模型调用**：更快、更便宜，且不可能幻觉（答案就是库里的行）。
- 路由是纯函数，可完整单测；「为什么这样回答」有了确定性的解释（§26）。
- 歧义、冲突、无证据都有**诚实的出口**，而不是各自生成一段看似合理的文字。

### Negative / Trade-offs

- 路由质量直接决定体验：把本该直查的问题判成综合问题，只是退化到旧行为（可接受）；
  反过来把有歧义的问题误判为直查，才是真错——因此规则的顺序是保守的（歧义不直查）。
- 直查依赖「谓词候选唯一」，而候选来自确定性的 hint 匹配，覆盖不了所有自然表达；
  覆盖不到就回退，不会猜。
- 确定性答案没有自然语言润色，读起来比模型生成的句子生硬。

## Current vs Target

- **CURRENT**
  - `app/domain/query_router.py`：`QuerySignals` / `QueryPlan` / `classify`（纯函数），
    `has_temporal_intent` / `has_multi_hop_intent`（确定性标记）。
  - `app/workflows/ask_workflow.py`：`extract_signals`（实体最长匹配 + 已注册谓词候选）、
    `plan_question`、`try_direct_answer`（0 LLM，绝不抛异常）、`explain`。
  - `app/main.py::/api/ask`：先路由；直查命中返回 `direct=True` 且不记 model step；
    歧义返回拒答；缺失证据回退到原有检索+生成路径。响应新增
    `route` / `direct` / `llm_expected` / `status` / `answer_value`。
  - 评测：`app/evaluation/retrieval_eval.py`、`app/evaluation/qa_eval.py` +
    `tests/evaluation/{retrieval,qa}/golden.json`（Recall@k、Citation Coverage、
    Groundedness、Unknown Answer Hallucination ≈ 0）。
  - `tests/test_ask_direct_api.py`：端点级证明「0 次模型调用」与「歧义拒答」。
- **TARGET**
  - STRUCTURED_REASONING 与 RESEARCH 目前仍走同一条检索+生成路径，尚未有各自的专用流程。
  - 谓语候选唯一性依赖 hint 匹配；需要用真实问题集扩充 hint 词表。
  - 多跳问题的图式查询（Claim Evolution / Graph）尚未落地。
  - **「拒答」目前有两个判定**：`domain/citation_validation.py::_is_refusal`（宽松：
    无 citations 且长度 <80 也算拒答）与 `evaluation/qa_eval.py::is_refusal`（严格：
    必须带显式标记）。二者语义不同且各有用途，但应当收敛为**同一定义**，否则
    `/api/qa/validate` 与评测会对同一条回答给出不同结论。

## References

- `app/domain/query_router.py`, `app/workflows/ask_workflow.py`, `app/main.py`
- `app/evaluation/retrieval_eval.py`, `app/evaluation/qa_eval.py`
- `tests/test_query_router.py`, `tests/test_ask_workflow.py`, `tests/test_ask_direct_api.py`,
  `tests/test_qa_evaluation.py`
- 任务书 §19–§24、§26、§40

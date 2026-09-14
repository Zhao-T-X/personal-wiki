# ADR-012 — Two-Stage Extraction

- Status: Accepted
- Date: 2026-09-14
- Implementation status: **CURRENT**
- Related: [ADR-010](./ADR-010-DETERMINISTIC-FIRST.md), [ADR-011](./ADR-011-KNOWLEDGE-COMPILATION-PIPELINE.md), [ADR-008](./ADR-008-CONTEXT-RUNTIME-BOUNDARY.md)

## Context

抽取原本是「一批 chunk → 一次完整的抽取调用」。这有两个问题：

1. **为没有价值的 chunk 付费**：一段过渡文字、目录、引用列表里没有可抽取知识，
   却仍然要付一次完整的抽取 prompt（含 skill 合约与推理循环）。
2. **一次调用要决定太多**：模型在同一轮里既要判断「有没有知识」，又要判断「是哪一类」，
   还要抽出内容。判断与抽取耦合，浪费且不稳定。

任务书 §12 的要求：

```text
Document -> Chunk -> Pass 1: Lightweight Detection -> Pass 2: Targeted Extraction
```

## Decision

抽取按 batch 分两阶段：

- **Pass 1（Lightweight Detection）**：一个**极简** agent（无工具、无 skill catalogue、
  无 reference）只回答「这批 chunk 含哪几类知识」：
  ```json
  {"entities": true, "claims": true, "events": false, "ideas": false, "questions": false}
  ```
- **Pass 2（Targeted Extraction）**：仅当 Pass 1 点名了至少一类时才运行，并把这批
  「需要的类别」作为作用域提示传入；返还结果中未被点名的类别被清空（schema 结构保持不变）。

两条硬约束：

1. **检测失败必须降级，不得丢失批次。** Pass 1 遇到任何异常（无模型、网络、解析失败）
   都视为「作用域未知」，退回原来的单阶段完整抽取。
2. **两阶段都要可追溯。** Pass 1 记为独立的 runlog step（`detect_batch`），
   §26 的 Knowledge Trace 因此能看见「为什么这批跳过了抽取」。

## Consequences

### Positive

- 没有可抽取内容的 batch **完全不调用**抽取模型（Pass 2 被跳过）。
- Pass 2 的作用域变小，prompt 与输出都更聚焦，选择难度下降。
- Pass 1 用小 prompt + 无工具，单次成本远低于完整抽取 prompt。

### Negative / Trade-offs

- 有内容的 batch 多了一次 LLM 调用（Pass 1），token/延迟上升；收益取决于
  「空 batch / 总 batch」的比例。用 §16 的 Golden Set 与 runlog 的 token 统计校准。
- Pass 1 是一次额外的判断点，可能**漏判**（把有内容的 batch 判成空）。
  因此 Pass 1 的判定必须保守，且失败一律降级为「全抽」。

## Current vs Target

- **CURRENT**
  - `app/agents/extraction_agent.py`：`Detection`、`detect_structured`、`wanted_kinds`、
    `scope_hint`、`build_detection_agent`（无 toolkit 的最小 agent）。
  - `app/llm.py`：`_detect_one`（含降级）、`extract` 两阶段编排、`_restrict` 作用域裁剪。
  - `tests/test_runlog.py`：runlog 现记录 `detect_batch` + `extract_batch` 两步。
  - `tests/test_two_pass_extraction.py`：跳过 Pass 2、作用域裁剪、降级路径。
- **TARGET**
  - 用 runlog 的 token/延迟统计 + Golden Set 量化「空 batch 比例」，
    决定是否值得对短 chunk 直接走单阶段。
  - 将 Pass 1 的判定并入 Context Runtime 的预算规划。

## References

- `app/llm.py`, `app/agents/extraction_agent.py`
- `tests/test_two_pass_extraction.py`, `tests/test_runlog.py`
- 任务书 §12、§16、§26

# ADR-015 — Correction Full Loop, Claim Evolution and Predicate Registry v2

- Status: Accepted
- Date: 2026-09-14
- Implementation status: **CURRENT**
- Related: [ADR-005](./ADR-005-CLAIM-EVOLUTION.md), [ADR-011](./ADR-011-KNOWLEDGE-COMPILATION-PIPELINE.md), [ADR-013](./ADR-013-QUERY-ROUTING-AND-ZERO-LLM-LOOKUP.md), [ADR-014](./ADR-014-REFUSAL-SEMANTICS.md)

## Context

Phase 5 要验证的是一条**闭环**，不是一个页面：

```text
一句话 -> Intent -> Ontology Resolution -> Entity Resolution -> Candidate Retrieval
      -> Evolution Judgment -> Correction Plan -> 用户确认
      -> Operation -> 新 Claim + Evidence + Evolution Relation
      -> Current State -> QA 立即反映 -> Trace 可追
```

三个障碍挡在路上：

1. **没有合适的谓词。** 「CEO」这种稳定领域关系不在受控词表里，用 `is` / `defined_as`
   硬凑会把知识语义做错；而运行时**不允许**新增谓词（ADR-011）。
2. **`supersedes` 从不被推断。** `claim_relations` 有意只在单值谓词对象变化时报
   `contradicts` 交人裁决——因为它当时没有依据说「这是演进」。于是「换 CEO」只能变成
   一条待审冲突，沉默的旧值仍然是「当前」。
3. **`new_ceo` 无处安放。** 模型一定会发明这类词；旧流程只能要么拒收、要么污染词表。

## Decision

### 1. 扩词表是**设计时**行为（Registry v2）

运行时不能新增谓词；但 Ontology Maintainer 可以在评审后新增。区别是：
**运行时禁止动态创建，设计时允许显式演进。**

`schemas/claim-predicate-registry.json` 升到 `1.2`，谓词条目的语义完整化：

```json
"has_ceo": {
  "label": "首席执行官",
  "aliases": ["CEO", "chief executive officer", "首席执行官", "首席执行长", "行政总裁"],
  "domain": ["Organization"],
  "range": ["Person"],
  "functional": true,
  "temporal": true,
  "evolution": "supersedable"
}
```

语义要求（`Organization → Person`、单值、可演进）因此成为**数据**，不是散落在各处的
Python 常量（§31）。`PredicateSpec` 暴露 label/aliases/domain/range，别名索引由 registry 生成。

### 2. 候选 → 规范谓词（别名 + 时态）

```text
new_ceo
  -> split temporal: base="ceo", signal="new"
  -> registry alias: "ceo" -> has_ceo
  -> predicate=has_ceo, temporal_signal=new      （source="temporal_split"）
```

`new_ceo` **从不成为谓词**，但它也不再是失败：它是候选，编译后得到唯一规范关系。
`CEO` 直接命中 registry 别名；`random_relationship` 仍然 `UNRESOLVED`。

### 3. 演进判定（registry 驱动，确定性）

`claim_relations` 的保守默认没有改变——它仍然不给普通冲突扣「supersedes」的帽子。
新增的是**有依据就敢下结论**：

```text
single-valued(contradicts) + registry(functional ∧ temporal ∧ supersedable)
                            + 语句本身带变更信号（new/current/next/successor…）
        => 建议 SUPERSEDE
```

三者缺一不可，且全部来自有权威的地方（规划器 / registry / 陈述本身）。计划仍然是
**只读**的，用户确认后才写。

### 4. 计划阶段就做 domain/range 闸门

谓词能解析不代表用得合法：`Organization has_ceo Location` 必须被拒。计划阶段用同一个
编译器闸门检查，避免「用户确认后才发现组合非法」。

### 5. Claim 记录 `ontology_version`

每条 Claim 存下编译时的词表版本（`1.2`），并区别于 `registry_version()`（内容哈希，用于缓存失效）。
这样将来问「这条当初为什么能被识别、现在不能」时有据可查，而不是靠回忆。

### 6. 历史问题也是**已存知识**，同样 0 LLM

`supersede` 只移动生命周期状态，**从不删除**（ADR-005），所以「之前的 CEO 是谁」可以直接
从 superseded Claim 回答——这正是「演进真的打通了」的证明。过去时的直接查找由
`try_historical_answer` 承担，与当前值的直查对称；多个历史值时报安全停机而不是挑一个。

## Consequences

### Positive

- 「换 CEO」得到完整的演进链：新 Claim + Evidence + `supersedes` 关系 + 旧 Claim 保留为历史。
- QA 立刻反映变化：现在问得到新值、历史问得到旧值，都不需要模型。
- 扩词表有了可复制的样板：`has_founder` / `has_president` / `owned_by` 沿用同一机制。
- 运行时封闭性没有被削弱：`new_ceo` 可被解析，但**永远不可被创建**。

### Negative / Trade-offs

- 词表变更是**代码评审级**的动作，需要同步更新 registry 与测试；比「让模型随便写」慢。
- 别名是词面匹配：`president` 之类若未声明别名，仍然 `UNRESOLVED`（保守，不猜）。
- 演进判定依赖 LLM 给出变更信号；信号缺失时退回「普通冲突待审」，不会自作主张。
- `ontology_version` 只记录「编译时」，不回填历史 Claim（旧行为默认 NULL）。

## Current vs Target

- **CURRENT**
  - `schemas/claim-predicate-registry.json` v1.2 + `has_ceo`；
    `app/ontology.py`：`PredicateSpec`(label/aliases/domain/range)、`claim_predicate_alias_index`、
    `resolve_claim_predicate`、`claim_predicate_endpoint_allowed`、`claim_registry_version`。
  - `app/domain/predicate_resolver.py`：registry 别名 + 时态拆分。
  - `app/domain/compiler.py`：claim 谓词的 domain/range 闸门。
  - `app/domain/claim_evolution.py`：演进判定（`supersede_recommended`）。
  - `app/workflows/correction_workflow.py`：`intent_from_draft`（可离线回放的 Draft→Intent）、
    计划阶段 domain/range 闸门、supersede 建议。
  - `app/workflows/ask_workflow.py`：`try_historical_answer`；`app/main.py` 接线。
  - Claim 新增 `ontology_version` 列（含旧库迁移）。
  - 测试：`tests/test_correction_loop_e2e.py`（闭环 + 负向 A–E）、`tests/test_ontology_registry.py`。
- **TARGET**
  - 更丰富的演进判定（多跳、跨谓词、时间区间），而不是只看变更信号。
  - 让 Correction UI 直接展示 Trace（§26）；本 ADR 只保证数据链完整、可查。
  - 词表变更的自动化校验流程（当前以测试代替 lint）。

## References

- `schemas/claim-predicate-registry.json`, `app/ontology.py`
- `app/domain/{predicate_resolver,compiler,claim_evolution}.py`
- `app/workflows/{correction_workflow,ask_workflow}.py`, `app/main.py`, `app/db.py`
- `tests/test_correction_loop_e2e.py`, `tests/test_ontology_registry.py`
- 任务书 Phase 5

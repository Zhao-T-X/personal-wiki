# ADR-011 — Knowledge Compilation Pipeline

- Status: Accepted
- Date: 2026-09-14
- Implementation status: **PARTIAL**
- Related: [ADR-010](./ADR-010-DETERMINISTIC-FIRST.md), [ADR-002](./ADR-002-LAYERED-ARCHITECTURE.md), [ADR-009](./ADR-009-KNOWLEDGE-OPERATIONS.md)

## Context

LLM 会「发明词汇」。给它一个自由文本的 `predicate` 字段，它就会把同一个关系的时态 /
演化语义编码成新的谓词：

```text
CEO          -> ceo
new CEO      -> new_ceo
former CEO   -> former_ceo
serves as CEO-> serves_as_ceo
```

如果任由这些值流入知识库，本体（ontology）会持续膨胀，**同一个事实出现多个谓词**，
检索、冲突检测、图谱全部失真。这是一个**封闭性问题**，不是「prompt 写得不够严」的问题。

关键判断：

> **Prompt 只负责引导，真正的保证来自架构。**
> 让 LLM 尽量遵守，同时**即使 LLM 不遵守也无法造成破坏**。

因此不能把「不要乱造谓词」当成某个 Agent 的 prompt 措辞，而要把它变成系统级的编译管线。

## Decision

所有会产出本体取值（Entity Type / Predicate / Event Type / Relation Type / 枚举）的路径
（Extraction / Research / Correction / Curator / Review）**统一经过 Knowledge Compilation Pipeline**：

```text
LLM draft（只能产出 candidate 与 signal）
      ↓  ① Schema     机器可校验的结构约束（Pydantic Literal / JSON Schema）
      ↓  ② Registry   封闭词表约束（唯一合法取值来源）
      ↓  ③ Resolver   candidate → 已注册谓词 | AMBIGUOUS | UNRESOLVED
      ↓  ④ Domain     domain / range、claim_type / polarity / modality 约束
      ↓  ⑤ Compiler   CanonicalClaim（只有它能进入持久化）
      ↓  ⑥ Operation / Repository  落库（变更唯一入口）
```

配套的 **ONTOLOGY MUTATION POLICY**（系统级约束，唯一表述于
`app/domain/ontology_policy.py`）：

1. Predicate Registry is closed at runtime.
2. LLM cannot create predicates.
3. Agents cannot create predicates.
4. Workflows cannot create predicates.
5. Repository cannot create predicates.
6. Only explicit ontology maintenance may modify the registry.
7. Runtime knowledge generation may only reference existing predicates.
8. Unresolved predicates never become persisted knowledge.

三条硬性设计：

- **Candidate vs Canonical 命名分离。** LLM 产出的是 `ClaimDraft.predicate_candidate`，
  编译后才得到 `CanonicalClaim.predicate`。命名本身就阻止「字段即事实」的误解。
- **给 LLM 选择题，不给填空题。** 运行时只注入**任务相关的 registry 子集**
  （`match_claim_predicates`，确定性、无 LLM），绝不把全量 registry 塞进 prompt。
- **给 LLM 合法失败出口。** 允许 `unresolved`。若某个谓词无法映射到注册表，系统**如实报告并拒绝写入**，
  而不是逼模型造一个词。

时态 / 演化语义（new / current / former / previous / next / successor…）**不是谓词**，
必须进入 `temporal_signal`，与谓词分离：

```text
new CEO   -> predicate=has_ceo, temporal_signal=new
current CEO-> predicate=has_ceo, temporal_signal=current
former CEO-> predicate=has_ceo, temporal_signal=former
```

Resolver **不允许发明式映射**：`new` + 已注册词根可以机械拆分；候选是 registry 别名可以映射；
但 `head_of_company -> has_ceo` 这种「猜」在没有别名、没有已注册词根时**一律 UNRESOLVED**。

## Consequences

### Positive

- 本体封闭：任何 runtime 路径都无法扩展词表，`new_ceo` 这类污染在编译层被拦下。
- 可回归：解析、编译是纯函数，可用反幻觉本体测试覆盖（`tests/test_knowledge_compilation.py`）。
- 失败可解释：`unresolved` 携带候选与原因，人和上层都能看清「为什么没写进去」。
- 契约统一：新增任何 Agent 都不必再讨论「怎么保证谓词不乱造」。

### Negative / Trade-offs

- 需要维护 registry 与别名表；遇到合法但未注册的关系时，行为是**拒绝**而非尽力而为。
- 谓词原本是「自由 snake_case」，现在受限于受控词表，表达力以一致性为代价。
- 别名自动归一（如 `optimises -> improves`）会改变抽取的接受/拒绝边界，
  原先「未注册即拒绝」的契约收紧为「未注册 → 先解析，解析不到才拒绝」。

## Current vs Target

- **CURRENT（已落地）**
  - `app/domain/ontology_policy.py`：ONTOLOGY MUTATION POLICY（8 条）与封闭词表校验。
  - `app/domain/predicate_resolver.py`：RESOLVED / AMBIGUOUS / UNRESOLVED，时态信号分离，禁止发明式映射。
  - `app/domain/compiler.py`：`ClaimDraft → CanonicalClaim` 统一编译入口，含 domain/range。
  - Schema / Pydantic / `normalize_extraction` 支持 `temporal_signal` 与合法失败出口；
    抽取链路已改走 resolver。
  - 纠正流程（`app/workflows/correction_workflow.py`）改走编译器：谓词候选受控，无法映射时
    `blocked` 并如实说明；`apply_correction` 二次校验，任何路由都无法写入未注册谓词。
  - **Quality Gate 已接入编译器**（`app/domain/quality_gate.py`）：确定性信号权重（10–20 分/项）
    严格高于 LLM 自报置信度（5 分）；`reject` 能否决一条本体合法的 claim。
  - **抽取评测基建**（`app/evaluation/` + `tests/evaluation/extraction/golden.json`）：
    Entity/Claim Precision·Recall、Evidence Accuracy、OntologyViolationRate；
    当前 Golden Set 上 OntologyViolationRate = 0。
  - 谓词语义（`functional`/`temporal`）已回归 Registry（§31），并由边界测试守住。
- **PARTIAL / TARGET**
  - Extraction 之外的其他 Agent（Research / Curator / Review）尚未全部显式接入编译器。
  - 运行时「任务相关谓词子集」已用于纠正流程；抽取 Agent 的 prompt 注入仍为 TARGET。
  - `temporal_signal` 目前落在 Claim 的 `context` JSON 中，尚无独立列（见 ADR-001 演进）。
  - `AMBIGUOUS` 的人工/模型裁决回路尚未接入 UI。

## References

- `app/domain/ontology_policy.py`, `app/domain/predicate_resolver.py`, `app/domain/compiler.py`
- `app/extraction.py`, `app/agents/extraction_agent.py`, `app/knowledge.py`
- `app/workflows/correction_workflow.py`, `app/main.py`
- `schemas/extraction.schema.json`, `schemas/claim-predicate-registry.json`
- `skills/knowledge-extraction/SKILL.md`, `skills/knowledge-extraction/references/extraction-v2.md`
- `tests/test_knowledge_compilation.py`, `tests/test_registry_subset.py`

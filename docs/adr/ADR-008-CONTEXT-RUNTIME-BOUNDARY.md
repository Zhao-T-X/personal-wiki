# ADR-008 — Context Runtime Boundary

- Status: Accepted
- Date: 2026-09-14
- Implementation status: **CURRENT**
- Related: [ADR-007](./ADR-007-AGENTSCOPE-AS-RUNTIME-ADAPTER.md), [ADR-002](./ADR-002-LAYERED-ARCHITECTURE.md)

## Context

项目引入了 Context Runtime（P1–P4），负责把每次 LLM 调用从「Prompt + Everything」升级为 **Progressive Context Loading**：token 计量、预算、规划、编译、Trace、Agent 间 Task Packet、Context Cache。

问题：它属于 **Knowledge Domain** 还是 **执行基础设施**？

若把它当作领域的一部分，就会：

- 让「上下文如何装配」与「知识是什么」纠缠；
- 让 Domain 反向依赖预算/缓存等执行概念；
- 使领域规则被 token 预算这类非语义因素左右。

## Decision

> **Context Runtime 属于执行基础设施，不属于 Knowledge Domain。**

边界：

- **职责**：为一次 LLM 调用装配上下文——计算预算、决定 LOAD/SUMMARIZE/RETRIEVE_LATER/NEVER_LOAD、统计 token、记录 trace、缓存编译结果。
- **不做**：不决定知识正确性；不写知识表（trace / cache / packet 等自身表除外）；不裁决 current。
- **依赖方向**：Application / Agent 可使用 Context Runtime；**Domain 不得依赖 Context Runtime**。

组件（CURRENT）：budget / planner / compiler / manager / providers / trace / packet / cache / history / tokens / value。

## Consequences

### Positive

- 「每个 token 都有明确任务价值」的优化与知识语义解耦。
- Context 装配可独立观测与调优（trace / metrics / cache 命中率），不影响领域。
- Domain 保持纯粹，不被执行层策略牵制。

### Negative / Trade-offs

- Provider 生成上下文时需要引用领域概念（实体摘要、证据），需小心只依赖「读视图」而非修改领域。
- 缓存必须绑定版本（见下），否则会返回过期上下文。

## Current vs Target

- **CURRENT**：已落地。`app/context/` 提供 planner/compiler/providers/trace/packet/cache；`context_runs` / `context_sections` / `task_packets` / `conversation_summaries` / `context_cache` 落库；`compose_prompt` / `build_context` 集成。
- **版本失效**：Context Cache 键绑定 prompt 内容 + skill/reference 版本 + `ontology.registry_version()`（`schemas/*.json` 指纹）+ 历史/包状态；任一变化即新键，**失效按构造成立**。
- **TARGET**：维持边界；Provider 访问领域一律经 Facade 读视图。

## References

- `app/context/`（budget/planner/compiler/providers/trace/packet/cache/history/tokens/value）
- `docs/superpowers/specs/2026-09-11-context-runtime-design.md`
- [../architecture/ARCHITECTURE.md](../architecture/ARCHITECTURE.md) §5

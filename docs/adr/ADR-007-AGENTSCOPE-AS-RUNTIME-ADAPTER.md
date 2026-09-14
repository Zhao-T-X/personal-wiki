# ADR-007 — AgentScope as Runtime Adapter

- Status: Accepted
- Date: 2026-09-14
- Implementation status: **CURRENT**
- Related: [ADR-002](./ADR-002-LAYERED-ARCHITECTURE.md), [ADR-008](./ADR-008-CONTEXT-RUNTIME-BOUNDARY.md)

## Context

LLM-Wiki 使用 AgentScope 2.x 作为多智能体执行层（ReAct 循环、工具调用、Skill 加载）。存在一个根本问题：**AgentScope 是「执行框架」，还是「领域框架」？**

如果 Agent 直接持有领域规则与数据库访问，则：

- 领域规则泄漏进框架层，无法独立测试；
- 更换执行框架会波及业务；
- Agent 可能「直接决定」知识状态。

## Decision

> **AgentScope 是执行框架（Runtime Adapter），不是领域框架。**

具体规定：

1. Agent（Personal / Knowledge / Research / Curator / Review / Extraction）**只负责编排与调用**，通过工具与 Facade 访问系统能力。
2. Agent **不直连 SQLite**，**不实现领域规则**（不判断 predicate 合法性、不裁决 current）。
3. LLM 产出视为**不可信候选**，必须经 Domain 校验。
4. AgentScope 可被整体替换，领域层不受影响。

## Consequences

### Positive

- 执行框架与领域解耦，框架升级/替换风险可控。
- Agent 行为可通过提示词与 Skill 配置调整，而不改领域代码。
- 领域规则集中、可测。

### Negative / Trade-offs

- Agent 需要经 Facade/工具，多一层间接，需保证工具粒度合理。
- 迁移期存在 Agent 直接读表的旧路径（`app/tools/knowledge_tools.py`），需收敛。

## Current vs Target

- **CURRENT**：`app/agents/base.py` 用 AgentScope 构建 Agent；工具在 `app/tools/knowledge_tools.py`（`search_knowledge` / `get_entities` / `get_entity` / `get_entity_graph`）与 `skill_tools.py`；只读工具无条件允许（`_ALLOW`）。工具内部仍直接查库，属待收敛路径。
- **TARGET**：工具改为经 Facade 访问；Agent 侧不含领域判定。
- **MIGRATION**：随 Facade 建立逐步收敛（Phase 1–2）。

## References

- `app/agents/base.py`, `app/agents/*_agent.py`
- `app/tools/knowledge_tools.py`, `app/tools/skill_tools.py`
- [../architecture/ARCHITECTURE.md](../architecture/ARCHITECTURE.md) §6

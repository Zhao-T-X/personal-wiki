# TASK-DEVELOPMENT-AND-ACCEPTANCE — 任务生命周期与验收规范

这是本项目所有后续开发任务必须遵守的**统一开发与验收规范**。

目标不是"把功能写出来"，而是确保每一次开发都满足：实现正确、行为可验证、不破坏已有能力、边界清晰、可追踪、复杂度可控，并且最终确实提升产品价值。

从现在开始，任何新功能、Bug 修复、重构、性能优化、Agent、Workflow、API、前端页面、知识处理能力，都必须遵守本规范。

## 本文档与其他规范的关系

本目录已有的文档回答「**具体怎么写**」；本文档回答「**一个任务从需求到验收要经过什么、交付什么证据**」。两者不重叠：

| 主题 | 权威来源 |
|---|---|
| 编码总则、LLM 编程规范 | [CODING-STANDARD.md](./CODING-STANDARD.md) |
| 模块边界、依赖方向 | [MODULE-BOUNDARIES.md](./MODULE-BOUNDARIES.md) |
| 测试分层、Domain Invariant Tests | [TESTING-STANDARD.md](./TESTING-STANDARD.md) |
| 新功能的准入判定（九问） | [FEATURE-DEVELOPMENT.md](./FEATURE-DEVELOPMENT.md) |
| API / 错误 / 日志约定 | [API-STANDARD.md](./API-STANDARD.md) 等 |
| 设计决策的「为什么」 | [../adr/](../adr/README.md) |
| 知识 / 本体语义标准 | [../standards/](../standards/README.md) |

本文档引用它们时**不重复其内容**——每条规则只有一个权威来源（与第五条同构）。若发现本文档与权威来源冲突，以权威来源为准，并修正本文档。

---

# 一、任务生命周期

任何开发任务统一遵循：

```text
需求 → 方案 → 实现 → 自测 → 核心场景验证 → 回归测试 → 产品验收 → ACCEPTED
```

开发完成不能直接等于任务完成。

任务状态统一分为：

- **IMPLEMENTED**：代码已经完成
- **VALIDATED**：已经通过自动化测试和核心场景验证
- **ACCEPTED**：产品行为符合预期，并确认值得保留

未经 VALIDATED，不得声称任务已经完成。未经 ACCEPTED，不得声称该功能已经正式完成。

# 二、每个任务必须先明确范围

开发前必须明确：

## 1. Task

说明本次任务是什么。

## 2. Why

说明解决什么实际问题。

## 3. Scope

明确：本次要做什么、本次**不**做什么。

禁止为了"顺手"扩大任务范围。如果发现其他问题：

- 若阻塞当前任务，修复；
- 若不阻塞，记录为后续任务；
- 不允许无边界扩张。

# 三、实现原则：Deterministic First

凡是代码能够确定完成的事情，不允许交给 LLM。例如：Schema 校验、Enum 校验、Ontology Registry 匹配、Predicate 是否存在、Entity 类型检查、Domain/Range 校验、状态转换、权限判断、数据完整性检查、去重规则、时间格式、ID 处理、数据库约束。

LLM 负责：语义理解、信息提取、候选生成、复杂语义判断、需要自然语言理解的任务。但 **LLM 输出永远视为"不可信输入"**。

统一流程：

```text
LLM → Parse → Schema Validate → Ontology Resolve → Normalize → Entity Resolve
    → Domain Validate → Evidence Validate → Quality Gate → Operation → Repository
```

不能直接把 LLM 输出写进数据库。

> Canonical：[../adr/ADR-010-DETERMINISTIC-FIRST.md](../adr/ADR-010-DETERMINISTIC-FIRST.md)、[CODING-STANDARD.md §3](./CODING-STANDARD.md)、编译管线见 [../adr/ADR-011-KNOWLEDGE-COMPILATION-PIPELINE.md](../adr/ADR-011-KNOWLEDGE-COMPILATION-PIPELINE.md)。

# 四、架构约束

项目遵循 Presentation → Application → Domain → Infrastructure。

- **API**：参数接收、参数校验、调用 Workflow / Facade、返回 DTO。不允许直接操作 Repository。
- **Agent**：理解任务、组织上下文、调用 Tool / Workflow、生成候选结果。不允许直接访问 SQLite、直接修改 Domain 状态、自建业务规则、创建第二套 Ontology、绕过 Operation 修改知识。
- **Workflow**：编排完整业务流程。
- **Domain**：业务规则唯一归属。
- **Repository**：只负责持久化，不负责 LLM / Prompt / Agent / 产品业务判断。

> Canonical：[../adr/ADR-002-LAYERED-ARCHITECTURE.md](../adr/ADR-002-LAYERED-ARCHITECTURE.md)、[../adr/ADR-004-DOMAIN-AND-REPOSITORY-SEPARATION.md](../adr/ADR-004-DOMAIN-AND-REPOSITORY-SEPARATION.md)、[MODULE-BOUNDARIES.md](./MODULE-BOUNDARIES.md)。
>
> 现状说明：`main.py` 目前仍有直接查库的路径，这已作为 CURRENT→TARGET 差距记录在 [MODULE-BOUNDARIES.md](./MODULE-BOUNDARIES.md) 与 [../architecture/MIGRATION-PLAN.md](../architecture/MIGRATION-PLAN.md)。新代码按目标写，存量按迁移计划渐进修正，不得以「现状如此」为由在新代码里复制旧模式。

# 五、禁止出现第二套业务规则

任何规则必须只有一个 canonical implementation。特别注意：Predicate Registry、Entity Type Registry、Event Type Registry、Relation Type、Claim State、ClaimStateResolver、Ontology Resolver、Evidence validation、Entity resolution、Quality Gate。

禁止在 Agent、API、UI、Repository、Workflow 中复制一份类似逻辑。如果已经存在 canonical implementation，必须复用；如果发现重复逻辑，优先收敛，不要继续复制。

> Canonical：[../adr/ADR-011-KNOWLEDGE-COMPILATION-PIPELINE.md](../adr/ADR-011-KNOWLEDGE-COMPILATION-PIPELINE.md)、`app/domain/claim_state.py`（current/history/resolve）、[../standards/03-CLAIM-PREDICATE-REGISTRY.md](../standards/03-CLAIM-PREDICATE-REGISTRY.md)。`tests/test_architecture.py` 用 import 规则 + 行为对照把「唯一实现」变成可执行的检查。
>
> 例外规则：允许在 canonical 之**旁**存在一个更窄的判定，但它必须回答一个不同的问题（例如「能否作为答案陈述」不同于「是否被取代」）、有文档说明、且被测试钉住。无文档、无测试的旁路判定即违规。

# 六、Ontology 是运行时封闭的

运行时 LLM / Agent 只能使用已有 Registry 中的标准。禁止自动创建 Predicate、Entity Type、Event Type、Relation Type，禁止自动扩展 Enum。

例如"新任 CEO"不能生成 `new_ceo`，而应解析为已有的 `has_ceo`；"新任 / 现任 / 前任"作为时间、状态与演化语义处理。只有项目开发阶段维护 Ontology Registry 时，才允许新增 Ontology。

> Canonical：[../adr/ADR-011-KNOWLEDGE-COMPILATION-PIPELINE.md](../adr/ADR-011-KNOWLEDGE-COMPILATION-PIPELINE.md)、[../standards/03-CLAIM-PREDICATE-REGISTRY.md](../standards/03-CLAIM-PREDICATE-REGISTRY.md)、[../standards/14-CLAIM-EVOLUTION-STANDARD.md](../standards/14-CLAIM-EVOLUTION-STANDARD.md)。

# 七、知识修改必须可追踪

任何知识变更都必须有：Operation、Actor、Timestamp、Before、After、Reason、Evidence（适用时）、Trace / Audit。禁止静默修改知识。

特别是 Correction、Merge、Supersede、Contradiction resolution、Research Candidate adoption 都必须可追踪。

> Canonical：[../adr/ADR-009-KNOWLEDGE-OPERATIONS.md](../adr/ADR-009-KNOWLEDGE-OPERATIONS.md)、[../architecture/KNOWLEDGE-OPERATIONS.md](../architecture/KNOWLEDGE-OPERATIONS.md)。

# 八、历史知识不得被静默覆盖

新 Claim 不直接覆盖旧 Claim。必要时通过关系表达：`duplicate` / `coexists` / `supersedes` / `contradicts` / `unclear`。当前知识由 Claim State + Claim Relations + ClaimStateResolver 共同决定。

任何代码需要判断"这个 Claim 当前是不是有效知识"，必须优先使用 ClaimStateResolver。禁止自己写一套 `if claim.status == ...` 来替代 canonical resolver。

> Canonical：[../adr/ADR-005-CLAIM-EVOLUTION.md](../adr/ADR-005-CLAIM-EVOLUTION.md)、[../standards/14-CLAIM-EVOLUTION-STANDARD.md](../standards/14-CLAIM-EVOLUTION-STANDARD.md)、`app/domain/claim_state.py`。展示用词（当前 / 待确认 / 有争议 / 历史）的唯一来源是 `app/readmodels/knowledge_view.py`。

# 九、Evidence 必须是一等公民

重要知识不能只有 Subject / Predicate / Object，还要能追溯"这条知识来自哪里"。任何涉及 Extraction、Research、QA、Correction、Knowledge adoption 的功能，都必须考虑 Evidence / Provenance。

不能出现："AI 说这是事实，但找不到依据。"

> Canonical：[../adr/ADR-006-EVIDENCE-AS-FIRST-CLASS-DOMAIN.md](../adr/ADR-006-EVIDENCE-AS-FIRST-CLASS-DOMAIN.md)、[../standards/09-EVIDENCE-PROVENANCE-STANDARD.md](../standards/09-EVIDENCE-PROVENANCE-STANDARD.md)。

# 十、开发任务必须有 Golden Scenario

每一个有业务行为的任务，至少提供一个完整的 Golden Scenario：

```text
## Input     用户输入或者系统输入
## Expected  明确期望行为
## Actual    实际运行结果
## Result    PASS / FAIL
```

例如：

- **Input**: `苹果 CEO 是 John Ternus`
- **Expected**: 找到已有 Apple entity；找到已有 has_ceo predicate；生成新 claim；旧 claim 不直接删除；正确建立 claim evolution；QA 返回最新状态；全过程可审计。

不得只测试 HTTP 200，必须验证最终业务结果。

# 十一、每个功能必须有 Negative Case

任何新增能力必须至少有负面场景。不能只测试"正常情况"。

| 场景 | Expected |
|---|---|
| 未知 Predicate（`new_ceo`） | 不能落库 |
| 歧义 Entity（`Apple` 存在多个候选） | 不能自动选择 |
| 证据不足（没有可靠 Evidence） | 不能生成确定性知识 |
| Merge（明显不是同一个 Entity） | 不能自动 Merge |
| One Box（`苹果。`） | 不猜测意图，进入澄清流程 |

# 十二、必须做 Regression Test

新功能不是只测试自己。每次开发必须确认没有破坏已有能力，至少检查 API、Workflow、Domain、Repository、Agent、UI、数据库行为。

如果原功能行为发生变化，必须明确写出 **Breaking Change**：什么变了、为什么变、是否故意、谁受到影响。禁止静默破坏原有接口。

# 十三、测试分层

测试至少分为三层，与 [TESTING-STANDARD.md](./TESTING-STANDARD.md) 一致：

- **Unit Test**：Domain Rule、Resolver、Validator、Normalizer、Registry、State Resolver
- **Integration Test**：SQLite、Repository、Workflow、LLM provider、Embedding、Agent adapter
- **E2E Test**：完整用户路径（Import → Extraction → Knowledge → Search → QA → Correction → Re-query）

不能只依赖 Unit Test。

# 十四、数据库变更要求

任何 Schema 变化必须：可迁移、可初始化、有测试、不破坏已有数据库。禁止直接修改初始化 SQL 后就认为数据库功能完成。

至少验证：① fresh DB；② existing DB migration；③ real data。

> 项目现行的做法见 `app/db.py`：fresh DB 用当前 Schema，旧库在启动时原地迁移，两个路径都有测试覆盖。

# 十五、前端开发要求

前端不是只检查"页面能不能打开"，必须验证真实用户路径。例如：进入知识库 → 搜索知识 → 查看知识 → 查看依据 → 修改知识 → 确认 → 再次搜索，必须保证完整闭环。

UI 原则：简单、人类可理解、不暴露内部实现、不让用户理解 Agent / Workflow / Operation 才能使用产品。UI 只展示业务语义。

# 十六、不要泄漏内部 identifier

Predicate 没有 human-readable label 时，**不能直接显示** `has_ceo`——应该不显示，或采用明确的产品语义表达。禁止把数据库字段直接暴露给用户。

同样包括：claim_id、entity_id、operation_id、internal status、agent name、workflow name——除非 Developer Mode 明确需要。

> Canonical：`app/readmodels/knowledge_view.py`（label 缺失时返回 `None`，让 UI 无法拼出标识符）、[../adr/ADR-011-KNOWLEDGE-COMPILATION-PIPELINE.md](../adr/ADR-011-KNOWLEDGE-COMPILATION-PIPELINE.md)。

# 十七、不要为了架构而架构

任何新增（Agent / Service / Workflow / Repository / Table / Component / API）之前必须问：**已有能力为什么不能完成？** 只有以下情况才允许新增：

1. 新的明确业务能力；
2. 新的独立生命周期；
3. 新的边界；
4. 新的持久化语义；
5. 明确解决已有复杂度问题。

不能为了"看起来更专业"新增抽象。与 [FEATURE-DEVELOPMENT.md](./FEATURE-DEVELOPMENT.md) 的九问是同一件事在任务维度上的表述。

# 十八、性能优化必须有数据

如果声称"性能更好了"，必须提供 Before / After，至少选择适合当前问题的数据：latency、P50、P95、SQL count、LLM call count、tokens、memory、embedding calls、network calls。

没有测量，就不能宣称性能提升。

# 十九、AI 功能不能靠"感觉更准确"

涉及 Extraction、QA、Research、Correction、Entity Resolution、Ontology Resolution 的改动，必须提供可验证样本：至少 Positive cases + Negative cases + Edge cases。对于可以统计的能力，逐步建立 Precision、Recall、Error Rate、Unsupported Claim Rate、Evidence Accuracy、Ontology Violation Rate。

禁止使用"看起来更智能 / 应该更准确 / 感觉不错"代替验证。

# 二十、LLM Token 优化必须以准确性不下降为前提

目标不是"少调用一次 LLM"，而是"删除不必要的 LLM 调用，同时保证质量不下降"。

可考虑的方向：deterministic 化、复用已有结果、缓存、先检索后加载、缩小上下文、Progressive Loading、Skill / Reference、只加载相关 Ontology subset。优化后必须做回归验证。

# 二十一、Skill / Prompt / Reference 分工

Prompt 负责当前任务、输出约束、少量核心规则；Skill 负责专门能力、操作方法、检查步骤；Reference 负责 Ontology Standard、Registry、Schema、详细标准；Context 负责当前任务相关上下文。禁止把整个 Ontology / Standard 全部塞进每一个 Prompt。

> Canonical：[../standards/13-AGENT-PROMPT-SKILL-STANDARD.md](../standards/13-AGENT-PROMPT-SKILL-STANDARD.md)、Context Runtime 规格 [../superpowers/specs/2026-09-11-context-runtime-design.md](../superpowers/specs/2026-09-11-context-runtime-design.md)。

# 二十二、所有 mutation 必须经过统一业务入口

知识写入必须经过 canonical pipeline：KnowledgeCompiler → Quality Gate → KnowledgeOperation → Repository。

不能出现多个地方分别 `Agent → DB`、`API → DB`、`Workflow → DB`、`Script → DB`，然后各自实现一遍规则。

> Canonical：[../adr/ADR-009-KNOWLEDGE-OPERATIONS.md](../adr/ADR-009-KNOWLEDGE-OPERATIONS.md)、[../architecture/KNOWLEDGE-OPERATIONS.md](../architecture/KNOWLEDGE-OPERATIONS.md)。

# 二十三、高风险操作必须二阶段

以下操作不能自动完成：Entity Merge、高风险 Claim Conflict Resolution、大规模删除、高风险知识修正。

统一流程：**Detect → Preview → Confirm → Apply → Recheck → Surface Issues**。不能因为"LLM 很确定"就跳过用户确认。

> 现成实现：`app/integrity.py`（merge-impact 预览 → 用户确认 → 合并后复检 → issues 报告）。

# 二十四、开发完成后必须做代码清理

任务结束时必须检查：dead code、duplicate logic、stale constants、unused imports、temporary workaround、debug code、TODO、duplicated Registry、duplicated business rule。尤其检查历史代码是否还存在旧逻辑。

**"新逻辑已经实现"不代表"旧逻辑已经删除"。**

# 二十五、每个任务最终必须提交统一验收报告

开发完成后必须按以下模板输出验收报告：

```markdown
## Task              本次做了什么
## Why               解决什么问题
## Changes           修改了哪些文件 / 模块
## Core Scenario     核心场景
## Expected          预期
## Actual            实际
## Tests             Unit / Integration / E2E
## Regression        Before / After / Failures
## Negative Cases    关键失败场景
## Boundary          Does / Does NOT
## Performance       Before / After（未测试须明确说明，禁止声称提升）
## Product Impact    用户操作是否减少 / 产品能力是否增强 / 认知负担是否降低
## Known Limitations 当前已知问题
## Architecture Impact  新增模块 / API / DB 表 / Agent；修改 Domain / Registry
## Risk              LOW / MEDIUM / HIGH
## Status            IMPLEMENTED / VALIDATED / ACCEPTED
```

# 二十六、任务完成的最低标准

一个任务只有同时满足以下条件，才能进入 VALIDATED：

- [ ] 代码完成
- [ ] 正常场景通过
- [ ] Negative Case 通过
- [ ] 核心业务场景通过
- [ ] Regression Test 通过
- [ ] 没有绕过 Domain Rule
- [ ] 没有出现第二套业务规则
- [ ] LLM 输出经过 Schema / Domain Validation
- [ ] Mutation 可审计
- [ ] 没有明显 dead code / duplicated logic
- [ ] 已知限制已经记录

涉及 UI 时增加：用户完整路径通过。
涉及 AI 时增加：Positive Case / Negative Case / Edge Case。
涉及数据库时增加：Fresh DB / Existing DB / Migration / Real Data。

# 二十七、最重要的开发原则

以后所有开发都遵守下面这句话：

> **不以"代码写完"为完成标准，以"功能可验证、旧功能不回归、边界清楚、结果可追踪、产品确实变好"为完成标准。**

> **不为了减少代码而减少代码，不为了减少 LLM 而减少 LLM，不为了增加架构而增加架构。所有技术决策最终服务于产品质量。**

> **能确定的事情交给代码，需要理解的事情交给 LLM，需要验证的事情必须验证，需要用户决定的事情不能替用户决定。**

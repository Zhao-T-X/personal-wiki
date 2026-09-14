# Architecture

LLM-Wiki 是一个 **local-first、LLM-native** 的个人知识系统：原文永不被结构化知识覆盖，一切断言携带可定位证据，LLM 只做语义判断、程序决定状态。

本目录描述系统的**长期架构**、**模块边界**、**公共入口**与**扩展方式**。它是代码审查时判断「这个改动放对地方了吗」的第一依据。

---

## 文档定位（不要混淆）

| 目录 | 回答的问题 | 生命周期 |
|---|---|---|
| `docs/architecture/` | 系统怎么分层、边界在哪、怎么扩展 | 长期稳定 |
| `docs/development/` | 代码怎么写、API/测试/错误/日志规范 | 长期稳定 |
| `docs/adr/` | **为什么**这样设计（决策记录，不改写） | 追加式，不可变 |
| `docs/standards/` | 知识 / 本体 / 抽取的语义标准 | 随本体演进 |
| `docs/superpowers/specs/` | 单次功能的设计规格 | 随功能交付冻结 |

---

## Architecture Principles

1. **Domain First** — 业务规则属于 Domain，不属于 API / Agent / 数据库。
2. **Deterministic First** — 能用程序确定的事，不交给 LLM。
3. **Evidence First** — 一切知识断言必须可回溯到原文偏移与引用。
4. **Single Source of Truth** — 原文是唯一事实来源；知识是派生视图，不是覆盖。
5. **Public Facade First** — 外部只能经公共入口访问系统能力，不得直连内部。
6. **Repository Only Persists** — 持久层只负责读写，不承载业务规则。
7. **Agent Does Not Own Domain Logic** — Agent 是执行器，不是领域规则的拥有者。
8. **Workflow Represents Use Cases** — 一个用例对应一条 Workflow 编排，不是散落的 endpoint。
9. **Operations Are Auditable** — 每一次知识变更都有操作记录，可追踪、必要时可回滚。
10. **Extension Over Modification** — 优先注册式扩展，而不是不断修改中心分支。

---

## Documents

| 文档 | 内容 |
|---|---|
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 总体架构：四层模型与依赖方向 |
| [LAYERING.md](./LAYERING.md) | 分层定义、依赖规则、现有模块归属 |
| [PUBLIC-ENTRYPOINTS.md](./PUBLIC-ENTRYPOINTS.md) | 五个公共 Facade 与「禁止绕过」红线 |
| [DOMAIN-MODEL.md](./DOMAIN-MODEL.md) | 核心领域模型：Document→…→Claim Evolution |
| [KNOWLEDGE-OPERATIONS.md](./KNOWLEDGE-OPERATIONS.md) | 八类知识操作与不变量 |
| [EXTENSION-POINTS.md](./EXTENSION-POINTS.md) | 允许注册的扩展点 |
| [MIGRATION-PLAN.md](./MIGRATION-PLAN.md) | 从当前实现到目标架构的 `CURRENT → TARGET` 迁移矩阵 |

---

## 状态标记约定

本文档体系处于 **v0.2 架构治理**阶段。当前代码是 v0.1，**并未全部符合**本规范。因此每篇文档都必须区分：

- **CURRENT** — 当前代码已具备的能力与真实位置。
- **PARTIAL** — 部分具备，仍有关键缺口。
- **TARGET** — 目标架构，尚未实现，禁止描述为既成事实。
- **MIGRATION** — 从 CURRENT 到 TARGET 的路径（见 [MIGRATION-PLAN.md](./MIGRATION-PLAN.md)）。

> 规则：任何文档都不得把 TARGET 写成 CURRENT。新增功能若跨越 CURRENT/TARGET 边界，必须同步更新迁移矩阵。

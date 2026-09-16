# LLM-Wiki 文档

本目录是 LLM-Wiki 的文档总入口。文档按**职责**分区，彼此不重叠。

## 分区总览

| 目录 | 回答的问题 | 性质 |
|---|---|---|
| [architecture/](./architecture/README.md) | 系统怎么分层、边界在哪、怎么扩展 | 架构规范（长期） |
| [development/](./development/README.md) | 代码怎么写、API/测试/错误/日志规范、任务怎么验收 | 开发规范（长期） |
| [adr/](./adr/README.md) | **为什么**这样设计 | 决策记录（追加式） |
| [standards/](./standards/README.md) | 知识 / 本体 / 抽取的语义标准 | 语义契约 |
| [superpowers/specs/](./superpowers/specs/) | 单次功能的设计规格 | 随功能冻结 |
| [archive/](./archive/README.md) | v0.1 时代的历史文档（仅供溯源） | 已归档 |
| [prototype/](./prototype/) | 原型 | 参考 |

---

## 我该看哪个？

| 你的问题 | 看这里 |
|---|---|
| 这个改动应该放在哪一层？ | [architecture/LAYERING.md](./architecture/LAYERING.md) |
| 能不能绕过 Facade 直接查库？ | [architecture/PUBLIC-ENTRYPOINTS.md](./architecture/PUBLIC-ENTRYPOINTS.md) |
| 领域模型里 Claim / Evidence 是什么？ | [architecture/DOMAIN-MODEL.md](./architecture/DOMAIN-MODEL.md) |
| 修改知识为什么不能直接 UPDATE？ | [architecture/KNOWLEDGE-OPERATIONS.md](./architecture/KNOWLEDGE-OPERATIONS.md) |
| 新增能力怎么扩展？ | [architecture/EXTENSION-POINTS.md](./architecture/EXTENSION-POINTS.md) |
| 当前代码和目标架构差多少？ | [architecture/MIGRATION-PLAN.md](./architecture/MIGRATION-PLAN.md) |
| 写代码的十条总则？ | [development/CODING-STANDARD.md](./development/CODING-STANDARD.md) |
| LLM 输出的处理纪律？ | [development/CODING-STANDARD.md](./development/CODING-STANDARD.md) §3 |
| API / 错误 / 日志约定？ | [development/API-STANDARD.md](./development/API-STANDARD.md) 等 |
| 新功能开发流程？ | [development/FEATURE-DEVELOPMENT.md](./development/FEATURE-DEVELOPMENT.md) |
| 一个任务怎样才算完成、验收报告怎么写？ | [development/TASK-DEVELOPMENT-AND-ACCEPTANCE.md](./development/TASK-DEVELOPMENT-AND-ACCEPTANCE.md) |
| 有哪些必须守住的测试？ | [development/TESTING-STANDARD.md](./development/TESTING-STANDARD.md) |
| 某个设计的来龙去脉？ | [adr/](./adr/README.md) |

---

## 架构与开发规范（v0.2 治理）

本次固化的规范体系区分 **CURRENT / TARGET / MIGRATION**：

> 当前代码是 v0.1，**并未全部符合**规范。任何文档都不得把 TARGET 写成既成事实。

- 架构原则十条：[architecture/README.md](./architecture/README.md)
- 开发红线九问：[development/FEATURE-DEVELOPMENT.md](./development/FEATURE-DEVELOPMENT.md) §5
- 迁移矩阵：[architecture/MIGRATION-PLAN.md](./architecture/MIGRATION-PLAN.md) §1

---

## 既有文档

- [standards/](./standards/README.md) — 知识标准（14 篇）
- [superpowers/specs/2026-09-11-context-runtime-design.md](./superpowers/specs/2026-09-11-context-runtime-design.md) — Context Runtime 设计规格（P1–P4）
- [superpowers/specs/](./superpowers/specs/) — 其他设计规格（frontend-redesign / llm-run-records / pkos-redesign）
- [archive/](./archive/README.md) — **v0.1 时代的历史文档**（TECHNICAL-DESIGN / PROCESSING-PIPELINE / IMPLEMENTATION-PLAN / ORIGINAL-\* / API-EXAMPLES / README-IMPLEMENTATION）。已被现行架构、标准与根 README 取代，仅供溯源。

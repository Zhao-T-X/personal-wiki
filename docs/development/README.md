# Development

本目录是 LLM-Wiki 的**开发规范**：代码怎么写、边界怎么划、API / 错误 / 日志 / 测试怎么统一，以及一个任务从需求到验收要经过什么。

与 [../architecture/](../architecture/README.md) 的分工：

- `architecture/` 回答「系统长什么样、边界在哪」。
- `development/` 回答「照什么规矩写代码」。

---

## Documents

| 文档 | 内容 |
|---|---|
| [TASK-DEVELOPMENT-AND-ACCEPTANCE.md](./TASK-DEVELOPMENT-AND-ACCEPTANCE.md) | **统一任务生命周期与验收规范**：IMPLEMENTED / VALIDATED / ACCEPTED、Golden Scenario、Negative Case、回归、验收报告模板 |
| [CODING-STANDARD.md](./CODING-STANDARD.md) | 十条编码总则 + LLM 编程规范 |
| [MODULE-BOUNDARIES.md](./MODULE-BOUNDARIES.md) | 模块边界与跨模块调用规则 |
| [API-STANDARD.md](./API-STANDARD.md) | HTTP API 约定（URL / 方法 / 错误） |
| [ERROR-HANDLING.md](./ERROR-HANDLING.md) | 错误分类、异常映射、失败不污染 |
| [LOGGING-STANDARD.md](./LOGGING-STANDARD.md) | 日志与运行记录（runlog）规范 |
| [TESTING-STANDARD.md](./TESTING-STANDARD.md) | 测试分层 + Domain Invariant Tests |
| [FEATURE-DEVELOPMENT.md](./FEATURE-DEVELOPMENT.md) | 新功能开发流程与准入问题 |

---

## 规范的地位

- 本目录是**约束**，不是建议。PR / 改动必须符合。
- 任务是否算完成，由 [TASK-DEVELOPMENT-AND-ACCEPTANCE.md](./TASK-DEVELOPMENT-AND-ACCEPTANCE.md) 的状态定义（IMPLEMENTED / VALIDATED / ACCEPTED）与最低标准决定；各专项规范回答「怎么写才算符合」。
- 与 `docs/standards/`（知识语义标准）冲突时：语义正确性优先，随后修正本目录或代码。
- 现状与规范不一致时，按 [../architecture/MIGRATION-PLAN.md](../architecture/MIGRATION-PLAN.md) 渐进修正，**不得以「现状如此」为由拒绝规范**。

---

## 规范的地位

- 本目录是**约束**，不是建议。PR / 改动必须符合。
- 与 `docs/standards/`（知识语义标准）冲突时：语义正确性优先，随后修正本目录或代码。
- 现状与规范不一致时，按 [../architecture/MIGRATION-PLAN.md](../architecture/MIGRATION-PLAN.md) 渐进修正，**不得以「现状如此」为由拒绝规范**。

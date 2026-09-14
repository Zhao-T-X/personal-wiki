# FEATURE-DEVELOPMENT — 新功能开发流程

> 状态：**规范**。任何新功能都必须走这条流程，目的是防止项目重新变成「Agent 越加越多、路由越来越杂」。

## 1. 开发流程

```text
需求
 ↓
Domain 是否变化？
 ↓
已有 Facade 是否可以复用？
 ↓
是否需要 Operation？
 ↓
是否需要 Workflow？
 ↓
是否需要 API？
 ↓
是否需要 Agent？
 ↓
是否需要 Schema / Registry？
 ↓
Tests
 ↓
ADR（如果改变架构）
```

**顺序很重要**：越靠上的问题越先回答。绝大多数需求应该在「Facade 复用」或「新增 Operation」处就结束，**轮不到新增 Agent**。

---

## 2. 逐项判定

### Q1 Domain 是否变化？

- 若引入新的知识语义 / 不变量 / 状态 → 属 Domain 变更，需改 `docs/standards/` 与对应模块，并加不变量测试。
- 若只是新的编排方式 → 不进 Domain。

### Q2 已有 Facade 是否可以复用？

先看 [../architecture/PUBLIC-ENTRYPOINTS.md](../architecture/PUBLIC-ENTRYPOINTS.md)。能复用就**扩展现有 Facade**，不新增入口。

### Q3 是否需要 Operation？

凡涉及知识**变更**（修正 / 合并 / 取代 / 归档…）→ 必须实现为一个注册式 Operation（见 [../architecture/KNOWLEDGE-OPERATIONS.md](../architecture/KNOWLEDGE-OPERATIONS.md)），而不是新的 UPDATE 或特殊 endpoint。

### Q4 是否需要 Workflow？

一个新的**用例**（跨多步、有编排顺序）→ 新增 Workflow，编排 Facade。
简单的单步读写不需要 Workflow。

### Q5 是否需要 API？

只有当能力需要暴露给 UI / 外部时才加 API，并遵循 [API-STANDARD.md](./API-STANDARD.md)。

### Q6 是否需要 Agent？

**准入门槛（硬性）**：

> 必须说明**为什么现有 Agent + Workflow + Operation 无法完成**。

只有当职责边界确实不同、且无法用现有角色组合表达时才新增 Agent。否则应新增 Workflow / Operation / Tool 或扩展提示词。

### Q7 是否需要 Schema / Registry？

新的本体类型 / 谓词 → 改 `schemas/*.json` registry（并注意会改变 `registry_version()`，使 Context Cache 自动失效）。**不要**在代码里硬编码类型分支。

### Q8 Tests

按 [TESTING-STANDARD.md](./TESTING-STANDARD.md) 补齐分层测试；涉及领域规则必须有不变量测试。

### Q9 ADR

若改变了架构决策（分层 / 入口 / 演进 / 边界），必须新增 ADR，见 [../adr/](../adr/README.md)。

---

## 3. 文档位置

| 产出 | 放哪 |
|---|---|
| 新功能设计规格 | `docs/superpowers/specs/YYYY-MM-DD-<name>-design.md` |
| 架构决策 | `docs/adr/ADR-xxx-*.md` |
| 知识语义标准变更 | `docs/standards/` |
| 架构/开发规范变更 | `docs/architecture/` 或 `docs/development/` |

---

## 4. 反模式

```text
❌ 直接往 main.py 加 endpoint（绕过 Facade）
❌ 直接 UPDATE 覆盖知识历史（跳过 Operation）
❌ 为每个新需求加一个 Agent
❌ 在代码里硬编码本体类型/谓词（绕过 Registry）
❌ 让 LLM 决定 current / superseded（越过 Domain）
❌ 新功能不带不变量测试
```

---

## 5. 提交前红线自查

```text
□ 是否属于正确的 Layer？
□ 是否绕过了公共 Facade？
□ 是否重复实现已有 Domain Rule？
□ 是否让 Agent 直接决定 Domain State？
□ 是否让 Repository 承担业务逻辑？
□ 是否破坏 Evidence？
□ 是否绕过 Operation？
□ 是否需要 ADR？
□ 是否有 Domain Invariant Test？
```

**任一项答案不合理，就不应合并。**

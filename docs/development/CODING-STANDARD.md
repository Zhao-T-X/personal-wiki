# CODING-STANDARD — 编码总则

> 状态：**规范**。适用于所有新增与修改代码。与 [../architecture/](../architecture/README.md) 配套。

## 1. 十条总则

```text
1.  Domain first               业务规则属于 Domain，不属于 API / Agent / DB
2.  One business rule, one implementation
                               同一规则只有一个实现，禁止多处复制判定
3.  No cross-layer penetration 依赖只能向下，禁止跨层
4.  Repository only persists   持久层只读写，不含业务规则
5.  Agent does not implement domain rules
                               Agent 只编排与调用，不实现领域规则
6.  LLM output is untrusted input
                               LLM 产出视为不可信输入
7.  Every external boundary must validate
                               每个外部边界必须校验（API / 文件 / LLM）
8.  Every mutation must be auditable
                               每次变更必须可审计
9.  Prefer extension over modification
                               优先注册式扩展而非改中心逻辑
10. Prefer deterministic code over LLM reasoning
                               能用程序确定，不交给 LLM
```

---

## 2. 分层落地要点

| 规则 | 具体表现 |
|---|---|
| Domain first | 判定「谁 current / 类型是否合法 / 关系方向」写在 Domain，不写在 `main.py` |
| Rule 2 | 例如 current 判定只能有一处（目标 `ClaimStateResolver`）；不得在 retrieval / graph / ask 各写一份 |
| Rule 3 | Domain 不 import fastapi / agentscope / sqlite3 / vue |
| Rule 4 | Repository 里不出现 `if predicate in FUNCTIONAL_PREDICATES` 这类业务判断 |
| Rule 5 | Agent 通过 Facade / Tool 访问能力，不直连数据库 |
| Rule 6 | LLM JSON 一律先 Parse → Schema 校验 → Normalize → Domain 校验 |
| Rule 7 | API 入参用 Pydantic 模型；导入文件校验类型；LLM 输出走 `extraction.py` 校验 |
| Rule 8 | 变更走 Operation + runlog / claim_relations 等审计记录 |
| Rule 9 | 新能力注册到扩展点，而非 `if/elif` 链 |
| Rule 10 | registry / offset / 方向 / 去重由程序做，LLM 只判语义 |

---

## 3. LLM 编程规范（重点）

LLM 输出**不是数据**，而是**候选**。唯一允许的路径：

```text
LLM Output
   ↓ Parse（JSON）
   ↓ Schema Validation（jsonschema）
   ↓ Normalization（谓词 / 类型 / 极性 归一）
   ↓ Domain Validation（不变量）
   ↓ Operation（落库）
```

**永远禁止**：

```text
LLM → SQLite           ❌ 未经校验直接落库
LLM → 直接改状态        ❌ 让模型决定 current / superseded / deleted / merged
```

> **LLM 不拥有数据库状态。**

LLM 可以做的事：

```text
Suggest   Classify   Extract   Rank   Summarize   Explain
```

LLM **不可以**决定：

```text
Current   Superseded   Deleted   Merged
```

这些属于 Domain，由程序与用户裁决。参考 `app/extraction.py`（校验）、`app/llm.py`（调用+repair）、`app/claim_relations.py`（`supersedes` 永不自动推断）。

---

## 4. 命名与组织

- 模块名小写下划线；函数用动词短语；常量全大写。
- 「一个业务规则一个实现」：若发现第二处相同判定，先抽取，再复用。
- 禁止在路由函数里写裸 SQL 和业务分支；禁止把 `conn` 传进 Domain 纯逻辑函数（目标状态）。
- 兼容/迁移代码必须显式标注（如 `# legacy`），并登记到迁移矩阵。

---

## 5. 提交前自查

```text
□ 改动是否属于正确的 Layer？
□ 是否绕过了公共 Facade？
□ 是否重复实现了已有 Domain Rule？
□ 是否让 Agent 直接决定 Domain State？
□ 是否让 Repository 承担业务逻辑？
□ 是否破坏 Evidence？
□ 是否绕过 Operation？
□ 是否需要 ADR？
□ 是否有 Domain Invariant Test？
```

（完整审查清单见 [FEATURE-DEVELOPMENT.md](./FEATURE-DEVELOPMENT.md) 与 [TESTING-STANDARD.md](./TESTING-STANDARD.md)。）

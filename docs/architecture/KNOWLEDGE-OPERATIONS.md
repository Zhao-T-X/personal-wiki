# KNOWLEDGE-OPERATIONS — 知识操作

> 状态：**PARTIAL（v0.2 已落地）**。`app/domain/operations.py` 已实现注册式八类 Operation（CREATE/DUPLICATE/CONTRADICT/SUPERSEDE/CORRECT/MERGE/ARCHIVE/RESTORE），执行即写 `knowledge_operations` 审计；「一句话纠正」经 `CORRECT`。**残留**：`PATCH /api/knowledge/{kind}/{id}/status` 与 `PATCH /api/claim-relations/{id}` 仍走旧路径，待收敛进 Operation。

## 1. 知识不是 CRUD

知识变更不是「改一行记录」，而是**可审计的领域操作**。系统定义八类操作：

```text
CREATE      创建新知识
CORRECT     纠正既有知识（产生新 Claim，而非覆盖）
MERGE       合并重复实体 / 断言
SPLIT       拆分被错误合并的实体 / 断言
SUPERSEDE   新知识取代旧知识（旧知识保留为历史）
CONTRADICT  标记冲突（等待人工裁决，不自动改状态）
ARCHIVE     归档（退出 current，但保留可检索历史）
RESTORE     从归档 / 被取代状态恢复
```

> **CURRENT**：只有 `CREATE`（抽取落库）与粗粒度状态变更（`PATCH /api/knowledge/{kind}/{id}/status`）。`CORRECT / MERGE / SPLIT / SUPERSEDE` 尚无一等实现——`supersedes` 目前只能作为 `claim_relations` 中的关系被记录，缺少统一入口。

---

## 2. 每个 Operation 必须满足

```text
1. 有明确输入（input contract）
2. 有 domain validation（在 Domain 层完成）
3. 有执行结果（result contract）
4. 有 audit record（操作记录）
5. 可追踪（traceable）
6. 必要时可回滚（rollback）
```

「回滚」指操作在事务内执行且失败不产生半成品；对不可逆操作，则以**补偿操作**（如 RESTORE）实现。

---

## 3. 铁律：用户修改知识不得直接 UPDATE 覆盖历史

禁止：

```sql
UPDATE claims SET object_id=?, status='verified' WHERE id=?   -- 覆盖历史，禁止
```

改为「旧 Claim → 新 Claim → supersedes」：

```text
Old Claim ──supersedes<── New Claim
```

理由：
- 原文与证据不可丢：旧 Claim 仍指向历史证据；
- 「谁 current」是可推导的结果，不是被覆盖掉的存储状态；
- 冲突、审计、时间线（Evolution）只有在保留历史时才可能。

> 这与当前项目已经采用的 Claim Evolution 原则一致（`app/claim_relations.py` 顶部注释明确「Never overwrite a claim」）。

---

## 4. 操作与状态的关系

| 操作 | 对状态的影响 | 是否破坏历史 |
|---|---|---|
| CREATE | 新对象 `candidate` | 否 |
| CORRECT | 原对象不变；新对象经 `supersedes` / `contradicts` 关联 | 否 |
| MERGE | 保留主对象；被合并对象 `archived` 并建立别名/引用 | 否 |
| SPLIT | 拆分出新对象；原对象保留并标注 | 否 |
| SUPERSEDE | 旧对象**仍存在**，不再 current | 否 |
| CONTRADICT | 双方均保留，标记待裁决 | 否 |
| ARCHIVE | 退出 current，保留历史 | 否 |
| RESTORE | 回到 current 视图 | 否 |

> **没有任何操作会删除或覆盖历史。** 删除只属于「原文文档」的维护，不属于知识演化。

---

## 5. Current Knowledge 的唯一裁决者

`supersedes` 是让旧 Claim 停止 current 的**唯一**关系。所有读取 current 的地方——Retrieval / Graph / QA / Correction / Review——必须调用**同一个** `ClaimStateResolver`：

```python
is_current(claim) -> bool
get_current(subject, predicate) -> list[claim]
get_history(claim) -> list[claim]
resolve(claims) -> list[claim]   # 过滤 + 标注 lifecycle
```

> **CURRENT**：`app/retrieval.py::_current_claims` 内联实现了部分逻辑（superseded 且被 current 覆盖则丢弃，否则保留并标 `lifecycle`）。这是**唯一实现**，但位置在检索层、且无法被 Correction / Graph 复用。
>
> **TARGET**：抽出 `ClaimStateResolver` 到 Domain，`retrieval` / `graph` / `ask` / `correction` / `review` 全部经由它。见 [ADR-005](../adr/ADR-005-CLAIM-EVOLUTION.md)。

---

## 6. Operation 的执行骨架（目标）

```text
OperationRequest
   ↓ parse + schema validate
   ↓ normalize
   ↓ domain validate（不变量）
   ↓ 事务内执行（写知识 + 写 audit）
   ↓ OperationResult（含受影响对象与可回滚信息）
```

- 输入来自外部（API / Agent / Import / LLM 候选）时，**一律视为不可信输入**。
- LLM 可以 `suggest` 一个 CORRECT / SUPERSEDE，但**不能直接决定** `current / superseded / deleted / merged`——这些属于 Domain。

---

## 7. 与扩展点的关系

Operation 是**注册式扩展点**：新增一类知识变更应注册一个新 Operation，而不是在中心分支里加 `elif`。见 [EXTENSION-POINTS.md](./EXTENSION-POINTS.md)。

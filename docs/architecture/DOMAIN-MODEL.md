# DOMAIN-MODEL — 核心领域模型

> 状态：**TARGET 概念模型**，与 CURRENT 代码一一对照。字段细节仍以 `docs/standards/` 与 `app/db.py` 为准。

## 1. 模型总览

```text
Document
   ↓
Chunk
   ↓
Evidence
   ↓
Entity
   ↓
Claim
   ↓
Relation
   ↓
Claim Evolution
```

原文（Document / Chunk）是**唯一事实来源**；Evidence 把知识锚回原文；Entity / Claim / Relation 是派生知识；Evolution 描述知识随时间的变化，而不是覆盖。

---

## 2. 各对象定义

### Document — 原始知识源

用户导入的 Markdown / TXT / HTML 原文及其元信息。
- 不变量：`content_hash` 唯一（同内容不重复入库）；原文可被结构化知识引用，但**永不被结构化知识替换**。
- CURRENT：`documents` 表（`app/db.py`）。

### Chunk — 可定位片段

Document 的切分结果，携带**精确偏移**（`start_offset` / `end_offset` / `chunk_index`）。
- 不变量：chunk 偏移必须落在原文范围内，且与原文一致。
- CURRENT：`chunks` 表 + `app/chunking.py`。

### Evidence — 断言的来源

证明某个知识断言的来源：Document → Chunk → Offset → Quote。
- **一等领域对象**：Claim 不再「拥有一个 source」，而是「拥有多个 Evidence」。
- 不变量：Evidence 必须指向存在的 Chunk；quote 若能定位则记录精确 span，否则标记 imprecise。
- CURRENT：以 `claims.source_document_id / source_chunk_id / source_start_offset / source_end_offset / source_quote` 的**内联列**形式存在（尚未独立成表）；quote 定位在 `app/knowledge.py::_locate_quote`。
- TARGET：独立 Evidence 实体，支持 `one Claim → multiple Evidence`。见 [ADR-006](../adr/ADR-006-EVIDENCE-AS-FIRST-CLASS-DOMAIN.md)。

### Entity — 知识中的对象

14 类实体（Person / Organization / Product / Software / Technology / Method / Concept / Theory / Dataset / Model / Standard / Protocol / Resource / Location），支持多类型与别名。
- 不变量：实体名（不区分大小写）唯一；别名归一化后去重；类型必须来自注册表。
- CURRENT：`entities` + `entity_aliases`；`app/resolution.py`（exact / alias / fuzzy）；`app/ontology.py::canonical_entity_type`。

### Claim — 关于 Entity 的可验证断言

形如 subject + predicate + (object | object_text)，携带 `claim_type` / `polarity` / `modality` / `confidence` 与 Evidence。
- 不变量：predicate 必须来自 Claim Predicate Registry；必须携带有效 Evidence。
- CURRENT：`claims` 表；校验在 `app/extraction.py` + `app/ontology.py`；落库在 `app/knowledge.py`。

### Relation — Entity / Claim 之间的关系

- Entity 间关系由 Claim **确定性派生**（不信任 LLM 直接输出的 graph relation）。
- 不变量：relation predicate 来自 Relation Predicate Registry；端点类型须满足注册表约束；同一 (source, predicate, target, document, chunk) 不重复。
- CURRENT：`relations` 表；派生在 `app/normalization.py::derive_relations`；约束在 `app/ontology.py::relation_types_allowed`。

### Evolution — Claim 的历史

知识不静默覆盖历史。新 Claim 与旧 Claim 建立显式关系：

```text
duplicate     同一陈述再次出现（可自动 link evidence）
coexists      同一 subject+predicate、不同 object，可同时成立（可自动保留）
contradicts   不能同时为真且无从判断谁当前（交人工 review）
supersedes    新 Claim 取代旧 Claim（**仅当证据文本明确说明时**）
```

- 不变量：`supersedes` 是让旧 Claim 停止 current 的**唯一**关系；`contradicts` 不自动 supersede；Current Knowledge 不得由 Retrieval 层自行推导。
- CURRENT：`app/claim_relations.py`（判定 + 写 `claim_relations` 表）；`app/service.py::index_document` 在同一事务内调用 `detect_claim_relations`；生命周期过滤内联在 `app/retrieval.py::_current_claims`。
- TARGET：统一 `ClaimStateResolver`，见 [ADR-005](../adr/ADR-005-CLAIM-EVOLUTION.md)。

---

## 3. 附加对象（同属领域）

| 对象 | 说明 | CURRENT |
|---|---|---|
| Event | 事件：type / time / status / participants | `events` 表 |
| Idea | 想法及其生命周期 | `ideas` 表 |
| Question | 问题及其生命周期 | `questions` 表 |
| Research Task | 研究任务（用例产物） | `research_tasks` 表 |

---

## 4. 状态与生命周期

| 对象 | 状态集合 |
|---|---|
| Entity / Claim / Relation | `draft / candidate / verified / rejected / archived / (claim: superseded)` |
| Idea | `candidate / accepted / implemented / rejected / archived` |
| Question | `open / answered / partially_answered / resolved / rejected / archived` |

定义位置：`app/ontology.py`（`KNOWLEDGE_STATUSES` / `IDEA_STATUSES` / `QUESTION_STATUSES`）。

---

## 5. 领域不变量清单（供测试）

```text
文档：   content_hash 唯一；chunk 偏移落在原文范围内
实体：   名称（CI）唯一；类型属于注册表；别名归一化去重
断言：   predicate 属于 Registry；必须携带有效 Evidence
关系：   endpoint 类型满足注册表；同键不重复；由 Claim 确定性派生
演化：   superseded 的 Claim 不能是 current；contradicts 不自动 supersede
证据：   Evidence 不能指向不存在的 Chunk
```

这些不变量对应 [docs/development/TESTING-STANDARD.md](../development/TESTING-STANDARD.md) 中的 **Domain Invariant Tests**。

# Claim Evolution & Conflict Handling Standard v1.0

> Status: **Implemented (v1.0)**
> Scope: 在现有 Claim / Evidence / Candidate / Review / Relation 架构上的增量扩展
> 运行时实现：`app/claim_relations.py`、`app/service.py`、`app/retrieval.py`、`app/db.py`
> 语言说明：本文档以中文撰写（表名、状态值、API 路径等保留英文），其余标准文档为英文；两者通过文档内的交叉引用互相连接。

本文档定义 Personal Wiki 如何表达**知识的演化**：当新的 Claim 与已有 Claim 表达同一主题但内容变化时，系统如何在不破坏历史与证据的前提下，判断二者关系并把决定权交给人。

---

## 1. 问题

新文档持续进入知识库。抽取会产生新的 Claim。核心问题是：

> 当新 Claim 与已有 Claim 关于同一主题但内容不同时，是否应该覆盖已有知识？

```text
已有 Claim:  OpenAI CEO = Sam Altman
新 Claim:    OpenAI CEO = Alice
```

如果直接 `UPDATE` 原 Claim，会同时失去：历史知识、原始 Evidence、变化原因、"更新"与"冲突"的区别、以及后续 Agent / RAG 解释知识演化的能力。

**本标准的回答是：永不覆盖。保留 Claim 与 Evidence，把二者之间的关系显式建模，并从中派生"当前知识"。**

---

## 2. 核心原则

```text
1. Never overwrite Claim content.
2. Every new Claim gets its own Evidence.
3. Compare a new Claim with related existing Claims.
4. Model relationships explicitly:
   duplicate / coexists / supersedes / contradicts
5. Let the LLM suggest relationships; let application policy control mutations.
6. Use Candidate / Review for uncertain changes.
7. Derive Current Knowledge instead of destroying history.
8. Keep Ask / RAG aware of Claim lifecycle.
9. Preserve historical Evidence permanently.
10. Prefer incremental changes to the existing architecture.
```

两条与项目既有哲学一致的推论：

- **确定性优先**：所有关系判定由程序完成，同一输入永远得到同一结果；LLM 只做语义判断，且**按需**调用（§5.3）。
- **单一真相源**：Claim 的内容只有一处（`claims` 行）；"当前知识"是派生的（§6.3），不是存储的字段。

---

## 3. 核心链路

```text
                    New Claim
                        │
                        ↓
            ┌───────────────────────┐
            │ Related Claim         │   subject_id + predicate 结构化匹配
            │ Detection             │   （Entity Resolution 已完成身份归一）
            └───────────┬───────────┘
                        ↓
            ┌───────────────────────┐
            │ Relationship Detection│   确定性规则 → 5 种关系
            └───────────┬───────────┘
                        ↓
        ┌───────────┬───────────┬───────────┬───────────┐
        ↓           ↓           ↓           ↓           ↓
   duplicate    coexists    supersedes  contradicts   unclear
        │           │           │           │           │
        │           │           │           │           └─ 不落库
        ↓           ↓           ↓           ↓
    自动关联     自动保留    仅人工可建   进入候选
   （accepted） （accepted）  （accepted） （candidate）
        │           │           │           │
        └───────────┴───────────┴───────────┘
                        ↓
                 Candidate / Review
                        ↓
                 Current Knowledge        ← 派生，非存储
                        ↓
                  Search / Ask
                        ↓
                    Evidence
```

一句话：**Preserve → Compare → Relate → Review → Derive Current。**

---

## 4. 关系模型

| 关系 | 含义 | 例子 |
|---|---|---|
| `duplicate` | 两条 Claim 表达同一知识 | `Apple is a company.` / `Apple Inc. is a company.` |
| `coexists` | 不冲突，可同时成立 | `Apple develops iPhone.` / `Apple develops Vision Pro.` |
| `supersedes` | 新 Claim 明确替代旧 Claim | CEO 更替 |
| `contradicts` | 无法同时成立，且系统无法可靠判断哪个当前 | 同一指标两个来源给出不同数值 |
| `unclear` | 结构信息不足，无法判断 | subject/predicate 不一致，或 object 未解析 |

两个语义必须严格区分：

```text
Superseded  → 曾经成立/被接受，但不再代表当前状态。
Rejected    → 用户认为该 Claim 不应进入知识库。
```

`superseded` 不是错误状态，因此它拥有独立的生命周期值，而不是复用 `rejected`。

---

## 5. 检测

### 5.1 Related Claim Detection

匹配键：`subject_id` + `predicate`。

因为 Entity Resolution 已经把 `OpenAI` / `OpenAI Inc.` / `OpenAI, Inc.` 折叠为同一个 `subject_id`，按 id 匹配是**精确、有索引、低成本**的。语义兜底（embedding）刻意不做：它每条 Claim 都要一次 embedding 调用，必须先证明结构化召回不足。

### 5.2 确定性判定规则

实现：`app/claim_relations.py::compare_claim()`

| # | 条件 | 判定 | 置信度 | 建议动作 |
|---|---|---|---|---|
| 1 | 同 subject+predicate+object，polarity 相同 | `duplicate` | 0.97 | `link_evidence` |
| 2 | 同 subject+predicate+object，polarity 相反 | `contradicts` | 0.90 | `review` |
| 3 | 同 subject+predicate，object 不同且 predicate 单值 | `contradicts` | 0.55 | `review` |
| 4 | 同 subject+predicate，object 不同且 predicate 多值 | `coexists` | 0.70 | `keep_both` |
| 5 | 其它（subject/predicate 不同，或 object 无法解析） | `unclear` | ≤ 0.20 | `ignore` / `review` |

**单值谓词（`FUNCTIONAL_PREDICATES`）目前是保守的小集合**：`is`、`defined_as`、`classified_as`。原因见 §10.4——registry 里 46 个 claim predicate 只是字符串，没有基数元数据，且其中没有 `ceo_of` 这类职位谓词。

不在集合中的谓词一律回落到 `coexists`：这个选择**不破坏任何东西**，只是把两条事实并排保留，永远不会要求用户去解决一个并不存在的冲突。扩展这个集合要靠证据，不能靠猜测。

### 5.3 `supersedes` 永不自动推断

判断"新的取代旧的"需要时间信息或文本证据（例如来源明确写了"A 取代了 B"）。仅凭结构无法可靠判断，因此：

- 确定性规则**从不产出** `supersedes`；
- 只有人在审核界面确认「新知识取代旧知识」时，该关系才被建立；
- 需要语义判断时，用户可主动调用 LLM 分析（§5.4）。

宁可提出一个诚实的问题，也不要给出一个错误的确定结论。回归测试 `test_supersession_is_never_inferred_from_structure_alone` 锁住这条约束。

### 5.4 LLM 的职责边界（按需）

```text
LLM 可以：理解自然语言、判断语义关系、给出 reason 与 confidence、提出建议
LLM 不可以：删除/修改 Claim、设置 final current 状态、自动解决低置信度冲突
```

```text
LLM → Suggestion → Application Policy → Database Mutation
```

实现：`POST /api/claim-relations/{id}/analyze`（`app/claim_relations.py::analyze_relation()`）。

**该调用绝不自动发生**——导入文档时一次 token 都不花。它只解决确定性规则读不懂的语义：措辞不同的同一陈述、或来源文本中说明的"替换"。返回值只是建议，用户可以选择采纳。

---

## 6. 数据模型

### 6.1 `claim_relations` 表

```sql
CREATE TABLE claim_relations (
  id TEXT PRIMARY KEY,
  source_claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  target_claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  relationship TEXT NOT NULL CHECK(relationship IN
      ('duplicate','coexists','supersedes','contradicts','unclear')),
  confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1),
  reason TEXT,
  suggested_action TEXT,
  status TEXT NOT NULL DEFAULT 'candidate'
      CHECK(status IN ('candidate','accepted','rejected')),
  created_by TEXT NOT NULL DEFAULT 'system',
  target_previous_status TEXT,   -- 供精确撤销
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source_claim_id, target_claim_id, relationship)
);
```

两端 `ON DELETE CASCADE`：删除 Claim 不会留下悬挂关系，也不会触发外键失败（这是删除文档路径曾经踩过的坑，见 §11.3）。

`target_previous_status` 记录旧 Claim 被标记为 superseded **之前**的状态，使"撤销"是精确恢复而不是猜测。

### 6.2 Claim 生命周期

```text
candidate → verified → active
                    ├── superseded   （被更新的知识取代，历史保留）
                    ├── rejected     （用户判定不应进入知识库）
                    └── archived
```

`superseded` 加入 `KNOWLEDGE_STATUSES` 与 `claims.status` 的 CHECK 约束（迁移见 §11.1）。

**状态迁移只移动生命周期，不触碰内容**：

```sql
-- 允许
UPDATE claims SET status = 'superseded' WHERE id = :old_claim_id;

-- 禁止：用 UPDATE 表达知识变化
UPDATE claims SET object_id = :new_object WHERE id = :old_claim_id;
```

### 6.3 Current Knowledge 是派生视图

```text
current(claim) =
    claim.status != 'rejected'
    AND claim.status != 'superseded'
```

**不引入 `current = true` 字段。** Current 是派生状态，一旦存储就需要在每次关系变化时同步维护，必然漂移。判定依据是唯一的真相源：`claims.status` 与 `claim_relations`。

---

## 7. 应用策略

| 关系 | 默认处理 | 理由 |
|---|---|---|
| `duplicate` | 自动 `accepted` | 关联证据不改变知识库的任何断言 |
| `coexists` | 自动 `accepted` | 两条事实并排成立，不存在需要解决的问题 |
| `supersedes`（人工确认） | `accepted` + 旧 Claim → `superseded` | 只有人能判断"谁当前有效" |
| `contradicts` | `candidate`，进入审核 | 可能改变知识库的断言，必须人工确认 |
| `unclear` | 不落库 | 没有可行动的信息 |

这条分界线就是核心：**能改变"知识库当前断言什么"的操作，永远等待人的确认；只是关联或并排记录的操作，才自动完成。**

不这样分配，审核队列会被"并存"噪音淹没，用户就会开始无脑点通过。

---

## 8. 审核 UX

审核界面的单位不是"数据库记录"，而是**一次知识变化**：

```text
知识变化

[矛盾]  Feedback system · defined_as

  已有知识                     新信息
  Feedback loop                delay mechanism
  System design v1 · 查看原文    System design v2 · 查看原文

  为什么会有这条判断？
  `defined_as` holds one value at a time, but the object changed.
  Compare the sources before treating either as current.

  [新知识取代旧知识]  [两者都保留]  [让 AI 判断]
```

设计要求：

1. **并排对照**：已有知识与新信息同屏，不需要来回跳转；
2. **给出理由**：系统必须能回答"为什么认为这两条有关系"；
3. **双方证据可达**：两边都能一键跳到原文；
4. **选项限定**：只提供真正不同语义的动作（取代 / 并存 / 忽略），不做无意义的按钮；
5. **可撤销**：任何决定都能回到待确认状态。

Claim 详情页则展示该 Claim 的全部关系，并在被取代时给出显式横幅——但**历史记录与原始证据始终保留**。

---

## 9. 检索与问答

Retrieval 必须感知 Claim 生命周期（实现：`app/retrieval.py::_current_claims()`）：

```text
Retrieve
   ↓
Claim lifecycle filtering
   ↓
Relationship resolution
   ↓
Current knowledge selection
   ↓
Evidence retrieval
   ↓
Answer
```

规则不是简单的"排除历史"，而是：

- 同一 `subject + predicate` **存在当前说法** → 丢弃被取代的那条，所以"现任是谁"不会被旧值污染；
- **没有当前说法覆盖它** → 保留并标记 `lifecycle: 'superseded'`，所以"前任是谁"仍然有证据可答。

一个被取代的 Claim 是历史，不是错误。删除它会让系统无法回答任何关于过去的问题。

---

## 10. 与现有架构的对照

> 本节是写代码前必须做的核对：哪些已支持、哪些只需扩展、哪些会冲突。
> 结论先给：**本功能没有新增任何知识对象类型，只新增了一张关系表和一个生命周期值。**

### 10.1 已支持 —— 直接复用，零改动

| 现有机制 | 如何被复用 |
|---|---|
| `claims` 表 + 状态生命周期 | 新知识就是新行，不需要版本字段 |
| Evidence / Provenance（`source_document_id`、`source_chunk_id`、精确 offset、`source_quote`） | 每条 Claim 自带证据链，历史证据天然永久保留 |
| Candidate 机制（`status='candidate'` + `/api/review`） | `claim_relations.status='candidate'` 沿用同一语义 |
| Review 决策流程（`PATCH /api/knowledge/{kind}/{id}/status`） | 关系沿用"系统建议 + 人决策"，不需要新的审核概念 |
| Entity Resolution（`app/resolution.py`） | 提供 object 身份归一，是关系检测的输入前提 |
| `PATCH /api/knowledge/claim/{id}/status` | `superseded` 加入 `KNOWLEDGE_STATUSES` 后即可手动设置 |
| Context Runtime / Agent 工具 | 无需改动：它们消费的是检索结果，生命周期过滤已在检索层完成 |

### 10.2 只需扩展 —— 本次新增

| 扩展点 | 内容 | 位置 |
|---|---|---|
| 新表 `claim_relations` | Claim → Claim 关系 | `app/db.py` |
| `claims.status` CHECK | 增加 `'superseded'` | `app/db.py`（迁移见 §11.1） |
| `KNOWLEDGE_STATUSES` | 增加 `'superseded'` | `app/ontology.py` |
| 抽取流程挂接 | 抽取后在同一事务内检测关系 | `app/service.py::index_document()` |
| 检索生命周期解析 | `_current_claims()` | `app/retrieval.py` |
| 3 个 API | 查询 / 审核 / 按需 LLM 分析 | `app/main.py` |
| 前端状态映射 | `superseded → 已被取代` | `frontend/src/utils/status.ts` |
| 审核 UI | 「知识变化」区块 | `frontend/src/components/ClaimChanges.vue` |

### 10.3 会产生冲突 —— 明确禁止的做法

| 诱惑 | 为什么不能做 | 正确做法 |
|---|---|---|
| 把 claim→claim 关系塞进现有 `relations` 表 | `relations.source_id` / `target_id` 硬绑定 `REFERENCES entities(id)`，而 Claim 不是 Entity。改它会迫使图谱实体边与知识演化边共处一表，Graph API、工具层压缩、Agent 工具全部要跟着改 | 独立的 `claim_relations` 表 |
| 在 `claims` 上加 `superseded_by` 字段 | 与关系表形成双重真相源，必然不一致 | 单一真相源：关系表；current 派生 |
| 在 `claims` 上加 `current BOOLEAN` | 每次关系变化都要同步；必然漂移 | 派生：`status` + `claim_relations` |
| 用 `UPDATE` 改写 Claim 的 object 表达知识更新 | 丢历史、丢证据、无法解释变化 | 新增 Claim + 建立关系 |
| 把 `claim_relations` 并入 `/api/review` 的返回结构 | 语义不同：review 返回"待审的候选知识对象"，这里是"待确认的知识变化"。混在一起会让前端难以区分二者的处理方式 | 独立端点 `/api/claim-relations` |
| 让 LLM 在导入时自动判断关系 | 每篇文档都增加 token 成本，且引入不可控的自动变更 | 确定性优先；LLM 仅在用户主动请求时给出建议 |

### 10.4 两个"关系"系统的边界（最容易混淆）

项目里现在有两个叫"关系"的概念，维度完全不同，**必须共存而不是合并**：

| | `relations`（既有） | `claim_relations`（本标准新增） |
|---|---|---|
| 端点 | Entity → Entity | Claim → Claim |
| 语义 | 知识图谱的**结构边** | 知识的**演化关系** |
| 产出时机 | 抽取过程中，由 `derive_relations()` 确定性派生 | 抽取完成后，由 `detect_claim_relations()` 检测 |
| 维度 | 单文档、单次抽取内 | 跨文档、跨时间 |
| 谓词空间 | Relation Predicate Registry（19 个受控谓词） | 固定 5 个关系类型 |
| 可否进 Graph | 是（这就是图谱） | 否（不是图边） |
| 判定依据 | `05-CLAIM-RELATION-NORMALIZATION.md` 的 10 条 Direct Relation 判据 | 本标准 §5.2 |

`app/normalization.py::claim_to_relation_candidates()` 决定的是"这条 Claim 是否有资格变成图谱里的一条边"（`DIRECT_RELATION` / `CLAIM_ONLY` / `REJECTED`）。它与"这条 Claim 与历史知识是什么关系"无关。**看到 `claim_to_relation_candidates` 时不要误以为本功能已经存在。**

### 10.5 已知的模型限制（不是 bug，是当前边界）

| 限制 | 影响 | 说明 |
|---|---|---|
| Claim Predicate Registry 无基数元数据 | 单值/多值判断只能靠代码内的小集合 | `FUNCTIONAL_PREDICATES`；扩展需证据 |
| Registry 中没有 `ceo_of` 这类职位谓词 | 原设计文档的 CEO 例子在当前 ontology 下无法直接表达 | 需先扩展 predicate registry，属于 ontology 演进 |
| 无时间语义 | 无法区分"两条都成立但适用时间不同" | 见 §13 |

---

## 11. 迁移策略

### 11.1 `claims.status` 增加 `superseded`

SQLite 无法修改 CHECK 约束，必须重建表。迁移函数：`app/db.py::_migrate_claim_statuses()`

```text
1. 从 sqlite_master 读出 claims 表自身的 DDL
2. 正则替换表名 → claims_migrated；字符串替换 CHECK 值加入 'superseded'
3. 关闭外键，跨过 DROP / RENAME（其它表按名字引用 claims，重命名对它们透明）
4. INSERT INTO claims_migrated SELECT * FROM claims   ← 列顺序由原 DDL 复刻保证一致
5. 重建索引，恢复外键
```

从 `sqlite_master` 复刻而不是手写新结构，是为了兼容任何历史迁移追加过的列。

### 11.2 幂等与安全性

- 新库：`SCHEMA` 已含 `superseded`，迁移检测到后直接返回，不做任何操作；
- 旧库：只在检测到 CHECK 中缺少 `superseded` 时重建；
- 无法识别既有 DDL 形状时**不做任何修改**，而不是猜测；
- 迁移失败不会阻断启动（沿用 `init_db()` 既有的容错）。

### 11.3 与删除路径的关系

`claim_relations` 两端都是 `ON DELETE CASCADE`，删除 Claim 时关系自动清理。删除文档的路径（`delete_document()`）会先清理派生 Claims，因此关系也随之消失，不会产生悬挂行或外键失败。

---

## 12. 错误处理

```text
Extraction
   ↓
Claim created
   ↓
Relationship detection failed
   ↓
Claim 保持 candidate，relationship 视为 unclear
   ↓
进入 Review
```

> **Failure to understand a relationship must not cause loss of knowledge.**

实现上，`detect_claim_relations()` 对每条 Claim 单独兜底（`try/except`）：一条关系检测失败不影响该 Claim 落库，也不影响其它 Claim 的检测。

关系检测运行在**与 Claim 落库相同的 transaction 内**，因此不会出现"Claim 已创建但关系丢失"的中间状态。

---

## 13. 非目标与后续演进

本阶段**不做**（明确排除，避免过早复杂化）：

```text
完整 Temporal Database / 任意历史时间旅行
自动构建完整 Knowledge Timeline
全自动事实判断
Source Reliability Ranking
独立的 Knowledge Version 实体
大规模数据库架构重构
在 claims 上引入 current 标志位
```

**关于 Temporal 的立场**：目前没有证据表明存在"两条都成立但适用时间不同"的真实数据。贸然引入 `observed_at / valid_from / valid_until` 会让每条 Claim 背上两个几乎总是为空、或语义不清晰的字段。

渐进路径：现有 `context_json` 与 provenance 中已经带有时间线索 → 观察是否真的出现时序性冲突 → 再引入时间字段。

**后续演进方向**（当 v1.0 稳定后）：

```text
Phase 2  Temporal Claims（observed_at / valid_from / valid_until）
Phase 3  Source Reliability
Phase 4  Belief Evolution
Phase 5  Automatic Conflict Resolution
Phase 6  Temporal Knowledge Graph
```

---

## 14. 验收标准

| # | 场景 | 期望 |
|---|---|---|
| 1 | 普通新增 | 无相关 Claim → 正常创建，关系数 0 |
| 2 | Duplicate | 识别为 duplicate，不产生第二条独立知识，证据保留 |
| 3 | 单值谓词 object 变化 | 识别为 contradicts，进入审核 |
| 4 | Supersession | 人工确认后旧 Claim = `superseded`，新 Claim = current，**旧 Claim 文本未改写** |
| 5 | Conflict | 进入 Candidate / Review，系统不自作判断 |
| 6 | 历史查询 | Current 与 Historical Claim 及其 Evidence 均可检索与解释 |
| 7 | 撤销 | 关系回到 candidate，旧 Claim 精确恢复原状态 |
| 8 | 删除 | 删除 Claim / 文档不产生悬挂关系，不触发外键失败 |

---

## 15. 实现映射与测试

| 关注点 | 模块 |
|---|---|
| 关系判定 | `app/claim_relations.py::compare_claim()` / `compare_with_related()` |
| 相关 Claim 检索 | `app/claim_relations.py::find_related_claims()` |
| 关系落库 | `app/claim_relations.py::detect_claim_relations()` |
| 按需语义判断 | `app/claim_relations.py::analyze_relation()` |
| 抽取流程挂接 | `app/service.py::index_document()` |
| 生命周期解析 | `app/retrieval.py::_current_claims()` |
| 表结构与迁移 | `app/db.py::claim_relations` / `_migrate_claim_statuses()` |
| API | `app/main.py`：`GET /api/claims/{id}/relations`、`GET /api/claim-relations`、`PATCH /api/claim-relations/{id}`、`POST /api/claim-relations/{id}/analyze` |
| 审核 UI | `frontend/src/components/ClaimChanges.vue` |
| 测试 | `tests/test_claim_relations.py` |

---

## 16. 设计结论

本标准解决的不是"如何让新知识覆盖旧知识"，而是：

> **如何让 Personal Wiki 表达知识的演化，同时保持历史、证据与可解释性？**

对应到实现，最重要的三条不变量：

```text
1. Claim 的内容永不因为新知识而被改写。
2. 改变"当前断言什么"的操作，永远经过人的确认。
3. 历史与证据可以退出当前视图，但永远不被删除。
```

参考：`docs/standards/05-CLAIM-RELATION-NORMALIZATION.md`（Claim → Graph Relation）、
`docs/standards/09-EVIDENCE-PROVENANCE-STANDARD.md`（证据溯源契约）。

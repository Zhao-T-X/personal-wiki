# MIGRATION-PLAN — 从 CURRENT 到 TARGET

> 状态：**MIGRATION**。本文是唯一允许描述「理想架构」的地方，且必须与 CURRENT 明确对照。**不要求一次重写**，采用渐进的 Stabilize → Abstract → Migrate → Delete。

## 进展（Progress log）

| 日期 | 阶段 | 内容 | 验证 |
|---|---|---|---|
| 2026-09-14 | 治理 | 建立 `docs/architecture` / `docs/development` / `docs/adr`（v0.2 规范体系） | — |
| 2026-09-14 | Phase 1（部分） | **Repository 层落地**：`app/repositories/`（document / entity / claim / relation / evidence / event / idea / question / research / run / catalog），采用「参与调用方事务」的 unit-of-work；`main.py`、`service.py`、`retrieval.py`、`knowledge.py`、`resolution.py`、`claim_relations.py` 已**零内联 SQL** | 全量 192 通过 |

| 2026-09-14 | Phase 3（部分） | **三件套落地**：`app/domain/claim_state.py`（ClaimStateResolver —— Search/Graph 已统一调用）、`app/domain/operations.py`（8 类 KnowledgeOperation + `knowledge_operations` 审计 + 注册式扩展）、`app/workflows/correction_workflow.py` 与 `/api/knowledge/corrections`（一句话纠正）；`graph.py` 一并迁入 Repository 并接入 Resolver | 全量 212 通过 |
| 2026-09-14 | Phase 4（验收） | **v0.2 全链路验收**：`tests/test_e2e_knowledge_lifecycle.py` 跑通 导入→抽取→关系发现→一句话纠正→superseded→`ClaimStateResolver` 当前知识→Graph 状态→QA 溯源→审计；`tests/test_architecture.py` 机械封口（业务层零 SQL / Domain 不依赖框架 / LLM 不入 Domain） | 全量 215 通过 |
| 2026-09-14 | Phase 4（质量） | **KnowledgeQualityScore**：`app/domain/knowledge_quality.py`（七维确定性评分 + `score_claim_by_id` + `review_queue`）、端点 `/api/knowledge/claims/{id}/quality` 与 `/api/knowledge/quality/review`、`tests/test_knowledge_quality.py`（9 项）；**一句话纠正 UI**：`frontend/src/views/CorrectionView.vue` + 路由/导航，对接 `/api/knowledge/corrections`（预览，需模型）与 `/apply`（手动模式免模型）+ 审计历史；`ClaimView` 展示质量分（前端 vue-tsc + vite build 通过） | 全量 224 通过 |
| 2026-09-14 | Phase 4（质量） | **Citation Validation**：`app/domain/citation_validation.py`（`locatability`/`coverage`/`currentness` 确定性三维 + `support` LLM 维度以 callable 注入，模型不可用时降为确定性三项并标 `llm_unavailable`）、`app/workflows/citation_validation_workflow.py`（注入 `openai_model` 调用）、`POST /api/qa/validate`、`tests/test_citation_validation.py`（7 项）；前端 `QaView` 加「校验引用」按钮 + 报告面板（维度条 / grounding 断言 / issues） | 全量 231 通过 |

**架构已基本冻结（Phase 1–4 落地）**，质量工程主线全部完成。剩余非阻塞项：
- `KnowledgeQualityScore` 与 Review 队列 —— **已实现**：`/api/knowledge/quality/review` 按质量分把低分/带关键标记的 Claim 送入审核（最弱优先），`ClaimView` 与纠正 UI均展示七维分
- Knowledge QA 引用校验（Citation Validation）—— **已实现**：`POST /api/qa/validate` 对 `/api/ask` 的回答做 locatability/coverage/currentness 确定性校验 + support 语义校验（须 LLM，失败时降级），`QaView` 可一键校验并展示报告
- 旧路径未收敛：`PATCH /api/knowledge/{kind}/{id}/status` 与 `PATCH /api/claim-relations/{id}` 仍未走 Operation
- 基础设施仍含 SQL：`app/embeddings.py`、`app/tools/*`、`app/context/*`、`app/prompt_profiles.py`、`app/runlog.py`


## 0. 迁移总策略

```text
Current
  ↓
建立 Facade（内部先复用现有实现）
  ↓
新代码一律走 Facade
  ↓
逐一切换旧调用方
  ↓
删除 legacy path
```

黄金规则：**Facade 先做薄封装，不改变行为**。先让「入口统一」，再逐步把逻辑下沉到 Domain，最后才删除旧路径。任何一步都不得破坏现有测试。

---

## 1. CURRENT → TARGET 迁移矩阵

| 能力 | CURRENT（文件:符号） | TARGET | 风险 | 阶段 |
|---|---|---|---|---|
| 公共入口 | 无 Facade，`main.py` 直连 | 五个 Facade | 高 | P1 |
| 读取知识 | `main.py` /api/entities 等内联 SQL | KnowledgeFacade | 中 | P1→P2 |
| 检索 | `retrieval.py` 直接函数 | SearchFacade | 中 | P2 |
| 知识变更 | 抽取落库 + 状态 PATCH | OperationFacade（8 操作） | 高 | P3 |
| Claim 生命周期 | `retrieval.py::_current_claims` 内联 | `ClaimStateResolver`（统一） | 高 | P3 |
| 实体/谓词校验 | `ontology.py`（已较好） | OntologyFacade 封装 | 低 | P1 |
| 用例编排 | `service.py` + `agent_workflow.py` | WorkflowFacade | 中 | P2 |
| 抽取落库 | `knowledge.py::persist_extraction`（Domain+持久化混合） | Domain 校验 + Repository 分离 | 高 | P2 |
| 路由组织 | 单文件 `main.py` | Router 拆分（resource 维度） | 中 | P2 |
| Evidence | 内联列（claims.source_*） | 一等领域对象（1 Claim ↔ N Evidence） | 高 | P3 |
| 扩展机制 | 硬编码 + 局部注册 | 统一注册表 | 中 | P4 |

---

## 2. 分四阶段

### Phase 1 — 建立边界（不改行为）

```text
建立 Facade（Knowledge / Search / Operation / Ontology / Workflow）
建立 Domain State（ClaimStateResolver 雏形）
建立 Repository（从 main.py 抽 SQL）
```

交付判据：
- Facade 存在且被至少一个调用方使用；
- Repository 承接 `main.py` 中至少 `entities` / `claims` 的读 SQL；
- 全量测试保持通过。

### Phase 2 — 拆分与下沉

```text
main.py Router 拆分（按 resource）
service.py 用例迁移到 WorkflowFacade
persist_extraction 拆分：Domain 校验 / Repository 写入
```

交付判据：
- `main.py` 不含业务判定与裸 SQL；
- 抽取路径经 Domain 校验函数与 Repository；
- 测试保持通过。

### Phase 3 — 知识操作与证据

```text
Knowledge Operations（CREATE/CORRECT/MERGE/SPLIT/SUPERSEDE/CONTRADICT/ARCHIVE/RESTORE）
Evidence 成为一等领域对象
ClaimStateResolver 统一 current 裁决
```

交付判据：
- 不存在绕过 Operation 的知识写入；
- `retrieval` / `graph` / `ask` 均调用同一个 Resolver；
- Domain Invariant Tests 覆盖（见测试规范）。

### Phase 4 — 对外与扩展

```text
Correction 工作流
Merge / Split 工作流
MCP / CLI 适配
统一扩展点注册
```

交付判据：
- 外部（API / Agent / CLI / MCP）只经 Facade；
- 新增能力以注册方式接入。

---

## 3. 迁移期纪律

1. **双轨期**：新旧路径并存时，新代码必须走 Facade；旧路径标记 `# legacy`，并登记在矩阵表。
2. **不扩大 legacy**：禁止在旧路径上新增功能（可修 bug）。
3. **每阶段结束更新本矩阵**，标注完成项。
4. **行为等价的迁移不合并新功能**：重构 commit 与功能 commit 分开。
5. **不变量优先**：迁移前先补齐 Domain Invariant Tests，再动结构。

---

## 4. 与 ADR 的关系

每个阶段若改变架构决策，必须新增/更新对应 ADR：

```text
Facade 确立            → ADR-003
Domain/Repository 分离 → ADR-004
Claim Evolution 统一   → ADR-005
Evidence 一等领域      → ADR-006
Context Runtime 边界   → ADR-008
Operations 统一        → ADR-009
```

---

## 5. 验收红线（每阶段自查）

```text
□ 该改动是否属于正确的 Layer？
□ 是否绕过了公共 Facade？
□ 是否重复实现已有 Domain Rule？
□ 是否让 Agent 直接决定 Domain State？
□ 是否让 Repository 承担业务逻辑？
□ 是否破坏 Evidence？
□ 是否绕过 Operation？
□ 是否需要 ADR？
□ 是否有 Domain Invariant Test？
```

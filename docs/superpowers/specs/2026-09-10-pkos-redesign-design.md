# Design: LLM-Wiki 前端 v2 — Personal Knowledge OS

- Date: 2026-09-10
- Status: Approved（原型 `docs/prototype/llm-wiki-pkos-v1.html` 已由用户确认）
- 视觉与交互基准：该原型；本 spec 定义其与真实后端的接线方式。

## 核心决策

1. **IA 收敛**：一级导航 6 项（首页/知识/问答/研究/Agent 工作台/设置）。Events→知识·Timeline；Ideas/Questions/Evidence→Knowledge Object 内部 Tab 与研究闭环；Tasks/Logs→Agent 工作台·Runs；Database→设置·高级。
2. **Knowledge Object 成为实体详情的统一入口**（`/knowledge/object/:id`），Claim 详情独立页（`/knowledge/claim/:id`）。
3. **研究是闭环页**：Questions / 研究流程面板 / **Conflict Center** / Knowledge Health 四个 Tab。
4. **⌘K 命令面板**为全站导航入口；未命中命令时可直接转提问。
5. 视觉照抄原型 token；无真实数据的概念不渲染（不伪造指标）。

## 页面 → 数据映射（真实后端）

| 路由 | 页面 | 数据 |
|---|---|---|
| `/` | 首页 | stats + timeseries + documents(近期) + runs(近期) + questions/conflicts 计数 |
| `/knowledge` | 知识（Library/Graph/Timeline） | documents + entities + graph + events |
| `/knowledge/object/:id` | Knowledge Object | 🆕 `GET /api/entities/{id}/object` |
| `/knowledge/claim/:id` | Claim 详情 | 🆕 `GET /api/claims/{id}` |
| `/qa` | 问答 | `POST /api/ask`（Trace 按 evidence[].method 聚合）+ runs(ask) |
| `/research` | 研究 | questions + 🆕 conflicts + 🆕 health + `POST /api/agent/ask{role:'research'}` |
| `/agent` | Agent 工作台 | agent/roles + agent/prompts + 🆕 skills + runs(agent) |
| `/settings`、`/settings/database` | 设置 / 数据库 | settings + stats + integrity |

## 新增后端端点

1. `GET /api/claims/{claim_id}` — 单条 Claim（含 subject/object 名称）。
2. `GET /api/entities/{id}/object` — KO 聚合：counts（claims/relations/events/ideas/questions/evidence/documents）、claims、relations、evidence（带 quote 的 claims）、events/ideas/questions（按该实体 claims 的 source_document 集合过滤）、type_decision（来自 `defined_as` 类 Claims 的来源描述）。
3. `GET /api/conflicts` — 启发式冲突：同 (subject_id, predicate) 但 polarity 相反的 Claim 对。
4. `GET /api/knowledge/health` — evidence coverage、graph coverage（有 relation 的 claim 比例）、orphan claims（object 为文本且 subject 未入图谱计数）、open questions、stale（90 天未验证）。
5. `GET /api/skills`、`GET /api/skills/{name}` — 读取 skills/ 目录：名称 + 首个标题（purpose）/ 全文。

## 前端工程变更（在现有 Vue 应用上重构）

- Router：`/`、`/knowledge`、`/knowledge/object/:id`、`/knowledge/claim/:id`、`/qa`、`/research`、`/agent`、`/settings`、`/settings/database`。
- 删除一级视图：Dashboard、Entities、Events、Ideas、Questions、Evidence、Tasks、Logs（能力并入新结构）。
- 新组件：CommandPalette、Triple（claim 三元组）、BoundaryList、TypeDecision、FlowSteps、KpiStrip。
- 复用：AppDrawer、RunDrawer、DataTable、StatusTag、MarkdownView、KpiCard、PageHead、SegTabs。
- 保留：web/dist 构建产物服务 + 旧 index.html 回退。

## 验收

1. pytest 全绿（含 6 个新端点测试）；`npm run build` 零错误。
2. 六大导航逐页走查：首页提问入问答；知识三视图；KO 七 Tab 真实数据；Claim 页完整结构；问答 Trace/Boundary；研究四 Tab（冲突可真实检出或空态）；Agent 工作台（Prompt 管线 + Skills 真实目录 + Runs 真实记录）；⌘K 全部命令可跳转。
3. 旧 UI 回退仍可用。

# UX 问题清单（UX-PROBLEM-BACKLOG）

> 本阶段**只审计、先不改**。每条问题含：问题 / 用户场景 / 当前路径 / 用户困惑点 /
> 证据 / 严重程度 / 建议 / 是否需要后端配合。
>
> 严重度标准：
> - **P0**：导致不知道下一步 / 不知功能用途 / 无法完成核心任务 / 必须学内部概念 / 无法确认操作成功。
> - **P1**：步骤多 / 跳转多 / 信息重复 / 易迷路 / 结果不清晰。
> - **P2**：视觉 / 动画 / 细节交互 / 高级筛选。
>
> 架构红线（UX 改动不得破坏）：① 禁新增绕过 Operation 的写入口；
> ② 禁新增第二套 Current Knowledge 判断；③ 禁新增重复 Read Model。

---

## P0 — 阻断使用闭环

### P0-1 OneBox 无法理解回访型问句，留存触发器失效
- **问题**：含裸 `什么`/`谁` 的自然问句（"最近我记录了什么""这篇文章讲了什么"）被判定 `unknown`，无路由。
- **用户场景**：用户 B/C 隔几天回来，最自然的一句话就是"我之前记了啥 / 最近变了啥"。
- **当前路径**：OneBox → `app/intent.py:classify` → `_QUESTION_MARKS` 仅含"是什么/什么是/是谁/哪些…"，**缺裸 `什么`/`谁`**。
- **用户困惑点**：输入一句话却没有任何反应/选项，不知道产品能回答"我记录了什么"。
- **证据**：`intent.classify` 实测——"最近我记录了什么"→`unknown`；"我记录了哪些"→`ask`（命中"哪些"）；"这篇文章讲了什么"→`unknown`；"这篇文章是什么"→`ask`。
- **严重程度**：P0（直接命中阶段验收第 5 问"为什么还愿意回来"）。
- **建议**：在 `app/intent.py` 的 `_QUESTION_MARKS` 增补裸 `什么`/`谁`/以及"记了/存了/整理了"等回访词；**仅扩展意图词表，路由仍指向既有 `/api/ask`，不新增任何写路径**（守红线①）。
- **是否需要后端配合**：**是**（改 `intent.py`，纯词表扩展，低风险）。

### P0-2 提问时被强制理解 Knowledge / Agent 的区别
- **问题**：QA 页把"知识库问答 / Agent 问答"作为显式可选模式，并展示"自动路由 Agent""打开 Agent 工作台"。
- **用户场景**：用户 C 只想"问一句我存过的东西"，却被要求先选模式。
- **当前路径**：`QaView.vue` 暴露 Agent 模式切换 + 文案；后端 `intent.py` 已统一把问句路由到 `/api/ask`（单一 ASK 意图）。
- **用户困惑点**："知识库问答和 Agent 问答有什么区别？我该选哪个？"——这是指令明确点名"不允许要求用户理解"的区别。
- **证据**：`UX-CONCEPT-MATRIX.md` 中 `Agent` 行；`QaView` 含 `Agent 问答`/`打开 Agent 工作台`/`llm_runs` 等。
- **严重程度**：P0（命中阶段验收第 3 问）。
- **建议**：默认路径隐藏 Agent 模式（仅 Developer Mode 可见），或把两种模式合并为单一"问"，内部自动路由；前端不复制路由判断（已有 `utils/onebox.ts` 唯一真相）。
- **是否需要后端配合**：**否**（前端人话化/折叠即可；后端 `/api/ask` 已统一）。

### P0-3 研究页以内部概念为主语，用户须先学一套词汇
- **问题**：`ResearchView` 大量以 `Claims`/`predicate`/`KnowledgeAgent`/`ResearchAgent` 作为界面主语。
- **用户场景**：用户 C 说"帮我深入研究"，进入后面对 `Total Claims`/`Candidate Claims`/`已保留 X 条 Claim`/`Claims 极性一致` 等。
- **当前路径**：`ResearchView.vue` 直接渲染 `claims`/`predicate`/`Agent` 角色标签。
- **用户困惑点**："Claim 是什么？Predicate 又是什么？我还要选 Agent？"——被迫理解系统内部结构。
- **证据**：`UX-CONCEPT-MATRIX.md` Claim/Predicate/Agent 行；`ResearchView` KPI `Total Claims`/角色 tag。
- **严重程度**：P0（命中"必须学习系统内部概念"）。
- **建议**：页面主语改为用户语言——"待你确认的研究发现 / 冲突 / 健康度"；`Claim`/`Predicate`/`Agent` 下沉为折叠或 Developer 可见。
- **是否需要后端配合**：**否**（前端人话化+折叠；不新增 Read Model，守红线③）。

---

## P1 — 步骤/信息/概念暴露（已全部修复）

> P1 阶段已落地：ObjectView 的 Ontology 弹窗、KnowledgeView 的 chunk/Embeddings/Claims、
> CorrectionView 的 CORRECT/operation_id、SettingsView 的 Embedding 术语与 raw JSON、
> CommandPalette 的 Agent/Database/冲突中心/知识健康、ClaimView 的技术细节折叠，均已改为
> 仅 Developer Mode 可见或人话化；首页新增价值锚点。架构红线（无新写入口/无第二套 Current
> Knowledge/无新 Read Model）全程未触碰。

### P1-1 ObjectView 编辑弹窗暴露"Ontology 注册表约束"
- **问题**：编辑实体弹窗副标题"类型受 Ontology 注册表约束"含 raw `Ontology`+注册表，普通路径醒目。
- **用户场景**：用户 B 想改一个对象的类型。
- **当前路径**：`ObjectView.vue` 编辑弹窗副标题（约 387 行）。
- **用户困惑点**："Ontology 注册表是什么？我为什么要关心？"
- **证据**：`UX-CONCEPT-MATRIX.md` Registry/Ontology 行。
- **严重程度**：P1。
- **建议**：改为"选择这个对象的类别"（类别名来自后端，隐藏"受注册表约束"机制说明）。
- **是否需要后端配合**：**否**。

### P1-2 KnowledgeView 暴露 chunk / Embeddings
- **问题**：搜索结果/文档抽屉出现 `chunk #n`/`X chunks`/`Chunks`/`Embeddings` 按钮。
- **用户场景**：用户 B 搜知识或看一篇导入的文档。
- **当前路径**：`KnowledgeView.vue`（`chunk`/`chunks`/`Chunks`/`Embeddings` 多处）。
- **用户困惑点**："chunk 是什么？Embeddings 跟我找答案有什么关系？"
- **证据**：`UX-CONCEPT-MATRIX.md` Chunk/Embedding 行；`ImportPanel` 已中文化可作反例。
- **严重程度**：P1。
- **建议**：`chunk`→"片段"，`Embeddings`→"语义索引/生成索引"，或移入高级区。
- **是否需要后端配合**：**否**。

### P1-3 CorrectionView 暴露 operation_id / CORRECT 操作
- **问题**：纠正审计区展示 `CORRECT 操作`/`操作 ID：operation_id`/`已生成 … CORRECT`。
- **用户场景**：用户 C 改完一条知识，想看历史。
- **当前路径**：`CorrectionView.vue` 审计区（约 78/94/98/103 行）。
- **用户困惑点**："operation_id 是什么？CORRECT 操作跟我改的那句话有什么关系？"
- **证据**：`UX-CONCEPT-MATRIX.md` Operation 行。
- **严重程度**：P1（审计有价值，但术语需人话化）。
- **建议**：改为"这条知识的修改记录"，隐藏 `operation_id`/`CORRECT` 字眼（可放折叠"技术细节"）。
- **是否需要后端配合**：**否**。

### P1-4 SettingsView 暴露 Embedding 术语与 raw JSON
- **问题**：设置页有 `Embedding Model`/`Embedding Dimensions`/`embeddings` 及 `JSON.stringify(health)` 直接渲染。
- **用户场景**：用户配置模型/检索。
- **当前路径**：`SettingsView.vue`（约 76–98 行）。
- **用户困惑点**："Embedding Dimensions 是什么？这段 JSON 是给我看的吗？"
- **证据**：`UX-CONCEPT-MATRIX.md` Embedding 行；`SettingsView` raw JSON。
- **严重程度**：P1。
- **建议**：`Embedding`→"语义向量/索引维度"；health 结果做结构化展示，不直接渲染 JSON。
- **是否需要后端配合**：**否**。

### P1-5 CommandPalette 核心命令暴露"提问 Agent（ReAct Agent）"
- **问题**：⌘K 命令面板把 `提问 Agent（ReAct Agent）` 作为普通核心命令。
- **用户场景**：用户 C 用快捷键想快速提问。
- **当前路径**：`CommandPalette.vue`（约 20 行）。
- **用户困惑点**："ReAct Agent 是什么？和普通提问有何不同？"
- **证据**：`UX-CONCEPT-MATRIX.md` Agent 行。
- **严重程度**：P1。
- **建议**：核心命令仅保留"提问/记录/研究/纠正"；Agent 相关命令移入 Developer Mode。
- **是否需要后端配合**：**否**。

### P1-6 QaView 泄漏 raw 表名 `llm_runs`
- **问题**：问答页"运行记录"项内显示 `已写入任务列表（llm_runs · task_type=agent）`。
- **用户场景**：用户 C 查看本次问答的运行明细。
- **当前路径**：`QaView.vue`（约 396 行）`RunDrawer`。
- **用户困惑点**："llm_runs 是什么表？task_type=agent 又是什么？"
- **证据**：`UX-CONCEPT-MATRIX.md` Run 行。
- **严重程度**：P1。
- **建议**：改为"本次问答的执行记录"，隐藏内部表名/类型字段。
- **是否需要后端配合**：**否**。

### P1-7 首屏缺价值锚点 + 导入完成→下一步引导弱
- **问题**：首屏只说"交给我/问/记/研究"，未说明"交了之后变成什么"；导入完成后无明确引导到问答/审核。
- **用户场景**：用户 A 第一次打开 / 第一次导入。
- **当前路径**：`HomeView` 文案；`ImportPanel` 完成态。
- **用户困惑点**："我拖了一篇文章，然后呢？这东西对我有什么用？"
- **证据**：`USER-JOURNEY-AUDIT.md` Journey 01/02。
- **严重程度**：P1。
- **建议**：首屏加一条轻量示例链路（"拖入一篇 → 自动整理成可问答的知识"）；导入完成页加"去问答/去审核"两个明确去向。
- **是否需要后端配合**：**否**。

### P1-8 ClaimView/ObjectView 折叠"技术细节"仍露 Ontology/Context/Claim Type
- **问题**：折叠区直接显示 `Claim Type`/`Ontology`/`Context`/`Polarity`/`Modality` 等 raw 字段。
- **用户场景**：用户 B 看一条知识详情。
- **当前路径**：`ClaimView.vue`/`ObjectView.vue` 技术细节折叠。
- **用户困惑点**：展开详情即撞见一堆不懂的英文术语。
- **证据**：`UX-CONCEPT-MATRIX.md` Ontology/Context/Claim 行。
- **严重程度**：P1（需展开才可见，优先级最低）。
- **建议**：折叠区标题改为"高级信息"，内部字段中文化或仅 Developer 可见。
- **是否需要后端配合**：**否**。

### P1-9 回访无"进行中 / 未完成"入口
- **问题**：用户回来后，无法快速续上上次未完成任务（如进行中的 research、待确认的导入）。
- **用户场景**：用户 B/C 隔几天回来。
- **当前路径**：Home 仅有"最近知识变化 / 待判断"，无"进行中"区。
- **用户困惑点**："我上次那个研究做到哪了？那个待确认的东西还在吗？"
- **证据**：`USER-JOURNEY-AUDIT.md` Journey 06。
- **严重程度**：P1。
- **建议**：Home 增加"进行中"区块（聚合待确认/进行中研究/最近导入），前端聚合既有接口，不新增 Read Model（守红线③）。
- **是否需要后端配合**：**否**。

---

## P2 — 视觉/细节（规模相关，已验证并补护栏）

### P2-1 知识规模变大后列表/图谱可能失控
- **问题**：100+/1000 条时，以 `Claim`/`chunk` 为主语的列表与图谱易过载。
- **用户场景**：用户 B 知识库增长后浏览。
- **当前路径**：`KnowledgeView`/`ResearchView` 列表与图谱。
- **用户困惑点**："这里东西太多，我找不到我要的，也不懂这些零件。"
- **证据**：`USER-JOURNEY-AUDIT.md` Journey 07。
- **严重程度**：P2（随规模升级，先验证再处理）。
- **建议**：优先做 P0/P1 的概念下沉，规模问题在 P0/P1 完成后重测；必要时增加聚合视图（事实/来源/待确认）。
- **是否需要后端配合**：**否**（验证阶段）。
- **状态**：**已修复（2026-09-29）**。验证结论：文档/实体列表原本就分页；新补三处规模护栏——① 时间线事件、② 研究问题列表改为按页加载（后端 `/api/events`、`/api/questions` 已支持 offset）；③ 图谱上限从 120 提到 200 并显式提示"仅展示部分网络"。审核队列（`/api/review`）与知识变化（`/api/claim-relations`）后端默认上限 200 且不支持 offset，按 P2-1"不新增后端"约束，改为非静默截断提示（"仅展示前 200 条"）。架构红线全程未触碰。

---

## 汇总（按修复顺序建议）

| 顺序 | ID | 严重度 | 是否后端 | 一句话 |
|---|---|---|---|---|
| 1 | P0-1 | P0 | 是 | OneBox 词表补裸"什么/谁"，救回访留存 |
| 2 | P0-2 | P0 | 否 | 默认隐藏 QA 的 Agent 模式区别 |
| 3 | P0-3 | P0 | 否 | 研究页主语人话化（研究发现/冲突/健康度） |
| 4 | P1-1 | P1 | 否 | 修 ObjectView "Ontology 注册表"弹窗 |
| 5 | P1-2 | P1 | 否 | KnowledgeView 去 chunk/embeddings |
| 6 | P1-3 | P1 | 否 | 纠正审计去 operation_id |
| 7 | P1-4 | P1 | 否 | 设置页 Embedding/JSON 人话化 |
| 8 | P1-5 | P1 | 否 | 命令面板去 ReAct Agent |
| 9 | P1-6 | P1 | 否 | QA 去 llm_runs 表名 |
| 10 | P1-7 | P1 | 否 | 首屏价值锚点 + 导入引导 |
| 11 | P1-8 | P1 | 否 | 技术细节折叠中文化 |
| 12 | P1-9 | P1 | 否 | Home 增加"进行中"聚合 |
| 13 | P2-1 | P2 | 否 | 规模失控验证 |

> 唯一需要后端配合的是 **P0-1**（且仅为 `intent.py` 词表扩展，不涉及写路径）。
> 其余全部可在前端完成，且都应遵循三条架构红线。

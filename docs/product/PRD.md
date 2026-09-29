# LLM-Wiki 前端产品需求文档（PRD / 验收基准）

> **文档定位**：本文件是 LLM-Wiki 前端重构的**唯一正式规格与验收基准**。
> 所有前端改造以本文件为准；它替代且收敛了以下设计过程文档，后者作为支撑证据保留：
> - `FRONTEND-PAGE-MAP.md`（页面职责地图）
> - `USER-JOURNEY-AUDIT.md`（7 条真实用户旅程审计）
> - `UX-CONCEPT-MATRIX.md`（概念暴露矩阵）
> - `UX-PROBLEM-BACKLOG.md`（UX 问题清单，含 P0/P1/P2）
>
> **核心判据**：任何前端改动完成与否，最终由文末「§10 产品级验收标准」判定，而非"看起来漂亮"。
>
> **三条架构红线（改动不得破坏）**：
> 1. 禁止新增绕过 `Operation` 的写入口；
> 2. 禁止新增第二套 Current Knowledge（当前知识）判断；
> 3. 禁止新增重复 Read Model（读模型）。
>
> 后端已具备的复杂能力（`KnowledgeCompiler`、`ClaimStateResolver`、Operation、Evidence、Knowledge Evolution、Research Candidate）是这套简单界面的"发动机"，而不是 UI 本身。

---

## §1 产品定位

LLM-Wiki 不是"带 AI 的 Wiki"、不是"Entity / Claim / Graph 管理工具"、也不是"Agent 工作台"。

**它是一套「会自己维护的个人知识库」。**

用户只需要做三件事：

```
             LLM-Wiki
                 │
     ┌───────────┼───────────┐
     ↓           ↓           ↓
   交给我       问问我       帮我研究
     │           │           │
   保存/理解    查询知识      探索未知
     │           │           │
     └───────────┼───────────┘
                 ↓
           我的知识在持续成长
```

整个 UI 围绕一个闭环组织：

```
输入 → 理解 → 沉淀 → 使用 → 发现问题 → 纠正 → 知识演化 → 再次使用
```

**这是产品的唯一主叙事。** 任何功能若不能挂到这条闭环上，默认不做。

---

## §2 信息架构原则

**导航不要按技术架构设计。** 技术概念（Knowledge / QA / Research / Agent / Correction / Review / Extraction / Eval）是开发者理解系统的方式，不是用户理解的方式。

用户理解的是：

```
我的东西在哪里？  我能问什么？  我想研究一个东西怎么办？
```

最终导航只有 **4 个核心入口 + 设置**：

```
LLM-Wiki
│
├── 首页      一切的起点（OneBox + 最近发生 + 最近知识 + 知识状态）
├── 知识      我的知识（搜索 / 知识列表 / 知识详情）
├── 问答      问我的知识库，带依据，不对就改
├── 研究      我不知道的，帮我找、分析、形成结论
│
└── 设置      AI / 知识库 / Developer Mode
```

开发者才需要的界面（Review / Correction / Agent / Extraction / Eval / Ontology / Operations）**不进一级导航**，由 Developer Mode 决定是否出现（§7）。

---

## §3 页面规格

### 3.1 首页（最重要）

第一屏只回答一个问题：**"我现在可以让它帮我做什么？"**

- **不展示**统计 Dashboard（Knowledge: 2431 / Entities: 836 … 对第一次使用的人无意义）。
- **OneBox 保留并强化为统一入口**（见 §4）：它不再只是一个输入框，而是整个产品的入口。
- 第一屏结构（极简）：
  - 一句引导语（如"你的知识，交给我。"/"把知识交给我"）
  - OneBox 输入框（带 📎 导入、↑ 发送）
  - 三个平级动作：`导入资料` `问一个问题` `开始研究`（导入与提问共用 OneBox，研究是提问的延伸）
- **空状态（首次使用）**：只给一件事——"把你正在看的东西交给我"，按钮 `导入第一份资料`。

**第二屏：最近发生了什么**（解决"为什么还愿意回来"）。展示知识库最近的变化，而非统计：

```
● 刚刚   我理解了《OpenViking Architecture》— 新发现 12 条知识，关联 5 条已有知识
↻ 昨天   Apple 相关知识发生变化 — 发现一份新资料与已有信息存在差异
✓ 2天前  你纠正了「Apple CEO」— Tim Cook → John Ternus
```

**第三屏：最近知识**：卡片化（复用 `KnowledgeCard` `mode="compact"`），不要表格。

**辅助信息（轻量，非主视觉）**：

```
你的知识库
128 篇资料 · 2,431 条知识
最近 7 天 ↑ 24 条新知识  ↻ 6 条变化  ✓ 3 条被纠正
```

> 注：当前 `HomeView` 已基本落地上述结构（OneBox + 最近知识 + 最近知识变化 + 7 天 + 空状态 + "需要你判断"）。本 PRD 将其定为正式规格。

### 3.2 OneBox（产品核心，见 §4）

### 3.3 问答

- 页面名为「问答」，不叫"Knowledge QA / Agent QA"。
- 单一输入框 + 回答区 + **依据** + 历史问题。
- **删除 Knowledge / Agent 引擎选择**（普通用户不应理解此区别；仅 Developer Mode 暴露 Agent 模式，见 §7）。
- 回答下方展示**支撑知识**与**依据来源**（指向原文片段）。
- 答案旁有 `这条有问题` 入口，就地进入纠正流程（见 §3.6）。

### 3.4 知识

- 顶部搜索（知识优先于原文：先给"知识库里记着的事"，原文作为依据退到第二层）。
- 分类筛选：`全部 / 人物 / 项目 / 概念 / 事件`（按对象类型，不是技术类型）。
- 列表用 `KnowledgeCard`（`mode="normal"`）。
- 不第一版做独立 Graph 页面；关系理解放在详情页"相关知识"里（§3.5）。

### 3.5 知识详情

围绕 **"我知道什么？为什么知道？它发生过什么变化？"** 设计，**不是大而全的 Entity 管理页**。

```
← 返回知识
Apple
苹果目前的 CEO 是 John Ternus

当前信息
  John Ternus  ·  Apple CEO

为什么我知道？（依据）
  Apple 官方资料  2026-xx-xx
  Apple Leadership 2026-xx-xx
  相关研究        2026-xx-xx

知识变化
  Tim Cook（历史 CEO）  ↓  John Ternus（当前 CEO）

[ 这条知识有问题 ]
```

即 **Claim + Evidence + Evolution 的用户版本**。 Evolution 必须让用户看到"为什么以前是 A，现在变成 B"。

### 3.6 纠正流程（用户视角只有 3 步）

系统流程（Select Claim → Operation → Relation → Preview → Confirm）**不对用户可见**，用户只看到：

1. **这条信息有问题？** → 显示当前说法（如"Apple 的 CEO 是 Tim Cook"）
2. **你认为应该是什么？** → 输入框（支持自然语言一句话，如"不是 Tim Cook，是 John Ternus"）
3. 系统给出预览：
   ```
   Tim Cook   历史 CEO
        ↓
   John Ternus 当前 CEO
   [ 确认更新 ]
   ```
   → `✓ 知识已更新`

**自然语言能力是硬要求**：用户用一句自然语言即可完成纠正（对应验收标准第 5 条）。

### 3.7 研究

研究与问答必须明显区分：

- **问答**："我已经知道的东西，直接告诉我。"
- **研究**："我不知道，帮我找资料、分析，然后形成结论。"

**研究首页**：一个问题输入框 + `开始研究` + 最近研究列表（来源数 / 发现数）。

**研究详情（重点）**：过程人话化，禁止内部对象词（§8）。

```
研究问题 → 寻找资料 → 已找到 N 个来源 → 分析中 → 发现 M 个关键结论
────────────────────────────
研究结论（散文式结论）
来源 [1]..[n]
可以加入你的知识（候选知识卡片）
```

**Research 结果不直接变成 Current Knowledge**：必须经"采纳"动作（对应 `mode='candidate'`，验收标准第 7 条）。

---

## §4 OneBox：产品核心入口

OneBox 是连接 §3 三个动作的统一入口。用户输入一句话，背后是：

```
             OneBox
                │
          Intent Detection（意图识别，不调模型、不写任何东西）
                │
     ┌──────────┼──────────┐
     ↓          ↓          ↓
  Knowledge    Ask       Research
     ↓          ↓          ↓
   导入资料    回答知识    创建研究
                │
                ↓
             Correct（回答中发现不对 → 就地纠正）
```

**分流逻辑绝对不能让用户看到**——用户只看到"我理解为：…"，且读错了可一键改判。

支持输入：

| 输入 | 系统表现 |
|---|---|
| `https://xxx.com/article` | "我正在理解这篇文章……" → 导入 |
| `苹果现在的 CEO 是谁？` | 直接给出答案（读取类，立即执行） |
| `帮我研究 OpenViking 怎么降低 Token 消耗` | "已创建研究任务" |
| `Apple 的 CEO 不是 Tim Cook，是 John Ternus` | "我发现你的知识库里有一条相关信息，需要更新" → 纠正 |

三条产品规则（当前 `OneBox.vue` 已实现，定为规格）：
1. **读取直接执行，写入先确认**：问错只是重说一句；写错是用户对自己知识库的信任。
2. **读错了要容易改**：判断结果与理由常驻显示，可一键改判，不用重打。
3. **看得见的按钮背后一定有动作**：改判回到服务端唯一路由表（`app/intent.py` 的 `_route`），前端不留第二份映射。

---

## §5 问答与回答卡片（最重要组件之一）

回答结构：

```
苹果现在的 CEO 是 John Ternus。
────────────────────────────
依据
  ① Apple 官方资料
  ② 你保存的 Apple Leadership
  ③ 最近一次相关研究
  共 3 条依据

这条知识有问题？  [纠正]
```

**设计原则：「答案」和「知识」不彻底分开**。回答告诉用户"这是我现在认为正确的知识"，同时允许"这条不对"→ 进入纠正流程（§3.6）。

---

## §6 用户旅程总图

```
第一次打开 → 首页（OneBox）
   ├ 导入 → 理解 → 知识
   ├ 问答 → 回答 → 依据 → 纠正
   └ 研究 → 探索 → 候选知识 → 采纳
         ↓
       知识（使用 / 发现问题 → 纠正 → 演化）
         ↓
   知识持续演化 → 用户再次回来
```

---

## §7 Developer Mode

**普通界面隐藏复杂性 ≠ 系统删除复杂性。** 开发能力很多，但普通用户不能看到。

设置项：`Developer Mode  OFF / ON`

- **关闭（默认）**：导航只有 4 核心入口 + 设置。Agent / 抽取对比 / 评测 / Review / Correction 等技术界面不出现。
- **打开**：导航下方出现「开发者」分组，暴露维护系统本身才需要的界面。

原页面迁移映射（路由直连仍可用，仅控制侧栏可见性）：

| 原页面 | 新产品位置 |
|---|---|
| HomeView | 首页 |
| KnowledgeView | 知识 |
| QaView | 问答 |
| ResearchView | 研究 |
| CorrectionView | 内嵌 QA / Knowledge（不在一级导航） |
| ReviewView | Developer Mode |
| AgentView | Developer Mode |
| ExtractionExperimentView | Developer Mode |
| EvalView | Developer Mode |
| Ontology | Developer Mode |
| Operations | Developer Mode |
| Chunks | Developer Mode |
| Embeddings | Developer Mode |

> 当前 `App.vue` 已实现 Developer Mode 折叠（Agent 工作台 / 抽取对比 / 评测）。本 PRD 要求补全到上述完整映射，尤其 `Review` / `Correction` / `Ontology` / `Operations` 的入口。

---

## §8 研究页的概念翻译（强制）

普通模式下，以下内部词**禁止**出现在界面：

```
❌ Claim ID  ❌ Predicate  ❌ Entity ID  ❌ Research Task ID
❌ Agent  ❌ Run ID  ❌ Context  ❌ Operation ID  ❌ LLM Runs
```

替换为用户能理解的 6 个词：

```
问题  资料  发现  结论  来源  候选知识
```

Developer Mode 下可显示完整 Pipeline（Parse / Chunk / Extract / Normalize / Compile / Link / Validate / Persist + Operation / Claim / Evidence 计数）。

---

## §9 状态与组件体系

### 9.1 统一状态模型

所有异步过程统一为：

```
idle → working → success → error
```

- **导入** 对用户只显示：正在理解资料 → ✓ 已读取 → ✓ 已提取知识 → ✓ 已关联 → ✓ 已完成。
- **禁止** 向普通用户暴露：Parsing / Chunking / Embedding / Extraction / Entity Linking / Claim Compilation / Persistence。
- **Developer Mode** 下可看完整 Pipeline（见 §8）。

空状态 / 错误状态同样必须人话化（见 §10 验收第 9 条、错误状态示例）：

```
普通模式错误：
  "这份资料暂时没能完整理解。"
  "已保存原始资料，你可以稍后重新处理。"  [重新处理]
Developer Mode：
  ExtractionError · stage=object_classification …
```

### 9.2 组件体系（§19–§20）

最终前端核心组件：

```
AppShell
 ├── Sidebar
 ├── Topbar
 └── PageContainer

Core
 ├── OneBox
 ├── KnowledgeCard        ← 全局唯一知识展示，支持 mode 复用
 ├── AnswerCard
 ├── EvidenceList
 ├── ActivityItem
 ├── ResearchCard
 ├── CandidateKnowledgeCard
 └── CorrectionFlow

Research
 ├── ResearchTimeline
 ├── ResearchFinding
 ├── ResearchSource
 └── ResearchConclusion

System
 ├── EmptyState
 ├── LoadingState
 ├── ErrorState
 └── DeveloperPanel
```

**统一「知识卡片」是强制要求**：Search / QA / Research / Home **都不要各自实现知识展示**，全部复用 `KnowledgeCard`，通过模式区分：

| 场景 | mode |
|---|---|
| 首页 | `compact` |
| 问答 | `answer` |
| 研究（候选） | `candidate` |
| 知识 | `normal` |

---

## §10 视觉语言

**不要** Carbon 深色风 / "AI 科技风"（深黑背景、大量边框、绿色终端色、代码字体、发光渐变、复杂 Dashboard）。

**要**：高级生产力软件 + AI 的克制感（参考 Notion 简洁 / Mem 的 AI 感 / NotebookLM 研究感 / Linear 精致度，但不直接模仿任何一个）。

```
背景        非常浅的暖灰 / 米白
卡片        白色
边框        非常淡
圆角        10~14px
阴影        非常轻
字体        现代无衬线
主色        一个克制的品牌色（替换当前紫色发光渐变）
间距        宽松
内容密度    低
```

**页面宽度不铺满**：

```
Sidebar 220px  │  Content  max-width 1100px
OneBox 页甚至  max-width 900px
```

> 当前视觉使用紫色渐变品牌色（`#aa3bff` + `linear-gradient(120deg,#5b7cff,#8b67f7)`），属待收口项。本 PRD 要求改为暖灰底 + 克制品牌色 + 限宽。

---

## §11 产品语言词表（强制，比换颜色更重要）

任何界面文本一律使用产品语言，不暴露技术术语：

| 技术语言 | 产品语言 |
|---|---|
| Document | 资料 |
| Claim | 知识 |
| Entity | 对象 / 知识 |
| Evidence | 依据 |
| Research Task | 研究 |
| Finding | 发现 |
| Candidate Claim | 候选知识 |
| Operation | 更新 |
| Correction | 纠正 |
| Agent | 系统自动处理 |
| Extraction | 理解资料 |
| Current Knowledge | 当前信息 |
| Supersede | 知识变化 |
| Conflict | 信息存在差异 |

**一句话原则**：做任何前端功能前先问——"这是帮助用户完成任务，还是帮助用户理解我们的代码？"后者默认不放普通 UI。

---

## §12 前端实施顺序（分阶段，不一次推翻）

- **Phase 1 统一壳**：`AppShell` / `Sidebar` / `PageContainer` / `DeveloperMode` → 跑通 首页 / 知识 / 问答 / 研究 / 设置。
- **Phase 2 重做 Home**：OneBox + 最近发生 + 最近知识 + 空状态 + 导入入口（不改后端）。
- **Phase 3 重做 QA**：删 Knowledge/Agent 选择、单输入框、回答区、Evidence、Correction、历史问题。
- **Phase 4 重做 Research**：Question → Sources → Findings → Conclusion → Candidate Knowledge → Adopt（内部对象全翻译）。
- **Phase 5 重做 Knowledge**：Search → KnowledgeCard → KnowledgeDetail → Evidence → Evolution → Correction。
- **Phase 6 统一组件**：KnowledgeCard / AnswerCard / Evidence / CorrectionFlow / EmptyState / LoadingState / ErrorState 收口。

> 当前 Phase 1–5 的主体已在 `HomeView` / `QaView` / `ResearchView` / `KnowledgeView` / `App.vue` 落地；待收口集中在 Phase 6（统一组件抽取）+ 视觉（§10）+ 术语/导航对齐（§2/§7/§11）。

---

## §13 产品级验收标准（最终判定依据）

> 不要用"看起来漂亮"验收。逐条对照：

1. **第一次打开**：用户不看文档，能理解"这是一个帮我管理知识的 AI"。
2. **第一次导入**：用户 30 秒内找到"我要把资料交给它"。
3. **第一次提问**：用户不需要知道 Knowledge QA / Agent QA 即可直接问。
4. **问答**：回答能显示"为什么这么回答"（依据）。
5. **纠正**：用户可用一句自然语言（如"这个已经不是 Tim Cook 了，是 John Ternus"）完成纠正。
6. **研究**：用户理解"研究 ≠ 普通问答"，并看到 来源 → 发现 → 结论。
7. **Research → Knowledge**：研究结果不能直接冒充当前知识，必须经"采纳"。
8. **知识**：用户能知道"这条知识现在是什么"。
9. **知识演化**：用户能看到"为什么以前是 A，现在变成 B"。
10. **开发能力**：Developer Mode 打开后，原有 Agent / Extraction / Operation / Ontology / Eval 等仍可访问。

---

## §14 实现对齐状态（基线快照）

| 模块 | 状态 | 说明 |
|---|---|---|
| AppShell / 五空间导航 / Developer Mode 折叠 | ✅ 已落地 | `App.vue`；待补全开发入口映射（§7） |
| 首页（OneBox + 最近 + 变化 + 7天 + 空状态） | ✅ 已落地 | `HomeView`；与本 PRD §3.1 一致 |
| OneBox 意图分流 + 改判 | ✅ 已落地 | `OneBox.vue` + `utils/onebox.ts`；与 §4 一致 |
| 问答（单入口 + 依据 + 就地纠正） | ✅ 已落地 | `QaView`；Agent 模式已折叠至 Developer Mode |
| 研究（问题/冲突/任务/候选采纳/阶段时间线） | ✅ 已落地 | `ResearchView`；概念已基本人话化 |
| 知识（搜索知识优先 / 抽屉 / 图谱 / 时间线） | ✅ 已落地 | `KnowledgeView` |
| **视觉风格（§10）** | ✅ 已落地 | 暖灰米白底 + 白色卡片 + 单一克制品牌色（移除全部发光渐变）+ 圆角收敛 14 + 内容限宽 1120 |
| **术语与导航对齐（§2/§7/§11）** | ✅ 已落地 | 导航"我的知识库→知识" + 面包屑/搜索占位同步 + Developer Mode 补全"审核/纠正"（§2/§7）；§11 全量术语映射完成：对象详情英文分页标签（Claims/Relations/Evidence…→知识/关系/依据…）、What is it?/Type Decision/Key Claims 汉化、知识页"实体→对象""抽取→理解资料"、命令面板/知识卡/导入统计/纠正流程的 Claim/Entity/Predicate 泄漏全部产品语言化；Developer Mode 界面（审核/体检/纠正后台）保留系统语言 |
| **统一组件库（§9.2）** | ✅ 已落地 | 单一 `KnowledgeCard` 复用于 首页/搜索/知识/对象/知识详情（`compact` + 状态驱动动作：候选→采纳/忽略，否则→纠正/历史）；`AnswerCard`/`CandidateKnowledgeCard` 作为 answer/candidate 语境的专门变体，`EvidenceList`/`ActivityItem`/`ResearchCard` 等已独立抽取并复用；未逐字实现 `mode=` 命名 API，但「同一形状、不各自造布局」原则已落地 |

本基线文档与代码实现持续对照更新；任何改造完成后，需在对应模块回填状态并逐条过 §13 验收。

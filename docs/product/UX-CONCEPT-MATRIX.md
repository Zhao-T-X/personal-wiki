# 前端概念暴露矩阵（Concept Exposure Matrix）

> 扫描范围：`frontend/src` 全部 13 个 view + 关键组件（OneBox / ImportPanel /
> CommandPalette / ReviewQueue / IntegrityPanel / CorrectionFlow / KpiStrip /
> StatusTag）。判断依据：该词是否出现在**普通用户可见的文本/标签/标题/按钮/表格列/
> 状态名**中。
>
> 目标：**用户不需要学习 LLM-Wiki 的内部结构，只需要表达自己想做什么。**
> 凡把内部存储/管道术语当作界面主语的，都记为需要整改。

---

## Layer A — 用户必须知道（只允许保留）

这些是产品对用户承诺的价值词，界面应围绕它们组织：

```
知识    来源    答案    问题    研究    纠正    历史
```

- 当前首页（`HomeView` / `OneBox`）已用这组语言组织（✅ 范例）。
- "来源"对应后端 documents/chunks，但以"这篇文章/这段出处"呈现，不暴露 chunk。
- "历史"对应 Operation 审计，但需人话化为"这条知识的修改记录"（见 Backlog）。

---

## Layer B — 用户可以看到，但不需要理解

允许出现，作为状态/辅助信息，不作为界面主语，不要求用户解释其含义：

```
证据      当前状态    待确认    冲突    更新
```

- `ReviewView` 的"待审 / 待确认"属于此类（但要避免裸 `Claims` 当标题）。
- `StatusTag` 的状态名（`candidate`/`verified`/`archived` 等）经 `utils/status`
  中文化后属此类。
- `IntegrityPanel` 的"可能重复的主体 / 字面量未接入主体"已是人话（✅ 范例）。

---

## Layer C — 默认禁止出现在普通用户路径

这些词**只**能进入 Developer Mode / Detail 折叠 / Diagnostics：

```
Claim      Predicate    Operation    Run      TaskPacket
Agent      Context      Chunk        Embedding  Registry
Ontology   Repository
```

实测暴露情况（✓ = 普通用户路径可见；⚠ = 仅折叠/部分可见；DEV = 仅开发者模式）：

| 概念 | 普通用户路径实际暴露位置 | 状态 |
|---|---|---|
| **Claim** | `ResearchView`（`Total Claims`/`Candidate Claims`/`已保留 X 条 Claim`…）、`ReviewView`+`ReviewQueue`（`待审 Claims`/`下方「Claims」`）、`ObjectView`（`Key Claims`/`Claims` tab）、`ClaimView` 面包屑、`App.vue` 面包屑 | ✓ 严重 |
| **Predicate** | `ResearchView`（裸 `predicate`）、`ObjectView`（ Relations `→ predicate →`）、`CorrectionFlow`（`predicate: '谓词合法性'`） | ✓ |
| **Operation** | `CorrectionView`（`CORRECT 操作` / `操作 ID：operation_id` / `已生成 … CORRECT`） | ✓ |
| **Run** | `QaView`（`llm_runs · task_type=agent` raw 表名泄漏）、中文"运行记录" | ⚠ |
| **Agent** | `QaView`（`Agent 问答`/`打开 Agent 工作台`）、`ResearchView`（`KnowledgeAgent`/`ResearchAgent`）、`CommandPalette`（`提问 Agent（ReAct Agent）` 核心命令） | ✓ |
| **Context** | `ClaimView`/`ObjectView` 折叠"技术细节"（`Context`） | ⚠ |
| **Chunk** | `KnowledgeView`（`chunk #n` / `X chunks` / `Chunks` / `Chunks only`）、`ImportPanel` 已中文化（✅ 反例） | ✓ |
| **Embedding** | `KnowledgeView`（`Embeddings` 按钮）、`SettingsView`（`Embedding Model`/`Embedding Dimensions`/`embeddings`） | ✓ |
| **Registry** | `ObjectView` 编辑弹窗副标题"类型受 **Ontology 注册表**约束"（raw `Ontology`+注册表，普通路径醒目） | ✓ |
| **Ontology** | `ObjectView`（编辑弹窗 + 技术细节）、`ClaimView`（技术细节） | ⚠ |
| **TaskPacket** | 全前端 0 出现 | — |
| **Repository** | 全前端 0 出现 | — |

---

## 合规范例（保持，勿回退）

| 文件 | 做法 |
|---|---|
| `HomeView` / `OneBox.vue` | 只说"问/记/研究/纠正"，0 内部词 |
| `ImportPanel.vue` | "切分片段/抽取知识/断言/实体/关系" 中文化 |
| `IntegrityPanel.vue` | "知识体检/可能重复的主体" 人话 |
| `KpiStrip.vue` / `StatusTag.vue` | 纯渲染，标签来自父级，无硬编码禁止词 |
| `DatabaseView.vue` | 无 13 词（但本身是诊断页，仅在设置→高级） |
| `EvalDashboard.vue` / `ExtractionExperimentView.vue` | 开发者模式，本体已中文化 |

---

## 整改优先级（按"暴露醒目度 × 普通路径可达"）

1. **`ObjectView` 编辑弹窗 `Ontology 注册表约束`** —— 用户编辑实体时必看，raw 泄漏最刺眼。
2. **`ResearchView` 的 `Claims`/`predicate`/`Agent` 系列** —— 研究页主语全是内部词。
3. **`KnowledgeView` 的 `Chunks`/`Embeddings`** —— 用户搜结果/看文档时常见。
4. **`SettingsView` 的 `Embedding Model/Dimensions`** —— 设置页，但属普通可达。
5. **`CorrectionView` 的 `CORRECT 操作`/`operation_id`** —— 审计区暴露 Operation。
6. **`CommandPalette` 的 `提问 Agent（ReAct Agent）`** —— 核心命令，普通可达。
7. **`QaView` 的 `llm_runs` raw 表名** —— Run 泄漏进普通问答页。
8. **`ClaimView`/`ObjectView` 折叠"技术细节"** —— 需展开才可见，优先级最低。

> 整改手段统一为：中文化 / 折叠到 Detail / 移入 Developer Mode，**不得新增第二套
> 文案映射**（前端 `utils/onebox.ts` 的 `INTENT_LABEL` 已是唯一意图人话表，扩展在此处）。

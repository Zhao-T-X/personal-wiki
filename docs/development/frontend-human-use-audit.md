# Frontend Human-Use & Interaction Audit（Step 18 · 第一阶段）

> 目标不是「页面好不好看」，而是**一个第一次使用这个 Wiki 的普通人，能不能连续做完一件事**。
>
> 本轮只审查，不改业务 UI。所有结论都能落到 `file:line`。
>
> 配套文档：
> - 交互清单 → [frontend-interaction-inventory.md](./frontend-interaction-inventory.md)
> - 文案与术语 → [frontend-copy-audit.md](./frontend-copy-audit.md)

---

## 0. 范围与方法

| 项 | 内容 |
|---|---|
| 审查范围 | `frontend/src/` 全部 13 个 view、26 个 component、`stores/`、`api/`、`utils/`、`styles/` |
| 审查方式 | 按「用户任务」走查，不按页面逐页看；每条结论回读源码定位 |
| 不做的事 | 不新增功能 / 不新增 Agent / 不改后端业务规则 / 不改数据库 / 本轮不大规模改 UI |
| 验证基线 | `pytest -q` → **660 passed, 6 deselected**；`npm --prefix frontend run build` → **通过**（vue-tsc -b + vite build） |
| 判定原则 | 不因为「页面能正常 render」就算通过；只要存在 no-op / 错误态被表达成空态 / 高风险误操作，一律不通过 |

已确认**方向正确、无需推翻**的结构（本轮不再重复论证，只作为既有资产）：

- 五个一级空间（首页 / 知识 / 问答 / 研究 / 设置）对应五种「我要做什么」，见 `App.vue:24-30`；
- 审核 / 纠正 / Agent / 评测不进一级导航，而是长在上下文里（`App.vue:15-23` 注释 + `App.vue:83-87` 的「需要你确认」状态项）；
- One Box「一个输入、四条既有路径」，且严格走既有接口（`OneBox.vue:1-16`）；
- `KnowledgeCard` 是同一个知识形状在所有出现处的唯一实现（`KnowledgeCard.vue:1-16`）；
- 证据 / 历史 / 原文定位都要能回到 source chunk（`ClaimView.vue:86-92`、`ReviewQueue.vue:117-141`）；
- 高风险写操作有影响预览（`IntegrityPanel.vue:243-284`）和可撤销 toast（`stores/app.ts:128-142`）。

---

## 1. 一句话结论

> **产品结构是对的，但"人对系统的信任"在三个地方被打断了：系统失败时假装没有数据、纠正入口预填了要用户修改的那一句话的反面、以及 One Box 换意图后「按这个执行」是个空操作。**

其余问题集中在：入口过多且互为主入口、手机端丢掉「需要你确认」、以及技术词（polarity / modality / claim_id / operation_id / Embeddings / AgentScope）出现在普通用户路径上。

---

## 2. Human Task Matrix

| 用户任务 | 入口 | 完成路径 | 中断点 | 技术术语泄漏 | 是否迷路 | 问题等级 |
|---|---|---|---|---|---|---|
| 第一次使用 | `/`（首页） | 首页 → 拖入 或 One Box | 无（首屏只有一件事，做得好） | 无（`HomeView.vue:178-180` 是纯人话） | 否 | **P3**（仅「把知识交给我」与「记点什么」两种说法要不要统一） |
| 导入文档 | 首页 dropzone / 知识页 ImportPanel | 拖入 → 读取/解析/切分/抽取 四阶段 → 「我从这篇整理出了什么」→ 去确认 | **LLM 未配置或抽取失败时，导入面板内没有 Retry，也没有「下一步去哪里」**（`ImportPanel.vue:157-161, 173-177`） | `实体/断言/关系/事件/想法/问题` 尚可，但 `pending` 计数只说「需要你确认」不说去哪 | 否 | **P1-3** |
| 问问题 | 首页 One Box / 顶栏 ⌘K / 问答页 / 命令面板 / 知识页搜索框 ◎ | 任一入口 → 答案 → 支持知识 → 全部依据 | 无功能性中断 | `Hybrid 检索 / Grounded / AgentScope ReAct`（`QaView.vue:187-188`）、`检索轨迹` 里的 `hybrid/semantic` 原样显示（`QaView.vue:85-89, 334-341`） | **是**：5 个入口都像主入口 | **P1-11**（入口）、**P1-9**（术语） |
| 纠正知识 | 答案旁「这条有问题」/ 知识卡「纠正」/ `⌘K → 纠正一条知识` | 答案 → 展开 → 分析 → 已有知识→建议 → 确认修改 → toast | **预填内容是错的**：QA 给的是"问题"，知识卡给的是"原文引用" | `Schema/Evidence/Quote/实体解析/谓词合法性` 维度名（`CorrectionFlow.vue:43-47`） | 否（就地展开，不跳页，做得好） | **P0-3**、**P1-1** |
| 研究 | 首页 One Box（intent=research）/ 研究页 Questions | 问题 → 「继续研究」→ 阶段执行 → Findings → 研究候选 → 采纳 | **点「继续研究」就已经创建了任务**，收起后留下孤立任务且**没有删除入口**（`ResearchView.vue:119-131, 274-276`） | `Questions / Conflicts / Health / Total Claims / Findings / Candidate` 中英混用；冲突卡显示 `polarity / modality`（`ResearchView.vue:21-22, 224-229, 355-357`） | 部分：QA 与 Research 的边界只能靠文案猜 | **P1-4**、**P1-7** |
| 审核 | 首页「N 项需要你的判断」/ 侧栏「需要你确认」/ 知识页提示条 | 首页 → 审核 → 分组数字 → 实体/Claims/关系 → 通过/拒绝 → 撤销 | **手机端完全没有这个入口**（`App.vue:66, 121-126`） | `confidence 90% / modality · polarity / claim_type`（`ReviewQueue.vue:23, 229-234`） | 否（桌面不迷路） | **P0-2**（错误态）、**P1-2**、**P1-7** |
| 合并实体 | 知识页 / 审核页的「知识体检」 | 发现 → 查看影响 → 保留谁 → 合并 → 结果（含新冲突）→ 去处理 | 影响预览里的 `claims_moved / affected_relations` 是内部量 | `claims_moved / relation / 单值关系`（`IntegrityPanel.vue:254-267`） | 否 | **P2**（这是当前做得最完整的一条链，见 §9） |
| 查证据 | 任何 `KnowledgeCard` 的「依据」 | 卡片 → 依据 → 来源文档抽屉 + 高亮 chunk | 从审核队列跳走后再回来，筛选/展开全部重置（`ReviewQueue.vue:31-37`） | `chunk # / offset [start, end) / Chunks (n)`（`KnowledgeView.vue:309, 420`、`ObjectView.vue:348`） | 否 | **P2** |
| 手机使用 | 底部栏 5 空间 + 命令 | 首页 → 问答 → 纠正 → （找不到审核）→ 知识详情 | **审核入口缺失**；抽屉宽 `min(430px,94vw)` 且无遮罩、无 Esc（`tokens.css:94`、`AppDrawer.vue`） | 同桌面 | **是**（审核） | **P1-2**、**P1-14** |
| 出错恢复 | 任意 | —— | **多处 `await api(...)` 没有 catch**，失败后页面渲染成"空数据/已经清空" | —— | **是**（以为是空库） | **P0-2** |

---

## 3. 十个场景逐条走查

### 场景 1：第一次打开产品（空知识库）

**FIRST_USE_RESULT**

| 检查项 | 结论 | 证据 |
|---|---|---|
| 1. 第一屏能否立即知道该做什么 | **能**。标题「把知识交给我」+ 一个 dropzone 是首屏唯一动作 | `HomeView.vue:161-168` |
| 2. 是否同时出现太多概念 | **不会**。`hasContent === false` 时，最近知识 / 最近变化 / 开放问题 / 最近加入 都不渲染 | `HomeView.vue:117-118, 201, 244-245` |
| 3. 能否在 10 秒内完成第一次动作 | **能**（拖入，或点示例）。但空库时示例问题被隐藏，只留一句提示 | `HomeView.vue:175-180` |
| 4. 「把知识交给我」是否比「知识库/文档/Entity/Claim」更贴近第一次使用 | **是**，明显更好。首屏一个「知识库」字样都没有 | `HomeView.vue:163` |
| 5. One Box 是否让用户知道可以问 / 记 / 研究 / 纠正 | **勉强**。placeholder「问点什么、记点什么、研究点什么…」覆盖了问/记/研究，**唯独没说纠正**；而纠正是产品最高价值动作 | `OneBox.vue:97` vs `OneBox.vue:41-43` |

遗留问题：`HomeView.vue:176` 的示例问题是 `<span class="tag" @click>`，键盘不可达（P1-10）。

---

### 场景 2：导入第一篇文档

完整链路 `ImportPanel.vue` 走查：**Import → Parse → Chunk → Extraction → 结果**

| # | 用户需要知道的事 | 是否做到 | 证据 |
|---|---|---|---|
| 1 | 现在到哪一步 | **做到**。四阶段 chip，active/done/failed 三态 | `ImportPanel.vue:58-65, 246-252` |
| 2 | 成功还是失败 | **做到**（阶段变色 + `item.error`） | `ImportPanel.vue:247-253` |
| 3 | 提取到了什么 | **做到**。这是全产品最好的一处：实体 chips + 三元组 + 「其中 N 条需要你确认」 | `ImportPanel.vue:257-279` |
| 4 | 哪些需要自己确认 | **做到**（数字 + 「去确认这 N 项 →」） | `ImportPanel.vue:236-238, 273-276` |
| 5 | 下一步做什么 | **部分**。给了「去审核」「查看原文」「关于这篇你可以问」；但**失败时只留一句错误文案** | `ImportPanel.vue:281-288` vs `173-177` |
| 6 | 是否要自己重新找 Review | **不要**（面板内有按钮） | `ImportPanel.vue:236-238` |
| 7 | 能否直接回到原文 | **能** | `ImportPanel.vue:287, 298` |
| 8 | 失败时知道为什么吗 | **知道原因，但不知道怎么办** | `ImportPanel.vue:157-161` |
| 9 | 有没有 Retry | **没有**。必须关掉队列 → 去知识页 → 打开文档抽屉 → `Index with LLM` | `ImportPanel.vue` 无 retry；`KnowledgeView.vue:399-404` |
| 10 | 「系统完成了，但用户不知道完成了什么」 | **基本避免**（靠 `loadKnowledge` 回读真实结果） | `ImportPanel.vue:94-101` |

**结论**：导入是全产品完成度最高的一条链。唯一缺口是**失败/未配模型的恢复路径**（P1-3）。

---

### 场景 3：用户问一个已知问题

两条路径都试：

- `Home OneBox → intent=ask → 自动执行 → 答案 + 支持知识`（`OneBox.vue:58-59, 128-138`）
- `知识 → 搜索 → KnowledgeCard → 依据`（`KnowledgeView.vue:286-315`）

| # | 检查项 | 结论 |
|---|---|---|
| 1 | 哪个入口最自然 | 首页 One Box（一句话就够）。但要先输入再「解释意图」——**答案不是第一屏**，用户要先看到「我理解为：查你的知识」才能看到答案，因为 `obplan` 与结果同屏渲染（`OneBox.vue:111-125`） |
| 2 | 搜索和问答是否容易混淆 | **是**。知识页搜索框右侧那个 `◎` 按钮点击即跳问答、顶栏 `⌘K` 面板标题写「搜索知识，或直接提问…」但点开是命令面板，面板里又同时存在「提问知识库」「提问 Agent」「搜索知识」三项（`KnowledgeView.vue:266-270`、`App.vue:111-113`、`CommandPalette.vue:19-21`）→ **同一个意图 5 条入口，没有主入口**（P1-11） |
| 3 | 答案是否第一眼就是答案 | **是**。`MarkdownView` 是主角，卡片在下面（`QaView.vue:229-238`），这个顺序是对的 |
| 4 | 是否知道答案来自自己的知识库 | **是**（「支持知识 N 条 · 让这个答案站得住的事实」） |
| 5 | 是否知道为什么是这个答案 | **是**（全部依据 + 知识边界 + 检索轨迹三层） |
| 6 | Evidence 是否容易打开 | **是**。但 `QaView.vue:313` 用 `<div class="item" @click>` 而非 button → 键盘不可达（P1-10） |
| 7 | 是否会看到内部 Claim / Predicate / ID | **会**：`QaView.vue:336-340` 检索轨迹把 method 原样打印（`hybrid` / `semantic`）；`QaView.vue:281-289` 引用校验的维度名原样显示（P1-9） |
| 8 | 「这条有问题」是否自然 | **位置自然**（`QaView.vue:256-261` 就长在答案下面）；**但点开后的内容不对**（见场景 4，P0-3） |

---

### 场景 4：用户发现答案不对（产品最高价值交互）

完整走：`QA → 这条有问题 → 纠正 → 修改 → 确认 → 更新 → 再问`

| # | 检查项 | 结论 | 证据 |
|---|---|---|---|
| 1 | 是否必须离开当前上下文 | **不必**，就地展开（做得对） | `QaView.vue:262-263` |
| 2 | **输入框默认内容是不是用户真正想修改的东西** | **不是。这是 P0。** `:seed="question"` → textarea 预填「苹果的 CEO 是谁？」而 placeholder 却说「哪里不对？例如：苹果现在的 CEO 是 John Ternus。」 | `QaView.vue:262`、`CorrectionFlow.vue:36, 141-142` |
| 3 | 是否需要重新解释是哪条知识 | 会。因为预填的是问题，用户必须先想清楚"我到底要改哪条" | 同上 |
| 4 | 现有知识是否清楚 | **清楚**。`已有知识 → 建议` 的 diff 是全文里最好的设计之一 | `CorrectionFlow.vue:168-185` |
| 5 | 新知识是什么是否清楚 | **清楚**（`proposed.subject·predicate·object`）——但 `predicate` 是标识符（如 `has_ceo`），非人话 | `CorrectionFlow.vue:182`（P1-9） |
| 6 | 旧知识会发生什么是否清楚 | **清楚**。「旧知识不会被删除，会保留为历史」+ 关系标签 | `CorrectionFlow.vue:54-63, 183` |
| 7 | 是否有二次确认 | **有**（先分析再点「确认修改」），符合「AI 不静默改知识」 | `CorrectionFlow.vue:212-218` |
| 8 | 完成后是否明确告知「现在已经更新」 | **是**。toast「知识已更新 · 再问一次会得到新答案」+ 撤销/查看 | `QaView.vue:41-45, 110`；`OneBox.vue:149` |
| 9 | 能否一键再次提问 | **不能**。toast 的 action 是「看这条知识」，没有「再问一次」 | `QaView.vue:43` |
| 10 | 修改后是否立即看到新状态 | **不一定**。QA 页答案仍是旧答案（`result` 未清），用户会看到"改了但答案没变" | `QaView.vue:40-45`（只 `fixOpen=false`） |

**core finding**：第 2 项是明显的「预填错误上下文」——用户被要求修改自己刚提出的问题。这会直接导致把问题句当成纠正句提交给 `/api/knowledge/corrections`（`CorrectionFlow.vue:79`）。

---

### 场景 5：用户第一次看到 Research

| # | 检查项 | 结论 |
|---|---|---|
| 1 | 知道 Research 和 QA 的区别吗 | **不知道**。研究页副标题是`Question → Evidence → Claims → Review → Knowledge，一个闭环。`（`ResearchView.vue:220`），这是流程图不是人话 |
| 2 | 是否必须理解 Research Task | **是**。Tab 名直接叫「研究任务」（`ResearchView.vue:21, 237-238`） |
| 3 | 知道什么时候该 Research 而不是 Ask | **不知道**。One Box 会替你判断（`OneBox.vue:41-43`），但当判断错误时，用户只能看到两个按钮「其实我是想问 / 其实我是想研究」——**这正是 `override()` 的 no-op 现场**（P0-1） |
| 4 | Findings 和 Knowledge Candidate 是否容易区分 | **基本可以**：「Findings」是散文，下面另有「研究候选 N 条 · 采纳后才成为知识」 |
| 5 | 「研究候选」是不是明显不是事实 | **是**（卡片上带 `candidate` 状态标签 + 「采纳后才成为知识」） |
| 6 | 采纳入口是否清晰 | **是**（`KnowledgeCard` 按状态给「采纳/忽略」） |
| 7 | 研究完成后下一步是否明确 | **弱**。Findings 藏在 `<details>` 里（`ResearchView.vue:245-248`），完成后用户看到的是"任务多了一条" |
| 8 | 是否容易留下孤立的 research task | **非常容易，且无法清理**。`openFlow()` 一被点击就 `POST /api/research`（`ResearchView.vue:119-131`），用户随后点「收起」即留下一条无入口的任务；全前端没有任何删除/归档 research task 的动作 |

---

### 场景 6：用户需要审核知识

| # | 检查项 | 结论 | 证据 |
|---|---|---|---|
| 1 | 为什么这条需要审核 | **部分**。展开详情能看到来源原文/重复实体，但折叠态只有一行候选 | `ReviewQueue.vue:244-296` |
| 2 | 最少必要的证据是否可见 | **是**（来源原文 + 打开原文并定位） | `ReviewQueue.vue:277-283` |
| 3 | 是否要跳多个页面 | 不必须，但详情是折叠的，用户容易只看到标题就点「通过」 | `ReviewQueue.vue:238-240` |
| 4 | Accept / Reject 是否容易误点 | **容易**。逐条「通过/拒绝」是并排的小按钮、无确认，撤销只在 6 秒 toast 里 | `ReviewQueue.vue:239-240`、`stores/app.ts:136` |
| 5 | Undo 是否容易发现 | 只在 toast（`label: '撤销'`）出现 6 秒；页面内没有任何"最近决定"列表 | `ReviewQueue.vue:167`、`IntegrityPanel.vue:290-299`（后者有，前者没有） |
| 6 | 批量接受是否让用户觉得安全 | **不安全**。「接受高置信（N）」无确认、无预览、无逐条说明 | `ReviewQueue.vue:215-217, 182` |
| 7 | 「90% confidence」对普通用户有没有意义 | **没有**。它既不是概率（抽取器给的），也没有说明"90% 意味着什么" | `ReviewQueue.vue:23, 63-66, 229-231` |
| 8 | 能否区分候选知识 / 已确认知识 / 冲突 / 重复主体 | **部分**。四类分散在三处（inbox 计数、知识体检、知识变化清单），页面顶部同时铺 6 个数字 | `ReviewView.vue:22-28, 47-57`、`IntegrityPanel.vue:200-240`、`ClaimChanges.vue:175-206` |

**重复数字问题（P1-7）**：`ReviewView.vue:47-57` 用 `/api/review/inbox` 渲染 6 个格子，紧接着 `ReviewQueue.vue:194-199` 又用 `/api/review` 渲染 4 个格子（含「合计」）。两个数字来源不同、口径不同（inbox 含 `entity_duplicates` 与 `claim_conflicts`），同屏并存 = 用户第一次看到就会怀疑哪个对。

---

### 场景 7：发现两个可能重复的实体

链路：`发现 → 查看影响 → 选择保留 → Merge → 查看后果 → 出现冲突 → 去处理`

| 检查项 | 结论 | 证据 |
|---|---|---|
| 「保留谁 / 删除谁」是否理解 | **理解**。按钮文案是「合并到「X」」和「保留「Y」」，而不是 keep/drop | `IntegrityPanel.vue:278-283` |
| Merge 会发生什么 | **说清楚了**：「合并后 X 的 N 条知识会归到 Y 名下；两边原有的名称都会保留为别名」 | `IntegrityPanel.vue:250-253` |
| 是否依赖 entity_id/claim_id/operation_id | **不依赖**（这是全产品术语控制最好的一处） | 同上 |
| 后果是否可见 | **可见，并且提前说了**：「合并不会解决事实冲突——它可能产生 N 个新冲突」 | `IntegrityPanel.vue:262-273` |
| 冲突去哪儿处理 | **去了真的会列出它的地方**（`/review` 的知识变化清单，不是冲突中心） | `IntegrityPanel.vue:179-186`、`utils/issues.ts:35-42` |

**结论：这是当前最接近"用户在工作，而不是在操作后台"的一条链。** 仅两处可改：影响预览里的 `claims_moved / affected_relations / 单值关系` 是内部量（P2）；`swap()` 会重新发一次 `merge-impact` 请求，按钮上文字随预览变化，用户可能误点（`IntegrityPanel.vue:80-84, 282`）。

---

### 场景 8：查看一条知识为什么成立

`Apple → CEO → John Ternus`：`KnowledgeCard → Evidence → History → Related Claims`

`ClaimView.vue` 的自然阅读顺序：**知识卡 → 历史 → 依据 → 相关断言 → 技术细节（折叠）**

| 检查项 | 结论 |
|---|---|
| 当前答案 / 依据 / 历史 / 变化原因 是否形成自然阅读顺序 | **是**，顺序正确，且「历史」用竖线把每次变化串起来，是本产品最有说服力的一屏（`ClaimView.vue:121-161`） |
| Claim Type / Polarity / Modality / Confidence / Ontology 是否抢第一屏 | **没有**，全部收进 `<details>`（`ClaimView.vue:212-242`）——这一点做对了 |
| 遗留 | ① 「确认这条 / 标记不成立」在页面底部，但详情页本身没有说明这两个动作对图谱与问答的影响（`ClaimView.vue:204-209`）；② 技术细节里仍有 `Modality = possible` + `Normalize 规则` 的解释句（`ClaimView.vue:240`）——它折叠着，但一旦展开就是纯内部语言（P2） |

---

### 场景 9：手机使用

| # | 检查项 | 结论 | 证据 |
|---|---|---|---|
| 1 | Review 是否容易发现 | **完全不可能**。`MOBILE_ITEMS = items`，底部只有 5 个空间 + 命令；`store.pendingReview` 只在桌面侧栏用一个 `div.nav.due` 渲染 | `App.vue:66, 83-87, 121-126` |
| 2 | 搜索是否容易触发 | 可以：底部「命令」按钮打开同一个 `CommandPalette` | `App.vue:125` |
| 3 | Bottom bar 是否与桌面表达同一套概念 | **是**（同一数组），但正因为如此，它继承了"桌面靠角标、手机没有角标"的缺口 | `App.vue:66` |
| 4 | 操作按钮是否太多 | `ObjectView` 页首 4 个按钮 + 8 个 Tab（`ObjectView.vue:219-224, 242`），375px 下要横向挤 | P1-6 |
| 5 | 内容是否容易横向溢出 | `DataTable` 无 `overflow-x` 容器，`QaView` 的「最近问答」5 列表格在小屏会撑开 `.content` | `DataTable.vue:20-33`、`QaView.vue:176-179` |
| 6 | 重要操作是否仍然可见 | 纠正、采纳、依据都在组件内，OK | —— |
| 7 | modal / drawer 是否可退出 | `AppModal` 点遮罩可关；**`AppDrawer` 只有 `×`**（宽 `min(430px,94vw)`，无遮罩、无 Esc） | `AppModal.vue:7`、`AppDrawer.vue:10`、`tokens.css:94` |

---

### 场景 10：错误和慢请求

| # | 场景 | 当前行为 | 结论 |
|---|---|---|---|
| 1 | LLM 未配置 | 导入走到 chunk 即停，`item.error` 写「未配置语言模型…配置模型后可运行「用模型抽取」补上知识」，但**面板内没有那个按钮** | **P1-3** |
| 2 | `/api/review` 失败 | `ReviewQueue.vue:31-37` 无 catch → promise reject，`data` 保持初始空数组 → 渲染 `EmptyState「这里已经清空了」/"没有等待你判断的候选知识"` | **P0-2**（最严重：失败 = 全部清空） |
| 3 | `/api/documents`、`/api/entities`、`/api/events` 失败 | `KnowledgeView.vue:94-105, 131` 无 catch → 「还没有文档——导入或新建一篇」 | **P0-2** |
| 4 | 首页任一接口失败 | `HomeView.vue:120-146` 无 catch → `hasContent=false` → **整个产品回退成"全新空库"首屏** | **P0-2** |
| 5 | `/api/ask` 超时 / 失败 | `store.toast(e.message)`，`loading=false`，页面回到「输入问题开始提问」——**旧答案被 `result=null` 清掉了**（`QaView.vue:122`） | P1（不可重试且丢失上一次结果） |
| 6 | Research 失败 | `store.toast(e.message)` + `await load()`，阶段定格为 failed（`ResearchView.vue:189-193, 315-319`） | 可接受 |
| 7 | Correction 失败 | `error.value = e.message` 内联红框（`CorrectionFlow.vue:113-115, 150`） | **做得好**（唯一内联错误） |
| 8 | Merge 失败 | `store.toast`，预览保持（`IntegrityPanel.vue:100`） | 可接受 |
| 9 | 连续点击两次 | 大部分 mutation 有 `busy/disabled`（`ReviewQueue`、`IntegrityPanel`、`CorrectionFlow`）；**`QaView.send()` 无 busy 守卫**（`QaView.vue:205-206`，输入框与按钮都没 disable） | P1 |
| 10 | loading 时返回 | 抽取进度放全局 store，切页不丢（`stores/app.ts:143-158`、`RunProgress.vue`） | **做得好** |

**系统性结论（P0-2）**：前端只有两种错误处理风格——`store.toast` 或 `catch {}` 后把状态设成空值。**没有任何一处把"加载失败"表达成"加载失败"**（`ObjectView.vue:417-424` 与 `ClaimView.vue:245-252` 有错误页，但那是"对象不存在"，不是加载失败）。详见 §8。

---

## 4. 重点核实项 A–G

| 项 | 结论 | 证据 |
|---|---|---|
| **A. OneBox override** | **确认存在 P0 no-op。** `override()` 把 `steps` 置空并强制 `needs_confirmation: true`，于是「按这个执行」按钮出现（`needsConfirm` 为真），点它进入 `run()`，而 `run()` 第一行就是 `if (!plan.value?.steps?.length) return` → 静默返回。toast 还会说「先选一个意图，我再按它执行」，但**界面上根本没有选择意图的控件** | `OneBox.vue:81-86`（override）→ `OneBox.vue:121-123`（按钮）→ `OneBox.vue:64-65`（空返回） |
| **B. QA → Correction** | **确认 seed 传错。** `:seed="question"`，用户看到并要修改的是自己刚问的问题 | `QaView.vue:262`、`CorrectionFlow.vue:36` |
| **C. KnowledgeCard → Correction** | **确认 seed 是原文引用。** `:seed="claim.quote"`，而 placeholder 期待的是"要改成什么" | `KnowledgeCard.vue:130`、`CorrectionFlow.vue:141-142` |
| **D. Mobile Review** | **确认丢失。** 移动端只有 5 个空间 + 命令，没有待确认数量也没有审核入口 | `App.vue:66, 121-126`；对比 `App.vue:83-87` |
| **E. Knowledge Detail 8 tabs** | **确认过多。** `Overview / Claims / Relations / Graph / Events / Evidence / Questions / Ideas` 一次性平铺，其中 Relations 与 Graph 是同一件事的两种看法，Events/Questions/Ideas 是"文档级"数据被挂在对象上 | `ObjectView.vue:241-244`；`ObjectView.vue:327-340` 的 `Events` 自己就声明「事件本身不直接关联实体」 |
| **F. QA 双模式** | **确认不应由用户选择。** `知识库问答 / Agent 问答` 是显式分段控件，副标题还把两种实现的技术差异讲给用户听；同时「下一步」区又提供了互相切换的按钮（`换用 Agent 深入回答` / `换用知识库问答（带证据）`），等于承认**用户不需要一开始就选** | `QaView.vue:22-23, 193-202, 344, 359` |
| **G. Review 90% 批量接受** | **确认不够安全。** `HIGH_CONFIDENCE = 0.9` 直接驱动一个无确认的批量写操作，且用户看不出"90%"从哪来、意味着什么；`kindHint` 只解释了阈值，没有解释后果 | `ReviewQueue.vue:23, 68-78, 182, 215-217` |

---

## 5. P0 问题清单

### P0-1 One Box 换意图后「按这个执行」是空操作

## Issue
切换意图会把执行步骤清空，但执行按钮仍然存在并且点了什么都不做。

## User Situation
用户说「苹果的 CEO 是谁」，系统判断成「去研究」，用户点「其实我是想问」，然后看到「按这个执行」——点下去毫无反应。

## Current Behavior
`override()` 写入 `{ intent, needs_confirmation: true, steps: [] }`；`needsConfirm` 因此为真 → 渲染「按这个执行」；`run()` 首行 `if (!plan.value?.steps?.length) return` 直接返回，既无 toast 也无状态变化。toast 反而提示「先选一个意图，我再按它执行」，而界面上没有任何意图选择器。

## Expected Behavior
要么按新意图重新请求计划并生成可执行的 steps，要么把按钮收起来、明确要求用户重新输入一句话（并说明"换意图需要把话改写成问句/研究句"）。

## Code
- `frontend/src/components/OneBox.vue:81-86` — `override()`，`steps: []`
- `frontend/src/components/OneBox.vue:121-123` — 「按这个执行」按钮（`v-if="needsConfirm"`）
- `frontend/src/components/OneBox.vue:64-65` — `run()` 的空返回
- 交互路径：`override('ask')` → `run()` → return

## Impact
用户以为系统会照他的意图重做，实际什么都没发生；且这条路径正是"系统判断错意图"的唯一补救手段——补救本身失效，用户只能重新打一遍原句。

## Recommendation
`override()` 不再清空 steps，而是以 `intent` 重新调用一次计划生成（或在 `needs_confirmation` 为真时不渲染执行按钮，只渲染一句「改成这样的说法我再执行：<示例>」+ 聚焦输入框）。若保留 `steps: []` 语义，`run()` 必须把"没有可执行步骤"转成一条明确提示，不能静默返回。

## Priority
**P0**

---

### P0-2 加载失败被渲染成「没有数据 / 已经清空」

## Issue
多个页面对 `api()` 调用没有错误处理，请求失败后页面呈现为空库，用户无法区分"我没有知识"和"系统坏了"。

## User Situation
后端短暂不可用（或某个接口 500）。用户打开审核页，看到「这里已经清空了 / 没有等待你判断的候选知识」，于是以为审核已经做完；打开首页，看到全新空库的首屏，以为数据丢了。

## Current Behavior
| 位置 | 代码 | 失败后渲染 |
|---|---|---|
| `ReviewQueue.vue:31-37` | `data.value = await api('/api/review')`，无 try/catch | `EmptyState「这里已经清空了」`（`ReviewQueue.vue:299-302`） |
| `KnowledgeView.vue:94-105, 131` | `loadDocs/loadEntities/loadEvents` 无 catch，`onMounted(loadAll)` | `「还没有文档——导入或新建一篇」`（`KnowledgeView.vue:337`） |
| `HomeView.vue:120-146` | `loadAll` 无 catch（仅 health 有 `.catch(()=>null)`） | `hasContent=false` → 整个首页回退到"全新空库"首屏（`HomeView.vue:117-118, 178-180`） |
| `ResearchView.vue:41-51` | `load()` 无 catch | KPI 全 0 + `「没有开放问题——知识库状态良好」`（`ResearchView.vue:297`） |
| `QaView.vue:70-82` | `loadMeta()` 无 catch | 最近问答空表 + `「暂无数据」` |

对照：`ReviewView.vue:32` 反而是 `catch { inbox.value = null }` → 顶部数字条整块消失（既不报错也不说明）。同一份数据，两种错误的静默方式。

## Expected Behavior
每个数据区块必须能区分三态：`加载中` / `加载失败（可重试）` / `已加载但为空`。失败时应保留上一次成功的数据或显示"读取失败"和重试按钮，绝不呈现为"空"。

## Code
见上表；参考已有的正确实现 `ObjectView.vue:82, 97, 417-424`、`ClaimView.vue:70, 245-252`（它们至少有 `loadError` 分支）。

## Impact
这是本轮唯一会直接摧毁用户信任的问题类型：用户会把系统故障理解成"我的知识没了"或"我已经处理完了"。尤其 `ReviewQueue` 的假清空会让人**放弃本来必须做的审核**，等于让未验证知识被静默当作不存在。

## Recommendation
引入统一的加载态封装（例如 `useResource<T>()` 返回 `{ data, loading, error, reload }`），把"失败"渲染成 `<EmptyState>` 的第三种形态（`title="读取失败"` + `#action` 重试按钮）。最低限度：给上述 5 处补 `catch`，把 `error` 单独存起来并在对应面板里显示可重试的提示。

## Priority
**P0**

---

### P0-3 「这条有问题」预填的是用户自己的问题

## Issue
QA 的纠正入口把原问题当作纠正内容预填进 textarea，用户被要求修改自己刚提出的问题。

## User Situation
问「苹果的 CEO 是谁？」，答案不对，点「这条有问题」，文本框里出现的是「苹果的 CEO 是谁？」——而 placeholder 写着「哪里不对？例如：苹果现在的 CEO 是 John Ternus。」。用户要么误点「分析」（把问题句提交给纠正接口），要么被迫自己重写。

## Current Behavior
`<CorrectionFlow compact :seed="question" ...>`（`QaView.vue:262`）；`CorrectionFlow` 用 `const text = ref(props.seed)` 初始化（`CorrectionFlow.vue:36`），`analyze()` 直接把 `text` 提交到 `POST /api/knowledge/corrections`（`CorrectionFlow.vue:72-79`）。

## Expected Behavior
预填的应该是"待修改的那条知识 + 一个空白/示例的改写输入"。即：把 `result.knowledge[0]`（那条被引用的事实）作为"已有知识"传入，输入框留空或只放示例占位；不要用问题句、也不要用原文引用。

## Code
- `frontend/src/views/QaView.vue:262` — `:seed="question"`
- `frontend/src/components/CorrectionFlow.vue:36` — `text = ref(props.seed)`
- `frontend/src/components/CorrectionFlow.vue:72-79` — 提交 `text`
- 交互路径：`QA 答案 → 这条有问题 → 分析`

## Impact
纠正链是本产品的最高价值动作（"答案不对 → 就地改 → 再问"）。入口语境错误会让用户第一步就走偏，并且**一次误点就会把一个问题句写进知识库**。

## Recommendation
给 `CorrectionFlow` 增加显式的 `context`（`{ claimId?, existingText?, question? }`）而不是一个语义模糊的 `seed`：`seed` 只用于"用户已经写好一句纠正"的场合；QA 场景改为渲染"你要改的是这条：<现有知识>"，输入框留空并 focus。同时 QA 场景应把 `context_claim_id` / `related_claim_id` 一起传给纠正接口，避免用户回答"是哪条"。

## Priority
**P0**

---

## 6. P1 问题清单

### P1-1 知识卡「纠正」预填的是原文引用

## Issue
`KnowledgeCard` 的纠正入口把 `claim.quote`（来源引文）预填进纠正输入框。

## User Situation
用户在知识卡上点「纠正」，textbox 里出现一段原文摘录；他以为这是要改的内容，直接点「分析」。

## Current Behavior
`<CorrectionFlow v-if="fixing" compact :seed="claim.quote" />`（`KnowledgeCard.vue:130`）。placeholder 期待的是"要改成什么"（`CorrectionFlow.vue:141-142`）。

## Expected Behavior
引用应该出现在"已有知识"一侧（作为佐证），输入框留空或放示例；用户只需要写"现在应该是什么"。

## Code
- `frontend/src/components/KnowledgeCard.vue:130`
- 交互路径：`任意出现 KnowledgeCard 的位置 → 纠正 → 分析`

## Impact
同一个组件出现在搜索、问答、对象详情、文档抽屉、研究候选五处，所以这不是一个页面的问题，而是五处的默认行为。用户容易把引文当纠正句提交，产出无意义的候选。

## Recommendation
与 P0-3 同源：`seed` 只承载"用户已经写好的一句纠正"；`KnowledgeCard` 传 `existingText={claim.object}` 作为"要改的就是这个值"，输入框留空。可在输入框上方加一行「你正在改：<subject · predicate> = <object>」。

## Priority
**P1**

---

### P1-2 手机端没有「需要你确认」入口

## Issue
桌面侧栏有动态的待确认状态项，移动端底部栏没有对应入口，也没有数量。

## User Situation
手机用户导入一篇文档，系统提示「去确认这 3 项」，顺手点进底部「知识」继续浏览；之后再也找不到审核入口，只能靠记住 URL 或打开命令面板。

## Current Behavior
`const MOBILE_ITEMS = items`（`App.vue:66`）只含 5 个空间；待确认项是独立的 `div.nav.due`（`App.vue:83-87`），在 `@media(max-width:700px)` 下 `.side{display:none}`（`App.vue:161`），整个入口消失。底部栏 6 个按钮里也没有任何角标（`App.vue:121-126`）。

## Expected Behavior
移动端的待确认状态必须同样可见（例如把「知识」或「命令」按钮加一枚角标，或在底部栏出现第 6 个带数字的入口），并且与桌面使用同一个 `store.pendingReview` 数字。

## Code
- `frontend/src/App.vue:66`、`83-87`、`121-126`、`159-174`
- 数据源：`stores/app.ts:122-127`（`loadPendingReview`）

## Impact
审核是产品的核心闭环之一；移动端丢失它，等于移动端用户永远无法让候选知识变成可信知识。

## Recommendation
把「需要你确认」从侧栏专属改成导航模型的一部分：桌面渲染为状态项，移动端渲染为底部栏角标（例如「知识」按钮右上角显示数字，点击进入 `/review`）。数量统一取 `store.pendingReview`。

## Priority
**P1**

---

### P1-3 导入失败 / 未配模型后，导入面板内没有恢复路径

## Issue
导入流程能清楚报告"走到哪一步、为什么停"，但不提供重试，也不告诉用户去哪儿补做。

## User Situation
用户拖入文档，界面显示「未配置语言模型，已完成分块。配置模型后可运行「用模型抽取」补上知识。」然后——没有那个按钮。

## Current Behavior
`item.error` 只被渲染成一段灰字（`ImportPanel.vue:157-161, 253`）。真正能补做的入口在别处：知识页 → 文档抽屉 → `Index with LLM`（`KnowledgeView.vue:399-404`）。首页没有文档抽屉，也没有导入面板内的重试。

## Expected Behavior
错误行旁边给出下一步动作：`去配置模型 →`（带 `/settings`）或 `重试抽取`；并保留这次导入的 `documentId` 使重试可用。

## Code
- `frontend/src/components/ImportPanel.vue:157-161`、`173-177`、`253`
- 缺失的补做入口：`frontend/src/views/KnowledgeView.vue:400`
- 交互路径：`拖入 → 抽取失败 → 面板内无出口`

## Impact
这是产品第一印象。第一印象里出现"系统说了一半就停下，且没有下一步"，用户会认为产品是坏的。

## Recommendation
在 `p.v-if="item.error"` 旁补一个动作区：未配模型 → `去配置 →`（`store.health.llm_configured === false` 可判定）；抽取失败且已有 `documentId` → `重试抽取`（复用 `processFile` 的 analyze 段）。

## Priority
**P1**

---

### P1-4 「继续研究」的副作用是立即创建任务，且任务不可删除

## Issue
打开研究闭环面板（`继续研究`）会立即写库创建 research task；收起面板或失败后，任务留在列表里没有任何清理入口。

## User Situation
用户在 Questions 里点了一下「继续研究」想先看看是什么，然后点「收起」。研究任务列表里从此多出一条永远不执行的记录。

## Current Behavior
`openFlow(q)` 内直接 `POST /api/research` 并 toast「已创建研究任务」（`ResearchView.vue:119-131`）。全前端没有 `DELETE /api/research/{id}` 的调用，也没有"归档/忽略"。

## Expected Behavior
"查看"与"创建"必须分离：点问题先展示闭环面板（含 Known / 计划 / 预估），真正执行时才创建任务；未执行的任务可以被删除或自动作废。

## Code
- `frontend/src/views/ResearchView.vue:119-131`（创建时机）
- `frontend/src/views/ResearchView.vue:237-279`（研究任务列表，无删除）
- 交互路径：`Questions → 继续研究 → 收起`

## Impact
用户的"研究空间"会被未执行的任务填满，而真正在跑的结论被淹没；同时用户完全不知道刚才那一下点击已经写入了数据。

## Recommendation
`openFlow` 只取数不再写库；把 `POST /api/research` 移到 `continueResearch()`（真正点「开始研究」时）。同时在任务行提供「不再需要」操作（或后端侧自动作废规则）。

## Priority
**P1**

---

### P1-5 QA 让用户在提问之前先做一次技术选择

## Issue
问答页把「知识库问答 / Agent 问答」作为显式模式开关，并要求用户在提问前选择。

## User Situation
用户只想问「苹果的 CEO 是谁」，却先要面对两个自己无法判断的选项，副标题还写着「Hybrid 检索 + Grounded 回答」「AgentScope ReAct Agent 可自主检索知识库」。

## Current Behavior
默认 `knowledge`，但控件显眼（`QaView.vue:193-202`），并在「下一步」区提供互相切换（`QaView.vue:344, 359`）——这本身就说明**同一次提问可以两种方式都跑**，用户不需要先选。

## Expected Behavior
一个输入框，一套答案。系统自己选路径；需要"更深入"时再在答案下方给一个动作（把现有的「换用 Agent 深入回答」留下来即可）。

## Code
- `frontend/src/views/QaView.vue:22-23, 187-188, 193-202`
- `frontend/src/components/CommandPalette.vue:19-20`（同一选择被复制进命令面板）

## Impact
把"用户不需要理解 Agent"这条产品方向在入口处推翻了。用户被迫为一次普通的提问承担技术决策成本，而选错只会得到一个风格不同的答案，无从比较。

## Recommendation
移除提问前的模式切换。默认走 `knowledge`；把 `agent` 降级为答案下方的「让 Agent 再查一遍」动作（返回的答案标注来源方式）。副标题改成用户语言（见 copy audit）。

## Priority
**P1**

---

### P1-6 Knowledge Object Detail 有 8 个平级 Tab

## Issue
对象详情把 `Overview / Claims / Relations / Graph / Events / Evidence / Questions / Ideas` 全部平铺为一级 Tab。

## User Situation
用户从搜索点进「苹果公司」，想确认一件事，却先看到 8 个入口和一个 5 个数字的统计条；其中 Relations 与 Graph 看起来是同一件事，Events/Questions/Ideas 他并不知道和自己要找的答案有什么关系。

## Current Behavior
`ObjectView.vue:241-244` 平铺 8 个 Tab；`ObjectView.vue:232-239` 先把后端 counts 的原始 key 大写后当标签显示。`Events` Tab 自己承认「事件本身不直接关联实体」（`ObjectView.vue:328`），`Questions`/`Ideas` 是"共享来源文档"的间接数据。

## Expected Behavior
按用户任务分层：
1. **第一层（首屏）**：这是什么 + 关键事实（Overview 的描述 + 3~6 张 KnowledgeCard）；
2. **第二层**：依据与历史（Evidence 合并进每张卡的「依据」，Graph 作为"它和谁有关"的一种视图，放在 Relations 内部切换）；
3. **第三层**：Events / Questions / Ideas 这类"共享来源的周边内容"归入「更多」或折叠区。

## Code
- `frontend/src/views/ObjectView.vue:241-244`（8 Tab）
- `frontend/src/views/ObjectView.vue:254-273`（Overview 已承担"Key Claims / Relations"预览，与 Tab 重复）
- `frontend/src/views/ObjectView.vue:232-239`（counts 原样大写）

## Impact
入口越多，用户越难判断"我该点哪个"；而其中三个 Tab 的内容对当前问题通常无效，进一步稀释信号。

## Recommendation
收敛为 3 个一级视图：`概览`（是什么 + 关键事实 + 依据）、`关系`（列表/图谱视图切换）、`周边`（事件/问题/想法，默认折叠）。不要按后端数据结构决定一级入口。

## Priority
**P1**

---

### P1-7 审核页同屏两套数字，批量操作像后台管理

## Issue
审核页顶部有两组来源不同、口径不同的数字；同时提供「接受高置信（N）」「全部拒绝…」两个后台式批量操作。

## User Situation
用户被告知「3 条需要你确认」，进到审核页却看到 6 个格子（件待处理 / 可能重复主体 / 知识冲突 / 待审实体 / 待审知识 / 待审关系），紧接着下面又是 4 个格子（待审实体 / 待审 Claims / 待审关系 / 合计，数字可能不同）；然后需要决定要不要点「接受高置信（2）」。

## Current Behavior
- 双数字：`ReviewView.vue:47-57`（`/api/review/inbox`）+ `ReviewQueue.vue:194-199`（`/api/review`，另算一个「合计」）。
- 阈值直出：`HIGH_CONFIDENCE = 0.9`，`kindHint` 只解释阈值不解释后果（`ReviewQueue.vue:23, 75-78`）。
- 一键批量：`acceptHighConfidence()` 无确认（`ReviewQueue.vue:182, 215-217`）；`rejectAll()` 有 `confirm`（`ReviewQueue.vue:184-189`）。
- 术语直出：`confidence 90%`、`modality · polarity`、`claim_type`（`ReviewQueue.vue:229-234`）。

## Expected Behavior
- 一组数字，一个口径（= 用户还要做多少件事）。
- 批量操作先给后果预览（"这 2 条将进入图谱与问答；其中 1 条会与已有知识冲突"），再执行。
- 「90%」要么不给，要么翻译成用户语言（"系统很确定"）并在多处一致。

## Code
- `frontend/src/views/ReviewView.vue:22-28, 30-34, 47-57`
- `frontend/src/components/ReviewQueue.vue:23, 68-78, 182-189, 194-199, 212-219, 229-234`

## Impact
两个数字并存会让用户先怀疑数据，再怀疑自己；无确认的批量写入把"维护自己的知识"变成"操作后台数据"。用户对"通过"的信心建立不起来，审核会停止。

## Recommendation
① 删掉 `ReviewQueue` 顶部的 `KpiStrip`，只保留 `ReviewView` 的 inbox 数字（或反向，但只留一处）；② 批量接受前展示将影响的条目清单与后果；③ 置信度改为人话标签并在 copy audit 里统一（见 [frontend-copy-audit.md](./frontend-copy-audit.md)）。

## Priority
**P1**

---

### P1-8 高风险写入缺少撤销（采纳 / 忽略）

## Issue
`KnowledgeCard` 的「采纳」和「忽略」写库后没有任何撤销手段，而同产品的其他写操作都有。

## User Situation
用户在搜索结果里点「采纳」，发现点错了那条（那是另一个同名的候选），没有任何地方能撤回。

## Current Behavior
`adopt()` / `dismiss()` 只 `store.toast('已采纳为知识')` / `('已忽略这条候选')`，不带 `action`（`KnowledgeCard.vue:52-76`）。对照：`ReviewQueue.applyStatus` 的 toast 带「撤销」（`ReviewQueue.vue:167`），`IntegrityPanel.dismiss` 带「撤销」（`IntegrityPanel.vue:139`），`ClaimChanges.resolve` 带「撤销」（`ClaimChanges.vue:139`）。

## Expected Behavior
同一条知识操作，无论从哪个界面触发，都应给同样的可逆性。

## Code
- `frontend/src/components/KnowledgeCard.vue:52-76`
- 对比实现：`frontend/src/components/ReviewQueue.vue:143-179`

## Impact
采纳是把候选变成"可信锚点"（会进入图谱与问答），是一次高风险写入。没有撤销会让用户不敢使用"就地采纳"这条便捷路径，只能绕回审核页——而审核页正是我们希望用户少去的地方。

## Recommendation
让 `KnowledgeCard` 复用与 `ReviewQueue` 相同的撤销机制（`PATCH .../status { status: 'candidate' }`），toast 带「撤销」。

## Priority
**P1**

---

### P1-9 技术词出现在普通用户路径上

## Issue
多处在普通用户路径直接暴露内部概念：极性、模态、claim id、operation id、document id、method 名、ontology。

## User Situation
用户在冲突页看到「positive / negative」「asserted」「来源：a1b2c3d4」；在纠正页看到「新断言：<uuid>」「操作 ID：<uuid>」；在问答的检索轨迹里看到「hybrid」。

## Current Behavior
| 位置 | 泄漏内容 |
|---|---|
| `ResearchView.vue:355-357` | `c.polarity`（positive/negative）、`c.modality`、`c.source_document_id?.slice(0, 8)` |
| `CorrectionView.vue:96-98` | `claim_id` / `superseded_claim_id` / `operation_id` 作为可点文字 |
| `CorrectionView.vue:80-85` | 手动模式的 `subject` / `predicate` / `relationship` / 受影响断言 ID |
| `QaView.vue:85-89, 334-341` | 检索轨迹打印 method（`hybrid` / `semantic`） |
| `QaView.vue:281-289` | 引用校验维度 key 原样输出 |
| `ObjectView.vue:232-239` | counts 原始 key 大写当标签 |
| `ObjectView.vue:294` | `Normalization 规则：modality = possible / probable` |
| `ObjectView.vue:348` | `offset [start, end)` |
| `ClaimView.vue:240` | `modality = …，Normalize 规则要求…` |
| `KnowledgeView.vue:309, 420` | `chunk #n`、`Chunks (n)` |
| `ReviewQueue.vue:229-234` | `confidence` / `modality · polarity` / `claim_type` |

## Expected Behavior
普通路径只说"我要完成什么"。内部术语归入 Developer Mode 或「技术细节」折叠区（`ClaimView.vue:212-242` 已经是正确的范例，可复制到其他页面）。

## Code
见上表；替换建议逐条见 [frontend-copy-audit.md](./frontend-copy-audit.md#3-术语替换建议)。

## Impact
术语泄漏让用户以为自己需要理解系统内部结构才能使用产品，直接违背"用户不需要理解 Agent / Ontology / Claim"的方向。

## Recommendation
按 copy audit 的替换表逐条替换；需要保留的（如 object 是文本而非实体）改写成一句人话并只在必要时出现。

## Priority
**P1**

---

### P1-10 主要操作只能靠鼠标完成

## Issue
导航、列表项、示例问题、图节点都用 `@click` 挂在 `div` / `span` 上，没有 `role` / `tabindex` / 键盘事件。

## User Situation
只用键盘的用户打开产品后，无法进入任何一级空间（侧栏是 `div`），无法打开任何一条知识（列表项是 `div`），无法点示例问题（`span`）。

## Current Behavior
`grep` 结果显示全前端**只有 `HelloWorld.vue` 有 `role`/`aria-*`**。具体：
- 一级导航 `App.vue:77`（`div.nav @click`）；「需要你确认」`App.vue:83`；`App.vue:111`（omni `div @click`）
- 知识列表/实体卡 `KnowledgeView.vue:332, 347`；提示条 `KnowledgeView.vue:273`
- 首页示例问题 `HomeView.vue:176`（`span @click`）；最近知识 `HomeView.vue:205`；变更行 `HomeView.vue:220`；文档行 `HomeView.vue:246`
- 问答证据行 `QaView.vue:313`
- 对象关系/问题行 `ObjectView.vue:266, 287, 365`
- 设置分区 `SettingsView.vue:74-77`、开关 `SettingsView.vue:95-96, 107`
- 图谱节点（cytoscape canvas，`KnowledgeView.vue:257`、`ObjectView.vue:181`）——canvas 内节点无键盘可达路径
- 命令面板项 `CommandPalette.vue:125`（但有 ↑↓/Enter，属可达）

## Expected Behavior
至少主要任务链（进入空间、打开一条知识、打开来源、采纳/纠正、回到首页）可以纯键盘完成。

## Code
见上；参考已有的正确实现：`App.vue:121-126`（底部栏用 `<button>`）、`CommandPalette.vue:107-112`（键盘导航）、`ImportPanel.vue:218-222`（dropzone 同时 `@click` 且是块级可聚焦目标，仍缺 tabindex）。

## Impact
键盘用户被挡在门外；同时 `div @click` 也带来移动端 300ms 点击延迟与无法 `Esc`/`Tab` 退出的问题。

## Recommendation
把可点击的 `div`/`span` 换成 `<button class="…">`（样式已有 `.item`/`.nav` 可复用），或至少补 `role="button" tabindex="0" @keydown.enter.space`；图谱节点补一个纯列表的等价入口（"它和谁有关"列表）。

## Priority
**P1**

---

### P1-11 同一个意图有 5 个入口，且都像主入口

## Issue
"问一个问题"分散在首页 One Box、顶栏 ⌘K、问答页、命令面板两项、知识页搜索框旁按钮，没有任何一处被定义为唯一主入口。

## User Situation
用户想提问，面对：首页输入框、右上角"搜索知识，或直接提问…"、知识页搜索框、知识页搜索框旁边那个 `◎`、`⌘K` 面板里的三条。他无法判断哪条是"正确"的。

## Current Behavior
- 首页 One Box：`HomeView.vue:174`
- 顶栏 omni（文案说"搜索或提问"，点开是命令面板）：`App.vue:111-113`
- 问答页：`QaView.vue:204-207`
- 命令面板：「提问知识库」「提问 Agent」「搜索知识」三项并列：`CommandPalette.vue:19-21`
- 知识页搜索框右侧 `◎`（`title="转为提问"`）：`KnowledgeView.vue:269`

## Expected Behavior
一个 Primary Entry（首页 One Box）+ 若干有明确语境的 Contextual Entry（知识页搜索框 → "在结果里没有？去提问"；命令面板 → 一个"提问"项）。上下文入口不应看起来像另一个主入口。

## Code
见上。

## Impact
认知冲突：用户无法建立"我要提问就去 X"的心智模型，每次都要重新选择；同时"搜索"与"问答"的边界被抹平（用户得到一段答案却发现自己在检索）。

## Recommendation
① 顶栏文案与行为对齐（要么真做搜索，要么改成"提问或搜索…（⌘K）"并说明打开的是命令面板）；② 命令面板把「提问知识库 / 提问 Agent」合并为一项「提问」（见 P1-5）；③ 知识页的 `◎` 只在有搜索结果时出现，作为"没找到想要的 → 直接提问"的上下文动作。

## Priority
**P1**

---

### P1-12 页首动作脱离当前对象上下文

## Issue
对象详情的「审核」「研究」按钮跳到全局页面，用户丢失"我在看哪个对象"。

## User Situation
用户在「苹果公司」详情页点「研究」，落到研究空间的 Questions 列表；他刚才想研究的那个对象不见了。

## Current Behavior
`ObjectView.vue:221-222` 两个按钮都是裸 `router.push('/review')` / `router.push('/research')`。对比同页的「就此提问」正确地携带了对象（`ObjectView.vue:120-123`）。

## Expected Behavior
从对象出发的动作应该带着这个对象：研究 → 带着问题或对象上下文进入研究闭环；审核 → 至少带上筛选（与该对象相关的候选）。

## Code
- `frontend/src/views/ObjectView.vue:219-224`
- 正确范例：`frontend/src/views/ObjectView.vue:120-123`、`frontend/src/views/QaView.vue:343`

## Impact
用户需要在第二个页面重新告诉系统"我刚才看的是什么"——正是本轮要禁止的上下文丢失。

## Recommendation
「研究」改为 `router.push({ path:'/research', query:{ q: `${entity.name} 是什么？它和哪些东西有关？` } })`（复用 `askAbout` 的思路）；「审核」改为携带实体筛选，或在对象内直接展示"与该对象相关的待审候选"。

## Priority
**P1**

---

### P1-13 Settings 没有区分普通用户与维护者

## Issue
设置页把 Provider、Embedding 维度、Agent Runtime、Database 与开发开关混在同一条路径上，副标题用 "Provider、检索、Agent Runtime、数据与安全策略"。

## User Situation
普通用户打开设置想配置模型，却看到 `Embedding Dimensions`、`LLM Batch Chunks`、`Max Search Results`、`AgentScope`、`Database`、`Developer Mode`——不知道该不该动。

## Current Behavior
四个分区：基础配置（SQLite 数据库） / LLM / 检索与 Embedding / Agent Runtime（`SettingsView.vue:73-97`），外加上级「高级」区（Developer Mode / Database / 导出，`SettingsView.vue:102-117`）。

## Expected Behavior
两套视图（详见 [frontend-copy-audit.md](./frontend-copy-audit.md#5-settings-角色分层)）：
- **USER_SETTINGS**：填一个 API Key、选默认模型、测试连接、导出数据。
- **DEVELOPER_SETTINGS**（Developer Mode 之后）：Base URL、Embedding 维度、batch、检索上限、AgentScope、Database。

## Code
- `frontend/src/views/SettingsView.vue:15, 66, 72-99, 102-117`

## Impact
普通用户面对"向量维度""SQLite 路径"会直接放弃配置，产品因此停留在没有模型、无法抽取知识的初始状态。

## Recommendation
默认只显示 `USER_SETTINGS`；其余随 `store.developerMode` 出现（开关已存在：`SettingsView.vue:105-108`）。

## Priority
**P1**

---

### P1-14 移动端的溢出与退出路径

## Issue
小屏下表格横向溢出；文档抽屉没有遮罩也没有 `Esc`。

## User Situation
手机用户在问答页看到「最近问答」表格把页面撑宽；打开文档抽屉后只能精准点右上角 `×` 关闭。

## Current Behavior
- `DataTable.vue:20-33` 输出裸 `<table class="table">`，无 `overflow-x` 容器；`QaView.vue:176-179` 有 5 列。
- `tokens.css:94`：`.drawer{width:min(430px,94vw)}`，无遮罩；`AppDrawer.vue` 只有 `×`，不监听 `Esc`。`App.vue:53-56` 的全局 keydown 只处理 `⌘K` 与命令面板。
- `App.vue:159-174` 的移动断点是 `700px`，而 `tokens.css:101` 的通用断点是 `920px`，两套断点并存。

## Expected Behavior
表格在窄屏可横向滚动且不撑破页面；抽屉提供遮罩点击关闭与 `Esc`；断点语义统一。

## Code
- `frontend/src/components/DataTable.vue:20`
- `frontend/src/styles/tokens.css:93-94, 100-101`
- `frontend/src/components/AppDrawer.vue:6-14`
- `frontend/src/App.vue:53-56, 159-174`

## Impact
横向溢出会让整个页面可以左右拖动，直接破坏"不迷路"；抽屉无法 `Esc` 退出违反"用户应该容易退出错误操作"。

## Recommendation
给表格包一层 `overflow-x:auto`；`AppDrawer` 增加遮罩与 `Esc`（与 `AppModal` 对齐）；把 700 / 920 两个断点统一为一处定义。

## Priority
**P1**

---

### P2 汇总（仅登记，本轮不做）

| # | 问题 | 位置 |
|---|---|---|
| P2-1 | `store.searchTerm` 被写入但从未被读取（死状态） | `stores/app.ts:100`、`CommandPalette.vue:114` |
| P2-2 | `OneBox` 的 `contextClaimId` prop 从未被任何宿主传入，宣称的"不必重新说明是哪条"实际从未生效 | `OneBox.vue:26-29` vs 唯一宿主 `HomeView.vue:174` |
| P2-3 | 审核页跳走再回来，筛选与展开全部重置（无状态保持） | `ReviewQueue.vue:31-37` |
| P2-4 | `QaView` 的 `send()` 没有 busy 守卫，输入框与发送按钮都不 disable，可重复提交 | `QaView.vue:205-206` |
| P2-5 | 抽取失败后 `QaView` 把上一次 `result` 清空，用户丢失已有答案 | `QaView.vue:122, 155` |
| P2-6 | 纠正成功后 QA 页仍显示旧答案（没有"这条答案已过期"的标记，也没有"再问一次"） | `QaView.vue:40-45` |
| P2-7 | `HomeView` 的示例问题 `span` 可点但无 hover/焦点反馈 | `HomeView.vue:176` |
| P2-8 | `IntegrityPanel.swap()` 会重新请求 `merge-impact`，按钮文案随预览变化，存在误点风险 | `IntegrityPanel.vue:80-84, 282` |
| P2-9 | `ObjectView` 的 counts 直接大写后端 key 当标签（`CLAIMS` / `RELATIONS`） | `ObjectView.vue:233-238` |
| P2-10 | `KnowledgeView` 文档抽屉的按钮仍是英文 `Index with LLM` / `Chunks only` / `Embeddings` | `KnowledgeView.vue:400-402` |
| P2-11 | 断点两套（700 / 920）并存，移动规则分散在两个文件 | `App.vue:152-174`、`tokens.css:100-101, 182-183` |

---

## 7. 入口数量审查

| 用户任务 | 当前入口 | Primary Entry（建议） | Contextual Entry（合理保留） | 需要收掉的 |
|---|---|---|---|---|
| 问一个问题 | ① 首页 One Box ② 顶栏 omni→⌘K ③ 问答页 ④ 命令面板「提问知识库」⑤ 命令面板「提问 Agent」⑥ 知识页搜索框 `◎` ⑦ 对象页「就此提问」⑧ 知识卡…（无） | **① 首页 One Box** | ⑥（"没搜到→提问"）、⑦（对象上下文） | ⑤ 合并进 ④；② 文案与行为对齐；③ 保留为空间但去掉模式选择 |
| 导入文档 | ① 首页 dropzone ② 知识页 ImportPanel ③ 知识页「＋ 导入」④ One Box（intent=knowledge） | **① 首页 dropzone** | ②/③（知识空间内是自然的） | 不需要收，`④` 是 One Box 的正常分支 |
| 纠正知识 | ① 答案旁「这条有问题」② 知识卡「纠正」③ 命令面板「纠正一条知识」④ `/correction` 页面 | **① 答案旁（最痛的地方）** | ②（读到错的知识时就地改） | ③④ 保留但不要出现在任何主路径文案里 |
| 审核 | ① 首页「N 项需要你的判断」② 侧栏「需要你确认」③ 知识页提示条 ④ 对象页「审核」⑤ 命令面板「审核候选知识」⑥ 研究页「去审核」 | **①/②（同一个数字状态）** | ③⑥（上下文里被提及） | ④（脱离上下文，见 P1-12）；⑤ 可保留 |
| 研究 | ① 首页 One Box（intent=research）② 研究空间 ③ 命令面板「研究空间」④ 冲突卡「创建研究」⑤ 对象页「研究」 | **② 研究空间（有明确 Question 时）** | ④（冲突解决的自然下一步） | ⑤（见 P1-12） |
| 合并实体 | ① 知识体检（知识页 / 审核页共用同一个组件） | **① 知识体检** | —— | 无（设计良好） |
| 查看证据 | ① 任意 KnowledgeCard 的「依据」② 知识详情页「依据」③ 对象页 Evidence Tab | **① 卡片上的「依据」（离问题最近）** | ②③ | —— |
| 看历史 | ① 卡片「历史」→ 知识详情 | **①** | —— | 无 |
| 设置 | ① 侧栏「设置」② 命令面板「设置」③ 顶栏无 | **①** | ② | 无 |

**结论**：真正需要收敛的是"提问"（8 → 1 主 + 2 上下文）和"审核"（6 → 1 主入口 + 上下文提及）。

---

## 8. 上下文保持审查

| 跳转 | 是否丢上下文 | 结论 |
|---|---|---|
| QA → 纠正 | **不跳页**（就地展开） | ✅ 最佳实践 |
| 知识卡 → 依据 | 跳到知识页并自动打开文档抽屉 + 高亮 chunk；抽屉里有"这篇产生的知识"，所以**部分保留** | ⚠️ 但知识卡本身消失了，用户无法确认"我刚才看的那条"是哪条（没有高亮该 claim） |
| 知识卡 → 历史 | 跳 `/knowledge/claim/:id?history=1`，自动展开并滚动到历史 | ✅（`KnowledgeCard.vue:92-93`、`ClaimView.vue:76, 79-83`） |
| 研究候选 → 依据 / 历史 | 同知识卡 | ⚠️ 同上 |
| 研究 → 候选 | **不跳页**（details 内展开） | ✅ 但采纳后 `reloadTaskKnowledge` 只刷该任务，页面其他数字靠 `loadPendingReview` | 
| 审核 → 来源原文 | 跳知识页抽屉；回来时 `ReviewQueue` 完全重载（筛选/展开/滚动全丢） | ⚠️ P2-3 |
| 对象页 → 审核 / 研究 | 只带目的地，不带对象 | ❌ P1-12 |
| QA → 研究 | 携带 `q`（`QaView.vue:343`），但 `ResearchView` 只把它当 tab 参数用，并不预填问题 | ⚠️ `ResearchView.vue:25-27` 未读 `q` |
| 纠正 → 再问 | **没有路径**（toast action 是"看这条知识"） | ❌ P2-6 |
| 任一 → 命令面板 | 命令面板不记忆当前对象，实体列表是全库 | ⚠️ 可接受 |

---

## 9. 操作结果审查（每个 mutation 都要回答"到底成功了吗"）

| 操作 | loading | success | failure | 数据刷新 | 撤销 | 结论 |
|---|---|---|---|---|---|---|
| Import 文件 | 阶段 chip | 阶段 ✓ + 结果区 | 阶段 × + 一行灰字 | `emit('imported')` | ✗ | 缺恢复路径（P1-3） |
| Index with LLM（抽屉） | `RunProgress` 全局 | toast「抽取完成」+「去审核」 | toast | `loadAll` + 重开抽屉 | ✗ | ✅ 较好 |
| Ask（知识库） | 文本提示 | 答案 + 支持知识 | toast，**清空旧结果** | — | — | ⚠️ P2-5 |
| Ask Agent | 文本提示 | 答案 + 标签 | toast | — | — | ⚠️ 无 busy 守卫 |
| 校验引用 | 「校验中…」 | 维度条 + 结论 | toast | — | — | ✅ |
| 这条有问题 → 分析 | 「分析中…」 | diff 卡片 | **内联红框** | — | — | ✅ 最佳错误处理 |
| 这条有问题 → 确认修改 | 「更新中…」 | toast + 「看这条知识」 | 内联红框 | `loadPendingReview` | ✗ | ⚠️ 无"再问一次"（P2-6） |
| 卡片「采纳」 | `busy` | toast | toast | `loadPendingReview` | ✗ | ❌ P1-8 |
| 卡片「忽略」 | `busy` | toast | toast | — | ✗ | ❌ P1-8 |
| Review 单条通过/拒绝 | `busy` 全局 | toast + 行移除 | 「操作失败，候选保持不变」 | 本地移除 | ✅ toast 撤销 | ✅ |
| Review 接受高置信 | `busy` | toast + 行移除 | 同上 | 本地移除 | ✅ | ⚠️ 无确认（P1-7） |
| Review 全部拒绝 | `busy` | toast + 行移除 | 同上 | 本地移除 | ✅ | ✅（有 confirm） |
| 知识变化 确认取代 | `busy` | toast | toast | `load` | ✅ | ✅ |
| 知识变化 标记并存 | `busy` | toast | toast | `load` | ✅ | ✅ |
| Merge | 影响预览→执行 | **结果卡 + 新冲突清单** | toast，预览保留 | `load` + `emit('changed')` | ✗（用"不是同一个"是另一条路） | ✅ 最佳实践 |
| 对象链接 接入/确认/忽略 | `busy` | toast（忽略带撤销） | toast | `load` + `emit('changed')` | 忽略 ✅ / 接入 ✗ | ⚠️ |
| 创建研究任务（openFlow） | 无（隐藏副作用） | toast | toast | `load` | ✗ 且**无删除** | ❌ P1-4 |
| 继续研究（执行） | 阶段动画 | Findings + toast | 阶段 failed + toast | `load` | ✗ | ✅ |
| 把结论整理成候选 | `proposing` | toast「已整理出 N 条」 | toast | `reloadTaskKnowledge` | ✗ | ✅ |
| 采纳研究候选 | `busy` | toast / issues | toast | `reloadTaskKnowledge` + `loadPendingReview` | ✗ | ⚠️ 同 P1-8 |
| 新建文档/实体/事件/想法/问题 | — | toast | toast | 局部 reload | ✗ | ✅ |
| 删除文档 | — | — | toast | `loadAll` | ✗（有 `confirm`） | ⚠️ 删除无撤销 |
| 设置保存 | — | toast + reload health | toast | — | — | ✅ |
| 导出 | — | 浏览器下载 + toast | toast | — | — | ✅ |

---

## 10. 高风险操作审查

| 操作 | 知道后果吗 | 需要确认吗 | 容易撤销吗 | 能看见影响范围吗 | 判定 |
|---|---|---|---|---|---|
| **Merge** | ✅ 影响预览 + 新冲突预告 | ✅ 预览即确认 | ⚠️ 无直接撤销（但可视为一次策展判断） | ✅ | **P2**（标杆实现） |
| **Reject（单条）** | ✅ 页脚解释了后果 | ✗ | ✅ toast 6s | ⚠️ | ⚠️ 6 秒窗口偏短（P2） |
| **Bulk Accept（90%）** | ✗ 只有阈值说明 | ✗ | ✅ toast | ✗ | **P1-7** |
| **Bulk Reject（全部拒绝）** | ✅ 有 confirm，说明可撤销 | ✅ | ✅ toast | ⚠️ 只给数量 | ✅ |
| **Correction** | ✅ diff + 关系标签 | ✅ 「确认修改」 | ✗ | ✅ | ⚠️ 预填错误（P0-3/P1-1） |
| **KnowledgeCard 采纳** | ⚠️ 只说"已采纳" | ✗ | ✗ | ✗ | **P1-8** |
| **Delete 文档** | ✅ `confirm` 说"及其全部派生数据" | ✅ | ✗ | ⚠️ | P2（无撤销） |
| **Claim 标记不成立** | ✗ 没说会被检索/图谱排除 | ✗ | ✗ | ✗ | P2 |

---

## 11. 渐进披露逐页审查（第一屏 / 第二层 / 第三层）

| 页面 | 第一屏应该看到"什么" | 当前第一屏实际内容 | 判定 |
|---|---|---|---|
| `HomeView` | 一个输入 + 若有事才出现的一件事 | 问候 + 「把知识交给我」+ dropzone + One Box；有事时才出现判断面板 | ✅ **符合** |
| `KnowledgeView` | 搜索框 + 我的知识（文档/对象） | 搜索框 + 待审提示 + 知识体检 + 文档/图谱/时间线 Tab + 文档列表 + Knowledge Objects | ⚠️ 知识体检与待审提示占据第二屏顶部，且"知识"与"原文"混在一个 Tab 里 |
| `ObjectView` | 这是什么 + 关键事实 | 标题 + 4 个动作按钮 + 类型标签 + 5 个数字 + **8 个 Tab** + Overview 两栏 | ❌ 见 P1-6 |
| `QaView` | 一个输入框 | 标题 + 副标题（技术）+ **模式开关** + 输入框 + 空答案面板 | ❌ 见 P1-5 |
| `ResearchView` | 我的问题 / 在研究的 | 标题 + 副标题（流程图）+ 4 个 KPI（英文）+ 4 个 Tab | ⚠️ 见 P1-7（Research 的语言） |
| `ReviewView` | 有多少件事在等我 + 第一件 | **6 个数字** + 知识体检 + 知识变化 + 4 个数字 + 候选列表 | ❌ 见 P1-7 |
| `SettingsView` | 让模型能用 | 4 个分区（含 Database 与 Agent Runtime）+ 高级区 | ❌ 见 P1-13 |
| `ClaimView` | 当前答案 + 依据 | 知识卡 + 历史 + 依据 + 相关断言 + 折叠的技术细节 | ✅ **符合**（最佳范例） |
| `CorrectionView` | 一句话 + 分析 | 标题（"分析 → 确认 → 新断言 + 演化关系 + 审计"）+ 输入框 + 手动模式 + 审计 | ⚠️ 审计与手动模式应降到第三层 |

---

## 12. 最终 12 问

1. **第一次使用是否能在 10 秒内找到正确入口？**
   **能。** 空库首屏只有一个 dropzone 和一句"或者直接说一句话"（`HomeView.vue:161-174`）。唯一瑕疵是 One Box 的 placeholder 漏了"纠正"。

2. **导入完成后下一步是否天然清楚？**
   **成功时清楚**（「我从这篇整理出了什么」+「去确认这 N 项 →」+ 建议问题，`ImportPanel.vue:257-288`）；**失败时不清楚**（只有一行灰字，无重试无出口，P1-3）。

3. **Search / QA / OneBox 是否存在认知冲突？**
   **存在。** 知识页搜索框旁边就有一个"转为提问"按钮，顶栏写"搜索知识，或直接提问…"点开却是命令面板，命令面板里还有"提问知识库/提问 Agent"两项（P1-11、P1-5）。

4. **"答案不对 → 纠正 → 再问"是否真正一条链？**
   **不是。** 第一环就断了：入口位置对，但预填的是问题本身（P0-3）；改完之后页面仍显示旧答案、也没有"再问一次"（P2-6）。

5. **Research 与 QA 是否容易理解区别？**
   **不容易。** 研究页用流程图和 `Questions/Conflicts/Health/Findings/Candidate` 表达自己；用户唯一能获得的区别信息来自 One Box 的意图判断（P1-7、P1-5）。

6. **Review 是否像用户工作，而不是后台管理？**
   **偏后台。** 两套数字 + 4 个批量动作 + `confidence / modality / polarity / claim_type` 标签 + 无撤销窗口短的逐条决定（P1-7、P1-9）。

7. **Knowledge Detail 是否过于复杂？**
   **是。** 8 个平级 Tab，其中 Relations 与 Graph 重复、Events/Questions/Ideas 与该对象无直接关系（P1-6）。

8. **移动端是否丢失重要任务入口？**
   **是。** 「需要你确认」在 700px 以下完全消失，底部栏也没有角标（P1-2）。另有横向溢出与抽屉无法 `Esc` 退出（P1-14）。

9. **错误状态是否经常被表现成"没有数据"？**
   **是，而且是最严重的一类。** 5 个主要数据加载点没有错误处理，失败会渲染成"没有文档 / 已经清空 / 全新空库"（P0-2）。

10. **是否有技术术语泄漏到普通用户路径？**
    **有，且分布很广**（11 处，见 P1-9 表）。

11. **是否存在 no-op / 重复操作 / 上下文丢失？**
    **no-op：** One Box 的「按这个执行」（P0-1）。
    **隐藏副作用 / 重复操作：** 「继续研究」即创建任务（P1-4）；`QaView.send()` 无防重（P2-4）。
    **上下文丢失：** 对象页的「审核 / 研究」（P1-12）；QA → 研究未使用携带的问题（§8）。

12. **整个产品最应该优先修的 3 个 P0/P1 问题？**
    1. **P0-2** 加载失败被当成空数据 —— 它同时摧毁审核闭环和用户对"我的知识还在不在"的信任；
    2. **P0-1** One Box 换意图后的 no-op —— 它是"系统判断错了"时唯一的补救手段，而它不工作；
    3. **P0-3 + P1-1** 纠正入口预填错误上下文 —— 最高价值交互的第一步就走偏，且一次误点会写入错误知识。

---

## 13. 本轮结论与下一步

- **本轮判定：审查完成（VALIDATED 候选），未通过"产品可用"验收。** 依据是 §12 的第 4、6、7、8、9 问均给出否定答案，且 P0 均能在代码中复现。
- **本轮未做任何业务 UI 改动**，未新增功能、未新增 Agent、未改后端与数据库。
- **验证基线已通过**：`pytest -q` → 660 passed / 6 deselected；`npm --prefix frontend run build` → 通过。
- **下一轮建议顺序**（先 P0，再 P1；每项修完补一条回归）：
  1. P0-2（统一加载/失败/空三态，最少改 5 处）
  2. P0-1（One Box override 语义）
  3. P0-3 / P1-1（纠正入口上下文）
  4. P1-2（移动端待确认）
  5. P1-7（审核页一组数字 + 批量操作后果预览）
  6. P1-3（导入失败恢复路径）

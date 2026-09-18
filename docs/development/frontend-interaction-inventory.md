# Frontend Interaction Inventory（Step 18）

> 对 `frontend/src/` 中每一个「用户可以动手的地方」做一次 `Action → Feedback → Result → Next Action` 的四段式核查。
>
> 判据只有一条：**不允许存在 `Action → 什么都没发生`。**
>
> 配套：场景走查与问题等级见 [frontend-human-use-audit.md](./frontend-human-use-audit.md)；文案见 [frontend-copy-audit.md](./frontend-copy-audit.md)。

---

## 0. 统计总览

| 指标 | 数量 |
|---|---|
| 含 `@click` / `@keydown` / `@submit` / `@change` / `@drop` 的文件 | 30 |
| 交互点总数（约） | 190+ |
| DOM 事件监听点按文件分布（前 6） | `ObjectView` 24 · `KnowledgeView` 24 · `QaView` 15 · `ReviewQueue` 14 · `ResearchView` 13 · `SettingsView` 11 |
| `router.push` / `router.back` / `router.replace` 调用点 | 59+ |
| **No-op / 静默失败** | **1 处确认（P0-1）+ 5 处同类风险** |
| **无反馈的成功** | 0（所有 mutation 都至少有一个 toast） |
| **无撤销的高风险写入** | 4 处（采纳 / 忽略 / 对象接入 / 删除文档） |
| 键盘可达性缺口 | 20+ 处 `@click` 挂在 `div`/`span` 上（详见 §5） |

反馈通道约定（源码里实际使用的五种）：

| 通道 | 实现 | 适用 |
|---|---|---|
| `toast` | `stores/app.ts:132-137`（可带 `action`，带 action 时 6s，否则 1.8s） | mutation 结果、可撤销操作 |
| 内联错误 | `error.value`（`CorrectionFlow.vue:150`） | 需要用户就地修正的表单 |
| 页面错误页 | `loadError`（`ObjectView.vue:417-424`、`ClaimView.vue:245-252`） | 对象/知识不存在 |
| 空态 | `EmptyState.vue` | 已加载但为空 |
| 阶段进度 | `ImportPanel` 四阶段 chip、`ResearchView` 两阶段、`RunProgress` 全局浮层 | 长任务 |

> ⚠️ **缺少第六种**：`加载失败`。见 §4。

---

## 1. App 外壳

| Action | 位置 | Feedback | Result | Next Action |
|---|---|---|---|---|
| 点一级导航 5 项 | `App.vue:77`（`div.nav @click`） | hover / active 高亮 | `router.push` | 进入空间 |
| 点「需要你确认 N」 | `App.vue:83-87` | 无（本身就带数字） | `router.push('/review')` | 审核 |
| 点顶栏 omni | `App.vue:111` | 无 | 打开 `CommandPalette` | 输入 |
| `⌘K` / `Ctrl+K` | `App.vue:54` | 面板开合 | `cmdOpen` 切换 | 输入 |
| `Esc` | `App.vue:55` | 关闭命令面板 | — | — |
| 点底部栏 5 空间 | `App.vue:122`（`<button>`） | active 高亮 | `router.push` | 进入空间 |
| 点底部「命令」 | `App.vue:125` | — | 打开命令面板 | 输入 |
| 点开发者 3 项（Developer Mode 开时） | `App.vue:92-95` | hover | `router.push` | — |

**缺口**
- ❌ `Action → 无反馈`：移动端没有「需要你确认」，`App.vue:83-87` 的入口在 `<=700px` 被 `display:none` 掉（`App.vue:161`）→ **P1-2**。
- ⚠️ `App.vue:77` 导航是 `div`，键盘不可达 → **P1-10**。
- ⚠️ 无 `aria-current`，面包屑 `crumb`（`App.vue:46-51`）对 `/correction` 显示「纠正」但没有返回到来源的入口。

---

## 2. 首页

| Action | 位置 | Feedback | Result | Next Action |
|---|---|---|---|---|
| 拖入 / 点击 dropzone | `HomeView.vue:165-168` → `ImportPanel.vue:218-222` | `dragOver` 高亮 | `importFiles` | 四阶段进度 |
| 点示例问题 `span.tag` | `HomeView.vue:176` | ⚠️ 无 hover / 无焦点态 | `onebox.run(e)` → intent → 自动执行或待确认 | 看答案 / 点「按这个执行」 |
| 点「开始确认 →」 | `HomeView.vue:188` | — | `/review` | 审核 |
| 点「知识冲突」格子 | `HomeView.vue:194` | 数字变 warn 色 | `/research?tab=conflicts` | 冲突处理 |
| 点「进入知识空间 →」 | `HomeView.vue:203` | `.more` 有 pointer | `/knowledge` | — |
| 点最近知识行 | `HomeView.vue:205` | `.item:hover` | `/knowledge/object/:id` | — |
| 点知识变化行（仅 `r.to` 存在时） | `HomeView.vue:220-221` | `.clickable:hover` 变色 | `router.push(r.to)` | — |
| 点「进入研究空间 →」 | `HomeView.vue:235` | — | `/research` | — |
| 点开放问题行 | `HomeView.vue:237` | — | `/research` | — |
| 点「最近加入」文档行 | `HomeView.vue:246` | — | `/knowledge?doc=` | 打开抽屉 |

**缺口**
- ❌ `HomeView.vue:176` 的示例问题是 `span @click`（键盘不可达），且点击后**没有 loading 反馈**——`OneBox.route()` 里 `reading=true` 只在按钮文案上体现（`OneBox.vue:98-100`），而触发它的是首页的 span，用户看不到任何变化直到结果出现。
- ❌ 任一接口失败 → `loadAll` 无 catch → 首页回退成空库首屏 → **P0-2**。

---

## 3. One Box（`OneBox.vue`）

| Action | 位置 | Feedback | Result | Next Action |
|---|---|---|---|---|
| 输入 + Enter | `OneBox.vue:97` | — | `route()` | 意图判断 |
| 点「↑」 | `OneBox.vue:98-100` | 按钮变 `…`，`disabled` | `route()` | 同上 |
| 自动执行（ask） | `OneBox.vue:59` | `obstatus`「正在理解这句话…」→ `plan.summary` | 渲染答案 + 支持知识 | 读答案 |
| 点「按这个执行」 | `OneBox.vue:121-123` | `running` → `disabled` | `run()` | 结果面板 |
| 点「其实我是想问」 | `OneBox.vue:119` | toast「先选一个意图，我再按它执行」 | `plan = { intent:'ask', needs_confirmation:true, steps: [] }` | ❌ **再点「按这个执行」= no-op** |
| 点「其实我是想研究」 | `OneBox.vue:120` | 同上 | 同上 | ❌ 同上 |
| 点 unknown 的建议句 | `OneBox.vue:116` | — | `useSuggestion()` → 重新 `route()` | 重新判断 |
| 点「去看研究 →」 | `OneBox.vue:160` | — | `/research?tab=tasks` | — |
| 点「去知识空间 →」 | `OneBox.vue:171` | — | `/knowledge` | — |
| 纠正分支内的 `CorrectionFlow` | `OneBox.vue:148`（`:seed="text"`） | 组件内自带 | `@applied` → toast | — |

### 🚨 No-op 记录（P0-1）

```
Action      : override('ask')            (OneBox.vue:81-86)
              → plan.steps = []
              → plan.needs_confirmation = true
              → needsConfirm = true      (OneBox.vue:45)
Feedback    : toast「先选一个意图，我再按它执行」  ← 界面里没有意图选择器
Result      : 「按这个执行」按钮出现            (OneBox.vue:121-123)
Next Action : 用户点「按这个执行」              (OneBox.vue:122)
              → run() 首行 `if (!plan.value?.steps?.length) return`  (OneBox.vue:65)
              → 无 toast、无 loading、无状态变化
```

**结论：`Action → 什么都没发生`，且伴随一条误导性提示。登记为 P0。**

**其它缺口**
- ⚠️ `OneBox.vue:124-138` 的 `CorrectionFlow` 用 `:seed="text"` —— 在 One Box 场景下这是合理的（用户刚说的那句就是纠正句），与 `QaView` / `KnowledgeCard` 的错误 seed 形成对照，修 P0-3 / P1-1 时不要误改这一处。
- ⚠️ `contextClaimId` prop（`OneBox.vue:26-29`）没有任何宿主传入（唯一宿主 `HomeView.vue:174` 未传）→ 宣称的"不必重新说明是哪条"从未生效 → P2-2。
- ⚠️ `obstatus`（`OneBox.vue:104-108`）在 `plan && !result` 时与 `obplan` 同时渲染，两处都显示 `plan.summary`，重复。

---

## 4. 缺失的反馈通道：加载失败

**这是本清单里最重要的一节。**

当前所有数据加载点使用的模式是 `try { load } catch { 设空 }` 或不 catch。后果是**"加载失败"这条状态在前端根本不存在**，它被折叠进"空"。

| 位置 | 代码 | 失败后用户看到 | 用户由此得出的错误结论 |
|---|---|---|---|
| `ReviewQueue.vue:31-37` | 无 catch | `EmptyState「这里已经清空了」`（`:299-302`） | "审核做完了" |
| `KnowledgeView.vue:94-105, 131` | 无 catch | `「还没有文档——导入或新建一篇」`（`:337`） | "我的文档没了" |
| `HomeView.vue:120-146` | 无 catch（仅 health 有 `.catch`） | 全新空库首屏（`:117-118`） | "知识库被清空了" |
| `ResearchView.vue:41-51` | 无 catch | KPI 全 0 + `「没有开放问题——知识库状态良好」`（`:297`） | "一切正常" |
| `QaView.vue:70-82` | 无 catch | 最近问答空表 + `「暂无数据」`（`DataTable.vue:30`） | "我没问过问题" |
| `IntegrityPanel.vue:58-64` | `catch { scan = null }` | 面板整块消失（`v-if="visible \|\| result"`，`:190`） | "没有需要体检的问题" |
| `ReviewView.vue:30-34` | `catch { inbox = null }` | 数字条整块消失（`:47`） | "没有待办" |
| `CorrectionView.vue:29-33` | `catch { history = [] }` | `「暂无纠正记录。」`（`:104`） | "我没纠正过" |
| `CommandPalette.vue:92-95` | `catch { entities = [] }` | 命令面板里没有实体可搜 | "库里没有实体" |

**正确范例（可作为统一实现的模板）**

| 位置 | 做法 |
|---|---|
| `ObjectView.vue:82, 97, 417-424` | `loadError` 独立状态 + 错误页 + 「← 返回」 |
| `ClaimView.vue:70, 245-252` | 同上 |
| `CorrectionFlow.vue:93-95, 150` | `error.value` + 内联红框（唯一就地可修正的表单错误） |
| `stores/app.ts:49-53` | 抽取轮询超时后主动停止并 toast（"失败要收敛"的范例） |

> 建议：新增第六种反馈通道 `加载失败态`（`<EmptyState title="读取失败">` + `#action` 重试），并把上表 9 处逐一接上。这是 P0-2 的落地方式。

---

## 5. 键盘可达性清单

判定标准（本轮只要求"主要任务不是只能鼠标完成"）：

| 类型 | 数量 | 位置示例 | 判定 |
|---|---|---|---|
| 用 `<button>`（可达） | 多 | `App.vue:122`、`CommandPalette.vue:125`（另有 ↑↓/Enter）、各页 `.btn` | ✅ |
| `<summary>`（原生可达） | 4 | `KnowledgeView.vue:420`、`ResearchView.vue:247`、`ClaimView.vue:213`、`IntegrityPanel.vue:291, 304` | ✅ |
| `<label><input type=checkbox>` | 3 | `ClaimChanges.vue:182-185, 200-201` | ✅ |
| 原生表单元素 | — | 各页 `input`/`textarea`/`select`，Enter 已绑定（`OneBox.vue:97`、`QaView.vue:205`） | ✅ |
| **`div`/`span` + `@click`，无 `role`/`tabindex`/`@keydown`** | **20+** | 见下表 | ❌ |

需要补键盘支持的交互元素：

| 位置 | 元素 | 功能 |
|---|---|---|
| `App.vue:77` | `div.nav` | 一级导航（5 项） |
| `App.vue:83` | `div.nav.due` | 需要你确认 |
| `App.vue:92` | `div.nav` | 开发者入口 |
| `App.vue:111` | `div.omni` | 打开命令面板 |
| `HomeView.vue:176` | `span.tag` | 示例问题 |
| `HomeView.vue:194` | `div.duerow.clickable` | 知识冲突 |
| `HomeView.vue:203, 235, 244` | `span.more` | 进入知识/研究空间 |
| `HomeView.vue:205, 220, 237, 246` | `div.item` / `div.chgrow.clickable` | 打开对象 / 变更 / 问题 / 文档 |
| `KnowledgeView.vue:273` | `div.dueline` | 去审核 |
| `KnowledgeView.vue:302` | `div.item` | 打开搜索结果 |
| `KnowledgeView.vue:332, 347` | `div.item` | 打开文档 / 对象 |
| `KnowledgeView.vue:257` | cytoscape canvas node | 图谱节点（无等价列表入口） |
| `QaView.vue:313` | `div.item` | 打开证据来源 |
| `ObjectView.vue:266, 287` | `div.item` | 关系行 |
| `ObjectView.vue:365` | `div.item` | 相关问题 |
| `ObjectView.vue:181` | cytoscape canvas node / edge | 局部图谱节点与连线 |
| `SettingsView.vue:74-77` | `div` | 设置分区切换 |
| `SettingsView.vue:95-96, 107` | `div.switch` | AgentScope / 自动嵌入 / Developer Mode 开关 |
| `SettingsView.vue:109, 113` | `div.item` | Database / 导出 |
| `ImportPanel.vue:218` | `div.dropzone` | 拖入区（有 `@click`，缺 `tabindex`/`role`） |
| `ImportPanel.vue:265` | `div.ltriple` | 打开抽取出的知识 |
| `IntegrityPanel.vue:207, 228, 233` | `button`（✅） | — |
| `ReviewQueue.vue:313` | `span` | 去知识库 |
| `CorrectionView.vue:123` | `span.link` | 打开 claim |
| `ClaimView.vue:258` | `div.evonode` | 历史链节点 |
| `DatabaseView.vue:42` | `div.eyebrow` | 返回设置 |
| `RunDrawer` / `RunProgress` | `<summary>` / `<button>` | ✅ |

另：全局样式里**只有 `frontend/src/style.css:114-117` 定义了 `:focus-visible`**，而该文件未被 `main.ts` 引入（`main.ts:5` 只引入 `tokens.css`）。也就是说**当前生效的样式表里没有焦点可见样式**，所有自绘按钮和输入框在键盘 Tab 时没有可见焦点指示。

---

## 6. 每个 mutation 的四段式（完整表）

见 [frontend-human-use-audit.md §9 操作结果审查](./frontend-human-use-audit.md#9-操作结果审查每个-mutation-都要回答到底成功了吗)。此处只登记**不满足四段式的条目**：

| Action | 缺失的环节 | 问题分级 |
|---|---|---|
| One Box `override()` → 「按这个执行」 | **Result 缺失**（no-op） | **P0-1** |
| 首页示例问题点击 | Feedback 缺失（无 loading 指示） | P2-7 |
| 「继续研究」（`ResearchView.vue:290`） | Feedback 只报"已创建"，未提示这是**写入**；无 Result 回滚 | **P1-4** |
| `QaView.send()` | 无 `disabled`/busy → 可重复提交 | P2-4 |
| `KnowledgeCard.adopt/dismiss` | Feedback 有，**Next Action（撤销）缺失** | **P1-8** |
| 审核跳走再回来 | 状态保持缺失 | P2-3 |
| `IntegrityPanel.applyLinks/confirmLink` | 撤销缺失 | P2 |
| 文档删除（`KnowledgeView.vue:171`） | 撤销缺失（仅 `confirm`） | P2 |

---

## 7. 重复入口清单

| 意图 | 入口数 | 主入口建议 | 明细 |
|---|---|---|---|
| 提问 | **8** | 首页 One Box | `HomeView.vue:174`、`App.vue:111`→`CommandPalette.vue:19,20`、`QaView.vue:204`、`KnowledgeView.vue:269`、`ObjectView.vue:223`、`ImportPanel.vue:283`（建议问题 chip）、`QaView.vue:344,359`（模式互切） |
| 导入 | 4 | 首页 dropzone | `HomeView.vue:165`、`KnowledgeView.vue:322`、`KnowledgeView.vue:327`（`＋ 导入`）、`OneBox.vue:73-77`（intent=knowledge） |
| 纠正 | 4 | 答案旁「这条有问题」 | `QaView.vue:257`、`KnowledgeCard.vue:120-122`、`CommandPalette.vue:26`、`CorrectionView.vue` |
| 审核 | 6 | 状态项（同一个数字） | `App.vue:83`、`HomeView.vue:188`、`KnowledgeView.vue:273`、`ObjectView.vue:221`、`CommandPalette.vue:28`、`ResearchView.vue:366` |
| 研究 | 5 | 研究空间 | `HomeView.vue:174`（intent）、`ResearchView.vue:290`、`CommandPalette.vue:27`、`ResearchView.vue:365`（冲突→创建研究）、`ObjectView.vue:222` |
| 查看图谱 | 3 | 知识页「图谱」Tab | `KnowledgeView.vue:26`、`CommandPalette.vue:23`、`ObjectView.vue:242`（Graph Tab） |
| 时间线 | 3 | 知识页「时间线」Tab | `KnowledgeView.vue:26`、`CommandPalette.vue:24`、`ObjectView.vue:242`（Events Tab） |
| 查看历史 | 2 | 知识卡「历史」 | `KnowledgeCard.vue:119`、`ClaimView.vue:126` |
| 采纳 | 3 | 审核页 / 卡片按状态 | `ReviewQueue.vue:239`、`KnowledgeCard.vue:115`、`ResearchView.vue:259`（研究候选） |

---

## 8. overlay / 弹层清单

| 组件 | 打开方式 | 关闭方式 | Esc | 遮罩点击 | 判定 |
|---|---|---|---|---|---|
| `CommandPalette` | `⌘K` / 顶栏 | `Esc`（`CommandPalette.vue:111` + `App.vue:55`）、点遮罩（`:119`） | ✅ | ✅ | ✅ |
| `AppModal` | `showNew/showNewEntity/showNewEvent/showEdit/showIdea/showQuestion` | 点遮罩（`AppModal.vue:7`）、`×`、取消按钮 | ❌ | ✅ | ⚠️ 缺 Esc |
| `AppDrawer` | 打开文档 / 来源定位 | **只有 `×`**（`AppDrawer.vue:10`） | ❌ | ❌ 无遮罩 | ❌ **P1-14** |
| `RunDrawer` | 点击运行记录行 | 见组件（`RunDrawer.vue:1`） | 待补 | 待补 | ⚠️ 与 `AppDrawer` 同类 |
| `RunProgress` | 抽取开始 | 抽取结束 / 「取消」按钮（`RunProgress.vue:53`） | — | — | ✅（不可关闭但可取消） |
| toast | 任意 mutation | 1.8s / 6s 自动，或点击 action | — | — | ⚠️ 不可点 `×` 手动关闭 |

---

## 9. 结论

1. **确认 1 处 P0 no-op**：One Box `override()` → 「按这个执行」（§3）。
2. **确认 1 处系统性反馈缺陷**：全前端没有「加载失败」这一状态，9 个数据加载点把失败渲染成空（§4）。
3. 所有 mutation 都有成功反馈，但**撤销覆盖不一致**（`ReviewQueue`/`ClaimChanges`/`IntegrityPanel` 有，`KnowledgeCard`/`applyLinks`/删除文档没有）。
4. 键盘可达性是全局性缺口：20+ 个交互元素只有鼠标路径，且当前生效样式表中没有 `:focus-visible`（§5）。
5. 弹层退出路径不统一：只有 `CommandPalette` 支持 `Esc`（§8）。
6. 重复入口最多的是"提问"（8 条），且全部看起来都是主入口（§7）。

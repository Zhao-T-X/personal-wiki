# Frontend Copy Audit（Step 18）

> 界面语言审查：哪些词普通用户必须理解、哪些词是系统内部结构、哪些中英混用没有理由。
>
> 本轮**不要求全部翻译**。先统计「为什么混用」，再区分「用户功能词」与「技术术语」，最后给出替换表与 Settings 的角色分层。
>
> 配套：[frontend-human-use-audit.md](./frontend-human-use-audit.md)（问题等级）· [frontend-interaction-inventory.md](./frontend-interaction-inventory.md)（交互）

---

## 0. 先承认三件已经做对的事

在统计问题之前必须说明：本产品在文案上有三处**明显优于一般项目**的基础设施，替换时应复用它们，而不是另起一套。

| 设施 | 位置 | 作用 |
|---|---|---|
| **状态词表唯一** | `frontend/src/utils/status.ts:14-57` | 后端三套状态机（对象 / 想法 / 问题 / run）在这里翻译成中文（`candidate → 待审`、`open → 待研究`、`superseded → 已被取代`），未知值原样透出（`status.ts:59-62`），从不臆造 |
| **谓词标签唯一** | `frontend/src/utils/claim.ts:37-69` | 只从 `/api/ontology/predicates` 取人话标签；`hasLabel()` 专门用来阻止 `「X」的 requires 是什么？` 这种句子（`claim.ts:63-69`），`ImportPanel.vue:116-129` 是它的第一个正当用法 |
| **后端已给用户语言的状态标签** | `app/readmodels/knowledge_view.py:40-45, 58-60, 180-189` | `current → 当前`、`candidate → 待确认`、`disputed → 有争议`、`historical → 历史`、`research_candidate → 研究候选`。`KnowledgeCard.vue:102` 已经把它接到 `StatusTag` 的 `label` 上 |

**结论：状态与谓词不需要治理。** 需要治理的是**前端自己硬编码的英文标签**、**直接打印的后端枚举/标识符**、以及 **Settings / Research / QA 三个页面的技术性叙述**。

---

## 1. 中英混用统计

### 1.1 为什么混用？（三类原因，只有一类是合理的）

| 原因 | 是否合理 | 例子 |
|---|---|---|
| **A. 直接沿用后端枚举 / 字段名** | ❌ 不合理，属于漏译 | `polarity`、`modality`、`claim_type`、`claim_id`、`operation_id`、`source_start_offset` |
| **B. 在视图里硬编码了英文 UI 标签** | ❌ 不合理，无任何技术必要 | `Documents`、`Knowledge Objects`、`Open Questions`、`Total Claims`、`Answer`、`What is it?`、`Key Claims`、`Type Decision`、`Difference`、`Findings`、`Questions/Conflicts/Health`、`Index with LLM`、`Chunks only` |
| **C. 专有名词 / 产品名 / 格式名** | ⚠️ 部分合理 | `LLM-Wiki`、`SQLite`、`Markdown / TXT / HTML`、`OpenAI Base URL`、`AgentScope`、`Developer Mode`、`Hybrid` |
| **D. 已通过 `statusStyle`/`labelOf` 翻译过** | ✅ 合理，不需要动 | `待审 / 已验证 / 已被取代 / 研究候选`；`首席执行官`（来自注册表） |

**判定**：B 类必须改，A 类必须改，C 类只在**普通用户路径**上改（Developer Mode 里保留是对的）。

### 1.2 逐页混用清单

| 页面 | 英文文案 | 位置 | 归类 |
|---|---|---|---|
| 外壳 | `PERSONAL KNOWLEDGE OS` | `App.vue:74` | C（品牌副标，可保留） |
| 外壳 | 面包屑 `设置 / Database` | `App.vue:43` | B |
| 外壳 | `搜索知识，或直接提问…` + `⌘K` | `App.vue:112` | 中文但名实不符（见 §4.1） |
| 首页 | 无英文（`最近知识 / 最近知识变化 / 开放问题 / 最近加入` 全中文） | `HomeView.vue:203-244` | ✅ |
| 首页 | `候选实体 / 待确认断言 / 待确认关系 / 陈旧候选（>90 天）` | `HomeView.vue:191-197` | 中文但含概念（§3） |
| 知识 | `Documents` | `KnowledgeView.vue:324` | B |
| 知识 | `Knowledge Objects` | `KnowledgeView.vue:343` | B |
| 知识 | `Chunks (n)` / `chunk #n` | `KnowledgeView.vue:309, 420` | A |
| 知识 | `Index with LLM` / `Chunks only` / `Embeddings` | `KnowledgeView.vue:400-402` | B（抽屉内的三个动作按钮） |
| 知识 | `未索引` / `未抽取` | `KnowledgeView.vue:335`、`HomeView.vue:249` | 中英混（同一状态两种说法） |
| 知识 | `⚡ 生成全部嵌入` | `KnowledgeView.vue:326` | C（"嵌入"= embedding，见 §3） |
| 问答 | `Ask your knowledge` | `QaView.vue:185` | B |
| 问答 | `Hybrid 检索 + Grounded 回答 + 证据与知识边界` | `QaView.vue:187` | C/A（实现细节） |
| 问答 | `AgentScope ReAct Agent 可自主检索知识库、查看实体图谱后作答` | `QaView.vue:188` | C/A |
| 问答 | `Answer`（Agent 面板标题） | `QaView.vue:213` | B（同页另一个面板却叫「回答」`QaView.vue:229`） |
| 问答 | `hybrid / semantic`（检索轨迹 method） | `QaView.vue:85-89, 334-341` | A |
| 问答 | 引用校验维度 key 原样输出 | `QaView.vue:286-289` | A |
| 问答 | `hits` / `sources` | `QaView.vue:311, 334` | A |
| 问答 | `会话中 / 新会话 / ms` | `QaView.vue:216-219` | C |
| 研究 | `Questions / 研究任务 / Conflicts / Health` | `ResearchView.vue:21` | B/A（同一 Tab 列表里中英各半） |
| 研究 | `Open Questions / Conflicts / Resolved / Total Claims` | `ResearchView.vue:224-229` | B |
| 研究 | `Findings` | `ResearchView.vue:238, 247, 329` | B（且"结论"才是用户词） |
| 研究 | `Known · 相关证据` | `ResearchView.vue:324` | B（半英半中） |
| 研究 | `Difference` / `Conflict #1` | `ResearchView.vue:352, 360` | B |
| 研究 | `partially answered` | `ResearchView.vue:294` | A（`statusStyle` 已能翻成"部分回答"） |
| 研究 | `KnowledgeAgent / ResearchAgent` | `ResearchView.vue:136-139, 318` | C（实现名，见 §3） |
| 审核 | `待审 Claims` | `ReviewQueue.vue:196` | B（同组另两项是"待审实体/待审关系"） |
| 审核 | `confidence 90%` / `modality · polarity` / `claim_type` | `ReviewQueue.vue:229-234` | A |
| 审核 | `Claims`（Tab / 分组名） | `ReviewQueue.vue:27, 196`、`ObjectView.vue:242` | B |
| 对象 | 8 个 Tab：`Overview / Claims / Relations / Graph / Events / Evidence / Questions / Ideas` | `ObjectView.vue:242` | B |
| 对象 | `What is it?` / `Key Claims` / `Type Decision` / `Relations` | `ObjectView.vue:249, 251, 255, 264` | B |
| 对象 | `Normalization 规则：modality = possible / probable…` | `ObjectView.vue:294` | A |
| 对象 | 统计条的 key 原样大写（`CLAIMS` / `RELATIONS`） | `ObjectView.vue:233-238` | A |
| 对象 | `offset [start, end)` | `ObjectView.vue:348` | A |
| 知识详情 | `Claim Type / Polarity / Modality / Confidence / Ontology / Context` | `ClaimView.vue:215-221` | A（**已折叠**，可接受） |
| 纠正 | `subject（实体名）/ predicate（如 has_ceo）/ object / relationship（supersedes 等）/ 受影响断言 ID` | `CorrectionView.vue:80-85` | A |
| 纠正 | `subject · predicate · object`（建议卡片） | `CorrectionFlow.vue:182` | A |
| 纠正 | `Schema / Evidence / Quote / 谓词合法性` | `CorrectionFlow.vue:43-47` | A |
| 纠正 | `Operation / Claim / Evolution`（注释里说了前台不该出现，但 `操作 ID` 仍出现） | `CorrectionView.vue:98` | A |
| 设置 | `Provider、检索、Agent Runtime、数据与安全策略` | `SettingsView.vue:66` | C/A |
| 设置 | `Canonical persistence layer` / `OpenAI-compatible endpoint` / `Semantic retrieval` / `Agent workflow runtime` | `SettingsView.vue:81-95` | C（只该出现在 Developer Mode） |
| 设置 | `Database` / `Export` | `SettingsView.vue:111, 115` | C |
| 设置 | `Developer Mode` | `SettingsView.vue:106` | C（合理，就是给开发者看的） |

---

## 2. 术语逐词判定

判定标准：**用户看到的是"我要完成什么"，而不是"系统内部是什么"。**
只有当一个词是用户**必须**理解才能做决定时，才留在普通路径。

| 词 | 出现在 | 用户需要理解吗 | 判定 | 建议 |
|---|---|---|---|---|
| **Claim** | `ReviewQueue.vue:27, 196`、`ObjectView.vue:242`、`ReviewView.vue:26` | ❌ | **必须改** | 依上下文译作「知识」/「事实」/「待确认的说法」。审核页 → 「待确认的知识」；对象 Tab → 「相关事实」 |
| **Predicate** | `CorrectionView.vue:82`、`CorrectionFlow.vue:182` | ❌ | **必须改** | 普通路径只显示注册表标签（如"首席执行官"）；标识符（`has_ceo`）只进 Developer Mode / 技术细节 |
| **Entity** | `ReviewQueue.vue:26`、`ReviewView.vue:25` | ❌ | **必须改** | 「主体」/「对象」；审核页已用"待审实体"，统一为「主体」或「对象」二选一 |
| **Confidence** | `ReviewQueue.vue:230`、`ClaimView.vue:197, 218` | ⚠️ 部分 | **不能只显示数字** | 若保留，必须配一句它意味着什么（"系统很确定"/"需要你再看一眼"）；否则不给。见 §4.2 |
| **Agent** | `App.vue:35`、`QaView.vue:188, 196` | ❌ | **改（普通路径）** | 普通路径不出现。Developer Mode 保留（`/agent` 已有独立空间） |
| **AgentScope** | `QaView.vue:188`、`SettingsView.vue:95`、`ReviewQueue.vue`（无） | ❌ | **移入 Developer Mode** | 同 Provider / Runtime |
| **Provider** | `SettingsView.vue:66, 75` | ❌ | **移入 Developer Mode** | 普通用户只需要"模型服务地址（可选）" |
| **Runtime** | `SettingsView.vue:66, 77` | ❌ | **移入 Developer Mode** | 同上 |
| **Ontology** | `ClaimView.vue:219`、`ObjectView.vue:387`（"Ontology 注册表"） | ❌ | **移入 Developer Mode / 改说法** | 实体编辑弹窗副标题 → 「可选的类型由系统词表决定」 |
| **Database** | `App.vue:43`、`SettingsView.vue:81, 111` | ❌ | **移入 Developer Mode** | 面包屑 `设置 / Database` → `设置 / 数据` |
| **Context** | `ClaimView.vue:221` | ❌ | **移入技术细节** | 已在折叠区，可保留 |
| **Task** | `App.vue:37`（`评测`）、`ResearchView.vue:21`（`研究任务`） | ⚠️ 部分 | **改说法** | 「研究任务」→「在进行的研究」；用户不该理解"任务"这个对象 |
| **Findings** | `ResearchView.vue:238, 247, 329` | ❌ | **改** | 「研究结论」 |
| **Candidate** | 全站 | ⚠️ | **已部分改好** | 后端已给「研究候选」（`knowledge_view.py:58-60`）；剩余硬编码处（`ResearchView.vue:253` 已是"研究候选"，好）统一为「研究候选」/「待确认的知识」 |
| **Modality** | `ObjectView.vue:294`、`ReviewQueue.vue:233`、`ClaimView.vue:217, 240` | ❌ | **移入技术细节** | 折叠区内保留；列表里不要出现 |
| **Polarity** | `ResearchView.vue:355`、`ReviewQueue.vue:233`、`ClaimView.vue:216` | ❌ | **改** | 冲突场景应说「一种来源说成立，另一种说不成立」，而不是 `positive/negative` |
| **Embedding（嵌入/向量）** | `KnowledgeView.vue:326`（`生成全部嵌入`）、`SettingsView.vue:89-90` | ❌ | **改（普通路径）** | 普通路径说「让搜索更懂语义」；`Embedding Dimensions` 移入 Developer Mode |
| **Hybrid / Grounded / RRF / FTS5** | `QaView.vue:187` | ❌ | **移入 Developer Mode / 删除** | 副标题改成「答案来自你导入的内容，并给出来源」 |

---

## 3. 术语替换建议（可直接套用）

### 3.1 状态与知识本身

| 现在 | 建议 | 位置 |
|---|---|---|
| `待审实体` | `待确认的主体` | `ReviewQueue.vue:195`、`ReviewView.vue:25` |
| `待审 Claims` | `待确认的知识` | `ReviewQueue.vue:196`、`ReviewView.vue:26` |
| `待审关系` | `待确认的关系` | `ReviewQueue.vue:197`、`ReviewView.vue:27` |
| `待审知识` | `待确认的知识` | `ReviewView.vue:26` |
| `Claims`（Tab/分组） | `相关事实` / `知识` | `ObjectView.vue:242`、`ReviewQueue.vue:27` |
| `Key Claims` | `关键事实` | `ObjectView.vue:255` |
| `confidence 90%` | `系统很确定（90%）` 或去掉数字只留「系统很确定」 | `ReviewQueue.vue:229-231` |
| `modality · polarity` / `claim_type` | 删除（移入展开的「技术细节」） | `ReviewQueue.vue:233-234` |
| `positive` / `negative` | `成立` / `不成立` | `ResearchView.vue:355` |
| `来源：a1b2c3d4 · asserted` | `来源：<文档标题> · 明确陈述` | `ResearchView.vue:357` |
| `Total Claims` | `知识总数` | `ResearchView.vue:228` |
| `Open Questions` | `待研究的问题` | `ResearchView.vue:225` |
| `Resolved` | `已解决` | `ResearchView.vue:227` |
| `partially answered` | `部分回答`（走 `statusStyle`） | `ResearchView.vue:294` |

### 3.2 页面与区块标题

| 现在 | 建议 | 位置 |
|---|---|---|
| `Ask your knowledge` | `问你的知识库` | `QaView.vue:185` |
| `知识库问答：Hybrid 检索 + Grounded 回答 + 证据与知识边界` | `答案只用你导入的内容作答，并给出每条依据` | `QaView.vue:187` |
| `Agent 问答：AgentScope ReAct Agent …` | 删除（模式选择整体移除，见 P1-5） | `QaView.vue:188` |
| `Answer`（Agent 面板） | `回答` | `QaView.vue:213` |
| `Documents` | `文档` | `KnowledgeView.vue:324` |
| `Knowledge Objects` | `知识对象` | `KnowledgeView.vue:343` |
| `Index with LLM` / `Chunks only` / `Embeddings` | `用模型抽取知识` / `只切分片段` / `生成语义索引` | `KnowledgeView.vue:400-402` |
| `Chunks (n)` | `原文片段（n）` | `KnowledgeView.vue:420` |
| `chunk #n` | 删除（或无标签） | `KnowledgeView.vue:309` |
| `未索引` / `未抽取` | 统一为 `未抽取知识` | `KnowledgeView.vue:335`、`HomeView.vue:249` |
| `What is it?` | `这是什么` | `ObjectView.vue:249` |
| `Type Decision`（含"为什么是这个类型"） | `为什么是「组织」这个类型` | `ObjectView.vue:251` |
| `Questions / 研究任务 / Conflicts / Health` | `问题 / 研究任务 / 冲突 / 健康`（或全部改成用户语言） | `ResearchView.vue:21` |
| `Findings` | `研究结论` | `ResearchView.vue:238, 247, 329` |
| `Known · 相关证据` | `已知的相关事实` | `ResearchView.vue:324` |
| `Difference` | `两种说法的差别` | `ResearchView.vue:360` |
| `Conflict #1` | `冲突 1` | `ResearchView.vue:352` |
| `8 Tab` | 见 [human-use audit P1-6](./frontend-human-use-audit.md#p1-6-knowledge-object-detail-有-8-个平级-tab) 的收敛方案 | `ObjectView.vue:242` |

### 3.3 内部标识符

| 现在 | 建议 | 位置 |
|---|---|---|
| `新断言：<uuid>` / `旧断言已转为历史：<uuid>` / `操作 ID：<uuid>` | 显示知识内容（`subject · predicate · object`）并提供"打开"，不要显示 id | `CorrectionView.vue:96-98` |
| `subject（实体名）/ predicate（如 has_ceo）/ object / relationship（supersedes 等）/ 受影响断言 ID（可选）` | 这一整块是"无模型兜底"，应整体移入 Developer Mode 或折叠为「高级：直接填写」并用中文（主体 / 关系 / 取值） | `CorrectionView.vue:80-85` |
| `检索轨迹` 里的 `hybrid` / `semantic` | 保留但译作「关键词 + 语义共同命中」/「语义相近命中」（`KnowledgeView.vue:46` 已有 `MATCH_LABEL` 可复用） | `QaView.vue:85-89` |
| 引用校验维度 key | 复用中文标签表（参考 `CorrectionFlow.vue:43-47` 的 `DIM_LABELS` 做法，但要中文化并统一） | `QaView.vue:286-289` |
| `offset [start, end)` | 删除（用户只需"打开原文并定位"） | `ObjectView.vue:348` |
| 统计条原始 key（`CLAIMS`…） | 传中文标签 | `ObjectView.vue:233-238` |
| `Normalization 规则：modality = possible…` | 「因果类、且不确定的说法只留在知识里，不进关系图」 | `ObjectView.vue:294`、`ClaimView.vue:240` |

---

## 4. 需要重新设计的文案（不只是换词）

### 4.1 顶栏 omni 名实不符

`App.vue:111-113`：
```
⌕  搜索知识，或直接提问…   ⌘K
```
点击打开的是 `CommandPalette`（`App.vue:120`），面板里同时有「提问知识库」「提问 Agent」「搜索知识」三项（`CommandPalette.vue:19-21`）。文案承诺了"搜索或提问"，实际给的是一个命令列表。

**建议**：要么把 omni 做成真正的搜索输入（回车进 `/knowledge?q=`），要么文案改成 `按 ⌘K 打开命令面板`。**推荐前者**，并把"提问"作为搜索结果为空时的上下文动作。

### 4.2 「90% confidence」对普通用户没有意义

`ReviewQueue.vue:23, 63-66, 229-231`：数值来自抽取器的 `confidence`，可正可负归一化（`:57-62`），展示为整数百分比。用户无法知道 90% 是"系统算出来的概率"还是"历史准确率"。

**建议**（任选其一，但要全站一致）：
- **A（推荐）**：不给数字，给档位词 —— `≥0.9 → 系统很确定`、`0.6~0.9 → 需要你看一眼`、`<0.6 → 系统不太确定`；把数字放进展开的「技术细节」。
- **B**：保留数字，但每处都补一句来源说明（例如 hover/小字「抽取模型自评」），并禁止在批量操作按钮上只用数字（`接受高置信（N）` → `接受系统很确定的 N 条`）。

### 4.3 「审核」页的自我描述偏后台

`ReviewView.vue:42`：`系统提出建议，由你做最终判断——通过的知识会成为可信锚点，拒绝的会被检索与图谱排除。`

这句话是对的，但"可信锚点""检索与图谱排除"是系统语言。

**建议**：`系统会把新读到的东西先拿出来问你——你确认过的，以后才用来回答你的问题；你拒绝的，就不会再出现。`

### 4.4 研究页副标题是流程图

`ResearchView.vue:220`：`Question → Evidence → Claims → Review → Knowledge，一个闭环。`

**建议**：`问一个还不知道答案的问题，让系统去找证据、得出结论，再问你哪些可以留下。`

---

## 5. Settings 角色分层

### 5.1 现状

`SettingsView.vue` 四个分区（`SettingsView.vue:73-97`）+ 高级区（`:102-117`），全部对所有人可见：

| 分区 | 字段 | 适合谁 |
|---|---|---|
| 基础配置 | `SQLite 数据库`（路径输入框）+ 副标 `Canonical persistence layer` | 维护者 |
| LLM | `OpenAI Base URL`、`默认模型`、`API Key` | 普通用户（Base URL 属可选） |
| 检索与 Embedding | `Embedding Model`、`Embedding Dimensions`、`LLM Batch Chunks`、`Max Search Results` | 维护者 |
| Agent Runtime | `AgentScope`、`自动嵌入` | 维护者（`自动嵌入` 属普通用户可选） |
| 高级 | `Developer Mode`、`Database`、`导出全部知识` | 混合 |

### 5.2 USER_SETTINGS（默认可见）

| 项 | 用户语言 | 来源 |
|---|---|---|
| 模型服务 | `模型服务地址（一般不用改）` | `openai_base_url` |
| 默认模型 | `使用哪个模型` + 一句"识别文档、回答问题都用它" | `openai_model` |
| API Key | `访问密钥` + `已配置 — 留空保持不变`（现状保留，这句很好） | `openai_api_key` |
| 测试连接 | `测试连接`（现状保留） | `/api/health` |
| 自动语义索引 | `导入后自动让搜索更懂语义`→ 建议改写，去掉"嵌入" | `auto_embed` |
| 导出 | `导出全部知识（JSON 快照）` | `/api/export` |
| 侧栏 | `显示开发工具（Agent 工作台、评测）` —— 用一句人话描述开关后果，而不是 `Developer Mode` | `store.developerMode` |

**普通用户不需要出现的词**：`AgentScope`、`Embedding Dimensions`、`LLM Batch Chunks`、`Max Search Results`、`SQLite`、`Canonical persistence layer`、`Provider`、`Runtime`。

### 5.3 DEVELOPER_SETTINGS（`store.developerMode === true` 时出现）

| 项 | 理由 |
|---|---|
| `OpenAI Base URL` / `OpenAI-compatible endpoint` | 自建 / 代理场景 |
| `Embedding Model` / `Embedding Dimensions` | 换嵌入模型会改维度和库结构 |
| `LLM Batch Chunks` | 影响抽取成本与并发写锁 |
| `Max Search Results` | 检索行为调参 |
| `AgentScope` 开关 | 运行时实现 |
| `SQLite 数据库` 路径 | 数据落盘位置 |
| `Database` 页入口（表计数 / 完整性检查） | 维护 |
| 页面副标题 | 由 `Provider、检索、Agent Runtime、数据与安全策略` 改为仅在该模式出现 |
| 纠正页的「手动填写（无需模型）」的 `subject/predicate/object` 表单 | 现在就是给无模型兜底的技术路径，适合放这里 |

**实现方式**：开关已存在（`SettingsView.vue:105-108` + `stores/app.ts:111-114`），只需把上述字段用 `v-if="store.developerMode"` 包住，并按分区重排（普通区在上一屏即可完成）。

---

## 6. 文案一致性规则（建议写入 CODING-STANDARD）

1. **状态词只有一个来源**：显示状态必须走 `StatusTag` / `statusStyle`（`utils/status.ts`），不允许视图里硬编码 `partially answered`、`resolved` 等枚举。
2. **谓词只有一个来源**：显示谓词必须走 `labelOf()`；构造句子前先 `hasLabel()`（`utils/claim.ts:63-69`）。**标识符不得出现在普通路径**。
3. **标识符不得出现在普通路径**：`claim_id` / `entity_id` / `relation_id` / `operation_id` / `document_id` / `chunk_id` 只能出现在 Developer Mode 或「技术细节」折叠区，且必须是"复制"用途而非"阅读"用途。
4. **英文只允许三类**：专有名词（`OpenAI` / `SQLite` / `AgentScope`）、格式名（`Markdown`）、Developer Mode 内的技术标签。其余一律中文。
5. **动词优先于名词**：标题写用户要完成的事（`把知识交给我`），不写系统结构（`Knowledge Objects`）。
6. **数字必须带解释**：任何百分比/计数出现在普通路径时，必须能回答"这意味着什么"。否则移入技术细节。

---

## 7. 顺带发现（死代码，P3）

| 文件 | 问题 |
|---|---|
| `frontend/src/components/HelloWorld.vue` | Vite 脚手架残留，全项目无引用（是唯一带 `role`/`aria-*` 的组件） |
| `frontend/src/style.css` | 脚手架样式，`main.ts:5` 只引入 `styles/tokens.css`，此文件从未生效；而**它恰好是唯一定义了 `:focus-visible` 的地方**（`style.css:114-117`）→ 见 [interaction inventory §5](./frontend-interaction-inventory.md#5-键盘可达性清单) |
| `stores/app.ts:100` | `searchTerm` 被 `CommandPalette.vue:114` 写入但无人读取 |

以上三项不影响用户任务，建议随下一轮清理。

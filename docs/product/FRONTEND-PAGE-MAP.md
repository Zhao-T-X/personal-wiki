# 前端页面职责地图（Page Responsibility Map）

> 每条记录只回答一个问题：**这个页面解决用户什么问题？**
> 重点不是删页面，而是确认"用户不应因为页面存在而自己判断该去哪个页面"。
> 路由仍以 `frontend/src/router/index.ts` 为准（13 条普通 + 开发者模式）。

| 页面 | 用户目标（用 A/B/C 语言） | 当前主要能力 | 概念暴露风险 | 是否重复 | 是否保留 |
|---|---|---|---|---|---|
| **Home** | "我现在要做什么"（A/B/C 共同入口） | OneBox 单入口 + 导入 + 最近变化 + 待判断 | 低（合规范例） | — | ✅ 产品主链核心 |
| **Knowledge** | B："我有什么知识" | 搜索（知识/原文）、文档/对象/图谱/时间线、就地审核 | 中（露 `chunk`/`Embeddings`） | 与 Object/Claim 重叠 | ✅ 但需去重+降概念 |
| **QA** | C："我想知道答案" | 知识库问答 / Agent 问答 / 引用校验 / 边界 | 高（Agent 模式 + `llm_runs`） | 与 OneBox 的 ask 重叠 | ✅ 但需隐藏 Agent 区别 |
| **Research** | C："我想深入研究" | Research Task 闭环（Question→Evidence→Claims→Review） | **极高（Claim/Predicate/Agent）** | 与 Knowledge 重叠 | ✅ 但主语必须人话化 |
| **Review** | B/C："我需要确认什么" | 按类别分组的待审候选 + 就地处理 | 中（标题含 `Claims`） | 与 Knowledge 审核重叠 | ✅ |
| **Correction** | C："这条不对，我要改" | 一句话→计划→确认→演化+审计 | 中（`CORRECT 操作`/`operation_id`） | 与 QA"这条有问题"重叠 | ✅ |
| **Settings** | "我要配置系统" | Provider/检索/Agent Runtime/数据 | 中（Embedding 术语 + raw JSON） | — | ✅ |
| **Agent** | 开发者："我要调试 Agent" | 6 智能体能力/行为/运行历史 | DEV 专属（Agent/Run/Context/Ontology） | — | Developer Mode |
| **Eval** | 开发者："我要评测" | 抽取/问答/检索评测 + 基线对比 | DEV（本体已中文化） | — | Developer Mode |
| **Database** | 开发者："我要看数据库" | SQLite 直览 + 完整性检查 | DEV（raw 表名） | — | 设置→高级 |
| **Extraction Experiment** | 开发者："抽取质量实验" | Golden 前后对比 | DEV（无禁止词） | — | Developer Mode |
| **Claim**（/knowledge/claim/:id） | "这条知识是什么" | 知识详情 + 演化链 + 技术细节折叠 | 中（折叠露 Ontology/Context/Claim Type） | 与 Object 重叠 | ✅ 折叠即可 |
| **Object**（/knowledge/object/:id） | "这个对象是什么" | What/Type/Key Claims/Relations/Graph/Events | **高（编辑弹窗露 Ontology 注册表）** | — | ✅ 修编辑弹窗 |

---

## 重复与重叠分析（用户被迫"自己挑页面"的根因）

1. **Search 入口三处**：OneBox（ask）、QA 页、Knowledge 页搜索 —— 三者语义不同
   （一句话问 / 显式问答 / 搜知识对象），但用户视角都是"找答案"。建议 OneBox 与
   QA 合并为单一"问"，Knowledge 的搜索保留为"浏览知识库"。
2. **审核三处**：Home 待判断摘要、Review 页、Knowledge 就地审核 —— 功能重复。
   建议 Home 摘要点击直达 Review，Knowledge 仅做"查看"，不重复审核动作。
3. **知识详情两处**：Object（实体）与 Claim（断言）分离 —— 对普通用户，一条"事实"
   应是一个对象；技术上的 claim/object 拆分不应前置给用户。建议统一为"知识详情"，
   内部再分实体与断言。
4. **研究 vs 知识**：Research 本质是"带调研的问答 + 候选入库"，不应让用户先理解
   二者区别（见 Journey 05）。

---

## 判断结论

- **必须保留（用户价值页）**：Home、Knowledge、QA、Research、Review、Correction、Settings。
- **Developer 隔离（勿进普通导航）**：Agent、Eval、Database、Extraction Experiment。
- **结构性动作（下一步"改"阶段）**：
  1. 把 Claim / Object 合并为统一"知识详情"，消除"用户先选实体还是断言"的隐性门槛；
  2. 把 Research 的主语从 `Claims/Predicate/Agent` 改为"研究发现 / 冲突 / 健康度"；
  3. Home 摘要 → Review / QA 的跳转做顺滑，减少"自己判断去哪"的频次。

> 本地图只描述现状与职责，具体改法记入 `UX-PROBLEM-BACKLOG.md`，按 P0→P1→P2 排序。

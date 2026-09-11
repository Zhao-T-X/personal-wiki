# Design: LLM-Wiki 前端重设计（Vue 3 工作台）

- Date: 2026-09-10
- Status: Approved
- 视觉基准: `docs/prototype/llm-wiki-full-ui-v3-qa.html`（浅色蓝紫，token 体系照搬）

## 已确认决策

1. **架构升级**：Vue 3 + TypeScript + Vite + Vue Router + Pinia；样式用原型自带的 CSS 变量 token（不引入 Tailwind）；图谱 Cytoscape.js；Markdown 渲染 markdown-it。
2. **交互与布局重新设计**：抽屉优先（详情不跳页）、知识库四 Tab、全局搜索入口、Toast/加载态统一——不是 1:1 迁移。
3. **视觉**：以浅色原型为准（推翻早前的暗色 Obsidian 选择）。
4. **砍掉**：Workflows 页（含可编辑画布）、通知中心、用户头像体系。最终 **14 个页面**。
5. 原型中的伪造指标（无后端依据的百分比/趋势）一律不展示，有真实数据才渲染。

## 页面 → 数据映射（14 页）

| 路由 | 页面 | 数据源 |
|---|---|---|
| `/` | Dashboard | `/api/stats`、最近 runs、最近 documents、增长曲线 |
| `/knowledge` | 知识库 | `/api/documents`（四 Tab：Documents/Chunks/Claims/Runs） |
| `/qa` | 知识问答 | `POST /api/ask`；最近问答读 `/api/runs?task_type=ask` |
| `/entities` | 实体管理 | `/api/entities`、`/api/entities/{id}`、PATCH |
| `/graph` | 关系图谱 | `/api/graph`、`/api/entities/{id}/graph`，Cytoscape 实装 |
| `/events` | 事件管理 | 🆕 `GET /api/events` |
| `/ideas` | 观点与想法 | 🆕 `GET /api/ideas`（状态流转用现有 PATCH `/api/knowledge/idea/{id}/status`） |
| `/questions` | 问题管理 | 🆕 `GET /api/questions` |
| `/evidence` | 证据管理 | `/api/claims`（quote / offset / 来源列） |
| `/tasks` | 任务管理 | `/api/runs`（llm_runs）+ 详情抽屉 |
| `/logs` | 智能体日志 | `/api/runs/{id}` steps 链路（Runtime Trace 样式） |
| `/prompts` | Prompt & Skills | `/api/agent/prompts` 全套（版本/恢复/重置/预览） |
| `/settings` | 系统设置 | `/api/settings` GET/PUT |
| `/database` | 数据库 | `/api/stats` + 🆕 integrity 检查端点 |

## 新增后端端点（全部沿用 main.py 紧凑风格）

1. `GET /api/events?limit=` — events 表列表。
2. `GET /api/ideas?status=&limit=` — ideas 表列表。
3. `GET /api/questions?status=&limit=` — questions 表列表。
4. `GET /api/stats/timeseries?days=14` — documents/entities/claims 按 created_at 的逐日计数（Dashboard 曲线）。
5. `GET /api/database/integrity` — `PRAGMA integrity_check` + `page_count`/`page_size` 换算库大小。
6. `GET /api/documents` 扩展：每文档附 `chunk_count`、`last_run_status`、`last_run_at`（LEFT JOIN 聚合，一条 SQL）。

## 布局骨架（照原型）

- 左侧 242px 侧栏：品牌区 + 三组导航（概览 / 知识空间 / 智能体空间+系统）+ 底部系统状态卡（真实 `/api/health`）。
- 顶栏：全局搜索（回车→`/knowledge` 带条件）+ 设置图标；无通知铃铛、无头像。
- 内容区 `.content` 滚动；页头（标题+描述+动作区）按原型 `.pagehead`。
- 详情抽屉 430px 右滑（实体 / Run / 文档）；七个 modal 表单；Toast 右下。

## 工程结构

```
frontend/
  package.json  vite.config.ts  tsconfig.json  index.html
  src/
    main.ts  App.vue
    styles/tokens.css        # 原型 :root 变量 + 组件类移植
    api/client.ts  api/types.ts
    stores/app.ts            # 健康/设置/全局搜索词
    router/index.ts
    components/ (KpiCard, StatusTag, DataTable, AppDrawer, AppModal, AppToast, EmptyState, MarkdownView, KpiTrend)
    views/ (Dashboard, Knowledge, Qa, Entities, Graph, Events, Ideas, Questions, Evidence, Tasks, Logs, Prompts, Settings, Database)
```

## 构建与服务

- `npm run build` → `web/dist`；`vite.config.ts` dev proxy `/api` → `http://127.0.0.1:8000`。
- `main.py`：`/` 与 `/assets` 优先服务 `web/dist`（存在时），否则回退旧 `web/index.html`；`/api` 不变。
- `frontend/node_modules` 不进任何打包/上传范围。

## 测试与验收

- 后端新增端点：pytest 补 3 个用例（events/ideas/questions 列表、integrity、documents 聚合）。
- 前端：`npm run build` 零错误 + TypeScript 通过。
- 手动验收：14 页逐页对照原型核验布局；真实数据流（建文档→索引→Tasks/Logs 可见；Ask 出引用卡；Graph 可缩放点选）。
- 回退验证：删除 `web/dist` 后旧 UI 仍可用。

## 不做（YAGNI）

Workflows 画布、通知中心、头像/用户体系、伪造趋势指标、批量导入向导、可编辑工作流、暗色主题切换。

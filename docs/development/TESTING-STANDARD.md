# TESTING-STANDARD — 测试规范

> 状态：**规范 + CURRENT 事实**。测试分五层，其中 **Domain Invariant Tests 是项目最重要的一类**。

## 1. 测试分层

```text
Unit          纯函数 / 单个模块，无 IO
Integration   跨模块 + 真实 SQLite（临时库）
API           FastAPI TestClient 级别（httpx）
Workflow      端到端用例（ingest / extract / ask / research）
Invariant     Domain 不变量（见 §4，最高优先级）
```

## 2. 当前测试盘点（CURRENT）

截至 2026-09：**60 个测试文件，501+ 用例**。逐文件清单不再在此维护——那份表每加一个测试文件就会过期一次，过期的事实比没有事实更糟。

要看真实的测试面，直接看源头：

```bash
pytest --collect-only -q          # 全部用例清单
ls tests/test_*.py                # 测试文件列表
```

按**能力域**而非文件名理解测试覆盖：

| 能力域 | 代表测试 |
|---|---|
| 核心链路 / 端到端 | `test_core.py`、`test_full.py`、`test_pipeline.py` |
| 知识语义（编译 / 演化 / 操作） | `test_knowledge_compilation.py`、`test_knowledge_operations.py`、`test_correction_loop_e2e.py` |
| 检索与知识层 | `test_retrieval_dedup.py`、`test_search_knowledge.py`、`test_qa_knowledge.py`、`test_research_knowledge.py` |
| Context Runtime | `test_context_*.py`、`test_skill_lazy_loading.py`、`test_task_packet*.py`、`test_token_usage.py` |
| 知识完整性与修复 | `test_knowledge_integrity.py`、`test_predicate_migration.py`、`test_onebox_intent.py` |
| 架构红线 | `test_architecture.py`（import 规则 + 行为对照） |
| API 表面 | `test_pkos_api.py`、`test_frontend_api.py` |

运行：`pytest -q`（`pytest.ini` 指定 `testpaths = tests`）。

## 3. 分层职责

- **Unit**：判定函数（如 `app/claim_relations.py::compare_claim`、`ontology.match_claim_predicates`）用最小输入验证。
- **Integration**：对真实 SQLite 临时库验证 schema / 迁移 / 事务。
- **API**：校验状态码与 JSON 形状（404/409/422/503/502 映射）。
- **Workflow**：验证用例在失败与降级下的行为（如研究流水线知识步骤失败仍返回）。

## 4. Domain Invariant Tests（重点）

不变量测试断言**领域规则永不被违反**，是防回归的核心：

```text
superseded 的 Claim 不能是 current
contradicts 不自动 supersede
Evidence 不能指向不存在的 Chunk
Predicate 必须来自 Registry
Correction 必须产生 Operation 记录
Operation 失败必须 rollback
LLM 非法 JSON 不得进入 Domain
Repository 不得执行 Domain Rule
文档 content_hash 唯一
chunk 偏移落在原文范围内
实体名（CI）唯一 / 类型属于注册表
关系端点类型满足注册表约束
```

要求：

1. 每个新增/修改领域规则，**必须**附带或更新对应不变量测试。
2. 不变量测试命名清晰表达规则（如 `test_superseded_claim_is_not_current`）。
3. 迁移（[../architecture/MIGRATION-PLAN.md](../architecture/MIGRATION-PLAN.md)）在动结构**之前**先补齐不变量测试。

## 5. 测试环境

- `tests/conftest.py` 提供临时数据库与夹具；测试使用独立 DB，避免污染 `data/wiki.db`。
- 不依赖外部网络：LLM / Embedding 相关路径需可 mock 或跳过（无 Key 时降级路径也可断言）。
- 测试必须**可重复**且**相互独立**（顺序无关）。

## 6. 反模式

```text
❌ 只测 happy path，不测错误与降级
❌ 断言实现细节（如内部私有函数）而非行为
❌ 依赖真实 LLM 返回（不稳定、慢、花钱）
❌ 修改全局 settings / 真实 DB
❌ 用 sleep 等待异步，而非事件/轮询
```

## 7. 提交要求

```text
□ 新功能有对应分层测试
□ 改领域规则 → 有不变量测试
□ 改 API → 有状态码/JSON 形状测试
□ 全量 pytest 通过
```

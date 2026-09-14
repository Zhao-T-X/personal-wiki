# ERROR-HANDLING — 错误处理规范

> 状态：**规范**（以现有实现为参考）。核心目标：**失败可解释、失败不污染、失败不静默**。

## 1. 三条原则

```text
1. 可解释：错误必须携带类型与上下文，禁止吞掉后返回空对象。
2. 不污染：失败不得留下半成品状态（事务回滚 / 失败轮不推进历史）。
3. 不静默：可降级的失败必须记录（stderr / runlog），不可降级的必须上抛。
```

---

## 2. 错误分类与状态码

| 类别 | 触发 | HTTP | 处理 |
|---|---|---|---|
| 参数错误 | Pydantic / 业务校验失败 | `422` | 直接返回，不进入领域 |
| 未找到 | 目标资源不存在 | `404` | 返回明确 detail |
| 冲突 | 唯一性 / 状态不允许 | `409` | 返回冲突原因 |
| 类型不支持 | 导入扩展名不支持 | `415` | 返回支持清单 |
| 配置缺失 | 未配置 LLM Key / 关闭 AgentScope | `503` | 提示如何配置 |
| 上游失败 | LLM / Agent 调用异常 | `502` | 记录 traceback 后返回 |
| 未知异常 | 兜底 | `502` | 记录 traceback，返回 JSON |

参考实现：`app/main.py` 中 `index_doc` / `embed_doc` / `agent_ask` / `research_run` 的 `except KeyError/RuntimeError/ValueError → HTTPException` 映射。

---

## 3. 边界必校验（Rule 7）

每个外部边界都是**不可信输入**：

```text
API 入参        → Pydantic + 业务校验
文件导入        → 扩展名白名单（app/importer.py::SUPPORTED）
LLM 输出        → Parse → jsonschema → normalize → domain validate
Agent 工具返回  → 压缩 + 截断标记（app/tools/compression.py）
```

**禁止**：LLM 输出未经校验直接落库（见 [CODING-STANDARD.md](./CODING-STANDARD.md) LLM 规范）。

---

## 4. 失败不污染状态

- **事务性**：多步写入必须在一个事务内，失败回滚。示例：`app/service.py::delete_document`（派生表清除 + 文档删除同一事务，避免半删除与锁泄漏）；`index_document` 中 `persist_extraction` + `detect_claim_relations` 共享事务。
- **多轮对话**：失败轮不得推进历史。`app/workflows/agent_workflow.py::run_agent` 中，`begin_turn` 之后若 Agent 调用抛错，不会走到 `end_turn`，因此 `conversation_summaries` 不被污染。
- **抽取**：单条关系判定失败不得丢失该 Claim（`app/claim_relations.py::detect_claim_relations` 逐条 best-effort，异常被吞但记录策略是「不因关系失败而丢知识」）。

---

## 5. 可降级失败（Graceful Degradation）

当次要步骤失败、主任务仍可完成时，允许降级，但必须**记录**：

```text
检索无 embedding        → 回落纯词法（app/retrieval.py::search try/except）
研究流水线知识步骤失败   → ResearchAgent 无 packet 继续（run_research_pipeline）
Context Cache 端点失败   → 前端自动降级，不拖垮整页
runlog 写入失败          → 仅 stderr 告警，不影响主流程（app/runlog.py::_warn）
```

规则：**降级不得伪装成成功**。若结果可能不完整，应在响应中体现（如 `method: lexical`、`cached: true`、`packet_id: null`）。

---

## 6. Agent / LLM 错误

- `agent_ask` 必须始终返回 JSON：`RuntimeError → 503`，其它异常打印 `type + message` 与 traceback 后 `502`。
- 禁止把上游异常的原始 HTML / 非 JSON 直接透传给前端。
- LLM `repair` 流程（`app/llm.py`）允许一次修复重试；仍失败则按不可降级处理。

---

## 7. 日志与错误的关系

错误记录遵循 [LOGGING-STANDARD.md](./LOGGING-STANDARD.md)：结构性运行信息进 `runlog`（`llm_runs` / `llm_run_steps`），诊断信息进 stderr。**禁止**用 `print` 输出正常业务结果。

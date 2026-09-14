# API-STANDARD — HTTP API 规范

> 状态：**约定 + CURRENT 事实**。目标是不再往 `main.py` 无规则地加特殊 endpoint。

## 1. URL 结构

统一三种形态：

```text
/api/{resource}
/api/{resource}/{id}
/api/{resource}/{id}/{action}
```

示例：

```text
GET  /api/claims
GET  /api/claims/{id}

POST /api/documents/{id}/index
POST /api/documents/{id}/embed

POST /api/knowledge/operations      # 统一知识操作入口（TARGET）
POST /api/knowledge/corrections     # 纠正（TARGET）
```

## 2. 方法与语义

| 方法 | 语义 | 幂等 |
|---|---|---|
| `GET` | 读取，不产生副作用 | 是 |
| `POST` | 创建 / 触发动作 | 否 |
| `PUT` | 全量替换 / 更新 | 是 |
| `PATCH` | 局部更新 | 否 |
| `DELETE` | 删除 | 是 |

## 3. 请求 / 响应

- **入参**：一律用 Pydantic 模型（`app/models.py`）；文件上传用 `multipart`。
- **响应**：JSON。列表返回数组或 `{items, total}`；单项返回对象。
- **分页**：`limit` / `offset`，并做上下限钳制（现有 API 已统一 `max(1, min(limit, N))`）。
- **字段命名**：`snake_case`（与 DB / Pydantic 一致）。
- **时间**：ISO/SQLite `CURRENT_TIMESTAMP` 文本。

## 4. 错误响应

- 一律返回 JSON，且带清晰的 HTTP 状态与人类可读的 `detail`。
- **禁止**返回非 JSON 导致前端 `Unexpected token ... is not valid JSON`（`app/main.py::agent_ask` 已专门处理该问题）。

状态码约定：

| 码 | 场景 |
|---|---|
| `200` | 成功 |
| `404` | 资源不存在 |
| `409` | 唯一性冲突（如同一内容文档 / 重名实体） |
| `415` | 不支持的文件类型（导入） |
| `422` | 参数非法 / 校验失败 |
| `503` | 依赖未配置或不可用（如未配置 LLM、AgentScope 关闭） |
| `502` | 上游（LLM / Agent）调用失败 |

映射细则见 [ERROR-HANDLING.md](./ERROR-HANDLING.md)。

## 5. CURRENT 端点盘点（事实）

现有 API **大体**符合本规范，但存在少量历史特殊形态：

```text
POST /api/agent/ask                     动作型（保留：Agent 用例入口）
POST /api/ask                           动作型（保留：RAG 问答入口）
PATCH /api/knowledge/{kind}/{id}/status 粗粒度状态变更（TARGET 收敛为 Operation）
POST /api/runs/{run_id}/cancel          动作型（合理 /{id}/{action}）
POST /api/research/{task_id}/run        动作型（合理 /{id}/{action}）
```

> 这些不是立刻要改的错误，而是**登记在案**：新增能力不得再引入新的「无规则特殊 endpoint」。

## 6. 演进纪律

1. 新资源遵循 `/api/{resource}(/{id})(/{action})`。
2. 知识变更优先经 `POST /api/knowledge/operations`（TARGET），而不是新增 `POST /api/claims/{id}/fix-something`。
3. 路由组织应随模块拆分（Router by resource），`main.py` 只做挂载（见 [../architecture/MIGRATION-PLAN.md](../architecture/MIGRATION-PLAN.md) Phase 2）。
4. 破坏性变更需在 [docs/superpowers/specs/](../superpowers/specs/) 记录或在 ADR 中说明。

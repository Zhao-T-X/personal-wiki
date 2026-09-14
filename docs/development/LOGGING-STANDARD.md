# LOGGING-STANDARD — 日志与运行记录规范

> 状态：**规范 + CURRENT 实现**（以 `app/runlog.py` 为准）。

## 1. 两类记录

```text
Run Record   用户可见任务的结构化账本（SQLite: llm_runs / llm_run_steps）
Diagnostics  诊断信息（stderr）：降级、吞掉的异常、修复重试
```

正常业务结果**不写日志**，而是进 Run Record 或直接返回给调用方。

---

## 2. Run / Step 模型（CURRENT）

```text
Run   一次用户可见任务（extract / ask / agent），聚合 token 与状态
Step  一次实际模型调用（name / status / input_summary / output_text / usage）
```

- 入口：`app/runlog.py::record_run(task_type, document_id, agent_role, model)`，支持 `with` / `async with`。
- 步骤：`run.step(name, input_summary=...)`，同样支持上下文管理。
- 用量：`step.set_usage({'prompt_tokens','completion_tokens','usage_source'})`；provider 实测经 `app/agent_usage.py` 采集。

### task_type 取值

```text
extract   文档抽取
ask       RAG / 证据问答
agent     Agent 对话
```

（受 `llm_runs.task_type` 的 CHECK 约束，新增需迁移。）

---

## 3. 写入必须「best-effort」

> **记录失败绝不能破坏主流程。**

- 所有 runlog 写入包裹 try/except，失败仅 `_warn()` 到 stderr（`app/runlog.py::_warn`）。
- 连接 **lazy import**（`_connect()` 内 `from .db import connect`），使测试中数据库切换/reload 能重新绑定。
- 使用 `contextvars` 传递当前 Run（`current_run()`），避免显式层层传参。

---

## 4. 状态语义

| 状态 | 含义 |
|---|---|
| `started` | 已开始，未结束 |
| `success` | 成功结束 |
| `failed` | 异常结束（记录 `error_message`） |

- Run 结束时汇总成功 step 数与 token 总量（`prompt_tokens` / `completion_tokens`）。
- 取消是**协作式**的：`request_cancel` / `is_cancelled`，长流水线在批次间轮询；Run 结束清理取消标记（`clear_cancel`）。

---

## 5. 诊断信息（stderr）

允许写 stderr 的场景：

```text
降级发生        search 回落词法 / cache 端点失败
被吞的异常      关系判定单条失败 / 自动 embedding 失败
修复重试        LLM output repair
上游调用失败     agent_ask 的 traceback
```

格式建议：`[模块] 简述: 类型: 消息`，例如 `[runlog] write failed: ...`、`[workflow.research] knowledge step failed: ...`。

---

## 6. 禁止事项

```text
❌ 用 print 输出正常业务结果
❌ 记录失败导致主流程抛错
❌ 把完整敏感内容（API Key）写入日志或 runlog
❌ 在循环里逐条开新连接写日志（应批量/复用）
❌ 吞掉异常且不留下任何痕迹（至少 stderr 一行）
```

---

## 7. 可观测性衔接

Run Record 与 Context Runtime 的 trace（`context_runs` / `context_sections`）互补：

- `llm_runs` / `llm_run_steps` = **调用成本与结果**（provider 实测 token）。
- `context_runs` / `context_sections` = **上下文装配账本**（预算 / 实际 / 裁剪 / 为什么加载）。

两者通过 `run_id` 关联，供 Agent 工作台 Metrics 使用（见 `GET /api/context/metrics`）。

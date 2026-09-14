# EXTENSION-POINTS — 扩展机制

> 状态：**TARGET 注册式扩展** + **CURRENT 现状**。目标是「扩展优于修改」：新增能力靠注册，而不是修改中心分发逻辑。

## 1. 可注册的扩展点

系统明确允许以下扩展点：

```text
Operation      知识操作（create / correct / merge / split / …）
Provider       Context 提供者（knowledge / evidence / history / skill / tool）
Workflow       用例编排（ingest / extract / ask / research / review / correct）
Retriever      检索实现（lexical / semantic / hybrid / entity / claim）
Agent          AgentScope 角色
Exporter       导出格式（json / markdown / …）
Importer       导入格式（md / txt / html / …）
Ontology Type  本体类型（Entity Type / Predicate / Relation Type）
```

---

## 2. 反模式：中心分支

不要这样增长：

```python
if operation == 'correct': ...
elif operation == 'merge': ...
elif operation == 'split': ...
elif operation == 'supersede': ...
```

每加一个能力就改一次中心函数，最终无人敢动。改为注册：

```python
@register_operation("correct_claim")
class CorrectClaimOperation:
    def validate(self, req): ...
    def execute(self, req): ...
```

---

## 3. 各扩展点的现状与目标

| 扩展点 | CURRENT | TARGET |
|---|---|---|
| Operation | 无（仅抽取落库 + 状态 PATCH） | `@register_operation` 注册表 |
| Provider | ✅ 已有：`app/context/providers/`（base / knowledge / evidence / history / skills / tools） | 保持注册式 |
| Workflow | 部分：`app/workflows/agent_workflow.py` | Facade 化 + 可注册用例 |
| Retriever | 硬编码在 `app/retrieval.py` | `@register_retriever` |
| Agent | 固定 5 角色（`app/agents/__init__.py`） | 注册式角色 + 明确的准入理由 |
| Exporter | `scripts/export_json.py` + `GET /api/export` | 注册式导出器 |
| Importer | `app/importer.py::SUPPORTED`（扩展名 → source_type 映射） | 注册式导入器 |
| Ontology Type | ✅ 已有：`schemas/*.json` registry + `app/ontology.py` | 保持；加 `registry_version()` 版本失效 |

---

## 4. Provider 扩展（已落地参考实现）

Context Runtime 的 Provider 是当前最成熟的扩展点：

```text
ProviderRegistry.register(Provider)
Provider.collect(task, plan) -> Iterable[ContextItem]
```

新增上下文来源（例如「示例库 Provider」）应实现 `Provider` 并注册，而不是修改 Planner / Compiler。

---

## 5. Ontology 扩展

Entity Type / Predicate / Relation Type 全部来自 `schemas/` 下的 JSON registry：

```text
schemas/entity-type-registry.json
schemas/claim-predicate-registry.json
schemas/relation-predicate-registry.json
schemas/relation-normalization-rules.json
```

- 扩展本体 = 修改 registry（+ 迁移），而不是在代码里加 `if type == ...`。
- `app/ontology.py::registry_version()` 对 `schemas/*.json` 取指纹，供 Context Cache 版本失效使用。**任何 registry 变更都会改变指纹，从而让旧缓存自动失效。**

---

## 6. Agent 扩展的准入

新增 Agent 是一个**重决定**，必须先回答 [docs/development/FEATURE-DEVELOPMENT.md](../development/FEATURE-DEVELOPMENT.md) 中的准入问题：

> 为什么现有 Agent + Workflow + Operation 无法完成？

只有「职责边界确实不同、且无法用现有角色组合表达」时才新增 Agent，否则应新增 Workflow / Operation / Tool。

---

## 7. 扩展点注册的统一约定（目标）

```text
1. 每个注册点提供装饰器/注册函数
2. 注册项声明：名称、输入契约、能力、所需权限
3. 注册表可枚举（供 API / UI / 测试发现）
4. 未注册的请求一律拒绝，而不是 fallback 到 generic 行为
```

最后一条与 `docs/standards/12-IMPLEMENTATION-MAP.md` 的兼容原则一致：**未知谓词 / 类型 / 语义宁可拒绝，也不映射成 generic 值。**

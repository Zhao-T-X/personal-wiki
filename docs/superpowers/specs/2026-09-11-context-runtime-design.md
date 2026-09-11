# Context Runtime v1.0 — 设计规格

> 目标：把 LLM-Wiki 从 **Prompt + Everything** 升级为 **Progressive Context Loading**。
> 判据不是"prompt 更短"，而是：**每一个进入 LLM Context 的 Token 都有明确的任务价值。**

## 0. 现状基线（事实，带定位）

改造前每次 LLM 调用的 Context 组成：

| # | 位置 | 被整块塞入的内容 |
|---|---|---|
| 1 | `app/prompt_profiles.py:23-30` | 每个角色的 core contract |
| 2 | `app/prompt_profiles.py:204-207` + `app/skills.py:27-42` | 完整 `SKILL.md` body（knowledge-curation 959 B / knowledge-extraction 1.12 KB） |
| 3 | `app/agents/extraction_agent.py:199-203,214` | **内联 3 个 reference 全文**：`entity-types.md` 4.17 KB + `claim-predicates.md` 1.64 KB + `extraction-v2.md` 5.06 KB（其中 `extraction-v2.md` 正文**重复列出全部枚举**） |
| 4 | `app/prompt_profiles.py:208-209`（上限 `:153-154` 12000 字符） | 完整 user custom prompt |
| 5 | `app/prompt_profiles.py:210` | `[NON-NEGOTIABLE]` 固定段 |
| 6 | `app/agents/base.py:34-36,43-45` | AgentScope `LocalSkillLoader` 注入的 skill 目录（与 #2 双重注入） |
| 7 | `app/agents/base.py:16-22,41-42` + `app/tools/*.py` docstrings | 5 个工具的完整函数签名 + docstring → JSON schema |
| 8 | `app/agents/extraction_agent.py:26-119,226-229` | 完整 structured-output schema（含全部 Literal 枚举） |
| 9 | `app/llm.py:60-66` | 整批 chunk 全文（≤ `llm_batch_chunks`=8 × 1800 字符 ≈ 14400 字符） |
| 10 | `app/llm.py:32-37,74-76` | repair 时回传上一次**完整 JSON** |
| 11 | `app/retrieval.py:102-124`（`search` → `:110-113` 取整行） | `top_k`=8 个**整 chunk 全文** + 每 chunk 最多 6 条 claims |
| 12 | `app/retrieval.py:127-135` | `format_evidence` 把整 chunk + claims 拼成一段 |
| 13 | `app/llm.py:87-90` | 固定 system 文本 + Question + 上述整段 evidence |
| 14 | `app/tools/knowledge_tools.py:8-47` | 工具返回的完整 chunk / claims(`LIMIT 50`) / graph(`LIMIT 100`) JSON 进入 ReAct 上下文 |
| 15 | `app/runlog.py:68-83` | 完整 `output_text` 落库；**全仓无任何 token 计数**（`llm_runs`/`llm_run_steps` 无 token 字段） |

**已经做对的部分**（不要回退）：`app/ontology.py` 的 relation/claim registry 与 normalization rules **不进 prompt**，只做程序校验；`skills/knowledge-curation/references/` 已按需加载；prompt 版本历史已存 SQLite（`agent_prompt_profiles` / `agent_prompt_versions`）。

**关键缺口**：① 无 token 计量与预算；② 抽取 Agent 内联三份 reference（与 Pydantic 协议重复）；③ 无 Context Trace；④ 工具/知识/证据/历史均无分级加载；⑤ Agent 间无 Task Packet；⑥ 无 Context Cache。

## 1. 上下文分层

```
Static Context      Bootstrap / Identity / Core rules
Dynamic Context     Task / Knowledge / Evidence / History
On-Demand Context   References / Registry subset / Examples / Deep docs
```

Context Manager 职责：计算 Budget → 决定 LOAD/SUMMARIZE/RETRIEVE_LATER/NEVER_LOAD → 实时统计 → 返回 Effective Context。

## 2. 四种加载策略（规范）

| 策略 | 含义 | 例 |
|---|---|---|
| `LOAD` | 当前任务必须使用 | 抽取任务 → entity type 契约 |
| `SUMMARIZE` | 相关但过长，先压缩 | Conversation history → summary |
| `RETRIEVE_LATER` | 当前不需要，只保留 ID | Evidence/Claim/Entity/Document ID，后续用工具取 |
| `NEVER_LOAD` | 程序即可处理，或与任务无关 | SQLite schema、索引、无关内部 ID、后端实现细节、UI 配置 |

## 3. Bootstrap 约束

- 所有 Agent 初始 System Context **≈200–400 tokens**。
- 只含：Agent identity、grounding/safety、tool usage principle、context loading principle、output contract。
- 禁止放入：完整 Ontology、完整 Predicate Registry、完整 JSON Schema、全部 Skills、全部 Examples、全部历史、全部 Knowledge。

## 4. Skill 与 Progressive Disclosure

```
skills/<name>/SKILL.md            # Purpose / When to use / Core workflow / Required references / Prohibitions
skills/<name>/references/*.md     # 详细规范，按需加载
```

- SKILL.md 只放上述五项；细节一律进 `references/`。
- **禁止一次性加载全部 reference**：按任务特征决定（entity candidates → `entity-types.md`；claim candidates → `claim-predicates.md`；需要证据 → `evidence.md`；检测到 event → `events.md`；文本内明显无 event → 不加载 `events.md`）。
- 每个 Skill / Reference 带 cost metadata（`lazy` / `estimated_tokens` / `load_when`），供 ContextManager 计算 Skill Cost。

## 5. Registry / Schema 不整体进 Prompt

- Relation predicate registry（19 个）不整体注入；改为 **候选语义 → registry matcher → 2–5 个候选 predicate → LLM 只看到这几个**。
- JSON Schema 作为**机器契约**（structured output + deterministic validator），Prompt 只说"按 schema 输出"，不重复自然语言描述。
- **Deterministic logic first, LLM reasoning second**：schema/enum/type 校验、重复检测、offset 校验、关系方向一律由程序做。

## 6. Evidence 最小充分原则与升级阶梯

默认只传 `Evidence Quote + ±1 句上下文`，不传整 chunk：

```
L1 quote only → L2 quote + 邻句 → L3 相关段落 → L4 整 chunk → L5 多 chunk
```

默认从 L1 开始，仅在证据歧义 / 冲突 / 缺条件 / 需要整段时升级（Evidence Escalation）。

## 7. History 与 Agent 间传递

- History = 最近 3–5 条消息 + `conversation_summary` + 相关历史检索；summary 只含 user goal / decisions / constraints / important facts / unresolved questions / last actions。
- Agent 之间**传状态不传上下文**，统一使用 Task Packet：

```json
{ "task_id": "", "goal": "", "context_summary": "", "known_facts": [],
  "open_questions": [], "constraints": [], "required_actions": [],
  "entity_ids": [], "claim_ids": [], "evidence_ids": [], "document_ids": [],
  "required_output": {} }
```

## 8. Budget Manager 与压缩优先级

各 Agent 软预算（可配置，非硬截断）：

```
PersonalAgent 4000 | KnowledgeAgent 3000 | ResearchAgent 6000
CuratorAgent 4000 | ReviewAgent 3500 | ExtractionAgent 3500
```

超预算压缩顺序：① 无关 History ② Examples ③ Knowledge ④ Evidence ⑤ 延迟 Reference ⑥ Skill ⑦ 最后才 Runtime。
**绝不优先裁剪**：Evidence、Current Task、Critical Constraints、Required Output Contract。

## 9. 可观测性

- **Context Trace**（每次 Run 落 SQLite）：`run_id / agent / budget_tokens / actual_tokens / efficiency / sections[{name,tokens,source}]`。
- **Context Inspector**（Agent 工作台）：分区 token 明细 + 每项 `View / Why loaded? / Token cost / Source / Remove / Expand`。
- **Prompt 页**：显示 custom prompt 当前 token、推荐上限、状态；超阈值时提示"建议把 N tokens 移入 Skill: X"。
- **Metrics**：Average/Useful/Wasted Context、Efficiency，以及 Token per successful answer / verified claim / research task / extraction chunk。

## 10. 持久化与版本一致性

- 所有 runtime state 继续使用 **SQLite**（不新增 JSON canonical storage）。新增：`context_runs`、`context_sections`、`context_cache`、`conversation_summaries`、`agent_token_metrics`、`context_policies`。
- Context Cache 必须绑定 Prompt / Skill / Registry / Schema / Knowledge 版本；任一版本变化 → 对应 Cache 自动失效。

## 11. 明确禁止（十条）

1. 完整 Standard 每次进 Prompt ② 完整 Registry ③ 完整 JSON Schema ④ 完整历史传给每个 Agent ⑤ 全部 Tool Description ⑥ 整个 Knowledge Object 给 LLM ⑦ 整 chunk 当 Evidence ⑧ 让 LLM 做程序能做的验证 ⑨ 为修上下文重复复制规则 ⑩ 为"保险"无限增加 Context。

## 12. 最终 Context Policy

任何新功能必须回答：**为什么这个信息现在必须进入 Context？** 答不出 → 不加载。

---

## 13. 分期落地

| 期 | 范围 | 交付判据 |
|---|---|---|
| **P1 ✅ 已交付** | token 计量基座、ContextItem/Manager/Planner/Compiler/Provider/Budget/Trace/TaskPacket、`context_runs`+`context_sections`+`task_packets` 落库、抽取 Agent 去重复注入、Prompt token 预算 API、真实 provider 用量采集 | 12 条成功标准中的 9/10/11（可见、可配置、可审计）成立；抽取 system prompt token 实测下降 |
| **P2** | 拆三批：**P2a ✅ 已交付**（Evidence Escalation L1–L5 + Retrieval Dedup + Knowledge Summary，QA 链路已切换，实测 prompt −80%）｜**P2b ✅ 已交付**（Tool Result Compression：`app/tools/compression.py`，工具返回一律"决策卡"——quote 卡/实体卡/去描述图，截断必带 `truncated`+`hint`，id 全保留可下钻；真实库实测 search −82% / get_entity −50% / graph −96%。Batch Tools：新增 `get_entities` 一次取多实体卡（≤8 个，claims 0–5/实体），5 实体 −38% 且省 4 次往返；阈值入 config（`tool_quote_chars` 等 4 项）；`tests/test_tool_compression.py` 9 条，全量 116 通过）｜**P2c ✅ 已交付**（Skill/Reference cost metadata：`REFERENCE_LOAD_WHEN` 声明每份 reference 的 load_when，`references_for` 按 features 偏离默认值的信号确定性选取，默认形状任务零加载——P1 抽取基线不回退，SkillProvider 输出带 estimated_tokens；Registry Subset：`ontology.match_claim_predicates` 确定性 matcher（中英提示词），未注册 predicate 的校验错误携带 2–4 个候选经 `_repair_instruction` 进入 repair 调用；History Compression：`context/history.py::compress_history` 最近 4 条原文 + 旧消息确定性摘要（user goal/last action），`HistoryProvider` 单轮任务零成本，持久化留给 P3；测试 `test_skill_lazy_loading` / `test_registry_subset` / `test_history_compression` 共 17 条，全量 133 通过） | 1/2/3/4/6/7/8 成立 |
| **P3 ✅ 已交付** | **conversation_summaries**（`context/history_store.py`：持久行即压缩态——滚动 summary + 近窗 ≤4 条原文，每轮一次读一次 upsert，全文从不落库/重放；`begin_turn`/`end_turn` 滚动协议，失败轮不污染历史；summary 硬上限 1200 tokens；`/api/agent/ask` 增 `conversation_id`，QA 页带会话句柄）｜**Task Packet 调用链**（研究流水线 `run_research_pipeline`：KnowledgeAgent 采集 → `TaskPacket` 落库 → ResearchAgent 以 `TYPE_TASK` 必选项消费 `context_block()`（id 计数不带载荷，知识步骤失败自动降级 packet-less）；`build_context`/`compose_prompt` 透传 `packet`）｜**Context Cache + 版本失效**（`context/cache.py`：键 = 提示内容 + skill/reference 版本 + `ontology.registry_version()`（schemas/*.json 指纹）+ 历史/包状态的复合哈希，任一版本变化即新键——失效按构造成立；一行一键 ⇒ rows=misses、Σhits=hits，`GET /api/context/cache` 观测命中率；命中重放 miss 时账本（`cached_trace`，标记 `cached:true`），不产生重复 trace 行；实测同输入 513ms→334ms 回放一致）｜测试 `test_conversation_summaries`(7) + `test_context_cache`(6) + `test_task_packet_chain`(4)，全量 150 通过 | 5 成立（多轮 + Agent 间均不传完整历史/上下文）；缓存命中率可观测 |
| **P4 ✅ 已交付** | Agent 工作台 `Context` Tab（Efficiency/Cache-Hit KPI 卡、Per-Agent 账本、Token-per-Run（provider 实测）、Context Runs 列表 + 7/14/30 天窗口、可切全部 Agent）+ Context Inspector 抽屉（预算/实际/Efficiency/Trimmed/关联 Run + 分区「名称·策略·为什么加载·成本·来源」+ 刻意未加载 + 优化动作）；Prompt 页 Context Token 面板（custom prompt 用量条 vs 建议上限、健康/偏长状态、迁入 Skill 建议、分区成本、刻意未加载）；可选端点（cache 统计）失败自动降级不拖垮整页 | 10/11 的 UI 面 |

## 14. 成功标准映射（验收）

1. 默认 Agent Bootstrap < 400 tokens → P2（P1 提供计量）
2. ExtractionAgent 默认 Context < 3500 → P1 计量 + P2 惰性加载
3. 不加载完整 Registry → P2（现状已满足，P2 加 matcher）
4. 不加载完整 JSON Schema 到 Prompt → P1 部分（去掉与 Pydantic 协议重复的文本枚举）
5. Agent-to-Agent 不传完整历史 → P3
6. Knowledge QA 默认只加载 Top relevant evidence → P2
7. Evidence 最小充分原则 → P2
8. Tool Description 按需加载 → P2
9. Context Budget 可配置 → P1（`budget.py`）
10. Context Token Cost 可观测 → P1（trace + API）
11. Context Trace 可审计 → P1（SQLite）
12. 所有 runtime state 使用 SQLite → P1

测试要求（最终）：`test_context_budget`、`test_skill_lazy_loading`、`test_registry_subset`、`test_history_compression`、`test_task_packet`、`test_evidence_escalation`、`test_tool_selection`、`test_context_cache`、`test_context_trace`。P1 交付前三个中的 `test_context_budget` / `test_context_trace`，其余随各期交付。

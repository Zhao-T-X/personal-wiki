# Archive — v0.1 时代的历史文档

本目录存放项目 **v0.1 阶段**的设计与计划文档。它们描述的是项目最初期的形态，**不再是现行行为的权威描述**——保留它们是为了溯源（为什么当初这么做），不是为了照着做。

现行权威描述在：

| 历史文档 | 现行权威来源 |
|---|---|
| TECHNICAL-DESIGN.md / .docx | [../architecture/](../architecture/README.md)（分层 / 领域模型 / 边界）、根 [README](../../README.md) |
| PROCESSING-PIPELINE.md | [../standards/10-EXTRACTION-V2-STANDARD.md](../standards/10-EXTRACTION-V2-STANDARD.md)、根 README「知识标准与抽取流程」 |
| IMPLEMENTATION-PLAN.md / ORIGINAL-IMPLEMENTATION-PLAN.md | [../architecture/MIGRATION-PLAN.md](../architecture/MIGRATION-PLAN.md)（CURRENT→TARGET 迁移矩阵） |
| ORIGINAL-PROCESSING-FLOW.md | 同 PROCESSING-PIPELINE |
| ORIGINAL-EXTRACTION-PROMPT.md | [../standards/13-AGENT-PROMPT-SKILL-STANDARD.md](../standards/13-AGENT-PROMPT-SKILL-STANDARD.md)（现行 Prompt / Skill 结构） |
| API-EXAMPLES.md | 根 README「API 速查」节（随代码更新的接口清单） |
| README-IMPLEMENTATION.md | 根 README「特性概览」与「快速开始」 |

## 阅读这些文档时注意

- 标题中的 **v0.1** 是它们的年代标记，与当前实现（多智能体、Context Runtime、Claim Evolution、知识完整性闭环）相去甚远。
- 其中对 API、数据库 Schema、处理流程的描述**已过期**，以 `schemas/`、`app/` 代码与现行标准为准。

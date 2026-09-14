# ADR-001 — SQLite as Primary Storage

- Status: Accepted
- Date: 2026-09-14
- Implementation status: **CURRENT**
- Related: [ADR-004](./ADR-004-DOMAIN-AND-REPOSITORY-SEPARATION.md)

## Context

LLM-Wiki 是 **local-first、单用户**的个人知识库。它需要在无服务端运维的前提下，同时满足：

- 全文检索（中文也要能用）；
- 事务一致（抽取落库必须原子）；
- 简单备份与迁移；
- 零部署成本。

候选方案：嵌入式 KV、PostgreSQL、SQLite。

## Decision

**SQLite 作为唯一主存储**（canonical storage）。

- 全部 runtime state 存 SQLite，不引入第二套 JSON canonical storage。
- 使用 **WAL** 模式与 `busy_timeout`；外键开启。
- 词法检索使用 **FTS5**；embedding 以 float32 blob 存同库。
- 结构化知识（entities / claims / relations / …）与运行记录（llm_runs / context_runs / …）同库。

## Consequences

### Positive

- 单文件数据库，备份 = 复制文件；部署无外部依赖。
- 事务保证「文档 → 分块 → 抽取 → 关系」在一个事务内一致。
- FTS5 + 同库向量读取，检索路径短。
- 与 local-first 定位一致。

### Negative / Trade-offs

- 并发写入受限（单写者），不适合多用户高并发。
- 向量检索靠 NumPy 全表余弦，仅适合个人规模（未来可换 sqlite-vec 适配器而不改 schema）。
- SQLite 方言与迁移需自行维护（见 `app/db.py` 的 additive migration）。

## Current vs Target

- **CURRENT**：已全面使用 SQLite，含 WAL / FTS5 / 触发器 / additive migration。
- **TARGET**：保持。若引入向量加速，以新适配器替换实现，不改领域 schema。

## References

- `app/db.py`（SCHEMA / connect / init_db / 迁移）
- `sql/schema.v0.1.sql`
- README「数据库 Schema」章节

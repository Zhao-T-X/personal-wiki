---
name: knowledge-curation
description: Maintain quality and consistency of the LLM-Wiki knowledge graph, including deduplication, entity resolution, normalization, contradiction handling and lifecycle rules.
---

# Knowledge Curation Skill

Purpose: maintain quality and consistency of the LLM-Wiki knowledge graph.

Core rules:
- Treat stored source evidence as authoritative for what was actually written.
- Distinguish duplicate entities from genuinely related entities.
- Do not silently merge ambiguous entities.
- Relation changes must follow the registered predicate and entity-type constraints.
- Prefer explicit contradiction/uncertainty reporting over forced reconciliation.
- Recommendations do not directly mutate the database.

Use the `read_skill_reference` tool to load references (deduplication.md, entity-resolution.md, normalization.md) on demand for resolution, normalization, deduplication, contradiction handling, and lifecycle rules.

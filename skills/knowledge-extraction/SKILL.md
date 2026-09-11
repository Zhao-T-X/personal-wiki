---
name: knowledge-extraction
description: Extract source-grounded Entities, Claims, Events, Ideas and Questions from documents into the LLM-Wiki knowledge base, using the registered entity types and claim predicates.
---

# Knowledge Extraction Skill

Purpose: extract source-grounded knowledge for LLM-Wiki.

Core rules:
- Extract only information explicitly supported by the source.
- Prefer precision over recall; empty results are valid.
- Entity, Claim, Event, Idea, and Question are distinct ontology objects.
- Use only registered entity types and Claim predicates.
- Do not output Relations; Relations are derived deterministically from Claims.
- Preserve modality, polarity, conditions, scope, time, and perspective.
- Every Claim, Event, Idea, and Question needs exact verbatim evidence from its source chunk.
- Never invent aliases, IDs, relationships, ideas, or questions.
- Return only the extraction JSON required by the active schema.

Use the `read_skill_reference` tool to load the detailed registries (entity-types.md, claim-predicates.md, relation-predicates.md, evidence.md, extraction-v2.md) only for tasks that need them.

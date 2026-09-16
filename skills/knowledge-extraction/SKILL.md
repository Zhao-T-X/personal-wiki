---
name: knowledge-extraction
description: Extract source-grounded Entities, Claims, Events, Ideas and Questions from documents into the LLM-Wiki knowledge base, using the registered entity types and claim predicates.
---

# Knowledge Extraction Skill

Purpose: extract source-grounded knowledge for LLM-Wiki.

Core rules:
- Extract only source-grounded information; prefer precision over recall, empty results are valid.
- Entity, Claim, Event, Idea and Question are distinct ontology objects.
- Use only registered entity types and Claim predicates (closed vocabulary: select, never create).
- Temporal words go in `temporal_signal`, never in `predicate`; if no predicate fits, omit the claim.
- Do not output Relations (derived from Claims). Preserve modality, polarity, conditions, scope, time.
- Every Claim, Event, Idea and Question needs exact verbatim evidence from its source chunk.
- Never invent aliases, IDs, relationships or questions. Return only the schema JSON.
- A Mention is not an Entity: extract only stable-identity objects usable long-term as a Claim
  Subject/Object; values, descriptions, paths and field names are never Entities (`entity-types.md`).

Use the `read_skill_reference` tool to load the detailed registries (entity-types.md, claim-predicates.md, relation-predicates.md, evidence.md, extraction-v2.md) only for tasks that need them.

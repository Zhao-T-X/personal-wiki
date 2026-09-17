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
- `object_kind` is your verdict on what the Claim `object` is. `entity`: it names another
  extracted Entity. `concept`: it describes a mechanism, capability, method, mode or behaviour
  ("按需加载上下文", "上下文管理机制") — a description is never `literal`. `literal`: it is a
  value (`8192`, `高风险`, `每天`) or a source artefact (path, URL). If you type an object as
  `concept` or `literal`, do not also declare that same phrase as an Entity.
- Prefer the most specific registered predicate over a generic one. A statement about a chief
  executive is `has_ceo` (with `temporal_signal` for "现任"/"曾经"), never the generic `is`.
  The predicate's declared domain/range decide which side is the Subject: for `has_ceo` the
  Subject is the Organization and the Object is the Person.

Use the `read_skill_reference` tool to load the detailed registries (entity-types.md, claim-predicates.md, relation-predicates.md, evidence.md, extraction-v2.md) only for tasks that need them.

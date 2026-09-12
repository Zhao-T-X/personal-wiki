# LLM-Wiki Knowledge Standards

This directory contains the normative standards for knowledge extraction and normalization in LLM-Wiki.

## Status

The documents in this directory describe the intended v1.0 ontology and extraction contract. They are the semantic source of truth for implementation work.

Runtime behaviour and these standards may still diverge in places. Where they do, the standard wins: the code is brought in line explicitly rather than by silent coercion.

## Standards map

- [00-ONTOLOGY-OVERVIEW.md](./00-ONTOLOGY-OVERVIEW.md) — overall knowledge model and boundaries
- [01-ENTITY-TYPE-STANDARD.md](./01-ENTITY-TYPE-STANDARD.md) — Entity definition, 14 Entity Types, typing and multi-type rules
- [02-CLAIM-STANDARD.md](./02-CLAIM-STANDARD.md) — Claim semantics, polarity, modality and provenance
- [03-CLAIM-PREDICATE-REGISTRY.md](./03-CLAIM-PREDICATE-REGISTRY.md) — controlled Claim Predicate vocabulary
- [04-RELATION-PREDICATE-REGISTRY.md](./04-RELATION-PREDICATE-REGISTRY.md) — controlled Knowledge Graph predicates
- [05-CLAIM-RELATION-NORMALIZATION.md](./05-CLAIM-RELATION-NORMALIZATION.md) — deterministic Claim → Relation conversion rules
- [06-EVENT-STANDARD.md](./06-EVENT-STANDARD.md) — Event semantics, type, time and status
- [07-IDEA-STANDARD.md](./07-IDEA-STANDARD.md) — Idea semantics and lifecycle
- [08-QUESTION-STANDARD.md](./08-QUESTION-STANDARD.md) — Question semantics and lifecycle
- [09-EVIDENCE-PROVENANCE-STANDARD.md](./09-EVIDENCE-PROVENANCE-STANDARD.md) — evidence and provenance contract
- [10-EXTRACTION-V2-STANDARD.md](./10-EXTRACTION-V2-STANDARD.md) — LLM extraction contract and separation of responsibilities
- [11-NORMALIZATION-ENGINE-STANDARD.md](./11-NORMALIZATION-ENGINE-STANDARD.md) — deterministic normalization, Entity Resolution and Claim → Relation rules
- [12-IMPLEMENTATION-MAP.md](./12-IMPLEMENTATION-MAP.md) — mapping from standards to runtime modules and schemas
- [14-CLAIM-EVOLUTION-STANDARD.md](./14-CLAIM-EVOLUTION-STANDARD.md) — claim evolution, conflict handling and derived current knowledge

## Normative hierarchy

When two layers disagree, use this order:

1. Ontology and semantic standards
2. Registry definitions and constraints
3. JSON Schema structural constraints
4. Deterministic validators and normalization rules
5. LLM prompt wording
6. Legacy compatibility/coercion

Legacy compatibility must never silently weaken the current standards.

- [13 — Agent Prompt & Skill Standard](13-AGENT-PROMPT-SKILL-STANDARD.md)

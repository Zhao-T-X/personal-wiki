# LLM-Wiki Ontology Overview v1.0

## 1. Purpose

LLM-Wiki is a source-grounded personal knowledge base. Extraction is not summarization and not free-form knowledge generation. The system stores valuable knowledge only when it can be traced to source evidence.

## 2. Core objects

- **Entity** — who or what the knowledge is about.
- **Claim** — what the source explicitly says about an object, concept, event or relationship.
- **Relation** — a normalized long-lived Entity-to-Entity graph edge derived from explicit source-grounded semantics.
- **Event** — what happened, is happening, or is explicitly planned, with temporal meaning.
- **Idea** — a source-derived proposal, hypothesis, improvement direction or research direction.
- **Question** — a source-derived question with durable knowledge value.
- **Evidence** — the source fragment that directly supports a knowledge object.
- **Provenance** — source identity, location and extraction history connecting evidence to the stored object.

## 3. Separation of responsibilities

The LLM is responsible for semantic interpretation and candidate extraction. Deterministic application code is responsible for schema validation, registry validation, entity resolution, predicate normalization, Claim-to-Relation normalization, deduplication and persistence.

## 4. Entity versus Claim versus Relation

Entity answers **what is this object?**

Claim answers **what does the source say?**

Relation answers **which stable semantic edge should exist in the knowledge graph?**

A Claim may remain a Claim even when it contains a valid semantic predicate. A Relation is not simply a shorter Claim.

## 5. Source-grounding rule

No object may be verified without source evidence. The model must not add general knowledge, common-sense relationships, invented aliases, invented events, generated ideas or generated questions.

## 6. Lifecycle

Extracted knowledge initially enters a candidate state. Verification is a separate operation. Uncertain or contradictory source statements remain representable without being promoted to unconditional graph facts.

# LLM-Wiki Standards Implementation Map v1.0

This document maps the normative standards to runtime code.

| Standard | Runtime implementation |
|---|---|
| Entity Type Standard | `app/ontology.py`, `app/resolution.py` |
| Claim Standard | `app/extraction.py`, `app/knowledge.py` |
| Claim Predicate Registry | `schemas/claim-predicate-registry.json`, `app/ontology.py` |
| Relation Predicate Registry | `schemas/relation-predicate-registry.json`, `app/ontology.py` |
| Claim → Relation Normalization | `schemas/relation-normalization-rules.json`, `app/normalization.py` |
| Event Standard | `schemas/extraction.schema.json`, `app/knowledge.py`, `app/db.py` |
| Idea / Question Standards | `schemas/extraction.schema.json`, `app/knowledge.py`, `app/db.py` |
| Evidence & Provenance | `app/knowledge.py`, `app/db.py` |
| Extraction v2.0 | `schemas/extraction.schema.json`, `app/llm.py`, `app/extraction.py` |
| Normalization Engine | `app/normalization.py`, `app/resolution.py`, `app/knowledge.py` |
| Claim Evolution & Conflict Handling | `app/claim_relations.py`, `app/service.py`, `app/retrieval.py`, `app/db.py` |

## Runtime boundary

The LLM is responsible for semantic extraction. The application is responsible for registry enforcement, Entity Resolution, Claim normalization, Claim → Relation derivation, evidence validation, deduplication, lifecycle assignment, and persistence.

## Relation generation

LLM-Wiki v2.0 does **not** trust LLM-emitted Graph Relations. Relations are generated deterministically from normalized Claims and validated against the Relation Predicate Registry and Entity Type constraints.

## Compatibility

Legacy extraction input may be adapted only where the transformation is semantics-preserving (for example, `type` → `types`). Unknown Claim Predicates, missing mandatory evidence, unsupported Entity Types, and unsupported Relation semantics are rejected rather than mapped to generic values such as `related_to`.

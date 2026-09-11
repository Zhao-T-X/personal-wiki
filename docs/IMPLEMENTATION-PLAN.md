# LLM-Wiki v0.1 Implementation Plan

## M1 — Foundation

Done: SQLite schema, WAL mode, document CRUD, raw preservation, deterministic chunking, FTS5.

Acceptance: a document can be stored, re-indexed and searched without an LLM key.

## M2 — Ontology

Done: entities, aliases, claims, relations, ideas, questions, events, provenance and status lifecycle.

Acceptance: candidate knowledge can be created and reviewed.

## M3 — LLM extraction

Done: JSON Schema, extraction prompt, validation, normalization, entity resolution, extraction audit.

Acceptance: source chunk is mandatory and the original document remains untouched.

## M4 — Retrieval

Done: FTS5 + optional embedding storage + NumPy semantic search + RRF hybrid search.

Acceptance: retrieval works without vector infrastructure and becomes semantic when embeddings exist.

## M5 — Reasoning

Done: evidence pack + grounded Q&A + citations.

Acceptance: answer endpoint returns answer, evidence and citation metadata.

## M6 — Product UI

Done: notes, import, search, graph explorer, candidate review, ask UI.

## Future

- sqlite-vec adapter for faster local vector retrieval
- semantic entity resolution and explicit merge workflow
- richer timeline / belief evolution views
- background job queue for large imports
- OCR / PDF parsing
- authentication and multi-user deployment

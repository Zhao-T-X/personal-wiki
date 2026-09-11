# Implementation Notes

This repository is the implementation counterpart of the v0.1 design specification.

## Implemented now

- raw document storage + SHA-256 content hash
- deterministic chunking + exact offsets
- SQLite WAL + foreign keys
- FTS5 lexical search
- JSON Schema validation of LLM output
- normalized predicates
- exact/alias entity resolution
- claim/relation/idea/question persistence
- provenance constraints
- extraction run audit records
- candidate/verified/rejected state endpoint
- evidence-pack Q&A with citations

## Intentionally deferred

- embeddings/vector index
- semantic entity resolution
- automated contradiction clustering
- graph visualization beyond the neighborhood API
- multi-user/authentication

These are isolated enough to be added without changing the core raw-document model.

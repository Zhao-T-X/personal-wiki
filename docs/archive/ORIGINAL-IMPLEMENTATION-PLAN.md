# LLM-Wiki v0.1 Implementation Plan

## Milestone 1 - Foundation

Deliver:

- Python application skeleton
- SQLite initialization
- migrations/schema
- health endpoint
- document CRUD

Done when:

- app starts with an empty directory;
- a document survives restart;
- no LLM key is required.

## Milestone 2 - Chunking and search

Deliver:

- deterministic chunker
- FTS5 search
- document search API
- source-aware result model

Done when:

- searching an exact phrase returns the source document;
- re-indexing is deterministic.

## Milestone 3 - Ontology extraction

Deliver:

- extraction JSON schema
- prompt
- validation
- entity resolution
- claims/relations persistence

Done when:

- a test note creates candidate entities and claims;
- every claim has valid provenance.

## Milestone 4 - Graph

Deliver:

- neighborhood API
- relation explorer
- candidate/verified/rejected states

Done when:

- an entity can show its adjacent graph;
- the UI can navigate from a claim to its source.

## Milestone 5 - Semantic retrieval

Deliver:

- embedding provider abstraction
- chunk embeddings
- vector index adapter
- hybrid retrieval

Done when:

- a paraphrased query can retrieve a relevant note that keyword search alone misses.

## Milestone 6 - Personal reasoning

Deliver:

- ask endpoint
- evidence pack
- citations
- uncertainty handling

Done when:

- the system answers a cross-document question with traceable evidence.

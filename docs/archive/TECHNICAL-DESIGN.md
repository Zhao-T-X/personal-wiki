# LLM-Wiki v0.1 Technical Design

## 1. Goal

Build a local-first personal knowledge base in which original documents remain the source of truth, while LLMs derive ontology-aware knowledge that can be searched, reviewed, connected and used for grounded reasoning.

## 2. Layered model

```text
RAW
  Markdown / TXT / HTML
      ↓
SOURCE
  Document
      ↓
INDEX
  Chunk + offsets + FTS5 + optional embedding
      ↓
KNOWLEDGE
  Entity / Claim / Relation / Idea / Question / Event
      ↓
RETRIEVAL
  lexical + semantic → RRF
      ↓
REASONING
  evidence pack → LLM answer + citations
```

The original document is never replaced by an LLM representation.

## 3. Ontology

Entity types: `person`, `organization`, `place`, `concept`, `project`.

Knowledge types: `document`, `chunk`, `claim`, `relation`, `idea`, `question`, `event`.

Core relations: `is_a`, `part_of`, `contains`, `instance_of`, `related_to`, `similar_to`, `depends_on`, `enables`, `used_for`, `supports`, `contradicts`, `refines`, `extends`, `explains`, `derived_from`, `supported_by`, `mentioned_in`, `cites`, `believes`, `interested_in`, `working_on`, `decided`, `learned`.

Claims are first-class objects because personal reasoning depends on the distinction between a concept and a statement about that concept.

## 4. Provenance

Every extracted knowledge object points to its source document, source chunk and character offsets. The LLM can also return an `evidence_quote`; the server attempts to localize it inside the chunk. When localization fails, the containing chunk remains the provenance boundary.

## 5. Trust model

LLM output is treated as candidate data. The lifecycle is:

```text
candidate → verified
candidate → rejected
```

The UI exposes candidate review for entities, claims and relations. The API also supports status changes for ideas and questions.

## 6. Entity resolution

Resolution is conservative:

1. exact canonical-name match;
2. exact normalized alias match;
3. same-type fuzzy match above a high threshold;
4. otherwise create a candidate entity.

This intentionally favors duplicate candidates over unsafe merges.

## 7. Retrieval

Lexical search uses SQLite FTS5. When an embedding API is configured, chunk embeddings are persisted as float32 blobs and semantic search uses cosine similarity in NumPy. Hybrid retrieval uses reciprocal-rank fusion, allowing vector infrastructure to be replaced later without changing the ontology.

## 8. API boundaries

The FastAPI layer provides document CRUD/import, indexing, embeddings, search, entity/graph exploration, candidate review, statistics, export and evidence-grounded Q&A.

## 9. Storage

SQLite runs in WAL mode with foreign keys enabled. It is appropriate for the intended single-user/local-first workload. For a future multi-user deployment, the service layer can be ported to PostgreSQL while preserving the ontology concepts and API contract.

## 10. Operational principles

- Raw data is durable.
- Derived data is rebuildable.
- LLM output is untrusted until validated/reviewed.
- Every claim must be traceable to evidence.
- Retrieval and graph views are projections over the same knowledge store.

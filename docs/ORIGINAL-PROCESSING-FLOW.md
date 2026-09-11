# LLM-Wiki v0.1 Processing Flow

## Runtime flow

1. **Create document**
   - accept raw text and source metadata;
   - assign `document_id`;
   - persist the raw content before any LLM call.

2. **Chunk document**
   - deterministic character-window chunking;
   - store `chunk_id`, index, offsets, and content;
   - recompute chunks when the document version changes.

3. **Build search indexes**
   - FTS5 is updated automatically from `documents`;
   - embeddings are optional and can be attached to chunks.

4. **Extract candidates**
   - LLM receives the document or chunk set;
   - output must validate against `extraction.schema.json`;
   - extraction uses only evidence present in the source.

5. **Normalize**
   - lowercase/trim names for matching;
   - normalize aliases;
   - normalize predicates to snake_case.

6. **Resolve entities**
   - exact name -> alias -> optional semantic match -> create candidate entity;
   - preserve aliases instead of duplicating nodes.

7. **Resolve source chunk**
   - attach every claim/relation to the chunk cited by the model;
   - if the chunk cannot be resolved, reject the candidate rather than invent provenance.

8. **Persist candidates**
   - all LLM-derived objects start as `candidate`;
   - store confidence and creator as `llm`;
   - store document/chunk provenance.

9. **Promote or reject**
   - user or deterministic rules can set `verified` / `rejected`;
   - promotion should be auditable.

10. **Retrieve**
   - FTS5 for lexical matches;
   - embeddings for semantic similarity when available;
   - ontology traversal for known entities and claims;
   - merge and rerank.

11. **Answer**
   - construct a context pack containing source snippets;
   - ask the LLM to answer only from that context;
   - return answer plus document/chunk citations.

## Idempotency rules

- Re-indexing a document should not create duplicate entities when the names resolve to existing nodes.
- A document version should be identifiable by a content hash in a future version.
- Derived objects can be deleted and rebuilt from raw documents without losing the originals.

## Suggested service boundaries

```text
IngestionService
  -> DocumentStore
  -> Chunker
  -> IndexService
  -> ExtractionService
  -> EntityResolver
  -> KnowledgeStore
  -> RetrievalService
  -> AnswerService
```

## Logging

For every extraction run record at least:

- document id
- extraction timestamp
- model name
- success/failure
- number of entities/claims/relations extracted
- validation errors
- processing duration

A dedicated `extraction_runs` table is recommended for v0.2; v0.1 can use application logs.

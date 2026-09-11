# LLM-Wiki v0.1 Processing Pipeline

## Ingest

1. Accept Markdown/TXT/HTML or direct text.
2. Compute SHA-256 content hash.
3. Save the raw document.
4. Reject duplicate content.

## Index

1. Deterministically chunk the document.
2. Store exact `start_offset` and `end_offset`.
3. Refresh chunk embeddings when requested.
4. FTS5 indexes the original document title/body.

## Extract

1. Send batches of chunk IDs + text to the LLM.
2. Require JSON with `entities`, `claims`, `relations`, `ideas`, `questions`.
3. Require a source chunk for every derived object.
4. Prefer a verbatim `evidence_quote`.
5. Validate JSON Schema.
6. Normalize names and predicates.

## Resolve + persist

1. Resolve entities against canonical names/aliases.
2. Resolve or create candidate entities.
3. Validate every source chunk belongs to the document being processed.
4. Localize evidence quotes when possible.
5. Persist knowledge with `candidate` status.
6. Record an `llm_runs` audit row (plus one `llm_run_steps` row per model call, including the raw model output).

## Retrieve

```text
question
  ↓
FTS5 ───────┐
             ├─ RRF → top chunks
embedding ──┘       ↓
                 related claims
                     ↓
               evidence pack
```

## Reason

The LLM receives only the selected evidence pack and must cite `[doc:ID chunk:ID]`. It is instructed to state uncertainty or conflicts rather than inventing facts.

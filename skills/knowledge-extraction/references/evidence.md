# Evidence & Provenance Standard v1.0

## 1. Definitions

**Evidence** is the original source fragment that directly supports a knowledge object.

**Provenance** is the record of where the source came from, where within it the evidence occurs, and which extraction run produced the object.

## 2. Required traceability

The intended trace is:

```text
Document → Chunk → Evidence → Knowledge Object
```

## 3. Evidence rules

- Evidence must come from the source.
- Evidence must be verbatim; do not paraphrase it.
- Evidence should be the minimum sufficient fragment needed to support the object.
- Preserve qualifiers such as negation, modality, time and conditions.
- Evidence must be locatable in the source chunk.

## 4. Offsets

When available, use half-open offsets:

```text
[start, end)
```

Offsets refer to the original chunk text.

## 5. Source identity

Document identity should be based on source metadata and content hash. `source_uri` is not the same thing as the document identity.

## 6. Confidence

Confidence represents confidence in extraction and structuring correctness. It is not a probability that the source statement is true.

## 7. Verification

No knowledge object lacking valid evidence should become `verified`.

## 8. Auditability

Extraction runs should record at least document, model, prompt/schema versions, status, timestamps and summary counts. Failed runs must preserve an error reason.

## 9. Source deletion

Deleting a source should not silently fabricate or detach knowledge. Prefer archiving/unavailable states when historical provenance matters.

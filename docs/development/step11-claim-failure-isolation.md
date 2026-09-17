# Step 11 — Claim Failure Isolation / Partial Commit

**Goal:** one uncompilable claim must cost that claim, not the document.

## Root cause

`normalize_extraction` expressed every failure as `raise ValueError`, and its caller
(`app/llm.py::extract`) compiled a whole batch inside one `try`. The second failure —
after a single repair attempt — propagated out of `extract()` into
`service.index_document`, which held no partial state, so the document's transaction
rolled back and **every** batch was lost.

The Step 10 audit measured the cost on a real document: `KNOWLEDGE-OPERATIONS.md`
aborted on `CORRECT creates Claim` and took 19 entities and 18 claims with it. That
was the largest single source of lost knowledge — and it was a *process* failure, not
a filtering decision.

## Architecture change

    LLM draft
      -> compile EVERY item        (app.extraction_items.compile_extraction_items)
      -> ACCEPTED / REJECTED / NEEDS_REVIEW
      -> accepted items form the envelope
      -> persistence sees only accepted items
      -> document status

- `app/extraction_items.py` (new) — `ExtractionItemResult` (item_index, item_type,
  status, input, compiled_output, error_code, error_message, source_location,
  attempts, prior_errors) and `CompilationOutcome` (accepted / rejected / needs_review,
  `envelope`, `status`, `to_dict()`).
- `app/extraction.py` — the per-item normalisers (`normalize_entity`,
  `normalize_claim`) are now the single implementation; `normalize_extraction` is a
  strict wrapper over them, so its signature, its errors and its messages are
  unchanged for direct callers and tests. Rejections carry a code
  (`ENTITY_INVALID`, `SCHEMA_INVALID`, `EVIDENCE_INVALID`, `UNSUPPORTED_PREDICATE`,
  `DOMAIN_RANGE_INVALID`, `CLAIM_INVALID`) and `is_item_recoverable()` separates them
  from infrastructure faults.
- `app/llm.py::extract` — item-level repair: only the refused items are sent back for
  the one permitted retry, and only their results are re-compiled. Failures are
  carried on the run (`run.extraction_issues`), so the call is still `extract(chunks)`.
- `app/knowledge.py` — every write (entity / claim / relation / event / idea /
  question) runs under its own **savepoint** inside the document's single outer
  transaction. `is_item_write_error()` decides isolate-vs-abort: a `ValueError`
  (bad provenance) or `IntegrityError` (this row's constraint) is isolated; an
  `OperationalError` (locked / unreadable store) is re-raised so the document rolls
  back instead of half-writing.
- `app/db.py` — owns `savepoint()`; SQL stays inside the persistence layer
  (`tests/test_architecture.py` still passes).
- `app/service.py` — `index_document` compiles per item, persists only the accepted
  envelope, and reports `status`, `warnings`, `rejected_items`. `run.summary` gains
  `document_status` and `rejected_items` (no new table).

## Eligibility

`supported` entity names are read from the **accepted** envelope only. A rejected
claim can therefore never promote an Entity to KEEP; an entity mentioned only by a
refused claim stays a `candidate` (REVIEW). Locked by
`test_rejected_claim_cannot_promote_an_entity`.

## Document status

| status | meaning |
|---|---|
| `COMPLETED` | everything necessary was accepted |
| `COMPLETED_WITH_WARNINGS` | legal knowledge was kept, some items were refused |
| `FAILED` | nothing legal was kept, or the store itself failed |

An item-level refusal is never `FAILED`; a broken store always is.

## Real failure, Before / After

Recorded payload `tests/golden_corpus/failures/creates_domain_violation.json`
(`CORRECT creates Claim`; `creates` is registered as a relation predicate whose
`source_types` are Person/Organization, so a `Resource`-typed CORRECT is an illegal
endpoint):

| | Before | After |
|---|---|---|
| contract | `normalize_extraction` raises | items compile individually |
| legal claims kept | 0 | 4 |
| document | rolled back (FAILED) | COMPLETED_WITH_WARNINGS |
| rejected items | — | 1 (`DOMAIN_RANGE_INVALID`) |

Live run of the same real document through the real pipeline
(`scripts/semantic_boundary_experiment.py --only KNOWLEDGE-OPERATIONS`):
`status=COMPLETED`, 12 entities, 28 claims, 0 warnings. The model did not repeat the
`creates` claim in that run, so the isolation itself is proven by the recorded payload
above, not by that run.

## Cost

The experiment harness (replay + partial-commit tests) is 0-token; `pytest` stays at 0
by default (`LLM_TEST_MODE=disabled`, live tests deselected). One live run of one real
document was used, structured as 1 detection + 1 extraction call (0 warnings ⇒ no
repair call).

## Known limitations

- The `DOMAIN_RANGE_INVALID` message prints the *claim* spec's empty domain/range for a
  predicate that is also a relation (`creates`), which is misleading. Not fixed here
  (message wording, not validity).
- The item-to-retry correspondence is positional per section (the repair payload
  contains exactly the refused items). A repair that invents unrelated items is judged
  on its own merits, which is correct but not especially informative.
- Whether a live model still produces a given violation is non-deterministic; the
  fixtures are what make the behaviour reproducible.

## Status

**VALIDATED** for the stated scope: item-level partial commit works, eligibility did not
regress, a fatal store failure still rolls back, the recorded real failure replays into
4 kept claims + 1 isolated refusal, and the full suite passes (579 passed, 0 failed, 0
tokens).

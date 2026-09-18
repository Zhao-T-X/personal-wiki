# Step 15 — Single-Pass Extraction Context Prefetch

One extraction step used to cost two provider calls. It now costs one, with the same
semantics. This note records what actually changed, and the measurement that gates it.

## The two-call chain, as it really was

Verified from the capture, not from reading the code
(`tests/fixtures/extraction_cases/provider_request_snapshot_step14.json`):

```
call 0  system (604) + chunk (26) + 2 reminders (134) + tools (1529)   -> 2618
        model answers with read_skill_reference ×2
call 1  the same system and tools again
        + claim-predicates.md   2131 chars / 549 tokens   (whole document)
        + entity-types.md       5586 chars / 1408 tokens  (whole document)
                                                                       -> 4479
                                                             step total 7097
```

The second call was not "the model thinking harder". It was the same prompt sent twice,
carrying two documents that the project already had on disk. The registries are worth
showing the model — the `is` → `has_ceo` fix is what proved that — but re-delivering them
at full price on every step is not.

Note also that `claim-predicates.md` was *also* in `EXTRACTION_REFERENCES`, which the
ledger counted at 549 tokens while `render()` dropped it. It was never in the prompt; the
only copy that reached the model came through the tool round. That long-standing
`REFERENCE_NOT_RENDERED` flag and the second call were the same fact seen from two sides.

## What replaced it

```
plan_extraction_context(chunk)                     deterministic, offline, no model call
  |-- match_claim_predicates()  -> 2-5 candidates, each with its registry spec
  |-- entity_types.md index     -> 14 one-liners + the boundaries for candidate types
  +-- object_kind               -> not injected: SKILL.md already carries it
        |
        v
  one ContextItem (TYPE_EXTRACTION_CONTEXT, rendered after the skill contract)
        |
        v
  agent with NO function tool registered
        |
        v
  call 0  ->  GenerateStructuredOutput                                   one call
```

`app/context/extraction_context.py` is a **context candidate selector**. It reads the
chunk to *offer* candidates; it never decides meaning. `keyword → candidate` is allowed;
`keyword → verdict` is not. The final predicate is still chosen by the model and resolved
by `KnowledgeCompiler` against the Registry, which remains the authority.

Two consequences worth stating plainly:

- **"One call" is structural, not behavioural.** In prefetch mode
  `EXTRACTION_TOOLS` is empty, so `read_skill_reference` is not in the request. The model
  cannot open a second round even if it wants to — there is no tool to call. That also
  means §19's "record `PREFETCH_FALLBACK`" has nothing to count: the fallback path does
  not exist. A test asserts the toolkit really is empty, so this stays true.
- **The skill contract had to be qualified.** `SKILL.md` tells the agent to open the
  registries with `read_skill_reference`, which prefetch does not register. Rather than
  edit a document both modes share, the prefetched block opens with
  `[EXTRACTION CONTEXT — ALREADY LOADED]` and says no reference tool is available. A
  later, explicit correction beats a silent contradiction; a test pins it.

## Switching

`EXTRACTION_CONTEXT_MODE` (`app/config.py`, default `prefetch`):

| value | behaviour | provider calls per extraction step |
|---|---|---|
| `prefetch` | planner selects context up front | **1** |
| `agentic` | agent fetches references itself | 2 |

Both arms are kept, wired and tested. `EXTRACTION_CONTEXT_MODE=agentic` is the rollback.

## A/B — five cases, both arms, provider seam

`scripts/step15_ab.py` → `tests/fixtures/extraction_cases/step15_ab.json`.
Same five cases §12 names, same model, same production path (`extract_structured`), calls
and tokens counted at `AsyncCompletions.create`.

| Metric | Agentic | Prefetch | Δ |
|---|---:|---:|---:|
| Provider calls | 10 (2.0/case) | **5 (1.0/case)** | −5 |
| Input tokens | 38,315 (7,663/case) | **15,139 (3,028/case)** | **−23,176** |
| Output tokens | 4,330 | 4,061 | −269 |
| Prompt reduction | — | — | **−60.5%** |
| Semantic | 5/5 | **5/5** | — |
| Fallbacks | 0 | 0 (no path) | — |

Per case, semantic result identical in both arms:

| case | predicate | temporal | object_kind |
|---|---|---|---|
| `entity_context_runtime` | `used_for` | — | concept |
| `concept_progressive_loading` | `defined_as` / `is` | — | **concept** |
| `predicate_registry_ceo` | **`has_ceo`** | current | entity |
| `temporal_former_ceo` | **`has_ceo`** | **former** | entity |
| `evidence_attachment` | `supports` | — | concept |

`concept_progressive_loading` differs in predicate between arms (`defined_as` vs `is`).
Both are registered and both are correct for `是一种`; the case's gold pins `object_kind`,
not the predicate, so pinning it here would be fitting the expectation to one run.

Cost against §15's targets: before ≈7.2k, target ≤5.0k, soft ≤4.5k, floor −30%.
Measured **3,028/case, −60.5%** — all three met.

This is not "one call with a bigger prompt". The single call is indeed larger than the old
*first* call (3,028 vs 2,642), because it now carries the prefetched slice; what it
replaces is the whole step — the second call alone was 4,500–6,700 tokens. Step total
7,663 → 3,028, so the discarded 4,635 is prompt re-sent plus two documents delivered at
full length rather than as the slice the decision needs.

## Context composition (prefetch, project side)

| Component | Tokens |
|---|---:|
| Base system (bootstrap + constraints) | 72 |
| Skill contract (SKILL.md) | 497 |
| Extraction context header | 54 |
| Predicate context | 238 |
| Entity type context | 402 |
| Object kind context | 0 — not re-injected; SKILL.md is the single source |
| Tool catalogue line | 0 (no function tool to advertise) |
| Agent-skills catalogue | 0 (removed in Step 14) |
| Structured-output schema (= the Tools row) | 1,555 |
| Tool schemas registered on the toolkit | 0 |
| User chunk | 20 |
| **Total** | **2,838** |

Each reference appears **once**: the prefetched block is the only copy, and a test asserts
the predicate block and entity-type block occur exactly once in the request.

The Tools row deserves a caveat: `GenerateStructuredOutput` is added by AgentScope during
`reply()`, so `Toolkit.get_tool_schemas()` cannot see it. The 1,555 above is the Pydantic
JSON schema; the provider receives it as a 1,383-token tool schema. That is the one row
the project side cannot measure directly, and it is labelled as such in
`context_cost_audit.json`.

## Regression

- `tests/test_extraction_single_pass.py` (16): one call in prefetch, the reference round
  still present in agentic, no hidden fallback, relevant context in / whole documents out,
  no duplicated reference, structured output intact, same stored knowledge in both modes,
  Step 11 partial commit unchanged, planner field-set lock, no model call in the planner.
- Full suite: **638 passed, 6 deselected, 0 failed**; `pytest` still 0 tokens.
- Only deliberate expectation change: `test_context_planner` now expects the new
  `extraction_context` plan section — and asserts it is a *loaded* (budgeted) section, the
  opposite of the lazy reference it replaces.
- `tests/test_context_planning.py`'s lazy-reference contract is untouched.

## Cost of this round's live runs

| run | provider calls | prompt tokens | completion tokens |
|---|---:|---:|---:|
| A/B (5 cases × 2 arms) | 15 | 53,454 | 8,391 |
| Step 15.1 full suite (11 cases, prefetch) | 22 | 43,061 | 10,103 |

## Step 15.1 — cutover hardening

Two things the 5-case canary left open, closed.

**The skill contract no longer names a tool.** `SKILL.md` told the agent to open the
registries with `read_skill_reference`, which prefetch does not register — a real contract
conflict, not a wording problem. It now states the requirement mode-independently: use the
Reference Context you were given, never author a registry definition yourself, and *how*
that context arrives is a runtime concern. Locked by a test that asserts **no registered
tool name appears in the contract** (a rephrasing-safe property) and by one that asserts
both modes deliver the identical `[SKILL]` block. The prefetched block's
`ALREADY LOADED` header stays: it is a true statement about the request, no longer a
correction of a contradiction.

**The full 11-case suite ran under prefetch** (one live run, `live_last_run.json`):

| | |
|---|---:|
| cases | 11 (10 semantic + 1 abstention) |
| structural / registry / semantic | **11/11 / 11/11 / 10/10**, abstention 1/1 |
| semantic checks passed | **29/29** |
| extraction provider calls | **11 — exactly one per case**, zero second rounds |
| detection provider calls | 11 |
| extraction step tokens (provider) | min 3,075 · mean **3,099** · p95 3,150 · max 3,149 |
| estimated cost | $0.0367 |

The baseline was **not** rewritten: `live_baseline.json` still holds the approved Step 13.2
record, `live_failures.json` now records zero failures, and approval needs an explicit
`LIVE_SMOKE_APPROVE_BASELINE=1` run by whoever signs the cutover off.

One watch item, recorded rather than fixed (the planner is off-limits this round):
**`PREFETCH_CANDIDATE_NOISE`.** The planner scores the whole payload, which includes the
runtime's own Pass-2 scope hint ("Extract ONLY those kinds…"), so the hint word `extract`
nominate `extracts` in every case — including chunks with no extraction vocabulary at all.
Harmless here (10/10 semantic, and `has_ceo` still outranks it for the CEO cases), but it
dilutes the candidate signal and is worth a later round: score the chunk, not the
instructions around it.

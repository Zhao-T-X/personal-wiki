# Detection Pass Value Audit (Step 16)

Pass 1 detection costs one provider call and ~979 tokens per batch. This audit asks the
only question that matters about it: **what would extraction lose without it?** — and
answers it from the data flow, a replay A/B, and a 3-case live canary. No code was removed;
`EXTRACTION_DETECTION_MODE` stays `on`.

## 1. Data flow — verified, not inferred

`detect_structured` returns `{entities, claims, events, ideas, questions: bool}`. Its
result is read in exactly one file, `app/llm.py::extract`, in three places:

| consumer | line | effect |
|---|---|---|
| `if wanted is not None and not wanted: results.append(_empty()); continue` | the skip | **the only cost-saving path**: Pass 2 is skipped only when *all five* kinds are false — i.e. a whole batch (8 chunks) judged worthless |
| `scoped = f'{payload}\n\n{scope_hint(wanted)}'` | the prompt | appends `"[PASS 2 SCOPE] … Extract ONLY those kinds…"` — this **changes the prompt**, and therefore the text the prefetch planner plans from |
| `data = _restrict(data, wanted)` | post-hoc | **erases** every kind detection did not name, after extraction — including `entities` |
| `extract_summary` | bookkeeping | a log string |

Nothing else reads it. Specifically: the Context Planner does **not** consume the detection
result (`plan_extraction_context(chunk)` takes only the chunk text), and neither do
persistence, eligibility, retry, or chunk selection.

### Value model

| claimed capability | verdict |
|---|---|
| A. Scope selection | **partial** — `_restrict` + skip; the skip requires an all-empty batch |
| B. Candidate discovery | **NO_DOWNSTREAM_EFFECT** — the planner builds its own candidates from the Registry |
| C. Relevance filtering | **partial, and asymmetric** — it can only remove kinds, never add |
| D. Context planning | **NO_DOWNSTREAM_EFFECT** (an *accidental* one: the scope hint leaks into the planner input — see §6) |
| E. Validation | **NO_DOWNSTREAM_EFFECT** |
| F. Deduplication | **NO_DOWNSTREAM_EFFECT** |
| G. Chunk selection | **NO_DOWNSTREAM_EFFECT** — batching happens before detection |

## 2. Replay A/B — 11 cases, 0 tokens

`tests/test_detection_ab_replay.py`. The same *real* model output (captured in the Step
15.1 run) is replayed through the real `llm.extract` control flow in three arms, so any
difference is attributable to the detection plumbing, not to model variance:

- **OFF** — no detection call.
- **ON-perfect** — detection names exactly the kinds the output really contains. This is
  the *best detector that could exist* for these cases; if even it changes nothing, a real
  one cannot do better.
- **ON-lossy** — detection misses a kind the output really contains.

Results:

1. **ON-perfect ≡ OFF, all 11 cases** — identical canonical claims, entities, predicates,
   object_kind, evidence, eligibility, document status, rejected items.
2. **The skip fires exactly where extraction was already empty** — 1 of 11
   (`path_source_artifact`, whose real extraction returned nothing). Note it is *not* the
   abstention case: the model extracted something there, so a perfect detector would not
   have skipped it. Detection can only predict an outcome extraction already reaches.
3. **An imperfect detection erases knowledge in 8 of 11 cases** — omit `entities` from the
   detection result and `_restrict` deletes every extracted entity (苹果, John Ternus,
   Context Runtime, …). The filter is asymmetric: it can destroy knowledge, never create it.
4. **The scope hint reaches the planner** — `plan_extraction_context` is called on the
   payload *with* the hint appended, and the hint contains the word "Extract", so
   `extracts` enters the candidate list in every ON case. This is the cause of the
   `PREFETCH_CANDIDATE_NOISE` watch item recorded in Step 15.1.

## 3. Live canary — 3 cases, ON vs OFF (`step16_detection_ab.json`)

Replay holds the extraction output constant, so it cannot show whether the model extracts
differently without the scope hint. §9's canary measures that (9 provider calls):

| case | arm | detect | extract | in | out | semantic | output |
|---|---|---:|---:|---:|---:|---|---|
| predicate_registry_ceo | on | 1 | 1 | 3149 | 530 | PASS | 苹果 --has_ceo--> John Ternus, current |
| predicate_registry_ceo | **off** | **0** | 1 | 3098 | 497 | PASS | **identical** |
| concept_progressive_loading | on | 1 | 1 | 3119 | 1319 | PASS | --defined_as-->, concept |
| concept_progressive_loading | **off** | **0** | 1 | 3063 | 507 | PASS | --is-->, concept |
| path_source_artifact | on | 1 | 1 | 3077 | 263 | PASS | empty |
| path_source_artifact | **off** | **0** | 1 | 3051 | 236 | PASS | empty |

| Metric | Detection ON | Detection OFF |
|---|---:|---:|
| Detection calls | 3 | **0** |
| Extraction calls | 3 | 3 |
| Input tokens | 12,484 | **9,212** |
| Output tokens | 2,112 | **1,240** |
| Claims | 1 / 1 / 0 | 1 / 1 / 0 — identical |
| Entities | identical | identical |
| Semantic accuracy | 3/3 | **3/3** |
| Evidence | located, all | located, all |
| Eligibility | unchanged | unchanged |
| Document status | COMPLETED | COMPLETED |

Two things worth underlining:

- **The one case detection could have skipped was not skipped.** `path_source_artifact` is
  the only case whose extraction is empty, and the real detector still ran extraction on
  it — so in practice the saving mechanism did not fire even here.
- The scope hint is worth ~44 tokens of prompt per case (3,149 vs 3,098) and bought
  nothing: same semantic verdicts, and in this sample *more* output tokens with it.

## 4. Economics

Measured per batch (11-case run): detection 815 in + 164 out = **979**; extraction 3,099 in
+ 754 out = **3,854**. Detection is charged on *every* batch; it saves an extraction only
when it skips one.

**Break-even: ≥ 25.4% of batches must be entirely knowledge-free** (979 / 3,854).

Real corpus (`semantic_after.json`, chunked with the production `chunk_text`): 5 documents
→ 16 chunks → **4 batches**, because each document is 3–6 chunks and `llm_batch_chunks = 8`.
One batch per document. For detection to break even, **one of the five real documents would
have to contain no knowledge at all** — and all five are the knowledge-densest documents in
the repo. Measured over the 11 micro cases and the 4 real batches: **0 batches skipped**.

So for this corpus detection is a flat **+25%** on the extraction stage. The break-even is
corpus-dependent — a corpus dominated by boilerplate could cross 25% — but the mechanism
that would exploit that (predict per batch, before reading it, that extraction would return
empty) is dominated by extraction's own abstention contract: the model already returns an
empty envelope for content with nothing to assert, at a price already being paid.

## 5. Decision

**REMOVE** (§18 conclusion A), with one recorded caveat.

- No downstream effect on quality: proven by replay on 11 cases and by the live canary.
- The saving mechanism did not fire once in 15 observed batches (11 micro + 4 real), and
  needs a 25% all-empty batch rate to break even.
- The filter carries an asymmetric risk: a wrong detection silently deletes extracted
  entities (`_restrict`), which is a recall bug waiting for a detector hiccup.
- Caveat: the corpus is small and knowledge-dense. If a future corpus is boilerplate-heavy,
  the answer is a *deterministic* gate on chunk shape, not a second model call — and that
  would be a new decision with its own evidence, not a resurrection of this pass.

Per §19 the removal is **not** done here: `EXTRACTION_DETECTION_MODE` stays `on`, and
Step 17 (Detection Removal) owns the deletion. `off` is wired and tested as the rollback
that will become the default.

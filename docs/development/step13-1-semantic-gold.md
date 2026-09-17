# Step 13.1 — Semantic Gold, Three-Layer Metrics, and Prompt-Cost Measurement

Step 13 reported `Predicate Resolution Accuracy = 100%`. It was not. The model answered
a CEO question with `is`; `is` is registered, so a *registry* check passed while the
*meaning* was wrong. This step separates the layers, re-scores the recorded live runs
against a real semantic gold, measures where the prompt tokens go, and fixes the
contract defects the measurement exposed.

**Status: IMPLEMENTED** — semantic correctness 9/10, one unresolved recall wobble.

## 1. Three layers, three metrics

`tests/live_contract_eval.py`:

| layer | question | fails in |
|---|---|---|
| structural | is the envelope legal, is evidence attached, was anything rejected? | compiler |
| registry | did the proposal resolve into the closed vocabularies? | registry |
| **semantic** | **does it mean what the sentence means?** | model + prompt |

Only the third layer is "the model is right". `Predicate Registry Compliance` and
`Predicate Semantic Accuracy` are now reported separately, as are
`Structural Guardrail Accuracy` and `Object Kind Semantic Accuracy`.

Live cases carry a `semantic_expected` block next to their `invariants`; the invariants
guard structure, the gold guards meaning.

## 2. Before / After on recorded real runs (0 tokens)

Re-scored with `scripts/score_live_run.py` (the recorded raw envelope is pushed through
the real `compile_extraction_items`, so the numbers match persistence):

| metric | before (pre-fix run) | after (fix-verification run) |
|---|---|---|
| structural compliance | 10/10 | 9/10 |
| registry compliance | 10/10 | 10/10 |
| **semantic correctness** | **7/10** (23/28 checks) | **9/10** (26/28 checks) |

Fixed by the contract change: `predicate_registry_ceo` (`is` → `has_ceo`),
`temporal_former_ceo` (direction + `has_ceo` + `former`), `evidence_attachment`
(`literal` → `concept`).

Newly failing: `literal_weekly_frequency` returned an empty envelope. Open watch item.

## 3. Root cause: the model was never given the vocabulary

`app/agents/extraction_agent.py` had `EXTRACTION_REFERENCES = []` on the belief that the
closed vocabularies ship as the structured-output schema. They do not: the schema pins
*shapes* (`entity types`, `object_kind`, `claim_type` are Literal enums) while
`predicate` is a free-form `pattern`, resolved afterwards by the compiler. A model that
wants "the CEO relation" therefore has no way to learn that `has_ceo` exists and falls
back to the only registered predicate it can think of: `is`.

The `object_kind` definitions had the same problem — they live in
`references/extraction-v2.md`, which is lazy, and `SKILL.md` said nothing about
`object_kind` at all.

`references/claim-predicates.md` had also **drifted** from the authoritative
`schemas/claim-predicate-registry.json`: it listed 46 predicates and was missing
`has_ceo` entirely, so even a model that loaded it on demand would not have found the
role predicate.

### The fix (Skill / Contract only — no keyword heuristics, no protected lists)

1. `skills/knowledge-extraction/SKILL.md` (+~200 tokens): `object_kind` semantics
   (`concept` = mechanism/capability/method/behaviour, a description is never `literal`)
   + the consistency rule (a phrase typed `concept`/`literal` is not also an Entity)
   + "prefer the most specific registered predicate; `is` is the last resort" with the
   domain/range direction rule.
2. `references/claim-predicates.md`: added the missing `has_ceo` (label, aliases,
   domain/range) and the specificity rule.
3. `EXTRACTION_REFERENCES = ['claim-predicates.md']` (+549 tokens inlined, 3500-token
   budget, no trimming). `entity-types.md` and `extraction-v2.md` stay lazy.

## 4. Where the prompt tokens actually go

`scripts/measure_extraction_context.py` → `tests/fixtures/extraction_cases/live_context_cost.json`.
The live smoke's own spy records `model_calls` per step, which settles a wrong guess from
Step 13: **one extraction step is one model call**, not two.

| | before fix | after fix |
|---|---|---|
| system prompt (trace) | 392 | 1,149 |
| structured-output schema | 1,555 | 1,555 |
| tool schemas | 145 | 145 |
| **user chunk (a 20-char sentence)** | **20** | **20** |
| measured assembly | 2,112 | 2,869 |
| **provider-reported per call** | **4,163** | **4,435** |

The ~1,566-token gap between assembly and provider is AgentScope's own prompt
scaffolding (agent identity, ReAct/tool-call contract); it is not assembled by any
project code. **The document itself is 0.5% of the call.**

Most wasteful, in order: the structured-output schema re-sent on every call (~35% of the
prompt), that uncontrollable AgentScope scaffolding (~35%), then the project's own skill
and reference text. The heavy references are already correctly lazy (0 cost).

Price of the fix: **+272 prompt tokens per call** bought three semantic corrections.

## 5. Gold corrections (disclosed, not smuggled)

Two live-case golds were wrong and were corrected with reasoning recorded in the case
file itself:

* `path_source_artifact` — asserted a claim must exist. The contract permits an empty
  result for a sentence that carries only a location. Replaced by "the path is never an
  Entity" + `no_rejected_items`.
* `prose_slash_architecture` — pinned `object_kind: entity` for `统一认证`. The contract
  defines both `entity` and `concept` as legal for a capability, so the gold was fitted
  to one run's wording. Replaced by the contract-decidable facts: the architecture is an
  Entity candidate and must not be read as a source artefact.

Neither correction removes a check; both replace an unfounded expectation with one the
contract can actually decide.

## 6. Known limitations

* `literal_weekly_frequency` returned an empty envelope in the fix run. Contract-legal
  ("empty arrays are valid", "prefer precision over recall"), but it is a recall wobble on
  a low-value claim that appeared in the same run as the new specificity rule.
  **Unresolved**: attributing it to the prompt change or to run variance needs another
  live run, and guessing is what this step forbids. Not patched.
* The pre-fix `live_baseline.json` is still the approved baseline; the gate correctly
  refused to promote a semantically failing run, so the new run lives in
  `live_last_run.json`. The baseline should be re-approved once the last case is resolved.
* The `object_kind` precedence rule (`known_entity` wins over a declared kind) is what
  turned run 1's case 6 into `entity`. That is correct — a resolved entity *is* an entity —
  but it means an over-eager entity declaration silently re-classifies a concept. Worth a
  dedicated look in a later step.
* AgentScope's ~1.5k tokens of scaffolding cannot be measured offline, only inferred from
  provider totals.

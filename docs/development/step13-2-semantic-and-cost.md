# Step 13.2 — Semantic Case 收口 + Extraction Context Cost Audit

Two deliverables: make the live semantic cases honest (an abstention case that is allowed
to abstain, and a literal case that is actually decidable), and answer where the ~8k
prompt tokens of a one-sentence extraction call come from.

**Status: IMPLEMENTED** — semantic 9/10 with one open case-design gap.

## 1. Semantic cases

| case | kind | gold |
|---|---|---|
| `literal_weekly_frequency` | **abstention** | `abstain_ok` — extracting nothing is legal; it must not force an Entity/Claim, and anything it does extract must be compiler-clean. Removed from the literal-accuracy denominator. |
| `literal_retry_limit` | semantic (literal) | subject `KnowledgeCompiler`, predicate `allows`, `object_kind = literal` |

`tests/live_contract_eval.py` now reports four rates; abstention cases are counted
separately and never inside accuracy.

### The literal case is NOT achieved (open gap, deliberately left failing)

Two live runs, two wordings:

| run | sentence | observed object | object_kind |
|---|---|---|---|
| A | `KnowledgeCompiler 允许最多重试 3 次。` | `最多重试 3 次` | `concept` |
| B | `KnowledgeCompiler 允许重试 3 次。` | `允许 3 次` + `context:{"retry_limit": 3}` | `concept` |

Both are contract-consistent: `concept` covers a capability/limit description, and the
contract explicitly provides `context` for conditions. The predicate was correct in both
runs (`allows`) — that is what Step 13.1 fixed. Attribution:
**TEST_INVARIANT_ERROR (case design)**, not model, not guardrail, not registry.

A retry-limit sentence does not yield a bare-value object, so `object_kind=literal` was
never decidable there. The fix is a bare-value sentence (e.g. `最大 chunk 为 8192
tokens。`) verified with its own live run. Until then the case stays **red** rather than
reclassified green — a 10/10 produced by relabelling would be a lie.

## 2. Context cost audit

`scripts/measure_extraction_context.py` → `context_cost_audit.json` (0 model calls).

| Component | Tokens |
|---|---:|
| base system instructions | 72 |
| extraction skill (full SKILL.md) | 497 |
| reference (full claim-predicates.md) | 549 |
| tool catalogue line | 31 |
| agent-skills block (AgentScope, from `skills/`) | 271 |
| structured-output schema | 1,555 |
| tool schemas (Skill / list_skills / read_skill_reference) | 317 |
| user chunk | 20 |
| **known project total** | **3,312** |
| **provider total (extraction call)** | **8,468** |
| **UNATTRIBUTED** | **5,156** |

### Correction to Step 13.1

Step 13.1 reported "4,435 prompt tokens per call". That was the **average of two different
calls**. Split by step:

| call | prompt tokens |
|---|---:|
| Pass 1 detection | 815 |
| Pass 2 extraction | **8,055–8,468** |

The extraction call was understated by ~45%. `by_step` is now recorded per run.

### What the unattributed number is

`provider_total − known_project`. It is **not** "AgentScope tokens": it mixes AgentScope's
own message assembly (agent identity, ReAct/tool-call contract, framing, provider-side
tool/response-format wrapping) with the estimator's bias — `estimate_tokens` charges 1
token per 4 ASCII chars, which under-counts JSON. The provider/estimate ratio is 2.56×.
The two cannot be separated from here, so it is reported as a remainder and nothing is
claimed about its split.

### Flags for a later runtime task

* `REFERENCE_FULL_INJECTION` — `claim-predicates.md` (549 tokens) is injected **in full**
  on every call. The predicate names alone are 114 tokens; per-case relevant subsets are
  not supported by the current mechanism.
* `SCHEMA_COST_BOTTLENECK` — the structured-output schema (1,555 tokens, ~18% of the
  extraction prompt) is sent on every call. A compact form (titles/descriptions stripped)
  measures 1,085 tokens, i.e. ~470 available, but that requires an AgentScope-side change
  and is out of scope here.
* The Pass 1 detection call is a second call per case with its own 815-token prompt. For
  micro inputs it roughly doubles the call count; for real documents it is amortised.

### One unexplained variance

Extraction-step prompt tokens vary from 8,045 to 10,238 across cases with equally tiny
inputs. That spread (~2.2k, about one extra prompt) is only explicable by some steps
making an additional model call inside the step — most likely a skill-reference tool
round. The usage spy reported `model_calls = 1` per step, so it could not confirm it;
recorded as variance, **not** as a fact.

## 3. Production path unchanged

The smoke still runs `create_document → write_chunks → llm.extract → compile → persist`.
No `if TEST: minimal_prompt()` shortcut exists; every optimisation above is measured
against the production path and none was applied by cutting context for tests.

## 4. Cost

| run | cases | calls | prompt tokens | completion | est. cost |
|---|---:|---:|---:|---:|---:|
| A (literal case wording 1) | 11 | 22 | 97,577 | 11,276 | $0.0657 |
| B (literal case wording 2) | 11 | 22 | 102,118 | 12,507 | $0.0698 |

Two live runs, as budgeted; no further runs were made to chase a green result.

## 5. Regression

`pytest` → 604 passed, 6 deselected, 0 failed. Default runs cost 0 tokens; `-m smoke`
collects the single live suite.

## 6. Status

**IMPLEMENTED.** Two things remain open and are named rather than hidden: the
literal-accuracy case is not yet delivered (case design, evidence in
`live_failures.json`), and ~5.2k tokens per extraction call remain unattributed.

# Step 13.3 — Literal Case + Extraction Prompt Snapshot

**Status: IMPLEMENTED** — the literal case is delivered; the context is measured and
bounded but ~5.3k tokens per extraction call remain unattributed.

## 1. Literal case

`literal_chunk_size` — `最大 chunk 为 8192 tokens。` (replaces `literal_retry_limit`).

Chosen so the literal verdict does **not** depend on the model's mood: `8192 tokens`
matches the deterministic value guardrail, so `classify_object('8192 tokens',
object_kind='concept')` is still `literal`. The predicate (`is`) is registered with no
domain/range; no Registry change, no new predicate.

| | |
|---|---|
| expected | subject `chunk`, registered predicate, object `8192 tokens`, `object_kind = literal` |
| actual | subject `chunk`, `is`, object `8192 tokens`, `object_kind = literal`, evidence attached, no entity created for the value |
| result | **PASS** (1 live run, 2 calls, 8,852 prompt / 1,418 completion) |

`tests/test_live_semantic_contract.py` gained `LIVE_SMOKE_ONLY=<case>` so one contract can
be re-verified without the 11-case suite.

## 2. Prompt snapshot

`scripts/dump_extraction_prompt.py` → `extraction_prompt_snapshot.json` (0 model calls).

| Component | Tokens | Actually sent |
|---|---:|---|
| base system instructions | 36 | yes |
| extraction skill (full SKILL.md) | 552 | yes |
| reference (claim-predicates.md) | 549 | **NO — ledger only** |
| agent-skills block (AgentScope) | 271 | yes |
| structured-output schema | 1,555 | yes |
| tool schemas + catalogue line | 348 | yes |
| chunk | 9 | yes |
| duplicate content | **0** | — |
| **known project total (sent)** | **2,740** | |
| provider total (extraction call) | 8,041 | |
| **unattributed** | **5,301** | |

### Findings

1. **`REFERENCE_NOT_RENDERED` (new, real).** `reference` is in neither `SYSTEM_ORDER`
   nor `CONTEXT_ORDER`, so `CompiledContext.render()` drops it while the ledger still
   counts it (`actual_tokens` 1,149 vs 600 rendered; 549 of the difference is the
   reference). Requesting a reference does **not** put it in the prompt. This also
   corrects Step 13.1's account: the fix that worked was the SKILL.md text, not the
   inlined reference. Not fixed here — rendering it would *add* 0.55k tokens and change
   the prompt, which needs a full semantic smoke re-run.
2. **No duplication.** Skill body appears once (the agent-skills block is catalogue-only);
   the reference is not sent anywhere; the registry JSON is never sent; the schema ships
   once (response format) and is not repeated as prompt text; no instruction line appears
   in two sources.
3. **`SCHEMA_COST_BOTTLENECK`** — 1,555 tokens on every call (~57% of what the project
   sends). A title/description-stripped form measures 1,085; compacting it needs an
   AgentScope/provider-side change, out of scope.
4. **Unexplained variance** — extract-step prompt tokens range 8,856–11,056 across cases
   with equally tiny inputs. Only explicable by some steps making an extra model call
   (likely a skill-reference tool round); the usage spy said 1 call/step and could not
   confirm it, so it is recorded as variance, not as a fact.
5. **Budget** — project-controlled input context is 2,763 tokens: above the 2,000 target
   (`EXTRACTION_CONTEXT_BUDGET_WARNING`), inside the 3,000 hard ceiling.

### What the unattributed 5,301 is

`provider_total − known_project`. It is not a measured AgentScope figure: it mixes
AgentScope's message assembly and provider-side wrapping with the estimator's under-count
of structured JSON (provider/estimate ≈ 2.9×). It is reported as a remainder precisely so
it is not read as a fact.

## 3. Optimisation applied

**None.** There was no duplication to remove, and the one defect found
(`REFERENCE_NOT_RENDERED`) would raise cost, not lower it. Everything that would reduce
tokens changes the prompt and therefore requires a full semantic smoke re-run, which this
round's budget excludes. The flags are recorded for the next round.

## 4. Regression

`pytest` → 604 passed, 6 deselected, 0 failed. Default runs cost 0 tokens. Step 10/11/12
contracts untouched; production extraction path unchanged (no test-only branch); no new
keyword, no protected list, no second classifier.

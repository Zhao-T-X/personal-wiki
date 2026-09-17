# Step 13.4 — Provider Boundary Token Attribution

**Status: VALIDATED** — the real request is captured and explained. The remaining ~12%
per request is estimator imprecision, not unexplained content.

## 1. The seam

`agentscope/model/_openai_chat/_model.py :: _call_api`
→ `self.client.chat.completions.create(**kwargs)`

Nothing project-side sits below it, so this is the only honest place to measure. Patching
one agent instance captured nothing (`extract_structured` builds its own agent); the
client **class** is patched instead.

`scripts/capture_provider_request.py` → `provider_request_snapshot.json` (sanitised: no
keys, auth headers or base URLs), `--reaggregate` rebuilds the tool audit at 0 cost.

## 2. The finding: one extraction step is TWO provider calls

| | call[0] (base) | call[1] (follow-up) |
|---|---|---|
| messages | 4 | 8–9 |
| tools | 4 (1,699 tok) | 4 (same) |
| serialized (est) | 2,680 | 4,954 – 6,887 |
| **provider prompt** | **3,031** | **4,892 – 6,674** |
| tool calls | `read_skill_reference` ×2–3 | `GenerateStructuredOutput` |

Step total 7,923–9,705, matching the recorded 8,041–11,056. Everything the audit earlier
called "unattributed" is the **second request** plus the **reference text the model fetched
for itself**.

call[1] adds, as tool results:

* `claim-predicates.md` — 2,131 chars (est 549)
* `entity-types.md` — 5,586 chars (est 1,408)
* (`extraction-v2.md` when the model asks for it)

plus the replayed system prompt + tools, and AgentScope's own `<system-reminder>` messages
(226 + 305 chars, est ~134 per request).

So the agent **does** load the predicate registry — by asking for it. That also explains
why Step 13.1's predicate fix worked even though `EXTRACTION_REFERENCES` never rendered:
the model fetched `claim-predicates.md` itself.

## 3. Three token layers

| layer | value |
|---|---|
| project-known (per request) | 2,740 |
| serialized request, estimated | 2,680 (agrees within 2%) |
| provider-reported, base call | 3,031 |
| provider-reported, step total | 7,923–9,705 |
| serialization/provider delta | ~350 per request (~12%) → **UNRESOLVED_BY_PROVIDER**: the estimator charges 4 ASCII chars/token and no local tokenizer is installed, so this is estimator imprecision, not content |

## 4. Tools

All four are sent on **every** call of the step (1,699 tokens):

| tool | tokens | used |
|---|---:|---|
| `GenerateStructuredOutput` (the schema) | 1,383 | yes |
| `read_skill_reference` | 145 | yes |
| `Skill` | 104 | **never** |
| `list_skills` | 67 | **never** |

`response_format` is `None`: the schema ships **once** per request, as a tool — not
duplicated inside a request. It is paid on both calls.

## 5. The 8k → 10k variance

Same case, two runs: call[1] measured 4,892 and 6,674. The difference is **how many skill
references the model chooses to fetch** (2 vs 3), whose full text then rides in call[1].
Not "maybe an extra round" — a counted difference.

## 6. Bookkeeping fix

`app/context/compiler.py` now separates the two numbers the audit had been conflating:

* `actual_tokens` — the ledger (still what the budget is enforced against)
* `rendered_tokens` + `not_rendered[]` — what `render()` actually emits

`reference` is in neither `SYSTEM_ORDER` nor `CONTEXT_ORDER`, so requesting it inflated the
ledger by 549 tokens that never left the process. Requesting a reference does not put it in
the prompt; that is now visible in the trace instead of implied.

## 7. Optimisation applied

Only the bookkeeping fix. **No request content was changed.** The tools the model never
calls were left in place: this round measures.

## 8. Where the next cut should go

| option | tokens/step | risk |
|---|---:|---|
| drop `Skill` + `list_skills` (never used) | ~342 | low — but they are the entry point if a future task needs the catalogue |
| avoid the second call (no tool round) | ~3,000 + the fetched references | **high** — this is how the model gets the registries; removing it is how predicate selection regresses |
| compact the structured tool schema (1,383) | ~470 | medium — needs a provider-side change |

The double payment of the base prompt (~2,680 × 2 ≈ 5,360 of ~8k) is the structural cost,
and it buys the on-demand reference loading the semantics now depend on.

## 9. Regression

`pytest` → 612 passed, 6 deselected, 0 failed, 0 tokens by default.
`tests/test_provider_request_accounting.py` covers: no credentials, sanitizer, message
roles, `response_format` traceability, tool list, the two-call shape, and the
unrendered-reference bookkeeping.

# Extraction Agent — Tool Surface Audit (Step 14)

Step 13.4 established that one extraction *step* costs two provider calls, and that both
calls carry the whole tool block plus the whole system prompt. It also recorded which
tools the model actually called. This audit answers the one question that record left
open: **which of those tools does the extraction workflow actually need?**

Scope: `app/agents/base.py`, `app/agents/extraction_agent.py`, `app/tools/skill_tools.py`.
Evidence: two real provider captures (`provider_request_snapshot.json` = Before,
`provider_request_snapshot_step14.json` = After) and the recorded live runs. No live call
was made to answer any question in this document.

## 1. What the extraction agent registered (Before)

Measured from the real request, not from a registry listing: the Step 13.4 capture at
`tests/fixtures/extraction_cases/provider_request_snapshot.json`, case
`literal_chunk_size`, call 0.

| Tool | Schema (est. tokens) | Sent in every call | Actually called | Registered by | Needed by extraction? |
|---|---:|:---:|:---:|---|---|
| `Skill` | 104 | yes | **no** | AgentScope `Toolkit(skills_or_loaders=[LocalSkillLoader])` — `app/agents/base.py` | **no** |
| `list_skills` | 67 | yes | **no** | `app/tools/skill_tools.py`, listed in `extraction_agent` | **no** |
| `read_skill_reference` | 145 | yes | **yes** | `app/tools/skill_tools.py` | **yes** |
| `GenerateStructuredOutput` | 1383 | yes | **yes** | AgentScope, from `reply(structured_schema=Extraction)` | **yes** |
| | **1699** | | | | |

The same four tools are also the entire tool-catalogue block our own Context Runtime
renders into the prompt (`context.providers.tools.TOOL_CATALOG`, intent `extract` →
`{skill}` capability → `list_skills`, `read_skill_reference`), so `list_skills` was paid
for twice: once as a schema, once as a prompt line.

## 2. Evidence that the two removed tools were never used

The rule this round follows is "proven never needed", not "we did not see it used once".

1. **Recorded tool calls.** `tool_usage_audit.json` (Step 13.4, 4 provider calls over 2
   cases) records `used=false` for both — while `sent_in_every_call=true`.
2. **The new capture.** In the Step 14 capture the model called
   `read_skill_reference` twice in call 0 and `GenerateStructuredOutput` in call 1. It
   never asked for a skill catalogue, because it does not need one: the skill contract is
   already inlined by the Context Runtime.
3. **Code search — who could call them.** `list_skills` is *defined* in
   `app/tools/skill_tools.py`; the only registrations are `app/agents/base.py::DEFAULT_TOOLS`
   (knowledge / research / curator / review / personal agents) and the extraction list.
   Nothing else in `app/` calls it — no API route, no workflow, no repo layer. The
   extraction workflow's registry access goes through `app/skills.py::read_reference` via
   `read_skill_reference`; `describe_skills()` (what `list_skills` returns) is used by
   nothing but `list_skills` itself.
4. **Removal cannot break another tool path.** `read_skill_reference` takes
   `(skill, reference)` names directly and does not consult the catalogue. The skill
   contract reaches the prompt through `EXTRACTION_REFERENCES`, not through the viewer.
5. **`Skill` is redundant by construction.** It opens a skill document. The extraction
   prompt already contains that document's body (`[SKILL] # Knowledge Extraction Skill`),
   because `compose_prompt(role='extractor')` pulls `skills/knowledge-extraction/SKILL.md`
   in.

### The bundled prompt cost nobody had counted

`Skill` is not free-standing: AgentScope appends an `<agent-skills>` catalogue to the
system prompt when a loader is registered, and that catalogue says *"you MUST use the
`Skill` tool"*. Removing the tool while keeping the text would advertise a tool that no
longer exists, so the two had to go together.

Measured from the same two captures, the block is **1084 characters ≈ 271 estimated
tokens — 31% of the whole system prompt** — and it shipped an absolute local path
(`E:\test_project\...\skills\knowledge-curation`) to the provider on every call of every
step.

## 3. The change

- `app/agents/base.py`: `build_toolkit(tools, *, skills=True)` / `build_agent(..., skills=)`
  — the loader is now opt-out. Default unchanged, so the five non-extraction agents keep
  the `Skill` tool and the catalogue.
- `app/agents/extraction_agent.py`: `_SKILL_TOOLS = [list_skills, read_skill_reference]`
  becomes `EXTRACTION_TOOLS = [read_skill_reference]`, and `build_extraction_agent()`
  passes `skills=False`.
- `scripts/measure_extraction_context.py`: reads `EXTRACTION_TOOLS`; the tool-schema
  component label is derived from the live toolkit instead of hardcoded.
- `scripts/capture_provider_request.py`: gained `--tag`, so a later capture round cannot
  overwrite the baseline it is measured against.

Not changed: the prompt text, the extraction schema, the Skill contract, the predicate
registry, the compiler, AgentScope, and every tool's behaviour.

## 4. Result (measured, not predicted)

From the two captures, call 0 — the only call whose non-tool content is byte-identical
between the rounds (same system prompt body, same three user messages):

| | Before | After | Δ |
|---|---:|---:|---:|
| tools sent | 4 | 2 | −2 |
| tool schema tokens / request | 1700 | 1529 | **−171** |
| system prompt tokens (est) | 872 | 587 | **−285** |
| provider prompt tokens, call 0 | 3031 | 2618 | **−413** |

The provider's own number for call 0 (−413) is the authoritative per-request saving.
Our estimator says −456, about 10% high — it is a heuristic, the provider tokenizer is not.

Because a step makes two calls and both carry the block:

| | Before | After | Δ |
|---|---:|---:|---:|
| tool schema tokens / step | 3400 | 3058 | **−342** |
| provider prompt tokens / step (structural) | — | — | **≈ −826** |

Cross-check on real same-case runs (same model, same 2 steps per case, `live_run_13_2_full_suite.json`
→ `live_last_run.json`), which independently lands on the same figure:

| Case | Before (13.2) | After (14) | Δ | % |
|---|---:|---:|---:|---:|
| `predicate_registry_ceo` | 8880 | 8054 | −826 | −9.3% |
| `concept_progressive_loading` | 8865 | 8048 | −817 | −9.2% |

## 5. What this does not touch

The second call is still the expensive one: 4479 provider tokens in the Step 14 capture,
because it replays system + tools **and** carries every reference document the model
asked for. Removing two unused schemas does not change that — nor was it meant to
(Step 14 §11). Killing the second round is Step 15's question, and it needs the semantic
regression data this round protects.

## 6. Flags left open

- **`REFERENCE_NOT_RENDERED`** — `extraction_prompt_snapshot.json` still records 549
  ledger tokens for `claim-predicates.md` that `render()` never emits. Unchanged by this
  round; `context_cost_audit.json` is regenerated and still carries the flag.
- **`SCHEMA_COST_BOTTLENECK`** — `GenerateStructuredOutput` (1383 tokens, 91% of the
  remaining tool block) is now the only large tool cost. It is the extraction output
  contract and is not removable.
- **Project-controlled context** fell with this change; `context_cost_audit.json` now
  reports 2308 tokens against a 2000 target, so the budget warning survives on its own
  merits rather than because of a tool that was never called.

# Step 17 — Knowledge Extraction Skill Minimality Audit

**Question.** Every earlier cut proved something had *no* value (Detection, unused tools).
This one is different: the extraction Skill is not obviously useless — it is the agent's
behavioural contract. The task is to find the **shortest Skill that does not lower
accuracy**, and to prove the reduction with a live A/B, not by feel.

**Current contract.** `skills/knowledge-extraction/SKILL.md` — 2157 chars, **557 est. tokens**
(`app/context/tokens.py`: CJK ≈ 1 token/char, else 1 token/4 chars).

**Deliverable.** A `compact` variant (`SKILL_COMPACT.md`, 378 est. tokens) selectable with
`EXTRACTION_SKILL_VARIANT`, A/B'd against `current` at replay (0 tokens) and live. Production
stays on `current` until the live canary shows parity.

---

## 1. Line-level audit

Token = whole logical rule (continuation lines summed). Source of Truth = where the fact is
*authoritatively* defined. Decision ∈ {KEEP, COMPRESS, MOVE_TO_REFERENCE, MOVE_TO_SCHEMA, DELETE}.

| # | Section | Token | Purpose | Source of Truth | Necessity | Decision |
|---|---|---:|---|---|---|---|
| H | `# Knowledge Extraction Skill` + `Purpose:` | 21 | A. task instruction — what this call does | this file (task-scoped) | names the job; not derivable elsewhere | **KEEP** |
| R1 | "Extract only source-grounded … prefer precision over recall, empty results are valid" | 25 | B. precision / abstention contract | this file (behaviour) | the core "don't hallucinate, empty is OK" rule; schema cannot express it | **KEEP** |
| R2 | "Entity, Claim, Event, Idea and Question are distinct ontology objects" | 18 | C. output classes | `extraction.schema.json` — five required arrays | schema already ships five *separate* arrays; the sentence adds no selection power | **DELETE** |
| R3 | "Use only registered … closed vocabulary: select, never create" | 25 | B. predicate/type closure | `claim-predicates.md` §3 + ontology registry | `predicate` is a free-form `pattern` in the schema (not an enum), so *nothing but this rule* stops an invented name | **COMPRESS** |
| R4 | "Temporal words go in `temporal_signal`, never in `predicate`" | 26 | B. temporal placement | `extraction.schema.json` (`temporal_signal` enum) + ADR-011 | the field exists but the *placement* rule is behaviour the schema cannot state | **KEEP** |
| R5 | "Do not output Relations … Preserve modality, polarity, conditions, scope, time" | 26 | C. output contract | schema (no `relations` key, `additionalProperties:false`); compiler derives relations | "no relations" is *structurally impossible* to violate; "preserve …" restates mandatory fields | **COMPRESS** |
| R6 | "Every Claim … needs exact verbatim evidence" | 23 | B. evidence contract | `evidence.md` §3 (verbatim) + schema (`evidence_quote` minLength 1) | schema only requires *non-empty*; "verbatim / from its chunk" is the model behaviour | **KEEP** |
| R7 | "Never invent aliases, IDs … Return only the schema JSON" | 22 | C. output contract | structured-output contract (`GenerateStructuredOutput`) | "return only the schema JSON" is enforced by the output tool | **COMPRESS** |
| R8 | "A Mention is not an Entity … values, descriptions, paths and field names are never Entities" | 50 | B. entity eligibility | `entity-types.md` §1.1 (enumeration) + `entity_eligibility.py` (gate) | the *one-liner* ("Mention ≠ Entity") is the semantic the model must hold; the enumeration is in the reference and the gate enforces it | **COMPRESS** |
| R9 | `object_kind` definition (entity/concept/literal + examples) | 123 | B. object-kind contract | this file — **only** place; schema ships the enum, not the meaning | schema cannot distinguish "a description is never `literal`"; this is the Step 10 contract and must stay | **COMPRESS** |
| R10 | "Prefer the most specific registered predicate … `has_ceo` … domain/range decides Subject" | 87 | B. predicate selection | `claim-predicates.md` §3 — *identical text*; the reference is inlined | the *principle* changes model behaviour; the worked `has_ceo` example is already in the reference | **COMPRESS** |
| P1 | "… use the Reference Context you were given … How that context reaches you … is a runtime concern; this contract names no retrieval tool." | 110 | D. registry pointer (+ runtime detail) | `claim-predicates.md` / `entity-types.md` (definitions) | the *pointer* is useful; the runtime-machinery sentence is implementation detail | **COMPRESS** |

Sum of the units above ≈ 556 (the 557 total includes blank lines). The four expensive
offenders are **R9 (123), P1 (110), R10 (87), R8 (50)** — 370 of 557 tokens (66%) sit in
four blocks, three of which restate a Reference or a Schema fact.

---

## 2. Five-way classification

| Class | Content | Disposition |
|---|---|---|
| **A. Task Instructions** | H (heading + Purpose) | KEEP — nothing else names the job |
| **B. Semantic Contract** | R1 precision/abstain, R3 closure, R4 temporal, R6 evidence, R8 Mention≠Entity, R9 object_kind, R10 predicate selection, P1 pointer | KEEP the *meaning*, COMPRESS the *wording*; the model must understand these and the schema cannot carry them |
| **C. Output Contract** | R2 five kinds, R5 relations/modality, R7 schema-only | DELETE or COMPRESS — the structured-output contract + schema already enforce them |
| **D. Registry Knowledge** | R3 vocabulary body, P1 definitions | MOVE_TO_REFERENCE — never copy the predicate/type catalogue into the Skill; the Reference Context carries it (`claim-predicates.md` is inlined in agentic mode, `predicate_context`/`entity_type_context` in prefetch) |
| **E. Compiler-enforced** | R5 no-relations, R8 path/structural garbage, R7 legal-JSON | DELETE the restatement; the compiler/eligibility gate is the validation boundary |

---

## 3. Duplicate information (`duplicate_sections`)

Principle: **one authoritative definition per fact.** The Skill may *remind*, never *reproduce*.

| Skill block | Duplicate type | Authoritative location |
|---|---|---|
| R2 five kinds | `DUPLICATE_WITH_SCHEMA` | `schemas/extraction.schema.json` `required` = the five arrays |
| R3 closed vocabulary | `DUPLICATE_WITH_REFERENCE` + `DUPLICATE_WITH_REGISTRY` | `claim-predicates.md` §3; ontology registry |
| R5 no Relations | `DUPLICATE_WITH_SCHEMA` + `DUPLICATE_WITH_COMPILER` | schema has no `relations` property; `KnowledgeCompiler` derives relations |
| R6 evidence verbatim | `DUPLICATE_WITH_REFERENCE` | `references/evidence.md` §3 |
| R7 schema-only | `DUPLICATE_WITH_SCHEMA` | `GenerateStructuredOutput` output contract |
| R8 path/description excludes | `DUPLICATE_WITH_REFERENCE` + `DUPLICATE_WITH_COMPILER` | `entity-types.md` §1.1; `app/domain/entity_eligibility.py` |
| R10 predicate specificity | `DUPLICATE_WITH_REFERENCE` | `claim-predicates.md` §3 (same rule, same `has_ceo` example) |
| P1 definitions pointer | `DUPLICATE_WITH_REFERENCE` | `claim-predicates.md` / `entity-types.md` |

`object_kind` (R9) is the notable **non**-duplicate: no schema enum states what the values
*mean*, and no reference defines them — it is genuinely Skill-owned.

---

## 4. Semantic contract that must survive (absence ⇒ bug)

The compact contract must still make all of the following explicit. Every one is asserted
mechanically in `tests/test_skill_minimality.py`.

| Contract | Present in compact as |
|---|---|
| **Entity** = stable identity, stand-alone subject/object; values/descriptions/paths/field-names are not Entities | "Entity: a stable-identity object that could stand as a Claim subject/object outside its own sentence. Values, descriptions, paths, field names and lone mentions are Mentions, not Entities." |
| **Concept** = abstract mechanism/method/capability/behaviour, not a `literal` | "Concept: an abstract mechanism, method, capability or behaviour. A description is never `literal`." |
| **Literal** = explicit value | "Literal: an explicit value (number, date, degree, path, URL)." |
| **Unknown** = abstain when undecidable | "Unknown: use when the object's kind cannot be determined reliably." |
| **Predicate** = registry-only, never invented | "select from the closed vocabulary, never create a name" |
| **Evidence** = verbatim, locatable | "needs verbatim evidence from its source chunk" |
| **Precision / abstention** = omission is legal | "Prefer precision over recall — an empty result is valid" + "If the source does not support it, omit it" |

---

## 5. `object_kind` review (Step 10 contract)

Kept as **four short definitions + one boundary rule**, no example dump:

- short definition per kind (entity / concept / literal / unknown);
- boundary rule: "If you call an object `concept` or `literal`, do not also declare that phrase
  as an Entity" (the declared-kind consistency the live cases check).

Dropped: the worked example list (`"按需加载上下文"`, `"上下文管理机制"`, `8192`, `高风险`, `每天`,
path/URL). Those are *instances* of definitions the model already holds; the Step 15.1 live run
produced `concept`/`literal` correctly with the reference context present, so the examples are
recall aids, not the contract. This is the single biggest saving (123 → ~45 tokens) and the
primary live A/B risk, so it is the first thing the canary checks.

---

## 6. Predicate guidance review

Kept: the **principle** — "Prefer the most specific registered predicate; `is` is the last
resort" — because it is a behaviour rule that changes output and is *not* enforced by schema.

Dropped: the `has_ceo` worked example and the domain/range sentence **because they are
verbatim duplicates of `claim-predicates.md` §3**, which is inlined into every extraction
prompt. Keeping both paid twice for one fact. The live cases `predicate_registry_ceo` and
`temporal_former_ceo` still assert `has_ceo` + temporal, so a regression here fails the canary.

---

## 7. Evidence / precision review

**Not** deleted on the grounds that "the compiler validates it". The compiler can check that
evidence is *non-empty and locatable*; it cannot decide whether the model *should* have made
the claim. So abstention ("empty result is valid", "omit it") and the verbatim-evidence rule
stay in the contract; only the redundant restatements go.

---

## 8. The compact candidate

`skills/knowledge-extraction/SKILL_COMPACT.md`, selected by `EXTRACTION_SKILL_VARIANT=compact`.

| | chars | est. tokens |
|---|---:|---:|
| current (`SKILL.md`) | 2157 | **557** |
| compact (`SKILL_COMPACT.md`) | 1499 | **378** |

**−179 tokens (−32.1%).** The floor was set at "accuracy must not drop"; 378 is above the
250–350 target band, which is acceptable per the brief ("if 400 is the minimum sufficient set,
keep 400"). The contract clauses in §4 are all present; only restatements of Schema / Reference
/ Compiler facts were removed.

What the compact contract deliberately **no longer contains**:
- the predicate/entity-type catalogue (Reference owns it),
- any schema restatement (five-array list, relations-absence, "return JSON"),
- runtime/tool machinery ("names no retrieval tool" is gone; no tool is named),
- worked example lists for `object_kind` and `has_ceo`.

---

## 9. Verification

- **Replay A/B (0 tokens)** — `tests/test_skill_minimality.py` (11 tests, green): both variants
  load; compact is strictly smaller and still carries every §4 clause; neither leaks a registry
  dump or a runtime tool name; the composed prompt shrinks by the measured delta; the 11 recorded
  cases compile and persist to identical knowledge under both (the pipeline is skill-agnostic
  *after* the model).
- **Live canary (real tokens)** — `scripts/step17_skill_ab.py`, 5 cases × 2 variants, exactly one
  extraction provider call per case, detection off for both arms, prefetch for both arms. Same
  model/chunk/planner/schema/tools/registry; the **only** variable is the Skill.
- **Cost** — skill 557 → 378 est. tokens; measured extraction prompt 3071 → 2908 tokens/case
  (−163, −5.3%). Artifact: `tests/fixtures/extraction_cases/step17_skill_ab.json`.

### Live result — and why compact is NOT promoted

Two independent full-suite runs produced the same split:

| arm | semantic | registry | structural | calls/case | prompt/case |
|---|---|---|---|---|---|
| current | **5/5** | 5/5 | 5/5 | 1.0 | 3071 |
| compact | **3/5** | 5/5 | 5/5 | 1.0 | 2908 |

The two compact failures (`predicate_registry_ceo`, `temporal_former_ceo`) are **not**
predicate loss. In both, compact returned the *correct* contract:

```
compact  subject=Apple       predicate=has_ceo  object_kind=entity  temporal=current   (want 苹果)
compact  subject=Apple       predicate=has_ceo  object_kind=entity  temporal=former    (want 苹果)
current  subject=苹果         predicate=has_ceo  object_kind=entity  temporal=current
```

The only difference is the entity **surface form**: compact wrote `Apple`, the gold expects `苹果`.
The failing check is `subject_is`, i.e. entity-name language, which §17 classifies as allowed
"object wording" — but the gold is stricter than §17 and is name-sensitive, so the gold fails.
The other drift, `concept_progressive_loading`, moved `is → classified_as` — an allowed predicate
synonym (arguably *more* specific) that no check failed on.

Is it a real arm effect or sampling noise? Two isolated re-runs of the two CEO cases (both arms)
returned `苹果` and passed **4/4**; across all runs `current` kept `苹果` 6/6 and `compact` 4/6.
So the naming difference is **not** a lost clause — neither contract states "keep the source's
surface form", so there is nothing in `current` to restore. It is a decoding side-effect in which
the shorter English-dominant compact tilts the model toward an English name.

**Verdict: IMPLEMENTED, not VALIDATED.** Compact is built, wired, replay-proven and cost-proven,
and every *contract dimension* (predicate, temporal, object_kind, evidence, registry, structure,
abstention) is at parity. But under the project's own gold, semantic accuracy is lower
(3/5 vs 5/5), and §16/§23 forbid promoting it while that is true. `EXTRACTION_SKILL_VARIANT`
stays at its default `current`. The gold was **not** loosened to manufacture a pass.

---

## 10. Final answers

1. **What is genuinely necessary?** The task line, and the semantic contract the schema cannot
   express: precision/abstention, predicate closure, temporal placement, evidence, `object_kind`,
   predicate selection.
2. **What was duplicate?** R2, R3 (body), R5, R6 (partial), R7, R8 (enumeration), R10, P1 — see §3.
3. **What moves to Reference?** The predicate/type definitions and the `has_ceo`/domain-range
   example — already in `claim-predicates.md`.
4. **What is already guaranteed by Schema/Compiler?** The five output arrays, the absence of
   relations, legal JSON, and (as a gate) path/structural entity rejection.
5. **How much smaller?** 557 → 378 est. tokens (−179, −32%). The *rendered prompt* shrank by a
   measured 163 tokens/case (−5.3%), not the full 179: agent/schema/planner overhead is unchanged,
   exactly the "Skill −200 but overhead fixed" case the brief warned about.
6. **Does live semantic accuracy hold?** On every contract dimension, yes; on the project gold,
   no — 3/5 vs 5/5, both failures being entity-name language (`Apple` vs `苹果`). Not promoted.
7. **Most sensitive case?** The two CEO predicate cases, and — for a different reason —
   `concept_progressive_loading`, where compact prefers `classified_as` over `is` (allowed, and
   more specific). `object_kind` cases were stable.
8. **Provider step per 100 skill tokens?** The measured slope is ≈0.91 prompt tokens/case per
   skill token (163/179); the extractor's system prompt is *cached*, and the raw per-call delta is
   reported, not assumed.
9. **Is the current Skill already minimal-sufficient?** Not proven: it is demonstrably *compressive*
   (66% of tokens in four restating blocks), but the compact's name effect shows the shortest
   contract is not automatically the safest. The minimal-sufficient set is ≥378 tokens.
10. **Can compact become the default?** **No** — not while it measures below `current` on the gold.
    To promote it we need either a name-robust gold (a scoring change, deliberately out of scope)
    or a larger canary showing the naming effect is sampling noise. Until then `current` stands.

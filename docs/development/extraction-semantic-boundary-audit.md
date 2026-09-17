# Extraction Semantic Boundary Audit (Step 10)

Purpose: stop Python heuristic / keyword / regex from growing into a second
natural-language understanding system. This audit lists every lexical rule that touches
extraction semantics, classifies it, and records the Step 10 decision.

Categories:

- **STRUCTURAL** — recognises a shape (number, path, identifier). Keep.
- **NORMALIZATION** — canonicalises a value against a closed vocabulary. Keep.
- **VALIDATION** — checks a value against the registry / compiler. Keep.
- **CANDIDATE_GENERATION** — may *propose*; never decides. Keep, but must not decide final semantics.
- **SEMANTIC_HEURISTIC** — guesses meaning from words/length/count. Refactor (move to the LLM).

Authority after Step 10: **the LLM decides the semantics** (``object_kind``,
predicate candidate); **``KnowledgeCompiler`` + Registry validate**; code applies
deterministic guardrails and downstream effects.

---

## 1. Object classification — `app/object_classification.py`

| Rule | Location | Current purpose | Category | Decision |
|---|---|---|---|---|
| `_BOOL` (`true/false/是/否`…) | `object_classification.py` | detect boolean literal | NORMALIZATION | **Keep** (closed vocabulary) |
| `_NUMBER_RE` | `object_classification.py` | detect number + unit | STRUCTURAL | **Keep** |
| `_DATE_RE` | `object_classification.py` | detect date | STRUCTURAL | **Keep** |
| `_REGISTERED_LITERALS` (was `_VALUE_PHRASES`) | `object_classification.py` | closed set of value phrases (risk/frequency/environment) | NORMALIZATION | **Keep** (registered literals, not a guess) |
| `_PATH_RE` / `_EMAIL_RE` | `object_classification.py` | detect source artefact | STRUCTURAL | **Keep** |
| `_TECHNICAL_RE` | `object_classification.py` | detect technical identifier (camelCase/snake/ALL-CAPS+digit/dotted) | STRUCTURAL | **Keep** |
| `_CONCEPT_MARKERS` (提供/使用/支持/机制/能力/规则…) | `object_classification.py` | decide `concept` from words | **SEMANTIC_HEURISTIC** | **Demoted** → renamed `_CONCEPT_HINT_MARKERS`, used only by `concept_hint()` (candidate hint, **never a verdict**) |
| `_is_descriptive()` (length + word-count + verb markers) | `object_classification.py` | decide `concept` | **SEMANTIC_HEURISTIC** | **Removed as verdict** → replaced by `concept_hint()` (hint only) |
| `_name_like()` (capitalisation / CJK length) | `object_classification.py` | decide `entity` | **SEMANTIC_HEURISTIC** | **Removed as verdict** → replaced by the LLM's `object_kind` |
| keyword → `object = concept` verdict | `classify_object()` | classify object kind | **SEMANTIC_HEURISTIC** | **Moved to LLM** (`object_kind`); compiler validates the value |
| `is_entity_like()` | `object_classification.py` | boundary predicate for linking | VALIDATION | **Keep** (now `object_kind`-aware) |

## 2. Entity eligibility — `app/entity_eligibility.py`

| Rule | Location | Current purpose | Category | Decision |
|---|---|---|---|---|
| `_FILE_PATH` / `_DIR_FRAGMENT` | `entity_eligibility.py` | drop file/dir | STRUCTURAL | **Keep** |
| `_RELATION_NAMES` / `_RELATION_SUFFIX` | `entity_eligibility.py` | drop relation/field names | STRUCTURAL | **Keep** |
| `_MODIFIER_WORDS` / `_is_pure_modifier` | `entity_eligibility.py` | drop bare modifiers/fillers | STRUCTURAL | **Keep** (closed vocabulary) |
| `classify_object(...) == LITERAL` → DROP | `entity_eligibility.py` | a value is never an Entity | VALIDATION | **Keep** |
| `classify_object(...) == CONCEPT` in `is_plausible_subject` | `entity_eligibility.py` | reject descriptive subjects | **SEMANTIC_HEURISTIC** | **Removed** (whether a phrase is a concept is the LLM's verdict, not a keyword rule) |
| KEEP vs REVIEW decision | `knowledge.persist_extraction` | eligibility verdict | VALIDATION | **Keep** — KEEP only from an **accepted** claim (`_accepted_support_names`), never from a keyword |

## 3. Predicate matching — `app/ontology.py`

| Rule | Location | Current purpose | Category | Decision |
|---|---|---|---|---|
| `_PREDICATE_HINTS` | `ontology.py` | synonym phrases per predicate | **CANDIDATE_GENERATION** | **Keep** — explicitly documented as candidate generator |
| `match_claim_predicates()` | `ontology.py` | score 2-5 candidates | **CANDIDATE_GENERATION** | **Keep** — never decides; Registry/Compiler resolves |

## 4. Resolution / similarity — `app/resolution.py`

| Rule | Location | Current purpose | Category | Decision |
|---|---|---|---|---|
| `name_similarity` (containment + legal-suffix strip) | `resolution.py` | order duplicate candidates | CANDIDATE_GENERATION | **Keep** |
| `_same_type_score` >= 0.93 auto-merge | `resolve_or_create_entity` | fold near-identical names at import | CANDIDATE_GENERATION (existing merge boundary) | **Keep, documented** — the one pre-existing deterministic merge bar; *not* a second resolution system. Flagged: high-risk identity still belongs to Curation/Merge. |
| `types_compatible` | `resolution.py` | candidate gate | VALIDATION | **Keep** |

## 5. Extraction normalisation — `app/extraction.py`

| Rule | Location | Current purpose | Category | Decision |
|---|---|---|---|---|
| `_legacy_to_v2` defaults | `extraction.py` | fill missing envelope fields | NORMALIZATION | **Keep** |
| predicate → `KnowledgeCompiler` | `extraction.py` | resolve predicate | VALIDATION | **Keep** (canonical boundary) |
| `validate_extraction` | `extraction.py` | JSON-schema check | VALIDATION | **Keep** |
| `object_kind` (new, optional) | `schemas/extraction.schema.json`, `agents/extraction_agent.py` | the LLM's object-kind verdict | STRUCTURAL contract | **Added** (minimal extension; validated by the schema) |

---

## Net change in semantic-keyword rules

- **Removed as verdicts (2):** `_is_descriptive` (length/word/verb → concept) and
  `_name_like` (capitalisation → entity). They no longer decide an object's kind.
- **Demoted to a candidate hint (1):** `_CONCEPT_MARKERS` → `concept_hint()`.
- **Added:** zero new keyword tables. One new *schema field* (`object_kind`) that lets the
  LLM state the semantics the code used to guess.

Semantic keyword rules therefore **net decreased**; the remaining keyword list
(`_CONCEPT_HINT_MARKERS`) is a hint that no consumer turns into a verdict.

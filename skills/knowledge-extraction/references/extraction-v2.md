# LLM-Wiki Extraction Standard v2.0

## 1. Mission

The extraction engine identifies and structures valuable knowledge explicitly supported by the input text. It does not summarize the document, inject external knowledge, or generate missing knowledge.

## 2. Absolute rules

1. Extract only source-grounded information.
2. Every extracted object requires source evidence.
3. Prefer precision over recall when classification is uncertain.
4. Do not extract every noun, proper noun, keyword, property, action or sentence.
5. Preserve modality, polarity, conditions, scope, time and perspective.
6. Do not invent aliases, predicates, relations, events, ideas or questions.
7. Use only registered Entity Types, Claim Predicates and Relation Predicates.
8. The LLM does not generate database IDs.
9. The LLM does not decide final Graph persistence.
10. Empty arrays are valid.
11. A Mention is not an Entity. Only extract an Entity that has a stable identity and
    can be referenced long-term as a Claim Subject or Object (see `entity-types.md`).
12. A Claim's `object` is an Entity reference only when it names another extracted
    Entity. A value (`8192`, `true`, `高风险`) or a description is kept as a short
    literal phrase in the Claim — it does not become an Entity.
13. Never create an Entity from a file/directory path, URL, relation/table/column
    name, schema field, lone modifier, description, or transient mention.

## 3. Output objects

The LLM extraction result contains:

```text
entities[]
claims[]
relations[]
events[]
ideas[]
questions[]
```

During the migration phase, `relations[]` may be accepted for compatibility, but the preferred future flow is to derive Relations deterministically from validated Claims.

## 4. Entity

Each Entity contains:

```text
name
types[]
aliases[]
description?
properties?
```

Registered `types[]` values (closed vocabulary; the LLM may not invent types):

```text
Person
Organization
Product
Software
Technology
Method
Concept
Theory
Dataset
Model
Standard
Protocol
Resource
Location
```

## 5. Claim

Each Claim contains:

```text
subject
predicate
object?
object_kind?
temporal_signal?
claim_type
polarity
modality
content?
context
confidence
source_chunk
evidence_quote
```

`object_kind` is the model's own verdict on what the object *is*:
`entity | concept | literal | unknown`. Emit it whenever `object` is present:

- `entity`  — the object names another extracted Entity (a real knowledge object);
- `concept` — the object is a description of a mechanism/behaviour;
- `literal` — the object is a value (8192, true, 高风险, 每天) or a source artefact;
- `unknown` — you cannot responsibly decide.

The application validates this value and keeps deterministic guardrails for the
unambiguous cases (numbers, paths, technical identifiers); it does **not** re-guess the
kind from keywords. A Claim `object` is an Entity reference only when
`object_kind = entity` *and* the object names an extracted Entity.

`predicate` must be a registered Claim Predicate (see `claim-predicates.md`).
It is a *candidate*: a deterministic compiler resolves it against the closed
registry and rejects anything unregistered. Never invent a predicate.

`temporal_signal` carries temporal/evolution meaning separately from the
predicate. Registered values:

```text
new
current
former
previous
prior
existing
next
future
upcoming
incoming
outgoing
successor
predecessor
past
interim
acting
designated
```

Temporal words MUST NOT be folded into the predicate name. Encode
"the new CEO of X is Y" as `predicate=has_ceo, temporal_signal=new` — one
predicate, not a family of `new_ceo` / `current_ceo` / `former_ceo` terms.

Registered `claim_type` values (closed vocabulary; the LLM may not invent types):

```text
factual
definitional
causal
comparative
evaluative
predictive
normative
hypothetical
```

Registered `polarity` values:

```text
positive
negative
```

Registered `modality` values:

```text
asserted
possible
probable
capable
necessary
recommended
```

`claim_type`, `polarity` and `modality` are closed vocabularies. Emit only the exact lowercase tokens listed above. Never emit synonyms such as `fact`, `actual`, `likely`, `certain`, `maybe` or `can`; map them to the registered token (`fact`→`factual`, `actual`→`asserted`, `likely`→`probable`, `can`→`capable`).

## 6. Relation

Each Relation contains:

```text
source
predicate
target
context
confidence
source_chunk
evidence_quote
```

`predicate` must be a registered Relation Predicate (see `relation-predicates.md`).

## 7. Event

Each Event contains:

```text
event_type
description
participants[]
time
location?
status
confidence
source_chunk
evidence_quote
```

Registered `event_type` values:

```text
creation
development
release
publication
deployment
acquisition
merger
migration
training
evaluation
experiment
update
decision
announcement
meeting
failure
incident
other
```

Registered `status` values:

```text
planned
ongoing
completed
cancelled
failed
unknown
```

`time.precision` values:

```text
exact
day
month
year
range
relative
unknown
```

## 8. Idea

Each Idea contains:

```text
content
status
confidence
source_chunk
evidence_quote
```

Registered `status` values:

```text
candidate
accepted
implemented
rejected
archived
```

## 9. Question

Each Question contains:

```text
content
question_type
status
confidence
source_chunk
evidence_quote
```

Registered `question_type` values:

```text
knowledge
research
design
implementation
evaluation
```

Registered `status` values:

```text
open
answered
partially_answered
resolved
rejected
archived
```

## 10. Extraction procedure

1. Read the complete chunk.
2. Identify candidate knowledge.
3. Decide whether each candidate is an Entity, Claim, Event, Idea or Question.
4. Apply Entity and Predicate registries.
5. Preserve qualifiers.
6. Attach minimum sufficient evidence.
7. Deduplicate within the extraction result.
8. Validate structure.
9. Return JSON only.

## 11. Deterministic post-processing

After LLM extraction:

```text
Schema validation
↓
Registry validation
↓
Entity normalization
↓
Entity resolution
↓
Claim normalization
↓
Claim → Relation candidate generation
↓
Relation type/predicate validation
↓
Provenance validation
↓
Deduplication
↓
Lifecycle assignment
↓
Persistence
```

## 12. Repair policy

If the model returns malformed JSON, a single repair attempt may be performed. The repair prompt must instruct the model to re-read the source and re-extract according to this standard rather than mechanically patching the previous output.

Repeated or broad coercion must not silently weaken the extraction contract.

## 13. Internal check before return

The extractor should internally verify:

- Is every object source-grounded?
- Does every object have valid evidence?
- Did I invent anything?
- Did I preserve negation and modality?
- Did I preserve conditions and perspective?
- Did I confuse Claim and Relation?
- Did I confuse Idea and Claim?
- Did I generate a Question?
- Did I create an Event from a static statement?
- Did I classify a low-value noun as an Entity?

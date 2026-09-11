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

## 5. Claim

Each Claim contains:

```text
subject
predicate
object?
claim_type
polarity
modality
content?
context
confidence
source_chunk
evidence_quote
```

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

## 8. Idea

Each Idea contains:

```text
content
status
confidence
source_chunk
evidence_quote
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

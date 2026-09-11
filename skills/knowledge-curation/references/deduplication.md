# Knowledge Normalization Engine v1.0

## 1. Purpose

The Normalization Engine converts source-grounded LLM extraction into consistent knowledge records without adding information that is not supported by the source.

The engine is deterministic wherever possible. The LLM supplies semantic candidates; application code supplies constraints and persistence decisions.

## 2. Processing order

```text
Raw LLM JSON
  ↓
Schema validation
  ↓
Registry validation
  ↓
Text normalization
  ↓
Entity resolution
  ↓
Claim normalization
  ↓
Claim → Relation candidate generation
  ↓
Type / Predicate matrix validation
  ↓
Evidence validation
  ↓
Deduplication
  ↓
Lifecycle assignment
  ↓
Persistence
```

The order matters. Do not generate final Graph Relations before Entity Resolution and predicate/type validation.

## 3. Entity normalization

### 3.1 Name

- Trim leading/trailing whitespace.
- Collapse repeated internal whitespace.
- Preserve meaningful punctuation and capitalization in the canonical display name.
- Do not rewrite the name using external knowledge.

### 3.2 Alias

- Normalize whitespace.
- Preserve only source-supported aliases.
- Deduplicate aliases case-insensitively.
- Do not add the canonical name as an alias merely because it is the canonical name.

### 3.3 Type

- All types must exist in the Entity Type Registry.
- At least one type is required.
- Multiple types are allowed only when independently supported by source semantics.
- Unknown type is not automatically mapped to Resource.

## 4. Entity Resolution

Resolution confidence levels:

```text
EXACT
ALIAS
HIGH_CONFIDENCE_FUZZY
UNRESOLVED
```

### EXACT

Canonical name matches an existing entity under normalized identity rules.

### ALIAS

The source mention matches a registered alias.

### HIGH_CONFIDENCE_FUZZY

Fuzzy resolution may be used only within compatible semantic types and above a conservative threshold. It must not merge entities based only on lexical similarity when identity ambiguity exists.

### UNRESOLVED

Do not merge. The system may create a candidate Entity if the source independently establishes an Entity; otherwise the mention remains unresolved and dependent Claims cannot be verified.

## 5. Claim normalization

For every Claim:

1. Normalize whitespace.
2. Validate Predicate against the Claim Predicate Registry.
3. Normalize object reference if it maps to a resolved Entity.
4. Preserve literal values when no stable Entity exists.
5. Preserve context without inventing fields.
6. Preserve polarity and modality.
7. Validate source chunk and evidence.

## 6. Relation candidate generation

A Relation candidate can be generated only if:

- subject resolves to Entity;
- object resolves to Entity;
- Claim Predicate maps to an allowed Relation Predicate;
- source and target types satisfy the Relation Registry;
- polarity is positive;
- modality is `asserted`;
- no material unresolved condition exists;
- explicit source evidence supports the relation;
- relation has long-term graph value.

## 7. Normalization outcomes

```text
DIRECT_RELATION
CONDITIONAL_RELATION
CLAIM_ONLY
REJECTED
```

### DIRECT_RELATION

Create a candidate/verified Relation according to lifecycle rules.

### CONDITIONAL_RELATION

Retain Claim with context. Do not create an unconditional Relation.

### CLAIM_ONLY

Retain Claim because its semantics are valuable but should not become a core Graph edge in v1.0.

### REJECTED

Do not persist the extracted object as verified knowledge when it is unsupported, structurally invalid, semantically invalid or impossible to resolve safely.

## 8. Predicate semantics must not be upgraded

Never rewrite one semantic relation into another stronger or different relation solely for convenience:

```text
uses            ≠ depends_on
designed_for     ≠ improves
based_on         ≠ derived_from
compares_with    ≠ better_than
```

## 9. Event derivation

When a Claim explicitly describes a concrete occurrence with temporal meaning, Event extraction should be considered separately.

Example:

```text
OpenAI acquired Company X in 2016.
```

Preferred structured representation:

```text
Event(type=acquisition, time=2016, participants=[OpenAI, Company X])
```

A normalized `acquired` Relation may be derived separately when useful, but must not replace the Event because Event carries temporal semantics.

## 10. Deduplication

Deduplicate only when identity and semantics are equivalent.

### Entity deduplication key

Conceptually:

```text
resolved identity = canonical entity identity
```

### Claim deduplication

Claims are duplicates only when subject, predicate, normalized object/content and material context are equivalent. Different evidence may still support the same Claim.

### Relation deduplication

Relations are duplicates when source, predicate, target and material context are equivalent.

Do not delete conflicting Claims simply because they mention the same subject and predicate.

## 11. Conflicts

Contradictory source statements must remain separately traceable.

Example:

```text
Source A: Model X improves accuracy.
Source B: Model X does not improve accuracy.
```

Keep both Claims with separate provenance. Do not overwrite one with the other.

A future curation layer may classify the conflict.

## 12. Verification lifecycle

Recommended lifecycle:

```text
candidate → verified
candidate → rejected
candidate → archived
verified  → archived
```

Verification is not equivalent to model confidence. Verification means the system or curator has accepted the object under the project's evidence and semantic rules.

## 13. Persistence boundary

The database writer must never assume that valid JSON means valid knowledge. Before persistence, run:

```text
Schema validation
Registry validation
Entity resolution checks
Predicate/type checks
Evidence/provenance checks
Normalization outcome checks
```

## 14. LLM versus deterministic code

### LLM

- Identify semantic candidates.
- Classify Entities.
- Extract Claims, Events, Ideas and Questions.
- Select only registered Claim Predicates.
- Supply source evidence.

### Deterministic application

- Enforce registries.
- Resolve Entities.
- Validate type/predicate compatibility.
- Generate final Relation candidates.
- Deduplicate.
- Preserve conflicts.
- Assign lifecycle.
- Persist.

## 15. Design principle

The Normalization Engine must reduce semantic entropy rather than increase it. When the system cannot safely determine a stronger normalized representation, preserve the more source-faithful representation instead of guessing.

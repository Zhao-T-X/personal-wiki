# Claim Standard v1.0

## 1. Definition

A Claim is a source-derived statement that can be independently recorded and traced. It records **what the source says**, not what the model already knows.

## 2. Core rules

- Claims must be directly supported by source evidence.
- A Claim should represent one meaningful independent unit of knowledge.
- Subject should normally resolve to an Entity.
- Object may resolve to an Entity or remain a literal/value/text when no stable Entity exists.
- Conditions, scope, time, perspective, comparison context, negation and modality must be preserved.
- Do not upgrade an author's evaluation into objective fact.
- Negative Claims remain representable.
- Confidence measures extraction/structuring confidence, not truth probability.

## 3. Claim types

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

## 4. Polarity

```text
positive
negative
```

Negation is represented through polarity, not by creating a new Predicate such as `does_not_support`.

## 5. Modality

```text
asserted
possible
probable
capable
necessary
recommended
```

Words such as may, might, can, likely and could are expressed through modality rather than being embedded into Predicate names.

## 6. Context

`context` is a structured object used for qualifiers such as benchmark, condition, time scope, population, metric, perspective, role or other information required to preserve meaning.

## 7. Claim versus Relation

A Claim is source-faithful and may remain Claim-only even when its Predicate appears meaningful. A Relation is a normalized graph edge created only after deterministic validation.

Two relationship dimensions must not be conflated:

- **Claim → Relation** (Entity → Entity): derives graph edges within a single extraction, per `05-CLAIM-RELATION-NORMALIZATION.md`.
- **Claim → Claim**: records how a newly extracted Claim relates to what the knowledge base already believed (`duplicate` / `coexists` / `supersedes` / `contradicts`), per `14-CLAIM-EVOLUTION-STANDARD.md`. These are not graph edges and never enter the knowledge graph.

A Claim's content is never rewritten to express an update: a new Claim is created, and the relationship between the two is recorded instead.

## 8. Examples

Source:

> RAG may improve answer quality when retrieved documents are relevant.

Claim:

```json
{
  "subject": "RAG",
  "predicate": "improves",
  "object": "Answer Quality",
  "claim_type": "causal",
  "polarity": "positive",
  "modality": "possible",
  "context": {
    "condition": "retrieved documents are relevant"
  }
}
```

This should remain Claim-only in v1.0.

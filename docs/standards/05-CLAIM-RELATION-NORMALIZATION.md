# Claim → Relation Normalization Standard v1.0

## 1. Goal

Convert only safe, explicit Claim semantics into stable Graph Relations. Do not force every Claim into the graph.

## 2. Outcomes

Every Claim considered for normalization has one of four outcomes:

```text
DIRECT_RELATION
CONDITIONAL_RELATION
CLAIM_ONLY
REJECTED
```

## 3. Direct Relation criteria

A Claim may become a Relation only when all of the following hold:

1. Subject resolves to an Entity.
2. Object resolves to an Entity.
3. Predicate exists in the Relation Predicate Registry.
4. The source explicitly expresses the same canonical semantics.
5. Polarity is positive.
6. Modality is `asserted`.
7. No unresolved condition materially changes the relation.
8. Evidence directly supports the relation.
9. The relation has independent long-term graph value.
10. Source and target types satisfy the Predicate Registry constraints.

## 4. Conditional Relation

If a potential relation is explicitly stated but depends on modality, conditions, benchmark, time scope or other qualifiers that would make an unconditional graph edge misleading, keep it as Claim.

Example:

```text
RAG may improve Answer Quality when retrieved documents are relevant.
```

Store the Claim with modality and context. Do not store an unconditional `improves` edge.

## 5. Claim-only examples

These should normally remain Claims in v1.0:

```text
RAG retrieves external documents.
RAG provides documents as context.
RAG supports knowledge-intensive question answering.
RAG improves answer quality.
GPT-4 performs better than Model X on MMLU.
```

## 6. Direct conversion examples

### trained_on

```text
GPT-4 was trained on Dataset X.
```

If GPT-4 is a Model and Dataset X is a Dataset:

```text
GPT-4 --trained_on--> Dataset X
```

### implements

```text
Software A implements Protocol B.
```

If the types match:

```text
Software A --implements--> Protocol B
```

### based_on

```text
Model A is based on Architecture B.
```

If both are stable Entities and the semantic match is explicit:

```text
Model A --based_on--> Architecture B
```

## 7. No semantic upgrading

Never change a weaker or different predicate into a stronger one merely because it seems plausible.

```text
uses != depends_on
designed_for != improves
based_on != derived_from
compares_with != better_than
```

## 8. Events

When a Claim describes a concrete occurrence with temporal meaning, also consider Event extraction. For example:

```text
OpenAI acquired Company X in 2016.
```

The acquisition should be represented primarily as an Event with time. A normalized `acquired` Relation may be derived when appropriate.

## 9. Deterministic implementation

Claim-to-Relation conversion should be performed by application code after Entity Resolution and registry validation. The LLM should not decide final Graph persistence.

## 10. Scope boundary

This standard covers **Entity → Entity** graph edges derived within a single extraction. It says nothing about how a newly extracted Claim relates to Claims already stored from earlier documents — that is Claim → Claim evolution (`duplicate` / `coexists` / `supersedes` / `contradicts`), defined in [14-CLAIM-EVOLUTION-STANDARD.md](./14-CLAIM-EVOLUTION-STANDARD.md).

The two are independent: a Claim can be `CLAIM_ONLY` here (not graph-worthy) and still `supersede` an earlier Claim — the older Claim simply stops being current.

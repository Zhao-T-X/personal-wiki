# Relation Predicate Registry v1.0

## 1. Purpose

Relation Predicates define the controlled set of long-lived Entity-to-Entity semantic edges allowed in the Knowledge Graph.

The registry deliberately contains fewer predicates than the Claim Predicate Registry.

## 2. Core registry

```text
subtype_of
part_of

uses
depends_on

develops
creates
maintains
owns

implements
based_on
derived_from
extends

trained_on
evaluated_on

studies
evaluates
compares_with

applied_to
designed_for
```

## 3. Canonical direction

Relations are directed unless explicitly marked symmetric.

Only the canonical predicate is stored. Inverse labels are query/UI concepts.

Examples:

```text
OpenAI --develops--> GPT-4
GPT-4 --trained_on--> Dataset X
Software A --implements--> Protocol B
```

Do not store both `develops` and `developed_by` edges.

## 4. Inverse labels

Typical inverse labels include:

```text
contains
used_by
dependency_of
developed_by
created_by
maintained_by
owned_by
implemented_by
basis_for
source_of
extended_by
training_data_for
evaluation_target_of
studied_by
evaluated_by
application_of
design_target_of
```

These are not additional stored predicates unless separately registered in a future version.

## 5. Special semantics

### subtype_of
May be treated as transitive during query-time reasoning. Inferred transitive edges must not be written as source-supported facts unless separately evidenced.

### part_of
May be treated as transitive during query-time reasoning, subject to future validation of part-whole semantics.

### compares_with
Symmetric. Only one canonical edge should be stored.

### trained_on
Strict type rule: `Model -> Dataset`.

### evaluated_on
Typical source types: `Model`, `Method`, `Software`, `Product`, `Technology`. Typical target types: `Dataset`, `Resource`.

## 6. Explicit evidence requirement

Every stored Relation must be explicitly supported by source evidence. Co-occurrence and common sense are insufficient.

## 7. Excluded predicates

The following are intentionally not in the core Graph registry:

```text
related_to
associated_with
connected_to
works_with
similar_to
supports
```

The Graph should not contain generic semantic buckets merely to avoid losing information.

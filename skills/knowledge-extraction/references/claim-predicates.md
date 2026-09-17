# Claim Predicate Registry v1.0

## 1. Purpose

Claim Predicates describe the semantic relation expressed by a source-grounded Claim. They are more expressive than Graph Relation Predicates because Claims must preserve source information that does not necessarily belong in the long-lived graph.

## 2. Core registry

```text
is
defined_as
classified_as

contains
includes
consists_of

uses
retrieves
accesses
provides
receives

generates
produces
creates
extracts
transforms

supports
enables
allows
improves
reduces
increases
decreases
prevents

causes
leads_to

depends_on
requires

implements
based_on
derived_from
extends

trained_on
evaluated_on
tested_on

studies
investigates
evaluates
analyzes

compares_with
better_than
worse_than

designed_for
used_for
applied_to
addresses

has_ceo        # 首席执行官 (aliases: CEO, chief executive officer, 首席执行官, 首席执行长, 行政总裁)
               # domain Organization -> range Person; temporal
```

## 3. Registry rules

- The LLM must select only registered predicates.
- The LLM must never invent predicate names.
- **Prefer the most specific registered predicate over a generic one.** `X 的 CEO 是 Y` is
  `has_ceo`, not `is`; `is` is the last resort when no more specific predicate fits. A
  predicate's `domain`/`range` decide which side is the Subject (for `has_ceo`: Subject is
  the Organization, Object is the Person).
- Modal words such as `may`, `can` and `likely` are not predicates.
- Negation is represented by polarity.
- Conditions belong in `context`.
- Dates and temporal qualifiers belong in `context` or Event when the semantics are event-like.

## 4. Claim-only predicates

The following are intentionally Claim-first in v1.0 and should not normally become Graph Relations:

```text
retrieves
provides
receives
generates
produces
extracts
supports
enables
causes
leads_to
improves
reduces
increases
decreases
prevents
addresses
better_than
worse_than
```

The reason is not that these statements are unimportant. The reason is that they often require contextual conditions, metrics, experimental settings or role information that should not be erased by a binary Graph edge.

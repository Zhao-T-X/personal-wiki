# Event Standard v1.0

## 1. Definition

An Event is a source-described occurrence, activity, process, action or state change with temporal meaning and independent knowledge value.

Entity answers **who/what**. Event answers **what happened**.

## 2. Event Types

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

## 3. Time

```json
{
  "start": "2023-03",
  "end": null,
  "precision": "month"
}
```

Allowed precision:

```text
exact
day
month
year
range
relative
unknown
```

Do not invent exact dates from vague wording.

## 4. Status

```text
planned
ongoing
completed
cancelled
failed
unknown
```

## 5. Event versus Claim

Claim preserves what the source says. Event structures a concrete occurrence. One source passage may legitimately yield both.

## 6. Event versus Relation

A Relation expresses a stable semantic edge. An Event represents a concrete occurrence, often with time, participants, status and location.

## 7. Non-events

Do not turn static properties, generic capabilities, definitions, ordinary usage statements or model predictions automatically into Events.

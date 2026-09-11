# Question Standard v1.0

## Definition

A Question is a source-derived question with independent knowledge value that can drive future knowledge acquisition, research, verification or problem solving.

## Rules

- Not every question mark represents a knowledge Question.
- Reject rhetorical or disposable conversational questions.
- The model must not generate Questions that are absent from the source.
- Questions may remain in the knowledge base after they are answered.

## Types

```text
knowledge
research
design
implementation
evaluation
```

## Status

```text
open
answered
partially_answered
resolved
rejected
archived
```

`answered` does not necessarily mean `resolved`.

## Evidence

Every Question requires source evidence.

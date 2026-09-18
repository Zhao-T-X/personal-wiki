---
name: knowledge-extraction
description: Extract source-grounded Entities, Claims, Events, Ideas and Questions from documents into the LLM-Wiki knowledge base, using the registered entity types and claim predicates.
---

# Knowledge Extraction Skill

Purpose: extract source-grounded knowledge for LLM-Wiki.

Extract only what the source supports. Prefer precision over recall — an empty result is valid.

Semantic contract:
- Entity: a stable-identity object that could stand as a Claim subject/object outside its own
  sentence. Values, descriptions, paths, field names and lone mentions are Mentions, not Entities.
- Concept: an abstract mechanism, method, capability or behaviour. A description is never `literal`.
- Literal: an explicit value (number, date, degree, path, URL).
- Unknown: use when the object's kind cannot be determined reliably.

Rules:
- Use only registered entity types and Claim predicates: select from the closed vocabulary, never
  create a name. Prefer the most specific registered predicate; `is` is the last resort.
- Temporal meaning ("现任", "曾经") belongs in `temporal_signal`, never in the predicate. Negation
  belongs in `polarity`; keep conditions and scope.
- `object_kind` is your verdict on the Claim object (`entity` / `concept` / `literal` / `unknown`).
  If you call an object `concept` or `literal`, do not also declare that phrase as an Entity.
- Every Claim, Event, Idea and Question needs verbatim evidence from its source chunk. If the
  source does not support it, omit it — never invent entities, aliases, predicates or relations.

Entity-type and predicate definitions come from the Reference Context you were given; a definition
it does not carry is not yours to assert.

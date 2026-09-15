"""Read-only projections for the product's surfaces.

Nothing here decides what knowledge *is*. A projection only arranges what other
modules already decided — the compiler and the registry for what a claim means,
``app.domain.claim_state`` for what is still current, the claim's own chunk for
what backs it — into the shape a person reads.

The distinction matters because these modules are shared: Search, Knowledge QA,
Research and the KnowledgeCard all render knowledge, and if each assembled its own
shape they would drift into looking like different products. A read model gives
them one shape to agree on while staying replaceable — it is not a domain model and
no business rule may move into it.
"""

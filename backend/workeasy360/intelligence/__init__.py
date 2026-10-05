"""intelligence/ - the shared AI, retrieval and analytics platform (Phase 8).

Workeasy360 owns the *mechanism*; products own the *meaning*. A product
registers a skill (its prompt and its input/output contract), indexes its own
documents and publishes its own events. This package decides whether it may,
selects and calls an approved model, bounds and meters what that costs,
retrieves only what the caller is authorized to see, and aggregates events
into metrics a product defined. It implements no product's business logic:
there is no deal, invoice or stock item here and there cannot be one (A14).

::

    gateway     skill invocation: authorize -> reserve -> retrieve -> assemble
                -> invoke -> validate -> settle. The one entry point products
                use for generation (ADR-030).
    rag         knowledge sources, versioned documents, chunks and pgvector
                embeddings; tenant- and product-scoped retrieval (ADR-031).
    ai          provider adapters, the model registry, credentials, spend
                reservation and metering, the skill/prompt registry, safety.
    analytics   the metric registry, facts extracted from routed product
                events, rebuildable aggregates and a bounded query (ADR-032).

Layering (import-linter ``intelligence-internal-layers``): ``gateway`` above
``rag`` above ``ai``; ``analytics`` is independent of all three. The package
sits above ``integration`` (egress, the due-work timer, routed events) and
beside ``commercial`` - neither imports the other, so estimated AI cost can
never become billing truth by accident.

**AI is never an authority** (Task 20). Nothing here writes a permission, an
entitlement, a subscription, a seat or an audit decision from model output,
and import-linter forbids the modules that could.
"""

# ADR-031: Retrieval lives in PostgreSQL with pgvector, authorized before it ranks

**Status:** Accepted
**Date:** 2026-09-25
**Deciders:** Architecture / Security
**Depends on:** [ADR-009](ADR-009-tenant-data-isolation.md), [ADR-030](ADR-030-ai-gateway-and-provider-ownership.md)

---

## Context

Phase 8 adds retrieval-augmented generation: products index their own content
per organization, and skills retrieve passages from it. Cross-tenant source
mixing is a P0; so is one product searching another product's knowledge in
the same organization. Retrieval that ranks everything and filters afterwards
leaks through the model's context, result counts and timing.

The stack is PostgreSQL 16 with RLS; pgvector was not installed.

## Decision

**PostgreSQL plus pgvector, no vector database.** The local and CI image
becomes `pgvector/pgvector:pg16-trixie` - the official `postgres:16` Debian
build plus one package, the same Debian release so collations are unchanged.
The extension is not "trusted", so a superuser creates it once
(`infra/postgres/init.sql` does so for new volumes; an operator runs one
statement on existing databases); the first retrieval migration creates it
where the role may and otherwise stops with that instruction.

**Every row is tenant data.** Sources, documents, versions and chunks are
`TenantScopedModel`s: RLS enabled and forced, organization-leading indexes,
composite `(id, organization_id)` foreign keys so a chunk cannot point at
another organization's document or version. Each row is bound to the
organization, the submitting product (from its credential), the product's
source and document identifiers, opaque access tags and a version.

**Authorization is the query.** The statement that orders by distance can see
only chunks that are in the organization (explicit predicate *and* RLS), owned
by the calling product, in sources the caller named and owns, of an indexed
document's *serving* version, in an active source, visible to the caller's
access tags, and embedded with the model the query is embedded with. There is
no retrieve-then-filter step.

**Exact search for now.** The candidate set is one product's sources in one
organization. An exact `ORDER BY embedding <=> query` over it, served by the
`(organization, product, source)` index, is fast at that size and has perfect
recall; an approximate index with a selective filter would lose recall for
nothing. The scaling trigger and path (per-model partial HNSW indexes over a
typed cast) are recorded in docs/phase-8/05-rag.md.

**The vector column is untyped.** `vector` with the model key and dimension
recorded per row, so the schema is not welded to one provider's model;
similarity is only ever computed within one model.

**Versions swap atomically.** Chunks are written only when a version is
activated, in one transaction that deletes every other version's chunks and
points the document at the new one; a version older than the newest submitted
never serves. Deletion and source revocation remove the serving pointer at
once and purge chunks (immediately, or behind a timer for a large source).

**Deny cross-product by default.** No sharing mechanism exists for knowledge
yet; a product sees only its own sources. Sharing, when needed, will be an
explicit grant by the owning product, the pattern skills and metrics already
use.

## Consequences

- No new infrastructure to operate, back up, secure or keep in sync with
  PostgreSQL's tenant model; RLS covers vectors like every other tenant row.
- An operator step (one superuser statement) is part of the upgrade.
- Past a measured size per organization and product, an approximate index
  will be needed.

## Alternatives rejected

- **A dedicated vector database.** A second tenant-isolation implementation,
  a second backup and a second consistency problem, with no measured need.
- **Embeddings as `real[]` computed in SQL.** Works without an extension but
  is one to two orders of magnitude slower per comparison.
- **A shared index filtered after ranking.** The leakage the brief forbids.

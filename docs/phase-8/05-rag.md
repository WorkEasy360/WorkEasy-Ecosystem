# Phase 8 — Retrieval (RAG)

Decision record: [ADR-031](../architecture/adrs/ADR-031-rag-storage-and-vector-isolation.md).

## 1. The model (Task 22)

```
rag_source             (organization, product_code, source_key)   status active|revoked
  rag_document         (source, document_key)                     status pending|indexed|failed|deleted
                         served_version_id, served_embedding_model, latest/served seq
    rag_document_version (document, version_label, seq)           status pending|indexing|retrying|
                         content (until indexed), content hash       indexed|superseded|failed|discarded
    rag_chunk          (document, version, ordinal)               text, embedding (pgvector),
                         product_code, source_id (denormalised)      embedding_model, dimensions
```

Every table is tenant-scoped (RLS, `organization_id` on every row) and joined
on composite `(id, organization_id)` keys, so a row can never point at another
organization's parent. Chunk identity columns and every stored version's
identity are frozen by trigger.

**Ownership comes from the credential.** `product_code` is the authenticated
product, never a request field; `source` is always *the caller's* source of
that name. Two products in one organization may both have a `help_center`
source and never see each other's.

## 2. The ingestion contract (Tasks 23-25, 38)

`POST /api/v1/integrations/rag/documents` with `organization_id` and one
`document` JSON field:

```json
{"source": "help_center", "document_id": "article-42", "version": "7",
 "title": "Returns policy", "url": "https://help.example.com/42",
 "access_tags": ["team:support"], "content": "..."}
```

| Field | Rule |
|---|---|
| `source` | slug; created on first use |
| `document_id` | 1-200 printable characters, the product's own identifier |
| `version` | 1-64 characters, the product's version label |
| `title` | ≤ 200 printable characters |
| `url` | optional; `https`, a host, no credentials |
| `access_tags` | ≤ 20 tags of `[a-z0-9:_.-]` |
| `content` | ≤ 200,000 characters (`RAG_MAX_DOCUMENT_CHARS`), ≤ 256 chunks |

**The platform never reaches into a product's database** — what is indexed is
exactly what the product submitted. Submission needs the same decision as any
AI call (indexing spends AI budget): product entitled for writes, the plan
grants AI, AI on, organization accepting work.

**Answers.**

| `status` | Meaning |
|---|---|
| `accepted` (`202`) | a new version, queued for indexing; the previous version keeps serving |
| `duplicate` (`200`) | the same version with the same content — nothing changes |
| `409 knowledge_conflict` | the same version label with different content — one label cannot stand for two contents |

Idempotency is a unique constraint on `(document, version_label)` decided under
the document's row lock, so concurrent submissions of one version produce one
version row and at most one set of chunks
(`test_one_document_version_submitted_eight_ways_indexes_once`).

**Status.** `POST …/rag/documents/status` reports `status`, the latest
version and its state, the indexed version, `stale`, `last_indexed_at` and
`last_error`. A document is never reported indexed because of a version that
did not index.

## 3. Chunking (Task 28)

One strategy, deterministic and pure (`rag/chunking.py`): paragraphs packed
greedily into chunks of ≤ 1,000 characters; an over-long paragraph split at
the last sentence end or whitespace before the limit; 100 characters of
word-aligned overlap. Determinism is what makes indexing retryable without
duplicates. The size bounds are enforced **at ingestion**, before a single
embedding is paid for: a document costs at most 256 embeddings, whatever its
shape — an oversized upload cannot be submitted, so it cannot fan out into
millions of vectors.

## 4. Indexing (Tasks 29, 39, 41, 42)

Asynchronous on `heavy`, driven by a due-work timer (`rag.index`) so a lost
message is re-dispatched:

```
tx 1  lock version; superseded? terminal? in flight? -> indexing, attempt n, lease
      chunk -> reserve AI spend -> embed (bounded batches of 64) -> settle
tx 2  lock document + version; fenced on (indexing, attempt n); still newest?
      insert chunks; delete every other version's chunks; serve this version
```

- **Never a transaction across the network.** Embedding happens between the
  two transactions.
- **Idempotent under retry and crash.** Chunks are written only in tx 2 under
  the attempt fence, and `(version, ordinal)` is unique: a duplicate dispatch
  or a worker that outlived its lease cannot produce a second set.
- **Metered.** Embeddings are AI spend: reserved, settled and recorded as
  usage lines of an `embedding` execution, against the same budgets.
- **Version race.** A version older than the document's newest is
  `superseded` at claim (no spend) or at activation (vectors discarded): an
  old version finishing late never replaces a newer one.
- **Failure is visible.** An outage, open breaker, spend limit or concurrency
  cap leaves the version `retrying` (30 s doubling to 1 h); after 5 attempts
  it is `failed` with `last_error`. If an older version is serving it keeps
  serving and the document reports `stale: true`.

## 5. Embeddings and vector storage (Tasks 26, 27, 30)

`pgvector` in the platform's PostgreSQL — no vector database. The column is an
untyped `vector` with the model and dimensions stored on every chunk, so a
model change needs no migration and a search only ever compares vectors of
the model the query is embedded with. The embedding model is a registry
entry (`openai.text-embedding-3-small`, 1,536 dimensions); non-finite values
are refused before storage.

**Changing the embedding model.** Chunks record their model; a document whose
serving model is not the current one reports `stale`, and retrieval embeds
the query with the current model, so un-reindexed documents simply stop
matching until the product resubmits them (a new version label). There is no
silent mixing of vector spaces.

## 6. Retrieval and isolation (Tasks 31-35)

**Authorization is decided before the search, in the query itself.** The one
statement that ranks by distance can only see chunks that are:

1. in this organization — the explicit predicate **and** RLS on every joined
   table;
2. owned by the calling product — another product's knowledge in the same
   organization is not in the candidate set (deny by default);
3. in the requested sources, each of which must be the caller's own
   (otherwise `404 knowledge_not_found`, indistinguishable from absent);
4. the serving version of an indexed document in an active source;
5. visible to the caller's `access_tags` — a tagged document only to a caller
   presenting one of its tags; an untagged document to every caller of that
   product;
6. embedded with the query's model.

There is no "retrieve then filter" anywhere: nothing outside the authorized
set can influence a ranking, a count, a timing, or a model's context. Exact
kNN (`ORDER BY embedding <=> query`) over that set, served by the
`(organization, product_code, source_id)` index.

**Access tags are the product's statement.** The platform cannot know a
product's internal permission model; the product derives the tags from its
own authorization of its user and presents them per request. The platform
guarantees the tag filter is applied before ranking and cannot be widened by
anything in the prompt.

Direct retrieval (`POST …/rag/retrieve`) returns passages with provenance and
the query-embedding usage; it is metered as an `embedding` execution. Skill
retrieval does the same inside an invocation. `409 index_not_ready`
(`pending: true|false`) is returned *before* the query embedding is paid for
when nothing in the requested sources is indexed.

## 7. Provenance (Task 36)

Every passage carries its source key, document id, served version label,
chunk ordinal, title, the product's https URL and a similarity score — from
stored metadata, never from a model.

## 8. Deletion, revocation, staleness (Tasks 37, 38)

| Action | Effect |
|---|---|
| delete a document | serving pointer removed and **all its chunks deleted in the same transaction**; stored version content cleared; pending versions discarded |
| revoke a source | nothing in it is retrievable **from the commit** (the source's status is in the search predicate); chunks purged in bounded batches behind a `rag.purge` timer |
| new version indexed | the old version's chunks deleted in the activation transaction |
| embedding model changed | documents report `stale` until resubmitted |

Chunks and embeddings never outlive their serving version. Content is kept on
a version only until it is indexed (it is needed to retry) and cleared on
activation, supersession, deletion and failure. Tombstones are pruned after
30 days. See [06-security-and-privacy.md](06-security-and-privacy.md) for
organization purge.

## 9. Scaling

Exact search is correct and fast at the scale the candidate set has: one
product's sources in one organization — thousands to low tens of thousands of
chunks (the per-organization cap is 50,000 documents × ≤ 256 chunks, but a
query spans only the named sources). Measurements are in
[11-performance.md](11-performance.md).

**When to revisit.** If `rag_retrieval_seconds` p95 exceeds 250 ms, or a
single source routinely exceeds ~200,000 chunks, add an approximate index
(`hnsw` on `vector_cosine_ops`, per embedding model via a partial expression
index with a typed cast). Because pgvector applies `WHERE` after an
approximate scan, enable `hnsw.iterative_scan = relaxed_order` (pgvector ≥
0.8) so the tenant/product/source filter cannot silently shrink results — and
keep the predicate in the query: an approximate index changes recall, never
authorization. Partitioning `rag_chunk` by organization hash is the step after
that. A separate vector database is not on this path (ADR-031).

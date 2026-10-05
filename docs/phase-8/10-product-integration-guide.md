# Phase 8 — Product integration guide

**For teams building a Workeasy360 product.** How your separately deployed
application uses the platform's AI, retrieval and analytics. You need the
Phase 3 integration you already have: a registered client (`client_id` plus
`client_secret`, or `private_key_jwt`) and a manifest.

## 0. The basics

- **Discovery.** `/.well-known/openid-configuration` lists every endpoint
  under `workeasy360_intelligence_endpoints`. Read paths from there; do not
  hard-code them.
- **Transport.** Every call is `POST`, `application/x-www-form-urlencoded`,
  with your client authentication as form fields (exactly as for
  `/integrations/events`), `organization_id`, and one JSON-encoded field
  (`request`, `document`, `query`, `skill`, `metric`).
- **Organization.** `organization_id` is a claim the platform checks against
  the live entitlement decision on every call. An organization that does not
  hold your product is indistinguishable from one that does not exist.
- **Errors.** JSON with `error`, `error_description`, `correlation_id` and
  sometimes context (`field`, `scope`, `resets_at`, `request_id`). Switch on
  `error`, never on the description. Table: [01-ai-gateway.md](01-ai-gateway.md) §6.
- **Retry** only `429` and `503`, with backoff, honouring `Retry-After`. Send an
  `idempotency_key` on AI calls so a retry can never be charged twice.
- **Everything you get back is untrusted.** Model output is text or JSON your
  code must validate and authorize like user input; never render it as HTML;
  never let it change permissions, money, stock or records without your own
  business rules deciding.

## 1. AI access

Your product gets AI in an organization only when the organization's plan
includes the feature `<your code>.ai.monthly_budget_micros` (sold through the
catalog like any feature). Without it every AI call is `403 ai_not_entitled`.
The organization's administrator can also switch AI off or cap it.

## 2. Register a skill

```
POST /api/v1/integrations/ai/skills      skill={...}
```

```json
{"key": "product.crm.draft_reply", "version": 1,
 "instructions": "Draft a short, polite reply to the customer message in the input.",
 "input_schema": {"type": "object",
   "properties": {"message": {"type": "string", "maxLength": 4000}},
   "required": ["message"]},
 "output_schema": {"type": "object",
   "properties": {"reply": {"type": "string"}}, "required": ["reply"]},
 "capabilities": ["structured_output"], "tier": "standard",
 "max_output_tokens": 800, "retrieval": {"enabled": false},
 "shared_with": []}
```

- The key must start with `product.<your code>.`.
- A version is immutable. Same definition again: `200`. Different definition
  under the same number: `409 ai_registry_conflict`. Change it by registering
  version 2; retire old versions with `…/ai/skills/retire`
  (`skill={"key": ..., "version": ...}`).
- `instructions` are static: **never template user data into them** — put it
  in `input`. Never put a credential in either.
- Schema keywords: [03-skills-and-prompts.md](03-skills-and-prompts.md) §4.
  Omit `additionalProperties` and objects are closed.
- `POST …/ai/skills` with no `skill` field lists your skills.

## 3. Invoke

```
POST /api/v1/integrations/ai/invoke      organization_id=...  request={...}
```

```json
{"skill": "product.crm.draft_reply", "version": 1,
 "input": {"message": "Where is my order?"},
 "idempotency_key": "reply-7f3a", "mode": "sync",
 "actor": "user:1234",
 "retrieval": {"query": "order delivery times", "sources": ["help_center"],
               "top_k": 5, "access_tags": ["team:support"]}}
```

| Field | |
|---|---|
| `version` | optional; latest active if omitted — pin it in production |
| `input` | must match the skill's input schema; ≤ 64 KiB |
| `idempotency_key` | optional, recommended; unique per organization for your product |
| `mode` | `sync` (default, ≤ 30 s) or `async` |
| `actor` | optional opaque reference to your user (≤ 128 chars) for your own tracing; no personal data |
| `retrieval` | only for skills with retrieval enabled; runs over **your** knowledge in this organization |

**`200` (sync):**

```json
{"request_id": "…", "status": "succeeded",
 "skill": {"key": "product.crm.draft_reply", "version": 1},
 "output": {"reply": "…"}, "truncated": false,
 "citations": [{"ref": "S1", "source": "help_center", "document_id": "article-42",
                "version": "7", "chunk": 3, "title": "Delivery", "url": "https://…"}],
 "context": [ … every passage supplied … ],
 "model": {"provider": "anthropic", "model": "anthropic.claude-opus-5"}, "fallback_used": false,
 "usage": {"input_tokens": 812, "output_tokens": 95, "embedding_tokens": 6,
           "estimated_cost_micros": 6440, "currency": "USD"}}
```

`output` is validated JSON when the skill has an output schema, otherwise a
string. `estimated_cost_micros` is spend control, not a bill.

**`202` (async):** `{"request_id": "…", "status": "queued"}`. Poll:

```
POST /api/v1/integrations/ai/requests/status   organization_id=...  request_id=...
```

→ `{"status": "queued"|"running"}`, the full answer once `succeeded` (kept 24
h, then `result_expired: true`), or `{"status": "failed"|"expired", "outcome":
…}`. Only your product can see its requests.

**Retrying.** Same `idempotency_key` and same request → the same execution: its
result (`replayed: true`), `409 ai_request_conflict` while it is still
running, or its failure. Same key, different request → `409`.

## 4. Index knowledge

```
POST /api/v1/integrations/rag/documents   organization_id=...  document={...}
```

```json
{"source": "help_center", "document_id": "article-42", "version": "7",
 "title": "Delivery", "url": "https://help.example.com/42",
 "access_tags": ["team:support"], "content": "…plain text, ≤ 200,000 chars…"}
```

- `202 {"status": "accepted"}` (queued), `200 {"status": "duplicate"}`, or
  `409 knowledge_conflict` (same version label, different content).
- **Send a new `version` label whenever the content changes.** The previous
  version serves until the new one is indexed.
- Send plain text. Split anything larger than the limit into several
  documents.
- **`access_tags`** decide who may retrieve a document: a tagged document is
  returned only to a query presenting one of its tags. Derive tags from *your*
  authorization of *your* user (e.g. `team:support`, `role:manager`); an
  untagged document is visible to every query your product makes in that
  organization.
- Secrets are removed before storage; don't rely on it — don't send them.
- Indexing spends the organization's AI budget (embeddings).

Status: `POST …/rag/documents/status` (`source`, `document_id`) →
`status`, `latest_version_status`, `indexed_version`, `stale`, `last_error`.
Delete: `POST …/rag/documents/delete` (purged at once). Revoke a whole source:
`POST …/rag/sources/revoke` (`source`). **When content is deleted or access is
removed in your product, call these** — the platform cannot see your database.

## 5. Retrieve without generating

```
POST /api/v1/integrations/rag/retrieve   organization_id=...  query={...}
```

`{"query": "…", "sources": ["help_center"], "top_k": 5, "access_tags": [...]}` →
`{"request_id", "results": [{ref, source, document_id, version, chunk, title,
url, text, score}], "usage"}`. `409 index_not_ready` if nothing in the sources is
indexed yet (no charge); `404 knowledge_not_found` for a source that is not
yours.

## 6. Analytics

1. **Publish events** exactly as in Phase 6 (`/integrations/events`), declared
   in your manifest. Nothing new to send.
2. **Register a metric** over one of your events:
   `POST /api/v1/integrations/analytics/metrics  metric={...}` — definition
   in [07-analytics.md](07-analytics.md) §2. Dimensions are enumerations: list
   the values you will send; anything else is counted as `other`. Money is
   integer minor units plus a currency field. A metric counts events routed
   after it is registered.
3. **Query:**
   `POST /api/v1/integrations/analytics/query  organization_id=...  query={...}`

```json
{"metric": "product.crm.deal_value", "version": 1,
 "start": "2026-09-01T00:00:00Z", "end": "2026-09-25T00:00:00Z",
 "granularity": "day", "group_by": ["stage"]}
```

→ `{"metric": {...}, "granularity", "start", "end", "group_by", "results":
[{"bucket", "dimensions", "count", "value" | "amount_minor"+"currency"}],
"freshness": {"pending_facts", "oldest_pending_since", "last_projected_at",
"up_to_date"}}`. Buckets are UTC. Share a metric with another product by
listing it in `shared_with` (a new version).

**Corrections** are new events with negative measures, never edits.

## 7. Limits you will meet

| Limit | Value |
|---|---|
| AI calls | 1,200/min per product; 300/min per organization; 120/min per skill and organization |
| in-flight AI per organization | 8 (all products together) |
| document | 200,000 chars; 256 chunks; 50,000 documents per organization |
| retrieval | `top_k` ≤ 20; ≤ 20 sources; query ≤ 2,000 chars |
| analytics query | hourly ≤ 7 days, daily ≤ 400 days, monthly ≤ ~3 years; ≤ 2 groupings; ≤ 5,000 rows |
| registry | 200 skills and 200 metric versions per product; 100 versions per skill |

## 8. Testing locally

`backend/tests/factories/reference_product_client.py` is a working reference
client (discovery, authentication, every endpoint above), and
`backend/tests/factories/fake_ai_provider.py` a loopback provider — the
acceptance tests `tests/phase8/test_ai_acceptance.py` and
`test_analytics_acceptance.py` show the whole flow end to end.

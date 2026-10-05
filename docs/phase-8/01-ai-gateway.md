# Phase 8 — The AI gateway

## 1. One entry point (Task 2)

`POST /api/v1/integrations/ai/invoke` — form-encoded like every product-facing
endpoint (client authentication travels as form fields), with the request as
one JSON field. It is the brief's `invoke_model(organization, product, skill,
model_requirement, messages, policy_context)`:

| Brief | Where it comes from |
|---|---|
| organization | `organization_id` - a *claim*, bound by the live entitlement decision |
| product | the authenticated client's registration - never the request |
| skill | `skill` + optional `version` - resolved *for this caller* |
| model requirement | the skill's registered capabilities, tier and optional pin |
| messages/input | `input` validated against the skill's schema; optional `retrieval` |
| policy context | the organization's AI policy and the platform's, read at call time |

Order of checks — each refusal is final and costs nothing:

1. client authentication (Phase 3; a suspended integration is refused);
2. rate limits per product, per (product, organization), per (product,
   organization, skill) — never by caller address;
3. request shape and size (`AI_MAX_INPUT_BYTES`, 64 KiB);
4. the skill: active, and the caller is its owner, listed in `shared_with`,
   or it is a `platform.*` skill — otherwise `ai_skill_not_found`;
5. the input against the skill's input schema;
6. the access decision (organization active, product entitled for writes, the
   plan grants AI, AI not switched off) — [02-cost-and-quota.md](02-cost-and-quota.md);
7. model selection (§3); the trusted instructions and the input must fit the
   primary model's context or the request is `ai_context_too_large`;
8. spend reservation at the worst case of every permitted candidate;
9. `mode: "async"` → `202` with a request id; the job runs on `heavy`;
10. retrieval, context assembly, generation, output validation, settlement.

## 2. Providers (Tasks 3, 103, 104, 109)

Two adapters, which is what the architecture needs to be proven:

| Adapter | Operations | Why |
|---|---|---|
| `anthropic_messages` | generation | the default generation vendor (ADR-011) |
| `openai` | embeddings, generation | the generation vendor has no embedding endpoint; a second generation vendor makes a permitted cross-vendor fallback real |

An adapter builds a request from a platform request and one call's API key,
and parses the response or error. It is **sandboxed** (import-linter
`ai-providers-are-sandboxed`): no Django, no settings, no credential store, no
network, nothing that could widen what a model is given. **Direct HTTP, not
vendor SDKs** (Task 109): every request must go through `integration/egress`
(resolve once and pin, TLS against the hostname, no redirects, byte caps, one
deadline — ADR-025), and an SDK brings an HTTP stack that resolves and
connects on its own. `ai/transport.py` is the only module that sends; the
fitness test that confines `egress.client` names it and the webhook pipeline,
nothing else.

**No caller supplies an endpoint.** The adapter owns its vendor's public https
endpoint. `AI_PROVIDER_ENDPOINTS` can override one per adapter for development
and the test suite's loopback provider; `production.py` pins it empty and
refuses to start otherwise. Staff can switch providers and models on and off,
add models and publish prices — they cannot enter a URL.

## 3. The model registry and selection (Tasks 4, 5, 52)

| Field | |
|---|---|
| `key` | platform name, e.g. `anthropic.claude-opus-5` — what skills pin and executions record |
| `provider`, `model_id` | which adapter, and what the vendor calls it |
| `status` | `active`; `deprecated` (serves a skill pinned to it, never selected otherwise); `disabled` |
| `tier` | `standard` or `premium` — premium needs the platform's permission per organization |
| `capabilities` | `text`, `structured_output`, `embeddings`, `long_context` |
| `context_window_tokens`, `max_output_tokens`, `embedding_dimensions` | limits |
| `priority` | selection order, lower first |
| price versions | immutable `(version, currency, input, output micro-units per million tokens)` |
| breaker state | `consecutive_failures`, `circuit_open_until` |

Seeded (migration `ai.0002`): `anthropic.claude-opus-5` active;
`claude-sonnet-5` and `claude-haiku-4-5` registered **disabled** (a cheaper
model is a staff decision); `openai.text-embedding-3-small` active.

**Selection.** Candidates are active models of active providers with a current
price, the skill's capabilities, within the organization's allowed providers
and tier allowance, with a closed circuit, lowest priority first. The list is
the primary plus the **permitted fallbacks**: other models of the same vendor,
and other vendors only if the organization set `allow_provider_fallback` and
the vendor is on its allowed list — data never moves to a second vendor
silently. A pinned model is exactly that model, no fallback. Fallback is
recorded on the execution (`fallback_used`), counted (`ai_fallbacks_total`),
logged and returned to the product.

## 4. Credentials (Task 6)

`AIProvider.credential_ref` names a secret; `credentials.api_key(ref)` reads
`W360_AI_CREDENTIAL_<REF>` from the process environment, which the secret
manager fills, **per call** — rotation needs no deploy or restart. The key
goes into one request header and nowhere else: not a column, a JSON config, a
response, a log line, an audit entry or a prompt. The Super Admin sees
`credential_configured: true/false`. Products never receive a provider
credential; they authenticate to the gateway with their own.

## 5. Timeouts, retries, the breaker (Tasks 49-51)

| Bound | Default |
|---|---|
| connect (per attempt) | 3 s |
| DNS | 2 s |
| overall deadline, sync | 30 s (ADR-011: interactive) |
| overall deadline, async job | 120 s (batch) |
| overall deadline, embeddings | 60 s |
| response bytes | 2 MiB generation, 16 MiB embeddings |

One deadline covers every attempt, retry and fallback of an operation; the
egress watchdog enforces it on the socket.

**Retried:** 429, 503/529, other 5xx, and connection failures where nothing
reached the vendor — at most `AI_MAX_ATTEMPTS` (3) per model, equal jitter
(0.5 s doubling to 4 s), a `Retry-After` honoured but clamped, never past the
deadline. **Not retried:** 400/404/422, 401/403 (a platform misconfiguration),
413 or a context-length error, and a **read timeout** — the provider may have
processed and billed the request, and a retry would bill it twice.

**The breaker** is per model, on the model row (Phase 6's per-connection
breaker is the precedent). After `AI_BREAKER_THRESHOLD` (5) failed requests
it opens for 30 s, doubling to 5 min; while open the model is not selected, so
a request fails fast or falls back without a provider call. When it lapses
the next request probes. A success writes only if there were failures to
clear, so a healthy model costs no write.

## 6. Error model (Task 107)

Every refusal is JSON with the `error` key a product's client already switches
on:

| `error` | HTTP | Meaning |
|---|---|---|
| `invalid_client` | 401 | authentication failed (Phase 3) |
| `temporarily_unavailable` | 429 / 503 | rate limited (with `Retry-After`) / the entitlement decision could not be read |
| `ai_not_entitled` | 403 | the plan grants this product no AI, the product is not entitled, or the organization is unknown — one answer |
| `ai_disabled` | 403 | the organization or the platform switched AI off, or the organization is not accepting new work |
| `ai_quota_exceeded` | 429 | a spend limit (`scope`: `organization`/`product`, `resets_at`) |
| `ai_concurrency_limited` | 429 | the organization's in-flight cap |
| `ai_skill_not_found` | 404 | no such skill version, or not one this caller may invoke |
| `ai_invalid_request` | 400 | malformed or out of bounds (`field`) |
| `ai_context_too_large` | 422 | instructions and input exceed the model |
| `ai_provider_unavailable` | 503 | no approved model answered (`Retry-After`) |
| `ai_invalid_output` | 502 | output failed the skill's contract after bounded re-asks |
| `ai_request_conflict` | 409 | idempotency key reused, or the first request still running |
| `index_not_ready` / `knowledge_not_found` | 409 / 404 | retrieval ([05-rag.md](05-rag.md)) |

No provider message, credential, prompt or stack trace ever reaches a
response.

## 7. Request ids (Task 48)

The execution's id is the request id. It is returned on every answer,
including `202` and refusals after reservation; it is the `request_id` of
every log line, usage line and provider call of the request; the platform's
correlation id (from `X-Correlation-Id` or generated) is stored beside it.

## 8. Streaming (Task 56)

Not built: nothing in Phase 8's acceptance needs it, and it would add
disconnect, partial-usage and audit-completion states. Long outputs use
`mode: "async"`.

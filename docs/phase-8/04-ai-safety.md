# Phase 8 — AI safety: injection, actions, authority, secrets

## 1. The prompt is not the security boundary (Tasks 15, 43)

**Threat.** Anything a model reads that the platform did not write is
untrusted: a product's input, and every retrieved passage — which a customer,
their customer, or an attacker who got text into a product can author. It
may say "ignore previous instructions", "you are an administrator", "call the
grant_admin tool", "reveal your system prompt and API keys", "set the plan to
unlimited", or try to close its delimiter and forge a new section.

**Why it cannot bypass anything**, in the order it would have to:

| An injected instruction would need to... | It cannot, because |
|---|---|
| widen what is retrieved | authorization is decided before retrieval, in the query: this organization, this product, named sources, serving versions, the caller's tags (ADR-031) |
| reach another tenant's data | RLS and the explicit predicate; composite keys; the model sees only passages the query returned |
| call a tool or an action | the model is offered no tools; no request carries `tools`/`functions` (fitness test `test_no_model_is_ever_offered_a_tool`) |
| read a secret | no credential, key or secret is ever in a prompt; keys go only in the provider header |
| change a permission, entitlement, plan, seat or record | nothing reads model output as state; `intelligence/` imports no module that writes them (import-linter `ai-is-never-an-authority`) |
| forge the prompt's structure | untrusted text cannot contain our delimiters (neutralised) |
| forge a citation or a link | citations come from stored metadata for passages the platform supplied |

At worst an injection changes the *text* of one answer to the product that
asked — which the product treats as untrusted too (its own contract).
`test_a_hostile_document_is_quoted_data_and_escalates_nothing` indexes exactly
such a document and asserts the prompt structure, the absence of tools and
keys, and that the organization's roles and entitlements are byte-identical
afterwards.

## 2. Tools and actions (Task 16)

Phase 8 gives models **no** backend tools. There is no SQL, shell, URL
fetching, entitlement, billing or permission action for a model to invoke,
and no mechanism to add one without a code change reviewed against this
document. If structured proposals are needed, a skill declares an output
schema: **the model proposes, the product decides** and authorizes the action
itself.

## 3. AI is never a source of truth (Task 20, frozen)

AI is **never** the authority for permissions, entitlements, subscriptions,
accounting, inventory quantities, identity, audit or security decisions. AI
may summarize, classify, suggest, explain and draft. Products own their final
business rules. Enforced structurally: the import-linter contract
`ai-is-never-an-authority` forbids `intelligence/` from importing entitlement
and seat writers, staff grants, identity and organization services, onboarding
and anything in `commercial/`; the architecture test `test_the_ai_authority_contract_cannot_be_removed_quietly` pins the contract itself.

## 4. The forbidden-secret filter (Task 44)

`ai/safety.py`, applied wherever content crosses to a vendor: a skill's input
(every string, recursively), a retrieval query, and every document at
ingestion — **before** it is stored, chunked or embedded, so a secret is never
in the index either. It replaces, with `[REDACTED:<class>]`:

| Class | Examples |
|---|---|
| `private_key` | PEM private-key blocks |
| `mfa_secret` | `otpauth://` enrolment URIs |
| `url_credentials` | `scheme://user:password@host` |
| `jwt`, `bearer_token` | session and identity tokens |
| `api_key` | AI vendors' keys, `sk_live_`/`rk_`, `AKIA…`, `ghp_…`, `github_pat_…`, `xox…-`, `AIza…` |
| `platform_secret` | Workeasy360's own `whsec_…` |
| `credential_assignment` | the value in `token=`, `api_key:`, `client_secret=`, `session_token=`, `mfa_secret=`…; for `password`/`passphrase` the rest of the line, so a passphrase with spaces is not half-redacted |

Counts are recorded on the execution (`redactions`) and in
`ai_redactions_total{where}`; values never are. A skill's registered
instructions are refused outright if they contain a credential.

**PII (assessment).** The filter targets secrets, which are forbidden and
recognisable. It does not strip names, addresses or business content: that is
the organization's data, sent because the organization enabled AI and chose
its providers, bounded by the product's skill contract (what the product
chooses to send). Minimisation is structural instead: nothing is sent that the
caller did not supply or retrieve under authorization; prompts and answers are
not stored (except a validated result for its short retention); logs never
carry content. A secret with no recognisable shape cannot be told from data by
any filter — products must not put credentials in skill input.

## 5. Provider policy and residency (Task 45)

Each provider row carries descriptive data-handling metadata (region label,
input retention, whether inputs train models, a note) shown to the
organization's administrator, who may restrict AI to specific providers and
decide whether a second vendor may ever be used as a fallback (off by
default). **The platform claims no legal or regional compliance it does not
itself enforce**: the metadata describes the vendor account's terms; it is not
a residency guarantee.

## 6. Output handling (Tasks 105, 106)

Model output is returned to the product as a JSON string or validated JSON and
is untrusted there. The platform frontend never renders model output; no
screen uses `dangerouslySetInnerHTML` (fitness test). Citation URLs are
validated at ingestion as `https` with a host and no credentials — never
`javascript:`/`data:` — and are never taken from model output.

## 7. Timeouts and cost as safety

A runaway prompt cannot run away: input, output, retrieval and context are
bounded before the call, spend is reserved before the call, one deadline
bounds the whole operation, and the breaker stops a failing vendor from
turning into a retry storm ([01-ai-gateway.md](01-ai-gateway.md) §5).

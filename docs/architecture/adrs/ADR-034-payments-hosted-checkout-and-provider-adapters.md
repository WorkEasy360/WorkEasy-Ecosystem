# ADR-034: Payments are hosted checkout behind sandboxed provider adapters; the webhook is the only settlement signal

**Status:** Accepted
**Date:** 2026-09-25
**Deciders:** Architecture, Security, Commercial
**Depends on:** [ADR-024](ADR-024-integration-platform-data-model-and-due-work.md), [ADR-025](ADR-025-outbound-http-and-ssrf-boundary.md), [ADR-030](ADR-030-ai-gateway-and-provider-ownership.md)

---

## Context

Subscriptions existed with prices, billing accounts and a lifecycle
(`trialing`, `active`, `past_due`, `grace`, `suspended`, ...), but nothing
issued an invoice or took money. The request: payments as a shared platform
capability, through provider adapters, never storing raw card data, secrets
only behind the secret boundary, every payment operation dangerous and
audited, and no duplicate billing system.

## Decision

**A new module, `commercial/payments`, beside `provisioning`.** It reads the
catalog, pricing, billing and subscription modules through their services and
selectors and moves a subscription only through `subscriptions/system.py`. It
owns invoices, invoice lines, payment attempts, refunds, providers and the
webhook log - nothing else.

**Invoices come from what was agreed.** One invoice per (subscription,
period), built from the price snapshots already on the subscription items -
never a live price. Zero totals are not invoiced; partner-billed subscriptions
are the partner's to bill. Invoices are issued by the commercial-change
listener and repaired by the payments clock, so a failure to invoice never
fails the purchase that caused it.

**Hosted checkout only.** A billing manager (`platform.billing.manage`,
dangerous) starts a payment; the platform creates a `PaymentAttempt` and a
due-work timer in the request transaction and returns 202. A task makes the
provider call **outside any transaction**, stores the hosted-page URL, and the
browser (polling the attempt) is sent to the provider. Card numbers, CVV and
bank details never reach Workeasy360. One attempt is in flight per invoice;
an `Idempotency-Key` makes the start retry-safe.

**Adapters are sandboxed like AI providers.** `payments/providers/{stripe,
razorpay,fake}` build an HTTP call, parse a response and verify a webhook -
they import nothing but `contracts` and their own contract (import-linter
`payment-providers-are-sandboxed`). `payments/transport.py` is the only
sender, through `integration/egress`. A provider row stores *references*
(`credential_ref`, `webhook_secret_ref`); the secrets are environment
variables the secret manager fills, read per call, never in a column, JSON,
log, audit entry or response. The `fake` adapter exists for development and
tests and is pinned off in production, as are endpoint overrides.

**The webhook is the only settlement signal.** A redirect back from the
provider proves nothing. A webhook is size-limited, verified against the
provider's signature scheme (Stripe `t=,v1=` HMAC with a 300 s tolerance;
Razorpay hex HMAC), de-duplicated by `(provider, event id)` in a control-plane
log that holds identifiers and amounts - never the payload - and applied in the
organization named by the metadata *we* attached at checkout, looking the
attempt up inside that organization. Settlement locks the attempt, then the
invoice. An amount or currency that does not match is recorded and never
settles. Delivery order does not matter.

**Dunning uses the existing lifecycle.** The clock moves an unpaid, overdue
subscription to `past_due`, then `grace` after `PAYMENT_GRACE_AFTER_DAYS`,
then `suspended` after `PAYMENT_SUSPEND_AFTER_DAYS`, noting that the
suspension was dunning's. A settled payment restores the subscription only
when no other invoice is overdue, and lifts **only** a suspension dunning
caused - never one staff applied.

**Refunds never exceed what was captured** and are idempotent per request.
Where the provider does not honour idempotency keys (Razorpay), a submission
whose outcome is unknown is parked as `needs_review` rather than re-sent.

**Staff operations are dangerous and reasoned.** `platform.staff_payment.manage`
(AAL 2) configures providers, refunds, marks an invoice paid with a reference,
or voids it - each with a reason and the invoice's version, each audited.

## Consequences

- Payment is customer-initiated each period (a hosted page per invoice); there
  is no stored payment method or off-session renewal charge yet (debt D-028).
- No proration and no tax calculation (debt D-027, D-029).
- A cross-tenant invoice query is forbidden, so staff collection totals come
  from the control-plane webhook log, where manual settlements also write a
  row.
- Adding a provider is an adapter and a registration - no model change.

## Rejected alternatives

- **Card fields in our own UI (tokenised or not)** - brings Workeasy360 into
  PCI DSS scope beyond SAQ A for no product need.
- **Settling on the browser's return URL** - forgeable; a lost redirect would
  also lose a real payment.
- **Calling the provider inside the request transaction** - holds row locks
  across a network call and makes a timeout indistinguishable from a failure.
- **A payment table per provider** - the contract types normalise what every
  provider returns; provider-specific data stays in the adapter.

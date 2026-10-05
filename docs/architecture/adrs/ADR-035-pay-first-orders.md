# ADR-035: A self-serve purchase is a pay-first Order, paid through its invoice; nothing is granted before the money arrives

**Status:** Accepted
**Date:** 2026-09-26
**Deciders:** Architecture, Commercial, Security
**Depends on:** [ADR-005](ADR-005-product-entitlement-model.md), [ADR-034](ADR-034-payments-hosted-checkout-and-provider-adapters.md)

---

## Context

Until Phase 9 a customer's purchase (`POST subscriptions/purchase`) created an **active** subscription line immediately. Provisioning then wrote the entitlement, and the invoice for the period was issued afterwards. Unpaid automatic invoices were due at once, but `past_due` restricts nothing, so a customer could use a paid product for 21 days before dunning suspended it. Suspension is read-only, not lockout. A trial could be "activated" without any payment.

The brief requires the opposite order:

    payment confirmed -> order paid -> subscription active -> entitlement active

It also requires orders as a customer-visible concept, and no trust in any price, plan or status sent by the browser.

## Decision

**An `Order` in `commercial/payments`.** It is tenant-scoped, with RLS and composite foreign keys.

- **Placing an order:**
  1. Validates the request against what Explore lists: public, available plans and bundles of listed products. A guessed id of a private offer is refused.
  2. Resolves the price on the server.
  3. Freezes the price in the order.
  4. Issues the order's **own invoice**.
- **Nothing else is written.** No subscription line, no entitlement.
- **Payment reuses ADR-034 unchanged.** The order's invoice is paid through the same attempt, hosted checkout, signed webhook and settlement path. There is no second payment system.

**Settlement marks the order paid and fulfilment runs after commit.**

- Fulfilment is actor-less, in `subscriptions/system.py`, the only system writer of subscriptions. It adds the line **at the frozen price**.
- A new subscription starts its first period at fulfilment. The order's invoice then takes that period, so the period invoicing finds it invoiced and paid, and never bills it twice.
- A line added to a running subscription pays the **prorated remainder** of the current period. It is invoiced in full from the next period.

**What owes nothing now is fulfilled at once and still recorded as an order:**
- a trial, or a free-trial offer;
- a line joining a trial;
- a zero price;
- an account whose billing terms (set only by staff or a partner) are `invoice` or `partner_billed`.

**Invariants, each enforced by the database or tested with real concurrent commits:**

- **At most one order awaits payment per organization**, enforced by a partial unique index. Two orders for one product, or two payments for one thing, cannot be represented.
- **The `Idempotency-Key` covers every argument.** A replayed key returns the same order; a changed request conflicts.
- **Lock order is invoice, then order**, in settlement, cancellation, expiry, fulfilment and staff void. None of them can deadlock another.
- **Fulfilment is durable.**
  - It runs after commit.
  - The payments clock re-drives any order that has been paid but not fulfilled for more than a minute.
  - It runs once, because the order's status is re-checked under its lock.
- **Paid but impossible** (for example the product was acquired another way meanwhile): the order is `failed` and its payment is **refunded automatically**.
- **Expiry and late money.** An unpaid order expires after `ORDER_TTL_HOURS` and its invoice is voided. Money that arrives for a void or already-paid invoice is refunded automatically.
- **A missing provider amount counts as a mismatch** and never settles.
- **Trial conversion** is an `activation` order. Payment starts the first paid period.

**Staff visibility.** Staff see orders across customers through `payments.OrderSummary`. It is a control-plane projection of the same class as `provisioning.CustomerSummary`: written in the order's own transaction, holding identifiers, codes and amounts only. It is debt D-031.

**The customer's direct purchase and activate routes are removed.** Staff and partner paths keep calling the subscription services directly: they are sales-led, and they authorise and audit on their own.

**Billing terms are the platform's.** A customer opening a billing account chooses its currency and billing address only. `collection_mode` (`partner_billed` meant "never invoiced") and `billing_region` (it picks the price book) are refused from a member and set by staff through `billing-terms`, which is dangerous and audited.

## Alternatives considered

- **Subscription status `incomplete` (Stripe's model).**
  - This handles a *first* purchase.
  - It cannot handle a product added to a running subscription, because the line would need its own invoice outside the period invoicing.
  - Rejected: orders cover both with one mechanism.
- **Keep grant-then-bill and tighten dunning.**
  - Rejected: the brief's order of events is explicit.
  - The revenue leak and the "paid nothing, used 21 days" window are structural, not a tuning problem.
- **An Order that is a control-plane table.**
  - This would avoid the projection.
  - Rejected: customers read their own orders, and every customer-readable table in this platform has RLS behind its selector.

## Consequences

- A customer's first access waits for the payment provider's signed confirmation, usually seconds.
- The clock re-drives a lost fulfilment within about five minutes.
- A mid-period addition is prorated. D-027 is narrowed: downgrades and cancellations still carry no credit.
- The dangerous-permission set changed by owner decision. `platform.billing.manage` (paying an invoice on the provider's hosted page) no longer needs a second factor. Refunds, void, mark-paid and provider configuration still do.

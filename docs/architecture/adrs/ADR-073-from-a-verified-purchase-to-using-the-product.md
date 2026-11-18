# ADR-073: From a verified purchase to using the product

- Status: proposed (2026-11-19). Branch `feat/purchase-to-access`, stacked on PR #9 (`4b183c6`). Not deployed.
- Context: the platform capability audit of 19 November 2026 (`review/PLATFORM-CAPABILITY-AUDIT.md` in the Accounts workspace), section 3, gaps G2, G3, G7, G8, G10, G12 and G13. The journey it measures: create an account, create an organization, buy CRM, open CRM, buy EasyBook, open EasyBook, switch products without a password, sign out everywhere.
- Builds on: ADR-043 (hosts and the hand-off), ADR-052 (orders and the cart), ADR-065 (the Store's own host), ADR-069 (back-channel logout), ADR-071 (Accounts hosts identity).
- Amends: ADR-065 - **while `W360_ACCOUNTS_IDENTITY` is on**, the Store signs in through Accounts instead of on its own host. ADR-069 - its logout outbox now also carries access endings, not only sign-in endings.

## Context

A verified, webhook-confirmed payment already gave the organization its entitlement. Between that and the buyer using the product, the audit found:

- **G2.** A per-seat product bought by a paid order gave the organization seats, and nobody held one. The buyer's own launch answered `no_seat` until someone (usually the buyer) assigned a seat by hand. A trial already seated its starter (`trials._seat_the_starter`).
- **G3.** The Store signed its customers in on its own host only. After buying there, the first product opened asked for the password again on Accounts.
- **G7.** Cancelling was of the whole subscription. There was no way to stop one product (EasyBook) and keep another (CRM).
- **G8.** A refund was money only. Ending the refunded access needed a separate staff action.
- **G10.** A removed member, a seat taken back, or an organization whose entitlement ended kept a live product session until the product's next entitlement re-check (up to 15 minutes). ADR-069 sent logouts only when a sign-in ended.
- **G12.** Changing the seat quantity was staff or partner only.
- **G13.** A paid product that ended dropped out of My Products and read as one never held ("Explore products"). Only an expired trial showed as expired.

## Decision

### 1. The buyer is seated (G2)

When a paid order is fulfilled (`orders.fulfil`, which every settled payment reaches: webhook, wallet, staff mark-paid), the member who placed it (`Order.placed_by_user_id`) is given a seat in each **per-seat** product it bought (`orders.seat_the_buyer`). It runs after the order's transaction commits and provisioning has granted the capacity (the change listener is synchronous), exactly where a trial seats its starter.

- **Never beyond the pool.** `seats.assign_seat` locks every grant of the pool and counts under the lock. A full pool leaves the order fulfilled and the seat for an administrator.
- **Idempotent.** A seat the member already holds is kept; `assign_seat` returns an existing assignment. Fulfilment itself runs once per order (status re-checked under the order lock), and a re-run of the seating takes nothing.
- **Only a current member.** The membership is read and held `FOR NO KEY UPDATE` (`active_membership_exists(lock=True)`) for the seating's transaction, so a concurrent removal waits and then releases what was written. A removed or suspended buyer gets nothing.
- **Per-seat only**, by the catalog's licensing mode (the same port sign-in reads). An organization-wide product needs no seat.
- **Best effort**, like the trial's: a failure is logged and counted (`commercial_order_buyer_seats_total`), never raised. Audited as the system's doing (`commercial.order.buyer_seat_assigned`).

Zero-amount orders (a free plan, a plan-level trial through the order path) are unchanged: they are not paid orders. Product trials already seat their starter.

### 2. The Store signs in through Accounts (G3)

While `W360_ACCOUNTS_IDENTITY` is on **and** the Store has its own host:

- The Store's `/login` (with no `mode`, or `mode=user`) starts the ADR-071 hand-off. The proxy sets the Store's own host-only binding cookie and redirects to `accounts./login?mode=store&next=<Store page>&state=<binding>`. `next` is kept only when it is a Store page (`safeNext("store")`, and again on the server).
- On Accounts, after the password, or at once if Accounts is already signed in, `POST /api/v1/auth/handoff {mode: "store"}` issues a one-time code for the Store's `/auth/continue`. The Store spends it there (`/api/v1/auth/handoff/redeem`, now also served on the Store's host) for its own host-only session, carrying the Accounts sign-in's id.
- The Store's other identity pages (`/signup`, `/verify-email`, `/forgot-password`, `/reset-password`, `/confirm-email`, `/r`) move to the same address on Accounts with a 302, as the customer portal's did under ADR-071. Emailed identity links for the Store name Accounts (`identity_url("store")`).
- The checkout's sign-in and sign-up steps become one button each (to the Store's `/login` or `/signup`). The buyer comes back to the same Store page: the hand-off's `next`, or, after a sign-up, the Store page remembered on Accounts and reached through the Store's `/login`.

What is preserved:

- **The checkout and `next=`.** The buyer returns to the checkout with what they chose.
- **Customer records.** The same users, organizations and memberships.
- **The Store's own session for its API calls.** The Store still has its own host-only session, so its checkout, Recharge and order endpoints are unchanged. "My Account" still passes that sign-in to User Admin (ADR-065).

After a Store sign-in the Accounts session exists, so the first product opened is passwordless. Signing out of the Store ends the Accounts sign-in too, because they share a sign-in id.

With the switch off (production today), nothing changes: `mode=store` is refused (`handoff_invalid`), the redeem endpoint answers `404 not_found` on the Store's host, exactly as the surface rule did before, and the Store keeps its own forms and links.

### 3. Ending one product (G7)

`subscriptions.end_product` ends one product line. It uses the mechanics a period-end downgrade already uses: the line's `removed_at` is set to the end of the period the customer paid for. Provisioning withdraws the product then, and the next period's invoice no longer includes it. The subscription and every other product stay.

- **Customer route:** `POST /api/v1/orgs/<slug>/subscription-items/<id>/end` with `Idempotency-Key`. It is permission-checked like the subscription cancel (`commercial.subscription.manage`, and not for a subscription a partner pays from its wallet). It always takes effect at the period end.
- **Staff route:** `POST /api/v1/admin/customers/<org>/items/<id>/end {reason, immediate}`. It needs `platform.staff_subscription.manage` and a reason, and it is audited in both logs. `immediate` ends the line now (the bounded override, as for cancelling a subscription).
- **What ends with it.** A scheduled change of the product (a successor line opening at the period end) ends with it, clamped to a zero-width window that grants nothing. A bundle line ends with every product it includes.
- **What is refused.** A product inside a bundle (`bundle_component`). The subscription's last product (`last_product`): that is cancelling the subscription.
- **No proration or refund.** The current period stays paid for and usable.
- **Concurrency.** The subscription row is locked before the lines are read, so simultaneous requests end the line once (one audit entry).

### 4. A refund may end access, when staff say so (G8)

The staff refund command takes `end_access: "keep" | "period_end" | "now"`. The default `keep` is today's behaviour: a refund is money only.

- Any other value also needs `platform.staff_subscription.manage`.
- It ends the subscription lines the refunded payment paid for (the invoice's charge lines), through `end_product`, in the refund's own transaction.
- If a line is the subscription's last product, the subscription is cancelled instead (`period_end` or now).
- It is keyed on the refund, so a replayed refund ends nothing twice. It is audited (`billing.refund_access_ended`) with the refund's reason.
- If the payment paid for no product line (a wallet top-up), access cannot be ended from the refund and the request is refused.
- Refund amounts, provider calls and confirmation are unchanged.

### 5. Access loss reaches products at once (G10)

`contracts.ports.sign_ins` gains a second listener, `notify_access_ended(user_ids, product_codes, reason)`, implemented by the ecosystem (`backchannel.record_access_ended`). The ADR-069 sign-in endings are untouched. The reasons are:

| Reason | When |
|---|---|
| `membership_removed` | A member is removed, for each product they could open through the organization |
| `membership_suspended` | A member is suspended, for the same products |
| `seat_released` | An administrator takes a per-seat product's seat back |
| `entitlement_ended` | A reconciliation finds the organization no longer holds a product: expiry, cancellation, a line ended, a trial over, a grant revoked |

For `entitlement_ended`, the people told are those holding a seat (per-seat) or every active member (organization-wide). "Could open" means: every product the organization holds, less the per-seat ones the member holds no seat in.

**Same outbox, same transaction.** For each (person, product) with a live `ProductSignIn` (products that take back-channel logout), one `BackchannelLogout` row is queued. It names `sub` only, and the sign-in rows are ended, in the transaction that took the access away. For a reconciliation, that is the entitlement write itself: `apply_entitlements(on_products_lost=...)` compares the snapshot row it is about to replace with the new one, under the organization's lock, and calls back inside its transaction. The worker delivers it as for any ADR-069 logout.

- **A reconciliation that loses nothing** costs no query: the comparison is against the snapshot row the write reads anyway. Two racing passes queue each logout once, because the second sees the snapshot the first wrote.
- **A failure to queue** is logged and counted (`provisioning_access_ended_total`). It never rolls the entitlement change back: the product then finds out at its next re-check, as before.

**Accepted consequence.** A logout token names a person, not an organization. A product told this ends that person's sessions **in that product for every organization**, including one where they still hold it. That is safe: their Workeasy360 sign-in is untouched, so the next sign-in is passwordless and is checked again for the organization it names. Products that do not take back-channel logout still rely on the entitlement re-check.

A time-based end (a trial's or a period's end) is denied by the gate at that moment by server time. The logout goes when the sweep or a reconciliation records it.

### 6. My Products shows an expired paid product (G13)

The app directory card gains `lapsed: {ended_at, plan} | null`. It covers a product the organization **paid** for whose access ended in the last 90 days and that it no longer holds. The line was removed, or its subscription was cancelled or expired, after any trial it had. `plan` is shown only to a member who may see subscriptions.

My Products lists such a product under Expired, labelled "Expired" (not "Trial expired"), with **Renew / Buy again** to the product's Store page instead of "Explore products". A plan-level trial that simply ran out stays Explore's "trial expired". The card's `cancel_at` now also shows when a product ended on its own (section 3) will end.

## Not done

- **G12, self-serve seat quantity:** not done. The staff and partner quantity change (`subscriptions.change_quantity`) adds seats **at once**, which is right for a sales-led account or a partner wallet but would give a self-serve customer seats before any payment. Payment activation stays webhook-verified only, so the self-serve form needs an order for the change: quote the prorated difference, pay it, then apply `change_quantity` at fulfilment, as a line added to a running subscription is now. A decrease (at the period end, nothing to pay) could be self-serve on its own.
- **No migration.** Nothing new is stored: the order already names its buyer, `BackchannelLogout.reason` is free text, and the refund's choice is in its audit entry.

## Rejected

- **Making every product organization-wide** to avoid seating the buyer. That changes what customers bought.
- **Issuing the Accounts session from the Store's sign-in form.** That is a second sign-in path and a cross-host cookie. The hand-off already exists and is tested.
- **An organization claim in logout tokens.** Back-Channel Logout 1.0 has none, and products key sessions on `sub` and `sid`.
- **Prorating or refunding when a product ends.** Out of scope. The paid period stays usable.

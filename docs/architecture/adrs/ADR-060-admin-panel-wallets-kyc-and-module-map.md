# ADR-060: Admin Panel: three wallet ledgers, KYC before activation, one module map

Date: 2026-11-09. Status: accepted (built on `feat/admin-panel`, not deployed). Extends ADR-052 (commission rules), ADR-056 (channel pricing) and ADR-057 (partner dashboard).

## Context

The owner's brief asks for a complete Admin Panel at `admin.workeasy360.com`: nine modules, real data, a ledger behind every financial operation and KYC approval before any partner rights. It names two documents as the authority on wallets and product access (`08_Credits_Wallets_System.txt`, `07_Product_Web_Mobile_Flow.txt`). Neither exists on this machine. The owner chose on 2026-11-09 to build from the brief, with every rate blank until an Admin enters it, and to add a public application form.

## Decision

1. **Exactly three balances.** `commercial/wallets` holds `platform`, `ai_credits` and `consumption`. Partners hold `platform` only. A partner commission is an earnings record in `commercial/commissions`, never a fourth wallet, and the partner portal says "Earnings".
2. **A balance is the sum of an append-only ledger.** `WalletEntry` is written only by `wallets.ledger.post`, under a row lock, with a unique idempotency key, a database check that a balance cannot go below zero, and a grant and trigger refusing update or delete. A reversal names the entry it undoes. `reconcile` compares every balance with its ledger.
3. **No rate means no charge.** `UsageRate` and `CreditPack` are data an Admin enters, versioned, never seeded. An event with no rate is recorded as `not_rated`. Too little in a wallet records `failed` and moves nothing. There is no overdraft. Allowances included in a plan come off before any charge.
4. **Top-ups and packs are ordinary orders.** `Order.Kind.WALLET` goes through the normal checkout and invoice, so tax, payment verification, refunds and activation retry apply as they do to any sale. The ledger key is the order, so re-driving fulfilment credits once. A refund of a top-up that has been spent is refused.
5. **Products report usage with their own credential.** `POST /integrations/usage/events` takes the product from the verified client, and only charges an organization entitled to that product. AI tokens are the exception: they are measured by the platform's gateway and charged from the view that returned the answer, because `intelligence` and `commercial` may not import each other.
6. **KYC is separate from account status.** `PartnerKyc` moves through draft, submitted, under review, correction required, approved and rejected. `PartnerStatus` remains active, suspended and so on. The gate sits only on draft to active, behind `PARTNER_KYC_REQUIRED`, which production asserts. Existing active partners stay active with KYC in draft: no approval is invented for them.
7. **Requirements are data.** `KycRequirement` rows are entered by an Admin. Documents are restricted files with an append-only access log, and every decision records reviewer, date and reason.
8. **A public application grants nothing.** `/partner/apply` creates a draft partner and a login. Rights arrive with approval. It answers the same way whether or not the address is registered.
9. **Cross-tenant Admin lists read projections kept by database triggers.** Invoices, payments, refunds and subscriptions are RLS-protected tenant rows, written with both `.save()` and `.update()`. A trigger is the one place that cannot be bypassed, so the summary tables stay true (`platform/tenancy/migrations_helpers.py`, debt register D-019 and D-040).
10. **One module map.** `adminModules.ts` is the single source for the nine-item sidebar, the tabs inside a module and the search palette. Existing admin addresses keep working.
11. **Plan rules take effect.** A line on a plan set to `manual` or `none` ends with the period the customer paid for, read when the period ends, so a change reaches running subscriptions. The customer can buy again afterwards. `auto_renew` is the default and behaves as before. `change_rule` uses the classification the change itself uses, so anything smaller counts as a downgrade.
12. **Pending Actions counts what can be acted on.** An order that could not be fulfilled is refunded automatically, so it waits for no one unless that refund fails. A failed or needs-review refund stays until a later refund of the same payment answers it.

## Consequences

- Nothing bills until an Admin enters rates and packs. That is the requested behaviour, and it also means the wallets are empty on the day of deployment.
- Rules the two missing documents might have decided stay open: credit expiry, minimum top-up, refund to wallet or to source, automatic renewal from a wallet, tax on top-ups and packs, low-balance defaults. See `docs/admin-panel/status.md`.
- No SMS or WhatsApp sender exists in the platform, so those charges come only from products reporting usage.
- Commission accrual keeps running while a partner is suspended (`accrual.py`). Suspension blocks new operations, and whether it also stops earning is the owner's rule, so it is unchanged and listed as a question.
- Partner wallets receive offline top-ups only. Nothing deducts a partner's subscription from them yet.

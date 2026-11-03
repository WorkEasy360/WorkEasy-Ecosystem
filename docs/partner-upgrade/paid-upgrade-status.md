# Paid partner upgrade + one panel (2026-11-03): status

Decision: [ADR-049 addendum](../architecture/adrs/ADR-049-partner-upgrade.md#addendum-2026-11-03-a-paid-upgrade-offline-or-online-and-one-panel). Built and verified locally, **uncommitted, not deployed** (branch `fix/ci-run-1`).

## What it does

1. **Super Admin sets the price.** In **Partner › Upgrade Fees** (also on Product Store › Currency & pricing, at the bottom), set a fee (amount and currency) for becoming a Reseller and for becoming a White-label partner, plus how to pay it offline (bank account, IFSC, UPI). Zero means no fee.
2. **The partner asks.** "Grow Your Partnership" on the Affiliate or Reseller dashboard opens the upgrade wizard (unchanged). On its Review step:
   - The fee is shown with two choices: **Pay online now** (shown only when a payment provider takes the currency) or **I have paid by bank transfer or UPI**.
   - **Offline**: the admin's instructions are shown, and the transaction reference is required. "Send for review" sends the request with that reference.
   - **Online**: "Pay ₹X and send for review" opens the provider's hosted page. Card or UPI details are entered there, never on Workeasy360. Only the provider's signed confirmation marks the fee paid, for the exact amount and currency, once. That confirmation also sends the request automatically. A failed or abandoned payment sends nothing, and the partner can try again or pay offline.
3. **Admins are told.** Every submitted or online-paid request emails each staff member who can approve upgrades. The email says from → to, the fee, and whether it was paid online or offline (with the reference to verify). The existing badge and dashboard count still apply.
4. **The admin approves.** On the partner's Upgrade panel the fee shows as "paid online, confirmed by the payment provider" (a reference is then optional) or "the partner says it paid offline, reference X. Verify the transfer" (pre-filled; required). Approval upgrades the **same account and login**, as before.
5. **One panel afterwards.**
   - **Affiliate → Reseller.** The Reseller panel now also shows an **Affiliate programme** group: Promote Products, Referral Tools, Marketing Hub, Referral Performance. The Affiliate panel is no longer offered as a second workspace to switch to.
   - **Affiliate or Reseller → White-label.** The White-label panel contains everything below it.
   - **Who sees the group.** It is drawn only for a partner holding the referral permission (one that was an Affiliate), so a plain Reseller sees no change.

## Verification

| Check | Result |
|---|---|
| New backend tests `tests/partner_upgrade/test_upgrade_payment.py` | **8 passed**. Covered: price and instructions; offline (reference required, staff emailed, approval needs a reference); free upgrade; online through the real webhook path (the request sends itself, staff emailed, approval without a reference); a failed payment followed by a retry; a short amount (mismatch, nothing paid); a duplicate callback (applied once); only the payer can complete a test checkout; online refused when no provider takes the currency |
| Backend regression | **1,536 passed**: partner_upgrade, ecosystem (fees), extension payments, phase9 orders, store, store_console, affiliate_products, partner_experience/panel/menu/center/setup, architecture, security, portals, payments and notifications unit tests. One architecture pin (the newest partners migration) was updated to record `0015`; it passes |
| `makemigrations --check` | no changes |
| mypy / import-linter | clean / 18 contracts kept |
| Frontend `vitest` | **1,053 / 1,053**. New tests: the Review step (offline reference, online redirect to the provider, the status once sent), the admin fee status (offline pre-filled, online confirmed), the combined Reseller panel (programme drawn, no switcher, promote Store under `promote/`), a plain reseller sees no programme, menu mappings |
| `tsc` (src), eslint | clean |
| Real payment provider | **NOT RUN**: no live Razorpay or Stripe account is configured. The online path was verified with the development test provider, which signs callbacks and uses the same webhook code |

## Owner steps

1. **Dev database: done** 2026-11-03 00:59, with your approval. Backup `workeasy360-dev-backups/20261103-0059-pre-upgrade-payment.dump` in your user folder, then `partners.0015` and `payments.0005` applied; 0 unapplied.
2. **To take money online:** add a live payment provider (Super Admin › Payments) for the fee's currency, with its credentials in the server's secret store. Until then partners see only the offline option.
3. Set the fees and payment instructions in Partner › Upgrade Fees.
4. Commit and deploy when satisfied. The migrations add one table and five columns with defaults, so they are safe with the previous release running.

## Known limits

- **Refunds of an upgrade fee are manual.** A partner who pays online and then withdraws the request keeps the payment on record (staff see it as paid); the provider dashboard is where a refund is made.
- **One fee per target.** Affiliate → White-label and Reseller → White-label cost the same, the White-label fee.
- **Affiliate dashboard figures** (clicks, conversions) are not added to the Reseller dashboard; the affiliate pages themselves are in the panel.

# Affiliate Store Console: status

Brief: *Implement Affiliate Store Console with Admin-Controlled Product Approval* (2026-10-30). Decisions: `docs/architecture/adrs/ADR-055-affiliate-store-console.md`.

## Verdict

**Built and verified locally. Not committed, not deployed** - waiting for the owner's approval of the verification report.

## 1. What already existed (reused, not duplicated)

| Need in the brief | Existing piece reused |
|---|---|
| Single product source | `catalog.Product` + `ProductMedia`; `catalog_selectors.promotable_products` (Store + affiliate channel) |
| Product-specific permission | ADR-054 `PartnerPromotionGrant`, enforced in `referrals.create_referral_code`, re-enable and capture |
| Links, codes, campaigns, materials | ADR-053/054 pages and services - unchanged |
| Attribution, idempotency, ledger | ADR-029/052/054 accrual (first touch, paid-only, one accrual per sale, append-only ledger) - unchanged |
| Admin notification | `notifications.notify_account` + registered templates |
| Request/approve pattern | ADR-049 upgrade requests (lock partner then request, 409 on stale decisions) |
| Starting price, commission rate | `marketplace.public_catalog` (generation-keyed cache), `domain.select_rule` |

## 2. What was added

- **Model** `partners.PartnerPromotionRequest` + migration `partners.0012_promotion_requests` (one new table).
- **Service** `commercial/partners/promotion_requests.py`: submit, approve, reject (reason required), revoke, staff queue/detail, derived per-product status, staff email after commit.
- `promotion.grant_promotion` split into `grant_locked` (shared by approval); `revoke_promotion` takes an optional reason and marks the linked request revoked.
- `authz.staff.holders(permission)`; `commissions.selectors.effective_rates`; `PartnerRequestConflict` (409).
- **API**: `GET partner/<id>/affiliate/store`, `POST partner/<id>/affiliate/product-requests`; `GET admin/partner-product-requests`, `GET .../<id>`, `POST .../<id>/{approve,reject,revoke}`.
- **Affiliate UI**: sidebar "My Products" -> **Store Console** (tabs My Products / Explore Products / My Requests; `/my-products` redirects). Explore: search, category and status filters, status badges, price, commission (only with commission view), Request Access form (prefilled website). My Products: ADR-054 page plus shortcuts to Referral Codes, Campaigns, Marketing Materials, Conversions, Commissions.
- **Admin UI**: Partners -> **Product Requests** (status tabs with counts, search, review panel with partner, product, answers, request and grant history; Approve / Reject with reason / Revoke). Partner page's promotion panel links to it.
- Tests: `backend/tests/store_console` (23), `store-console.test.tsx` (6), `product-requests.test.tsx` (7); pins updated in 4 backend architecture tests and 4 frontend tests (menu label, Partners nav).
- Harness: `scripts/store_console_check/` (rehearsal + browser journeys).

## 3. Security

Server-side, fail-closed: a request grants nothing; links still need the grant. Partner commands use `ctx.partner_id` only (no partner id in the body); other partners get 403/404. Only `platform.staff_partner.manage` decides; viewers and affiliates are refused (tests + browser D). Partner row lock then request row lock; partial unique index on pending; a real two-connection race test makes exactly one grant. Closed field set, https-only website, no partner text in audit entries; the partner never sees which administrator decided. Revocation never touches referrals or ledger rows.

## 4. Not built

- An affiliate withdrawing a pending request.
- A per-user rate limit on requests (one pending request per product bounds volume).

## 5. Verification (2026-10-30)

| Gate | Result |
|---|---|
| New backend tests `tests/store_console` | 23 passed (incl. transaction race test) |
| Related suites (affiliate_products, partner_center, partner_upgrade, ecosystem, architecture, notifications, authz) | 418 passed + 3 model-set pins updated, then 62/62 |
| Full backend suite (bare `pytest`) | 4953 passed, 0 failed (1 h 56 min) |
| ruff / ruff format / import contracts / mypy / bandit / `manage.py check` / `makemigrations --check` | clean / 18 kept / clean / 0 medium-high / clean / no changes |
| Frontend vitest | 988 passed (55 files) |
| eslint, tsc (typegen), `next build`, dynamic-rendering assertion, `npm audit --omit=dev` | clean, clean, built, pass, 0 vulnerabilities |
| Migration rehearsal (`prepare.py`) | PASS - 7 tables identical, table starts empty, reversible |
| Real browser (`browser_check.js`) | PASS - 7/7 journeys, 7 pages axe-clean, 30 viewport checks, 0 CSP/console errors |

# Phase 11 - Four-product ecosystem integration: gap map and design freeze

Started 2026-09-28. Brief: register Easy CRM, EasyBook, Easy Project and Easy Help
Desk; public store, free trial, buy-now, My Products, launch; Super Admin,
reseller, white-label and affiliate on the **existing** architecture.
Workeasy360 stays platform-only: no product-domain code, no second checkout, no
second partner system, no second branding system, no second entitlement writer.

## 0. Discrepancies between the brief and the code (reported, not hidden)

| # | Brief says | The code says | Decision |
|---|---|---|---|
| 1 | Product codes `easy-crm`, `easy-helpdesk`, `easy-project` | `ProductCode` is `^[a-z][a-z0-9_]{1,31}$` (a hyphen is illegal; the code is also the leading segment of every feature key, permission and event name) | Codes are `easy_crm`, `easy_helpdesk`, `easy_project`, `easybook`. The **slugs** (public URLs) are derived with `_` -> `-`, so `/products/easy-crm`, `/products/easybook`, `/products/easy-project`, `/products/easy-helpdesk` are exactly the brief's URLs. |
| 2 | "Implement trials using the EXISTING trial system" | The existing trial is **subscription-wide** (`Subscription.trial_ends_at`) and an organization may hold only **one** open subscription (`uq_one_open_subscription_per_org`). A free-trial plan on an organization that already has a subscription is refused (`subscription_exists`); a paid plan with `with_trial` is silently sold as a normal purchase. The mandatory Step 42 state (CRM active, EasyBook active, Easy Project **trial**) cannot be expressed. | A per-product, per-organization **`ProductTrial`** (durable commercial fact, unique per organization x product). It is an *input* to `provisioning.compute_entitlements` - the entitlement model already keys rows by `(organization, product, source_ref)` and the enum `EntitlementSourceKind.TRIAL` exists and is unused. No subscription is created or mutated; no invoice; conversion is an ordinary paid order (existing checkout). The plan-level trial stays untouched for plans that use it. |
| 3 | Step 22: show List Price, Partner Price, Retail, Margin to the reseller | Phase 7 decided the partner never sees list price, margin or rate (ADR-027/029); the partner price is the one price the customer is billed; the partner earns commission on it, not a markup. There is no retail price. | Staff (Reseller detail -> Products & pricing) already show list, partner, discount, commission, platform net - server computed. The partner portal shows the partner price only. **Retail price: NOT SUPPORTED** (not modelled). Reported, not "fixed". |
| 4 | Step 17/35: per-product flags "direct / reseller / trial / affiliate" | Direct = `Product.status`+`listing`+`Plan.is_public`; reseller = `PartnerProductGrant` per partner; affiliate = `CommissionRule.product_code`; only trial has no home | Only **trial** gets new configuration (`ProductTrialConfig`). The admin product page shows the other three as a read-only, derived summary. No duplicate flags. |
| 5 | Step 13: verify SSO per product | Registration needs `redirect_uris` (required by the manifest), an active integration with **no credentials** launches and then fails at the token endpoint; no state says "SSO integration required" | Add a derived `sso_status` (`connected` = an unrevoked, unexpired credential exists; else `integration_required`). The launcher opens the product's own launch URL (no token in a URL, ever) and the UI says single sign-on is not connected. |
| 6 | Step 36: health | `health_url` is stored and never fetched; no status field | Bounded, cached probe run by a scheduled task / staff action through the existing SSRF-guarded egress client, stored as technical health; never on a dashboard render; never a readiness gate. |
| 7 | Step 30/33: white-label launcher | Brand is applied only on `/login` by `host` | Presentation-only brand for a customer of a white-label partner inside the org console (resolved through the live partner link, not the Host header). |
| 8 | Step 23: partner Overview, Subscriptions, Users, Activity | Partner API has Customers, Catalog, Commissions, Payouts, Branding, Domains only | Add read-only Overview (cards), Subscriptions (per customer), Users (members), Activity (own audit). |

## 1. Gap map (what exists vs what this phase adds)

| Area | Exists | Gap (this phase) |
|---|---|---|
| Catalog | Product/Plan/PlanVersion/PlanPrice/Bundle, slug, tagline, listing, marketing_url, public catalog API, plan builder, pricing | `category`, `icon` (allow-listed key) on Product; four products; catalog exposes both |
| Trials | Plan-level `trial_days`, `TRIALING` subscriptions, trial-once derived history, expiry sweeper | Per-product trial config (enabled, days, eligible plans, seat limit, payment-method flag); `ProductTrial`; start/expire/convert |
| Checkout | Pay-first orders, hosted checkout, fake provider (labelled, dev only), Idempotency-Key | Product-level Buy/Trial intent that survives signup in another tab; nothing new in checkout |
| Entitlements | One writer (`provisioning`), diff-based, `EntitlementSourceKind.TRIAL` unused | Trial as a compute input |
| Launcher | Directory, launch (server-owned URL), seats, states | slug/category/icon/trial/plan/seats/renewal/sso/health on the directory; external launch verified in Chrome (CSP `form-action 'self'`) |
| SSO | OIDC provider (code + PKCE), credentials | Truthful per-product status; **none of the four products can be marked SSO-ready** (see 11c evidence) |
| Health | Nothing | Probe + status, isolated from readiness |
| Admin | Product manager, plan builder, partner grants | Products console (4 cards, commercial vs technical status, trial config, integration panel) |
| Reseller | Grants, price books, sale, commission, payouts, wizard | Grants verified per product; partner Overview/Subscriptions/Users/Activity |
| White-label | Brand, domain lifecycle, login-page brand | Org-console brand; honest D-035 notice |
| Affiliate | Referral codes, first-touch, accrual | Verified with the four products; no change |
| Frontend | Site with `/products/[slug]`, dashboard with a basic card grid, Explore as plans list | `/products` index, product icons, dashboard redesign, trial UX, empty states |

## 2. Design freeze

### 2.1 Catalog (11a)
- Migration `catalog.0004`: `Product.category` (char 64, free text, escaped everywhere, never HTML), `Product.icon` (char 32, one of `PRODUCT_ICON_KEYS`: a fixed allow-list of visual names such as `customers`, `book`, `board`, `headset`, `box`, `chart`, `megaphone`, `wallet`, `users`, `cart`, `cog`, `generic`). The frontend maps the key to an inline SVG; a product can never carry an image URL.
- `ProductTrialConfig` (one row per product; `enabled`, `duration_days` 1..365, `seat_limit` nullable >= 1, `payment_method_required` bool, `eligible_plan_codes` list). `payment_method_required = true` is **refused**: no payment-method model exists (D-028). Changes are audited (`commercial.product.trial_configured`); a running trial keeps the terms it started with.
- Trial is enabled only if the product has config, `enabled`, at least one eligible plan that is available, public and belongs to the product. **Nothing is hard-coded globally**; the four products are seeded with trial **disabled** in production and enabled explicitly by the browser-check seed (test data, labelled).
- `manage.py register_ecosystem_products` (idempotent): the four products (draft -> available, listed), their integrations (launch URL from the brief, https origin callback **placeholder** redirect URI that a product owner must replace) and **no** plans, prices or credentials. `--activate-integrations` is explicit. Runs as a named staff principal at AAL2 semantics exactly like the seed scripts (management commands are operator actions, audited with the actor).

### 2.2 Product trials (11b)
- `subscriptions.ProductTrial` (tenant-scoped, RLS, suspension write block): `product_code`, `plan_version` (PROTECT), `plan_code`, `seats`, `started_at`, `ends_at`, `status` (`active|converted|expired|cancelled`), `source` (`TrialSource`), `started_by`, `ended_at`. **Unique (organization, product_code)**: one trial per product per organization, by the database. Idempotent start (`Idempotency-Key`, existing `idempotency.claim`).
- Start rules (all server-side): caller holds `commercial.trial.start` (owners/billing managers); organization active; product available + listed; config enabled; plan eligible + available + public; product not already held by a paid line; no earlier trial of that product (subscription history **and** ProductTrial); seats = `min(plan.included_seats, seat_limit)`.
- `provisioning._compute` adds, per active trial not superseded by a paid line: `ComputedProduct(status=TRIAL, source_kind=TRIAL, source_ref=subscriptions.trial:<id>, effective_to=ends_at)`, the plan's features and a seat pool. Expiry is time-based (`effective_to`) so access ends at `ends_at` even before the sweeper; the sweeper (`process_due`) marks `expired`, audits, publishes `commercial.product_trial.ended` and reconciles.
- Convert = the customer buys the product through the existing order flow; the trial row is marked `converted` when a paid line for the product exists (sweeper and order fulfilment). Retry-safe: paid line wins in compute; the trial cannot restart.
- Cancel = customer ends the trial early (`cancelled`), entitlement recomputed.
- Commission: none on a trial (nothing is sold); ordinary accrual on the paid order.

### 2.3 Launch, SSO, health (11c)
- `sso_status` derived from the integration's credentials (selector); the directory returns `sso: "connected" | "integration_required"`.
- Launch stays a server-owned URL; query built with proper joining; the browser check drives a real external launch. If Chromium blocks the 302 under `form-action 'self'`, the launch becomes a same-origin `fetch` that returns the server-owned URL and the client navigates (still no client-supplied destination).
- Manifest URL hardening: reject private, link-local, loopback (non-dev) and metadata IP literals and `localhost` over https for `launch_url`/`health_url`/redirect URIs.
- Health: `ProductIntegration.health_status/health_checked_at/health_detail` written only by `ecosystem.record_health`; probing lives outside `ecosystem` (which is forbidden to make HTTP calls) in `integration`, through the egress client; scheduled + staff "check now"; result cached; dashboard reads the stored value.
- A suspended organization launching still gets the platform's ordinary domain-error JSON (402 `organization_not_writable`), from the tenancy write-block chokepoint - not a bespoke friendly redirect. **Correction, made during 11i verification:** this section originally proposed a friendly redirect for that case; it was never built, because the write-block fires as an exception from `DomainErrorMiddleware` before the launch view's own JSON/redirect branching ever runs, and rewriting that shared exception path for one caller was judged not worth the risk this late in the phase. The frontend's `LaunchButton` already degrades to its generic-but-honest denial message for a body with no `state` field, so nothing crashes or lies - it is simply less specific than a real `no_seat`/`no_entitlement` denial. Recorded here rather than silently left in a frozen plan that overstated what was built.

### 2.4 Public store and dashboard (11d, 11e)
- `/products` (four large cards, server prices only, "Contact sales" when no price), `/products/<slug>` upgraded; CTAs: Learn more / Start free trial (only if the server says trial is available) / Buy now (only if a price exists) / Contact sales.
- Product intent (`?product=<slug>&intent=trial|buy`) is kept in `localStorage` with a 24 h TTL (a verification link opens a new tab, which loses `sessionStorage`); consumed after sign-in at the organization landing; resolves to the server's offer for that product; it never carries a price or a destination.
- Dashboard: welcome strip (organization, active products, trials, action summary), My Products cards (icon, status badge, plan, trial days left, seat usage, renewal, Open / Manage), Explore More Products, empty states. Status labels come from one shared component.

### 2.5 Admin, reseller, white-label (11f-11h)
- Admin Products console over the existing catalog/integration/trial services (no credentials in the UI; launch URL editing validated server-side; AAL2 on every dangerous write).
- Partner: Overview cards, Subscriptions, Users, Activity (read-only, scoped by the verified partner id); grants stay staff-only.
- White-label: brand for a customer of a white-label partner in the org console through the live link; the Domains screen states plainly that sign-in on a custom domain is not available yet (D-035).

## 3. Non-goals (stated so they are not mistaken for omissions)
- No custom-domain sign-in (D-035 stays open; the analysis is in the final report, no shortcut).
- No payment-method storage, no real payment provider go-live, no retail price.
- No product-side code, no SSO client inside a product (not our repository).

## 4. Order of work
11a catalog + registration -> 11b trials -> 11c launch/SSO/health -> 11d store -> 11e dashboard -> 11f admin -> 11g reseller -> 11h white-label -> 11i verification (architecture, security, performance, upgrade, browser journeys, matrix) -> 11j docs + independent adversarial audit + final full suite.

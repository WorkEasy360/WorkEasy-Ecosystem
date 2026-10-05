# Phase 9 — Status

**STATUS: PASS** (2026-09-26)

Phase 9 implements the "industrial multi-product ecosystem" brief on top of the Phase 8 extension. It adds:
- the public website and catalog;
- self-service accounts with email verification and password reset;
- the customer dashboard;
- pay-first orders;
- the Super Admin's lifecycle views, orders, subscriptions, entitlements, staff roles and platform settings;
- an environment-driven Super Admin bootstrap.

Following the brief's first rule, whatever already existed and was stronger was kept and extended.

| Area | Where to look |
|---|---|
| Plan, and where the build differs | [00-plan.md](00-plan.md) |
| Operations | [01-operations.md](01-operations.md) |
| Decisions | [ADR-035](../architecture/adrs/ADR-035-pay-first-orders.md), [ADR-036](../architecture/adrs/ADR-036-self-service-accounts-and-staff-roles.md) |
| Rules | J1–J11 in [ARCHITECTURE.md](../../ARCHITECTURE.md) |
| Debt | D-031 (new); D-032 opened and **closed** in the Phase 9 closure (below); D-020 and D-027 narrowed; D-020 deferred to the reseller launch |

Owner decisions (2026-09-26):
1. Paying through hosted checkout needs no step-up.
2. Everyone lands on the dashboard.

## Gates

| Gate | Result |
|---|---|
| backend `pytest` (full suite, with the write-ownership guard) | **2,421 passed, 0 failed, 0 single-writer violations** (44 min, after the Phase 9 closure; the 2 warnings are structlog's pre-existing `format_exc_info` notice) |
| `ruff check` / `ruff format --check` | pass / pass (589 files) |
| `mypy` (contracts, strict) | pass (29 files) |
| `bandit -ll` | no issues (72,362 lines) |
| `pip-audit --strict` | no known vulnerabilities |
| `lint-imports` | 18 contracts kept, 0 broken |
| `manage.py check --fail-level WARNING` / `makemigrations --check` | no issues / no changes |
| frontend: clean `npm ci`, `tsc`, `eslint`, `vitest`, `next build`, `npm audit` | pass; **200 tests**, 12 files; every route dynamic (CSP nonce asserted); 0 vulnerabilities |
| fresh database; extension → Phase 9 upgrade, real rollback, re-apply (`scripts/rehearse_phase9_upgrade.py`) | **47/47** (below) |
| production build in a real browser (`scripts/phase9_browser_check/`) | **43 pages, 17/17 flows** (below; the five membership flows added by the closure) |

**Upgrade rehearsal.**
- **Fresh database:** migrate works, models match migrations, and every guard is in place:
  - RLS forced on `payments_order`;
  - no tenant policy on the three control-plane tables;
  - both composite foreign keys;
  - the check constraints and the one-pending-order partial unique index;
  - application-role grants, including the order-number sequence;
  - system checks run as the application role.
- **Upgrade:** a database built at Phase 8, seeded, migrated to the extension and seeded again, then upgraded:
  - no pre-existing column of any guarded row changed;
  - new columns hold their safe defaults;
  - slugs come from codes;
  - customer summaries are classified trial / customer / neither exactly as expected.
- **Rollback:** every Phase 9 migration reverses for real, the extension data is byte-identical afterwards, the permission flag is restored, and re-applying succeeds.

**Real browser** (production build, real backend, scratch database, Chrome):
- **Pages:** 41 page states across the public site, the new organization pages and the new Super Admin pages, all with:
  - 0 CSP violations;
  - 0 console errors (the check's own two refused requests are counted separately);
  - 0 axe WCAG 2.1 A/AA violations;
  - a visible focus indicator on every tab stop;
  - exactly one `h1`.
- **Reflow:** 30 URLs checked at 320px, none with horizontal scroll.
- **Flows (12/12 pass):**
  1. The Products menu lists the catalog.
  2. An unverified account cannot sign in, and is told why only after the correct password.
  3. The emailed link plus the password signs in and resumes the plan chosen on `/pricing` at checkout.
  4. Billing currency first, then the server's quote.
  5. The order is paid on the test provider's page with no step-up and fulfilled; the product is then held (entitlement active).
  6. A reset link sets a password once, and the new password signs in.
  7. Owner, administrator and member all land on the dashboard.
  8. The Super Admin created by `bootstrap_superadmin` lands on `/admin`.
  9. An order awaiting payment is paid from Orders with no step-up.
  10. The staff user segments reflect what each organization holds.

## Findings fixed during verification

| Finding | Fix | Test |
|---|---|---|
| Account emails (verify, reset, notices) were not dispatched at commit. The routing context they were queued under had ended, so the tenant task refused to be sent (`MissingTenantContext`) and every such email waited for the 5-minute sweeper. The browser check found it; unit tests had delivered the rows directly. | `notify()` dispatches under the context the notification was created in | `test_account_email_is_dispatched_when_the_signup_commits` |
| `User` had writers outside `platform/identity`: invitations, staff user actions, the control-plane anchor, and Phase 9's own signup and bootstrap. | `platform/identity/lifecycle.py` is now the only writer, and all of these call it | `test_only_identity_writes_the_user_table` (AST scan) |
| `Organization` and `Membership` have the same shape (onboarding, administration, the control-plane anchor). | Closed in the Phase 9 closure: one write boundary, `platform/organizations/services.py` | D-032 (closed) |
| Three links still pointed at `/marketplace` after it became `/explore`. The result was a dead page, and a prefetch that never settled on the billing page. | Links fixed | `test_every_console_link_in_the_frontend_resolves_to_a_page` |
| The Phase 2 purchase security tests exercised the removed route. | Re-pinned on `POST orders`: price, currency, status, organization, replay, missing or changed key, error shape, suspension | `test_commercial_security` |

## Phase 9 closure: single-writer organizations (D-032 closed)

A focused architecture closure before the freeze. It added no product feature.

**Old writers found.** `Organization` and `Membership` rows were written from seven places outside a single owner:

| Old writer | What it wrote |
|---|---|
| `platform/onboarding/services.py` | activating a partner-provisioned owner (`Membership` status, `joined_at`) |
| `platform/administration/invitations.py` | joining on acceptance (a new `Membership`, or a removed one reinstated) |
| `platform/administration/members.py` | suspension, reactivation, per-organization sign-out (`sessions_valid_after`) |
| `platform/administration/security.py` | the access policy and its version; staff break-glass; rename |
| `platform/authz/control_plane.py` | creating the control-plane anchor `Organization` |
| `Organization.suspend()` / `reactivate()` | model methods, called by the lifecycle service |
| `platform/administration/access.py` | `MemberSignIn` inserts (the prune was already in organizations) |

**Final ownership boundary.** `platform/organizations/services.py` is the one writer of:
- `Organization`;
- `Membership`;
- the creation ledger;
- member sign-in activity.

`projection.py` writes the chooser rows and is called only by that boundary. `administration/invitations.py` remains the one writer of `Invitation` rows.

The boundary's functions are:
- `create_organization_and_owner_membership`
- `ensure_organization`
- `set_lifecycle_status`
- `update_access_policy`
- `rename_organization`
- `lock_member`
- `set_member_status`
- `cut_off_member_sessions`
- `join_member`
- `activate_invited_owner`
- `mark_membership_removed`
- `record_member_sign_in`
- `prune_member_sign_ins`

Each function:
- locks the row it changes;
- enforces the row's invariants (the owner is never suspended or removed; the lifecycle moves only active↔suspended);
- keeps the chooser projection in the same transaction, which removed a sync call each caller used to repeat;
- returns a frozen `MemberRecord`, a `CreateOrganizationResult`, or the before-values a caller audits - never a model row.

Authorization, audit, roles, seats and events stay with the callers. APIs, response shapes, transactions and lock order are unchanged.

**Enforcement.**

| Check | What it enforces |
|---|---|
| `test_only_the_organizations_boundary_writes_its_tables` | Outside the package, no module imports `Organization` or `Membership` or reaches them another way (module import, `apps.get_model`, reverse relations, raw SQL). Inside it, only the boundary saves. |
| `test_the_ownership_scan_catches_every_way_round_the_boundary` | The scan is proven against ten bypass patterns. |
| **Write-ownership guard** (`backend/tests/write_ownership.py`, installed by the root conftest for every test session) | Every executed `save`/`delete`/`update`/`bulk_*` on an owned table must come from its owning module, or the run fails. Tables covered: the six organizations tables and `User`. |
| `tests/phase9/test_single_writer.py` | Proves the guard refuses a rogue update, save, insert and delete, and passes the boundary's own writes. |

**Findings of the closure, fixed.**

| Finding | Fix |
|---|---|
| `W360.E009` (no cross-module model signals) unpacked Django 5's 3-element receiver tuples as pairs. It also *called* any receiver connected with `weak=False`. It passed only because no model signal had a receiver, so the first receiver would have crashed `manage.py check`. | Fixed, with `test_a_cross_module_model_signal_is_reported` |
| Django's `update_last_login` wrote `User.last_login` on every sign-in from the auth view. Nothing reads it (the platform records `last_login_at`). It was a redundant UPDATE and a hidden second writer of `User`. | Disconnected in `identity.apps` |
| The staff user detail repeated the chooser query that `access_rows_for_users` already runs. | It now calls the selector |

**Code removed.**
- `Organization.suspend()`, `reactivate()` and `_sync_access_projection_metadata()`;
- the unused `OrganizationQuerySet` and `MembershipQuerySet`;
- `administration.security`'s `_locked_organization`, `_bump` and local `PolicyConflict` (now `contracts.errors.SecurityPolicyConflict`, same code and status);
- `administration.members`' `_locked` and `_refuse_owner_and_self`;
- five direct projection-sync calls in callers;
- the duplicated chooser query.

**Results.**

| Gate | Result |
|---|---|
| Backend | 2,421 passed, 0 failed, 0 single-writer violations |
| Static checks | ruff, format, mypy, 18/18 import contracts and bandit all clean; `check` no issues; `makemigrations` no changes (no schema change); pip-audit and npm audit clean |
| Frontend | clean `npm ci`, lint, typecheck, 200/200 tests, build (all routes dynamic) |
| Upgrade rehearsal | 47/47 |
| Browser | 43 pages clean, 17/17 flows |

**D-020.** Deferred to the reseller launch (debt register). The direct self-serve launch does not depend on it.

## The report

### 1. Architecture before

A Django modular monolith, the platform, with a Next.js shell. Products are separate applications that connect through OIDC, the entitlement API and events.

What was missing or wrong:
- There was no public website (`/` redirected to sign-in).
- There was no signup, email verification or password reset.
- A customer's purchase created an **active** subscription line before any payment (grant-then-bill).
- There were no orders.
- Customers chose their own collection mode and price region.
- Staff had 27 individual grants and no roles, and the bootstrap took only an address.
- There were no lifecycle views, and disabling the last security administrator from the console was possible.

### 2. Architecture after

The layering is the same. No layer, service or dependency was added. The new modules:

| Module | What it is |
|---|---|
| `platform/identity/tokens.py`, `lifecycle.py` (P1) | One-time account links; the single writer of `User` |
| `platform/administration/site.py`, `self_service.py` (P4) | Platform settings; the public signup, verification and reset flows |
| `platform/authz/staff_roles.py` | Staff roles as bundles of grants |
| `commercial/payments/orders.py` | Pay-first orders, on the existing invoice and payment path |
| `commercial/controlplane/users.py` | Staff lifecycle views |

On the frontend: the `(site)` route group, the account pages, `/dashboard`, and the commerce and Super Admin screens.

### 3. Shared platform boundaries

The platform is layered as follows, with `contracts` (L0) under everything:
- `platform` P0–P4;
- `integration`;
- `commercial`;
- `intelligence`;
- `interfaces`.

18 import-linter contracts are kept. F-6 (no cross-module ORM) holds. Each new module sits in the lowest layer that can own it. Signup lives at P4 because it composes identity, organizations, onboarding and notifications.

### 4. Product boundaries

Unchanged and verified:
- No product code is in this repository. `products/` is an empty guard (ADR-017, D-007).
- A product registers by manifest, authenticates with OIDC and `private_key_jwt`, reads entitlements through the API, and publishes versioned events.
- The shell has no product-named route (A24, and `test_the_frontend_contains_no_product_screens` now covers the public site too).

### 5. Domain ownership

| Data | Owner and single writer |
|---|---|
| `User`, `AccountToken` | `platform/identity` (`lifecycle`, `tokens`) — **enforced** (J10) |
| `Organization`, `Membership`, creation ledger, member sign-ins | `platform/organizations/services.py` — **enforced** (J12, J13; D-032 closed) |
| Chooser projection (`UserOrganizationAccess`) | `platform/organizations/projection.py`, called by the boundary |
| `Invitation` | `platform/administration/invitations.py` (the invitation system); joining goes through the boundary |
| `StaffGrant` | `platform/authz` |
| `PlatformSettings` | `platform/administration` |
| Product, Plan, Bundle | `commercial/catalog` |
| Price | `commercial/pricing` |
| Subscription | `commercial/subscriptions` (services; `system.py` for actor-less work) |
| Order, Invoice, Payment, Refund | `commercial/payments` |
| Entitlement, seats | `platform/entitlements`, written **only** by `commercial/provisioning` (A10) |
| `CustomerSummary` (with `access_class`), `OrderSummary` | Control-plane projections, written in the same transaction as their source (D-019, D-031) |
| Audit | Append-only through the audit service |

### 6. Database changes

New tables:
- `payments_order`: tenant-scoped, with RLS and composite foreign keys;
- `payments_order_summary`;
- `identity_account_token`;
- `administration_platform_settings`;
- the `payments_order_number_seq` sequence.

New columns:
- `identity_user.acquisition_source`;
- `catalog_product.slug` (unique), `tagline`, `marketing_url`, `listing`;
- `payments_invoice.order_id`;
- `provisioning_customer_summary.access_class` (indexed).

Relaxed: `payments_invoice.subscription_id` is now nullable, with a check that each invoice has exactly one of subscription or order.

The stored flag on `platform.billing.manage` changed.

### 7. Migrations

| Migration |
|---|
| `identity.0004` |
| `administration.0001` |
| `catalog.0003` |
| `payments.0002` |
| `provisioning.0002` |
| `authz.0009` |
| `tenancy.0011` |

All are additive, the backfills are deterministic, and all are reversible. The rehearsal results are above; the deploy notes are in [01-operations.md](01-operations.md) §1.

### 8. API endpoints

**Added:**

| Area | Endpoints |
|---|---|
| Public site | `public/site`, `public/catalog`, `public/catalog/products/<slug>` |
| Signup | `public/signup`, `public/signup/verify`, `public/signup/resend` |
| Password | `public/password/forgot`, `public/password/reset` |
| Orders | `orgs/<slug>/orders` (GET, POST), `orders/quote`, `orders/<id>`, `orders/<id>/cancel` |
| Admin | `admin/orders`, `admin/customers/<id>/billing-terms`, `admin/settings`, `admin/catalog/products/<id>` (PATCH), `admin/security/roles`, `admin/security/roles/revoke` |
| Filters | `admin/users?segment=`, `admin/customers?subscription_status=&access_class=&product=` |

**Removed:** the customer `subscriptions/purchase` and `subscriptions/<id>/activate`.

**Changed:** `billing/invoices` is keyset-paginated. The app directory carries `entitlement_status`.

### 9. Frontend changes

- **Public site** (`src/features/site`): the landing page, pricing, product, services and contact pages, and the Products menu.
- **Account pages** (`auth/SelfService.tsx`): sign up, verify, forgot password and reset. Links are read from the URL fragment through `useSyncExternalStore`.
- **`/dashboard`:** the landing resolver.
- **Commerce screens** (`console/CommerceScreens.tsx`): home, My products, checkout, orders and support.
- **Explore:** renamed from Marketplace; buying now goes through checkout.
- **Super Admin:** orders, subscriptions, entitlements, platform settings, staff roles, the product editor, and user lifecycle filters and detail.
- **API clients:** `public.ts`; order, settings and role calls.
- **What the shell still does not know:** it has no product list, price, role or plan hard-coded. Everything comes from the API, and the server re-checks every action.

### 10. Public website

`/` shows the catalog-driven landing page. The header carries the Products menu (available and coming-soon products), Services, Pricing, Contact, Sign in and Get started. `/products/<slug>` shows plans, prices, features and bundles.

- **Currency:** the page's currency is the Platform settings value, and a visitor can switch among currencies with published prices.
- **Choosing a plan:** remembered in `sessionStorage`, never in a URL parameter, and resumed after verification.

### 11. Customer dashboard

`/o/<slug>/dashboard`:
- a welcome;
- **My products** first, with plan, status and trial state;
- the products not yet held;
- for administrators, the Phase 8 insights.

The sidebar has:
- Dashboard;
- My products;
- Explore products;
- Subscriptions & billing;
- Orders;
- Organization, Members and Invitations;
- Profile;
- Security;
- Support;
- Sign out.

### 12. Product catalog

Each product has a slug, tagline, marketing URL and listing. Its public availability is one of:
- `available`;
- `coming_soon`;
- private (`hidden`);
- disabled (deprecated or retired; existing holders keep it).

Staff edit products in the Super Admin with validation and audit; the code never changes. The public catalog is anonymous, rate-limited and cached for 60 seconds per currency. Explore and the public site list only public plans of listed, available products, and bundles only when every product in them is available.

### 13. Purchase flow

Checkout runs Quote → Order → invoice → hosted payment → signed webhook → settlement → Order paid → fulfilment after commit → subscription line → provisioning → entitlement.

- **Pricing:** the price is the server's, frozen at placement.
- **Idempotency:** the `Idempotency-Key` covers every argument.
- **Concurrency:** at most one order per organization awaits payment.
- **Durability:**
  - fulfilment is re-driven by the clock;
  - if an order cannot be fulfilled after payment, it becomes `failed` and the payment is refunded automatically;
  - a late or duplicate payment is refunded automatically;
  - a payment with no amount never settles.
- **No payment owed:** trials, zero prices and sales-led terms are fulfilled at once and still recorded as orders.

### 14. Subscription flow

- **New subscription:** starts at payment, and the order's invoice is its first period's invoice, so the first period is never billed twice.
- **Addition to a running subscription:** pays the prorated remainder of the current period, then is invoiced in full.
- **Trial conversion:** an `activation` order.
- **Renewals, dunning and cancellation:** unchanged. Staff and partner selling are unchanged.

### 15. Entitlement flow

Unchanged: `commercial/provisioning` is the only writer.

New: `CustomerSummary.access_class`:
- `customer`: a live paid or granted product;
- `trial`: trials only;
- empty: nothing.

Provisioning writes it on every reconciliation. It is what the Explorer / Trial / Customer segments are derived from, and the person's identity never moves between tables.

### 16. SSO and product launcher

Unchanged: OIDC with PKCE; `can_launch_product` is the one decision and it is re-checked at launch. A product with no registered integration is "unavailable" before entitlement is consulted.

Added: `entitlement_status` in the directory, so My products can show trial or active.

### 17. Super Admin

**Pages at `/admin`:**
- Users: filters for Explorers / Trial / Customers / Unverified / Suspended (locked out) / Deactivated; the detail shows organizations with their products and subscription status, lifecycle, recent orders, and deactivate/reactivate/unlock/sign out;
- Customers, with status, access and product filters;
- Products, with the editor;
- Plans;
- Orders;
- Subscriptions;
- Entitlements (holders per product);
- Payments;
- Platform settings;
- Staff access (roles and grants);
- Audit, which includes staff sign-in and sign-out.

**`bootstrap_superadmin`:**
- reads `PLATFORM_SUPERADMIN_EMAIL` / `PLATFORM_SUPERADMIN_PASSWORD`;
- is idempotent;
- never prints or resets a password;
- is not a back door: it refuses once another security administrator exists;
- is audited.

### 18. RBAC

Customer RBAC is unchanged. Staff RBAC gains six roles as grant bundles:
- `SUPER_ADMIN`
- `USER_ADMIN`
- `PRODUCT_ADMIN`
- `BILLING_ADMIN`
- `SUPPORT_ADMIN`
- `READ_ONLY_ADMIN`

`authz.check` stays the only authority.

New permissions: `platform.staff_settings.view` / `.manage` (dangerous).

Changed by owner decision: `platform.billing.manage` is no longer dangerous.

### 19. Security improvements

**Payments and orders:**
- no grant before payment;
- over-collection closed: a payment arriving for a void or already-paid invoice is refunded, and an amount-less webhook is a mismatch;
- customers can no longer choose their collection mode or price region;
- private plans cannot be bought by id;
- a malformed id is a 400, not a 500;
- the development test checkout checks membership.

**Accounts and staff:**
- the last security administrator cannot be disabled from the console;
- defence against pre-account-hijacking (verification needs link and password; a reset or invitation replaces the password);
- signup, resend and reset answer uniformly, and the work they do is equalised;
- per-IP and per-inbox rate limits;
- links are hashed, single use, sealed and carried in the URL fragment;
- staff sign-in is audited;
- no credential in the repository (J8, `test_no_administrator_credential_is_committed`);
- `User` has a single writer.

### 20. Performance improvements

- The customer invoice list is keyset-paginated. It was capped at 100 with no cursor.
- The public catalog is cached for 60 seconds per currency, falling back to the database on a cache outage.
- User segments and customer filters are indexed subqueries and never load a table.
- The user detail and directory batch their per-organization reads.

### 21. Queries optimized

Pinned by query-count tests that grow the data and assert an equal count:
- the public catalog: 2 → 6 products, each with a feature and a priced plan;
- the customer order page and the staff order list: 1 → 5 orders;
- the staff user segment page.

Keyset cursors are used for orders, staff orders and invoices. The new `ix_custsummary_access` index backs the segments.

### 22. Dead and duplicate code removed

- the empty `interfaces/admin` package;
- CORS settings and their production checks (no middleware read them);
- `w360_bootstrap_staff`;
- the customer purchase and activate routes, views and the frontend client call;
- the redirect-only `/` page;
- three duplicated currency lists, now one constant;
- `visible_products()`'s unused parameter;
- the region and collection-mode inputs on the customer billing form;
- the stale `/marketplace` links.

### 23. Dependencies removed

None were unused, so none were removed. Phase 9 added none. Every backend dependency is imported or configured: Django, psycopg, Celery, kombu, redis (cache and broker), structlog, argon2 (hasher), PyJWT, cryptography, dnspython. The frontend's runtime dependencies are next, react and react-dom only.

### 24. Tests added

**Backend: 89 new test functions:**

| File | Tests |
|---|---|
| `test_orders.py` | 26 |
| `test_self_service.py` | 23 |
| `test_super_admin.py` | 19 |
| `test_public_catalog.py` | 13 |
| `test_product_twenty.py` | 2 |
| `test_query_bounds.py` | 2 |
| `test_phase9_architecture.py` | 4 |

Also, 9 security tests were re-pinned on orders, and the existing suites were updated for the new flows.

**Frontend:**
- `site.test.tsx` (11) and `commerce.test.tsx` (7);
- new admin-console cases;
- 200 tests in total, up from 178.

**Scripts:** the upgrade rehearsal and the browser check.

### 25. Actual test results

Backend: 2,402 passed, 0 failed. The first full run (before the verification fixes) showed 1 failure - the route allowlist - fixed and re-run.

Frontend: 200/200. Rehearsal: 47/47. Browser: 41 pages clean, 12/12 flows.

### 26. Build results

- `next build`: every route dynamic;
- `manage.py check`: no issues;
- `makemigrations --check`: no changes.

### 27. Security scan results

| Scan | Result |
|---|---|
| bandit | 0 issues |
| pip-audit | 0 known vulnerabilities |
| npm audit | 0 vulnerabilities |
| Architecture scans | credential scan, single writer, no product screens: pass |

### 28. Remaining risks

- **Email delivery:** signup depends on the notification email channel being configured in production. Until it is, close signup (01-operations §3).
- **No stored payment method (D-028):** renewals are paid by hand through hosted checkout.
- **No tax calculation (D-029).**
- **Reseller owners (D-020), deferred to the reseller launch:** a partner-provisioned owner can set a password through *Forgot password*, but nothing tells them to, and the owner invitation has no screen. The direct self-serve launch does not depend on it; do not enable partner provisioning of new-address customers in production until it is closed.

### 29. Deferred items

- Proration credits for downgrades and cancellations (D-027).
- Product logos: the monogram is reused.
- A per-product marketing page CMS: products carry a marketing URL instead.
- Serving the currency list from the API.
- The breach-corpus password validator (P1-5).
- Retiring the Phase 2 organization-scoped catalog routes (D-022).

### 30. Recommended next phase

1. Before the reseller channel launches, close D-020: an owner email at provisioning, plus an invitation card on `/dashboard`.
2. Stored payment methods and off-session renewal (D-028), before self-serve renewals matter.
3. A tax service (D-029), before selling in a taxed market.
4. The first real external product through the Product #20 path, end to end in staging.

## A–K

**A. Can Product #20 be added without modifying existing product internals?**

**Yes, verified.** `tests/phase9/test_product_twenty.py` takes a product whose code appears nowhere in `backend/workeasy360` or `frontend/src` (asserted) through the whole path over HTTP:
1. Create it in the Super Admin.
2. Add a plan and a price.
3. List it.
4. Register its integration manifest and credential.
5. See it in the public catalog.
6. A customer orders it and pays through the test provider.
7. It shows as held and launches.

Adding it changes no code.

**B. Are there any cross-product database dependencies?**

**No.** Products are separate applications with their own databases. This repository holds no product code or tables (`products/` is an empty guard, `test_products_are_structural_placeholders_only`). Products reach the platform only through OIDC, the entitlement API and versioned events. `test_no_product_imports_another_products_models` and 18 import contracts are kept.

**C. Does every authoritative domain have one writer?**

**Yes, verified (after the Phase 9 closure).**
- **Commerce:** catalog, pricing, subscriptions, orders and payments, entitlements (provisioning only, A10), staff grants and platform settings each have one writer.
- **`User`:** one writer, `identity.lifecycle`. Django's own `last_login` write was a hidden second writer and is disconnected; the platform records `last_login_at`.
- **`Organization`, `Membership`:** one writer, `platform/organizations/services.py`. So do the creation ledger and member sign-in activity. The chooser projection is written by `organizations.projection`, called only by that boundary, and invitations by the invitation system.

This is proved twice:
- **Statically:** `test_only_the_organizations_boundary_writes_its_tables`, whose power is itself tested against ten bypass patterns.
- **At runtime:** the write-ownership guard fails the suite on any executed write from another module.

**D. Can one product fail without bringing down unrelated products?**

**Yes.**
- Product and integration health never gate platform readiness (`test_product_health_never_affects_platform_readiness`, `test_integration_health_never_gates_readiness`).
- A suspended integration affects only its own launch (`test_a_suspended_integration_cannot_authenticate`).
- Entitlement reads are per product, and a product cannot probe another's (`test_a_product_client_cannot_probe_another_products_entitlement`).
- An order that cannot be fulfilled fails and refunds only itself.

**E. Are authentication, organizations, billing and entitlements centralized?**

**Yes:**
- `platform/identity`: the OIDC provider for every product;
- `platform/organizations`;
- `commercial/billing` and `commercial/payments`;
- `platform/entitlements`, fed only by `commercial/provisioning`.

**F. Is product authorization server-side?**

**Yes.** Every route calls `authz.check`, and launch re-checks entitlement, seat and integration. Orders validate the offer, price, currency and organization on the server. The browser's `can()` only hides controls. Evidence:
- `test_launch_is_denied_server_side_for_a_no_seat_member_even_if_the_browser_asks`;
- `test_commercial_security`;
- `test_only_what_explore_lists_can_be_ordered`.

**G. Are there duplicate implementations remaining?**

**One known route duplicate, D-022:** catalog and pricing are reachable through the Phase 2 organization-scoped routes and the `/admin` routes. Both go through the same handlers, services and grant check, so there is no duplicated logic.

The following are aliases or reuse, not duplicates:
- `/o/<slug>/billing` is an intentional short address for provider returns;
- the dashboard shares the product grid and its loader with My products.

**H. Is any dead or garbage code remaining?**

**None known.**
- The dead code found was removed (item 22).
- ruff finds no unused imports or variables.
- The link guard proves every console link resolves.

Two things only look dead and are deliberate:
- `products/`, a guard (D-007);
- the password-validator stub, a planned control (P1-5).

**I. Are there unnecessary dependencies remaining?**

**No** (item 23).

**J. Are there known N+1 or redundant query problems?**

**None known.** The new list paths are pinned by growth tests (item 21), alongside the earlier launcher, chooser and integration query tests. The public catalog costs a fixed handful of queries per currency per minute.

**K. Is the architecture suitable for long-term ecosystem growth?**

**Yes, with the open items stated.**
- Products are added by registration, not code.
- Commerce is pay-first, idempotent and concurrency-tested.
- Identity is one account per person.
- The layering and ownership rules are executable.

What to close before scale: D-028 and D-029 before the markets that need them, and D-020 before the reseller channel.

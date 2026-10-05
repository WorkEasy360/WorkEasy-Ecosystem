# Phase 9: the customer journey (audit, gap analysis and plan)

This phase follows the "industrial multi-product ecosystem" brief (2026-09-26), which came after the Phase 8 extension. The brief's first rule is to inspect before changing and to extend what already exists instead of rebuilding it. This document records that inspection and the plan that follows from it. Status and results are in [phase-9-status.md](phase-9-status.md).

## 1. Audit: what already exists and is kept

Most of the brief's architecture already exists and is enforced by tests. It is kept unchanged:

| Brief section | Existing implementation (kept) |
|---|---|
| §1–4 Ecosystem, dependency direction, no cross-product DB, single writer | Products are separate applications (ADR-017). `products/` is empty by rule A11. Import-linter keeps 18 contracts. F-6 bans cross-module ORM access. Entitlements have one writer (A10). |
| §5 One identity | `identity.User`, one row per person. `Membership` belongs to `Organization`, then subscriptions and entitlements hang off the organization. |
| §15 Entitlement domain | `platform/entitlements`. Its only writer is `commercial/provisioning`. It supports trial/active/past_due/grace/suspended/cancelled/expired and reconciliation (ADR-005). |
| §16 Organization model | Users can belong to many organizations. RLS isolates tenants, and the org chooser is backed by a projection. |
| §17 Launch / SSO | OIDC with authorization code + PKCE (ADR-018). `launcher.access.can_launch_product` is the one access decision and is re-checked at launch. The launch URL is never serialised to a client. |
| §23 Audit | An append-only, hash-chained `AuditEvent` log with a redactor. The control-plane log is anchored on the platform organization. |
| §24 Security | Argon2 password hashing, lockout, throttles, AAL2 step-up, a session registry, CSP with nonce, HttpOnly/Secure/SameSite cookies, and SSRF-safe egress. |
| §25 Failure isolation | Tiers, bulkheads and a degradation matrix. Product health never gates readiness. |
| §13 Plans, versioned prices | A plan builder. Published versions and prices are immutable (enforced by a trigger). |
| §14 Payments | Hosted checkout (Stripe, Razorpay and a fake provider for tests). Webhooks are signed, de-duplicated and settled under row locks. Dunning and refunds are supported. |

## 2. Gap analysis

Each gap below was verified against the code. File references were collected by the audit.

| # | Gap | Brief § | Decision |
|---|---|---|---|
| G1 | No public website: `/` only redirects, and there is no public catalog, pricing or product detail page | 6, 7, 37 | Build it |
| G2 | No anonymous catalog API. Products have no slug, tagline, marketing URL or public listing flag | 6, 7, 20 | Build it; add the fields |
| G3 | No self-service signup | 8 | Build it |
| G4 | No email verification. `email_verified_at` is never required | 8, 24 | Build it; sign-in requires a verified address for self-registered accounts |
| G5 | No password reset (and D-020 has no credential path) | 24 | Build it |
| G6 | **A purchase grants access before any payment.** Unpaid invoices only start dunning. | 14 | Pay-first checkout: order → payment → subscription → entitlement |
| G7 | No `Order` and no order history | 14, 10, 18 | Build `Order` in `commercial/payments` |
| G8 | Customers choose `collection_mode` (`partner_billed` means never invoiced) and `billing_region` (the price book) themselves | 14, 24 | Self-serve accounts are always `automatic`, region default. Only staff set these. |
| G9 | No Explorer / Trial / Customer classification | 5, 19 | Derived from entitlements. No new identity table. |
| G10 | No central `/dashboard`. No Explore, Orders or Support pages. My Products shows no plan or status. | 9–12 | Build them on the existing organization console. No second shell. |
| G11 | No staff role bundles (27 individual grants only) | 21 | Code-declared bundles over the existing `StaffGrant`s. No second authorization model. |
| G12 | No environment-driven, idempotent super-admin bootstrap | 22 | `bootstrap_superadmin` replaces `w360_bootstrap_staff` |
| G13 | **Disabling the last permanent security administrator through `/admin/users` locks the control plane out** | 21 | Refuse it (bug fix) |
| G14 | The admin user directory has no lifecycle or status filters, and no verified/products/subscription columns | 19 | Build them |
| G15 | No platform-wide Orders, Subscriptions or Entitlements lists for staff | 18, 38 | Build them |
| G16 | No product edit endpoint (only create and status) | 20 | Build it |
| G17 | Staff sign-in is not audited | 23 | Audit staff sign-in and sign-out in the platform log |
| G18 | No Platform Settings | 18 | Signup switch, contact addresses, public catalog currency. Audited, dangerous. |
| G19 | Settlement accepts a success on a void or already-paid invoice with no follow-up (over-collection). A missing amount skips the amount check. | 14 | Missing amount counts as a mismatch. Late or duplicate money is refunded automatically. |
| G20 | Marketplace lists bundles whose products are no longer available. Private plans can be bought by id. `declined_plan_version_ids` with a bad UUID returns 500. | 12, 24 | Fix |
| G21 | The development test-checkout does not check organization membership | 24 | Fix |
| G22 | The customer invoice list is capped at 100 rows with no cursor | 29 | Add keyset pagination |
| G23 | Dead configuration: CORS settings (package not installed), an unused password-validator stub. The frontend hard-codes the currency list three times. | 31, 34 | Remove, or serve the list from one source |

The following were considered and **not** changed:

- **The launch URL stays in the product's registered manifest.** The brief lists `launch_url` on Product. The existing split (catalog = what is sold, `ProductIntegration` = how the product connects) is stronger, and the brief says to keep stronger designs.
- **No product logo upload.** The launcher's monogram is reused, and a real logo is deferred.
- **No Django `admin` app.** `/admin` is the existing Super Admin console.

## 3. Owner decisions (2026-09-26)

1. **Customer checkout needs no step-up.** Placing an order and paying it through hosted checkout does not require AAL2. The provider authenticates the card (3-D Secure). Refunds, provider configuration, mark-paid/void and every staff payment action stay dangerous (AAL2). This reverses the extension rule that every payment operation is AAL2.
2. **Everyone lands on the dashboard.** Its first section is My Products. This reverses the extension rule that members land on My apps.

## 4. Architecture changes

### 4.1 Identity: self-service accounts (`platform/identity` and `platform/administration`)

- `UserStatus.PENDING` means the account awaits email verification. It cannot authenticate.
- `User.acquisition_source` records how the account was created: `self_signup`, `invitation`, `partner` or `operator`. It is empty for legacy rows.
- `AccountToken` holds one-time tokens with purpose `verify_email` or `reset_password`. Only a SHA-256 hash is stored. Each token is single-use and has a TTL. The link carries the token in the URL fragment and travels in a sealed notification, the same as invitations (X6).
- **Signup** always answers 202 "check your email", so it never reveals whether an address exists. For an existing active account it sends a "you already have an account" message instead. It is rate-limited per IP and per address, and closed when the Platform Settings switch is off.
- **Verification needs the link and the password together.** Clicking a link someone else requested therefore cannot activate an account with the requester's password (pre-account-hijacking). If the signup included a company name, verification creates that organization through the existing `onboarding.create_organization` and signs the person in.
- **Password reset** always answers 202. The token lasts 60 minutes. A reset bumps `session_epoch` (signs out everywhere), clears lockouts, marks the address verified and activates a pending account. A notice is sent afterwards.
- **Login** refuses a pending account with `email_not_verified`, but only after the password matched. Someone without the password learns nothing new.

### 4.2 Commerce: pay-first orders (`commercial/payments/orders.py`)

`Order` is tenant-scoped (RLS). Its statuses are `pending_payment → paid → fulfilled`, or `cancelled | expired | failed`. Its kinds are `plan`, `bundle` and `activation` (converting a trial). Every price is resolved on the server at placement and frozen in the order's snapshot. The browser sends only identifiers.

| Situation | What placing an order does |
|---|---|
| Paid plan/bundle, automatic collection, no open subscription | The order and its invoice are due now. Payment → subscription ACTIVE (period starts at payment) → entitlement. The order's invoice becomes the first period's invoice, so the customer is never billed twice. |
| Paid plan added to an ACTIVE subscription | Pays the **prorated** remainder of the current period now. After payment the line joins the subscription, and later periods are invoiced in full. |
| Trial, free-trial offer, or zero price | Fulfilled immediately (no payment). The order is recorded with total 0. |
| Converting a trial | An `activation` order for one period of the subscription's lines. Payment → ACTIVE. |
| `invoice` / `partner_billed` collection (set by staff or partners only) | Fulfilled immediately and invoiced with terms, as before (sales-led). |
| Subscription past due, in grace or suspended | Refused: pay the open invoice first |

Rules for orders:

- **At most one pending order per organization** (partial unique index). This removes the "two orders for one product" race.
- **Idempotency:** the `Idempotency-Key` covers every argument.
- **Payment uses the existing invoice path.** An order invoice is paid through `POST billing/invoices/<id>/pay`. The attempt, webhook and settlement code is unchanged.
- **Fulfilment is durable.**
  - Settlement marks the invoice paid and returns `fulfil_order` for after commit.
  - The payments clock re-drives any order that is paid but not yet fulfilled.
  - Fulfilment runs through `subscriptions/system.py`, the actor-less command surface, using the order's frozen price.
- **When fulfilment is impossible** (for example the product is already held), the order becomes `failed` and the payment is refunded automatically.
- **Expiry:** an unpaid order expires after `ORDER_TTL_HOURS` (24) and its invoice is voided. Money that arrives for a void or already-paid invoice is refunded automatically.
- **Staff view:** staff read orders across customers through the `OrderSummary` control-plane projection. It follows the same pattern as `CustomerSummary`: derived and written in the same transaction. It holds identifiers and amounts only, never names, emails or addresses.
- **Routes:** the customer route `subscriptions/purchase` is replaced by `orders`. The customer activate route is replaced by the activation order. Staff and partner paths are unchanged.

### 4.3 Lifecycle classification (derived, no identity table)

`CustomerSummary.access_class` records each organization's commercial access:

- `customer`: any product entitlement is active, past due, in grace, or cancelled-but-running
- `trial`: only trial entitlements
- empty: neither

Provisioning writes it on every reconciliation. A person's classification is computed from their active memberships:

- Customer if any of their organizations is a customer.
- Otherwise Trial if any is on trial.
- Otherwise Explorer.

A pending account is Unverified. The staff directory filters with subqueries and never loads whole tables. Buying a product moves the **same** user from Explorer to Customer. No credentials move.

### 4.4 Super Admin

- **Role bundles.** `SUPER_ADMIN`, `USER_ADMIN`, `PRODUCT_ADMIN`, `BILLING_ADMIN`, `SUPPORT_ADMIN` and `READ_ONLY_ADMIN` are code-declared sets of `platform.staff_*` grants. Granting a role creates the missing grants in one audited transaction. `authz.check` stays the only authority.
- **`manage.py bootstrap_superadmin`** reads `PLATFORM_SUPERADMIN_EMAIL` and `PLATFORM_SUPERADMIN_PASSWORD` and is idempotent.
  - It creates the account if absent, makes it a staff identity and grants `SUPER_ADMIN`.
  - It never changes an existing password and never prints one.
  - It runs under an advisory lock and is audited.
- **New admin pages:** users by lifecycle; orders; subscriptions; product holders (entitlements); product editing and listing; platform settings; staff sign-in audit.

### 4.5 Public catalog

The public catalog comes from `GET public/catalog` and `GET public/catalog/products/<slug>`. It is anonymous and rate-limited, with a 60-second server cache. Its currency is chosen from the currencies that have published direct prices.

Public availability of a product:

| Availability | Rule |
|---|---|
| `available` | status available and listing `listed` |
| `coming_soon` | listing `coming_soon` |
| private | listing `hidden`: not public, and not in Explore |
| disabled | status deprecated or retired: not public, and existing holders keep it |

Explore and the public site list only public plans of listed products. A self-serve order accepts only those.

## 5. Database and migrations

| App | Change | Safety |
|---|---|---|
| identity | `acquisition_source` (default `""`); status choices gain `pending`; new `AccountToken` table | Additive. No row changes. |
| catalog | `Product.slug` (backfilled from code, unique), `tagline`, `marketing_url`, `listing` (default `listed`) | Additive. Backfill is deterministic. |
| payments | `Order`, `OrderSummary`; `Invoice.order` (nullable one-to-one); `Invoice.subscription` becomes nullable, with a check that exactly one of subscription or order is set; `payments_order_number_seq` | Additive. Existing invoices all have a subscription. |
| provisioning | `CustomerSummary.access_class` (backfilled from status and products; the hourly reconcile corrects it) | Additive |
| administration | `PlatformSettings` singleton | Additive |
| authz | new staff permissions `platform.staff_settings.view/manage`; `platform.billing.manage` is no longer dangerous | Data migration, reversible |
| tenancy | RLS (FORCE, USING, WITH CHECK) and composite FKs for `payments_order` and the new invoice FK | Same pattern as `0010_payments_isolation` |

## 6. Implementation order

Each step keeps the suite green before the next begins:

1. Identity: pending status, tokens, signup, verification, reset, login gate, account email templates
2. Platform settings and the public site endpoint
3. Catalog marketing fields, product edit, public catalog, marketplace fixes
4. Orders and pay-first checkout; settlement hardening; billing-account restrictions; permission change
5. Lifecycle projection; staff directory filters; admin lists; role bundles; bootstrap; last-admin fix; staff sign-in audit
6. Frontend: public site, auth pages, dashboard, Explore, checkout, Orders, Support, admin pages
7. Cleanup pass; documentation; full gates (backend suite, ruff, mypy, import-linter, bandit, pip-audit, `check`, `makemigrations --check`, frontend install/typecheck/lint/test/build/audit, dynamic-rendering assertion)

## 7. As built: where the implementation differs from this plan

The plan above is kept as written. The build differs as follows (details in [01-operations.md](01-operations.md) and the ADRs):

| Plan | As built |
|---|---|
| G4: "sign-in requires a verified address for self-registered accounts" | Carried by the account's status, not by a check of `email_verified_at`: a self-registered account is `pending` until verified, and `can_authenticate` is false for it. Invited, partner and operator accounts are unaffected. |
| G14 / 4.3: user segments | Explorers, Trial, Customers, Unverified (`pending`), **Suspended = currently locked out** (`locked_until` or `mfa_locked_until` in the future) and **Deactivated = `disabled`**. The platform has no separate per-person suspension state. What each of the person's organizations holds, including a suspended subscription, is shown per membership in the user detail. |
| G10: routes | `/dashboard` resolves where to go (staff → `/admin`; one organization → its dashboard; several → the chooser; none → onboarding or the partner portal). The organization's pages: `/o/<slug>/dashboard` (My products first), `/o/<slug>/apps` (My products with launch errors), `/o/<slug>/explore` (was `/marketplace`), `/o/<slug>/checkout`, `/o/<slug>/orders`, `/o/<slug>/help` (Support). Organization, team, profile and security stay under `/o/<slug>/settings/*`. |
| G23: dead configuration | CORS settings removed (no middleware read them; the browser reaches the API same-origin through the rewrite). The password-validator stub is **kept**: it is the planned breach-corpus control (P1-5), not dead code. The frontend's currency list is one constant (`SUPPORTED_CURRENCIES`), not three. It is not yet served by the backend. |
| 4.1: a purchase started before signup | Carried in `sessionStorage` (a plan version id and interval, validated on read), never in a redirect or query parameter. After verification the organization's page resumes it at checkout. |
| 4.4: bootstrap | As planned. It also refuses once a *different* security administrator exists (no back door), and `--dry-run` reports what it would do. |
| 4.4: staff sign-in audit | Recorded as `staff.signed_in` / `staff.signed_out` in the control-plane audit log and shown by the existing Audit page. There is no separate page. |
| — | Added: the customer's billing account can no longer be opened with `collection_mode` or `billing_region`; staff set them through `admin/customers/<id>/billing-terms` (dangerous, audited in both logs). |
| — | Added: `platform/identity/lifecycle.py`, the one writer of `User`. Signup, invitations, staff user actions, the operator commands and the control-plane anchor call it; before Phase 9 three modules outside identity wrote the table. Found by the final verification. `Organization` and `Membership` had the same shape (D-032); the Phase 9 closure gave them one writer, `platform/organizations/services.py` ([status](phase-9-status.md)). |
| — | Added: an end-to-end Product #20 test (`tests/phase9/test_product_twenty.py`). A product whose code appears nowhere in the source is built in the Super Admin console, registered by its manifest, listed publicly, bought and launched, with no code change. |

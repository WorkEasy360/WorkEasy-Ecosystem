# Phase 9: operations

This runbook is for whoever deploys and runs the platform. It covers what Phase 9 adds: public signup, email verification, password reset, pay-first orders, the Super Admin bootstrap and Platform settings. Design: [00-plan.md](00-plan.md), [ADR-035](../architecture/adrs/ADR-035-pay-first-orders.md), [ADR-036](../architecture/adrs/ADR-036-self-service-accounts-and-staff-roles.md).

## 1. Deploying

**Migrations.** All Phase 9 migrations are additive, and each backfill is deterministic. Run them with the migration role, as for every phase:

| Migration | What it does |
|---|---|
| `identity.0004_self_service_accounts` | Adds `acquisition_source` and the `pending` status value, and creates `identity_account_token`. |
| `administration.0001_platform_settings` | Creates the Platform settings singleton. |
| `catalog.0003_product_presentation` | Adds `slug` (backfilled from `code`, then made unique), `tagline`, `marketing_url` and `listing` (default `listed`, so nothing disappears from sale). |
| `payments.0002_orders` | Creates `payments_order`, `payments_order_summary` and `payments_order_number_seq`. Makes `Invoice.subscription` nullable and adds `Invoice.order`, with a check that each invoice has exactly one of them. |
| `provisioning.0002_customer_access_class` | Adds `access_class` and backfills it. The hourly reconcile corrects any row the backfill judged differently. |
| `authz.0009_billing_manage_not_dangerous` | Reversible data migration. |
| `tenancy.0011_phase9_isolation` | RLS (FORCE, USING, WITH CHECK) on `payments_order`, composite foreign keys for `payments_order.subscription_id` and `payments_invoice.order_id`, and grants on the new tables and sequence. |

**Behaviour changes to announce before the release:**

1. **Customer purchases are pay-first.**
   - `POST /orgs/<slug>/subscriptions/purchase` and the customer `subscriptions/<id>/activate` route are **removed**.
   - The customer console uses `orders` instead.
   - An integration that called the removed routes directly gets a 404.
   - Staff and partner selling are unchanged.
2. **Paying an invoice no longer needs a second factor.** `platform.billing.manage` is no longer dangerous (owner decision). Refunds, void, mark-paid and provider configuration still need AAL 2.
3. **A customer can no longer set `collection_mode` or `billing_region`** when opening a billing account. Staff set them through *Customers → Billing terms*.
4. **Everyone lands on `/dashboard` after signing in.** Staff go on to `/admin`. `/` is now the public website.

**Frontend.**
- The new public routes are `/`, `/products/<slug>`, `/pricing`, `/services`, `/contact`, `/signup`, `/verify-email`, `/forgot-password` and `/reset-password`. They are listed in `src/proxy.ts` `PUBLIC_PATHS`.
- Every route still renders dynamically, so each one carries its CSP nonce. `scripts/assert-dynamic-rendering.mjs` checks this.

## 2. The first Super Admin

The platform has no built-in administrator account and no credential anywhere in the repository, image or CI. The first administrator comes from the deployment's secret store:

```sh
# Both values from the secret store (for example, injected as environment
# variables into the release job). Never on the command line.
PLATFORM_SUPERADMIN_EMAIL=<from secret store>
PLATFORM_SUPERADMIN_PASSWORD=<from secret store>
python backend/manage.py bootstrap_superadmin --dry-run   # says what it would do
python backend/manage.py bootstrap_superadmin
```

| Situation | Result |
|---|---|
| No account for the address | Creates it: verified, active, source `operator`. The password is checked against the platform's password rules. Grants `SUPER_ADMIN`. |
| The account exists but is not staff | Grants `SUPER_ADMIN`. **The password is not changed.** |
| The account already is the Super Admin | Changes nothing and exits 0. It is safe as a step in every deploy. |
| Someone *else* already holds security administration | Refuses (non-zero exit). Further staff are added in the Super Admin console, with a reason, a second factor and an audit entry. |
| Missing or invalid address, or a weak password for a new account | Refuses without echoing the value. |

Rules for operators:

- **The password is never printed, logged or audited.** The audit entry `staff.bootstrap_superadmin` records only that it ran and what it granted.
- **To change the Super Admin's password,** sign in and change it, or use *Forgot password*. The bootstrap never resets a password, so rotating the secret-store value does not change a live account.
- **After the first successful run,** `PLATFORM_SUPERADMIN_PASSWORD` may be removed from the release job. It is needed only when the account does not exist yet.
- **The new administrator must enrol two-step verification** before any dangerous action. Dangerous actions need AAL 2.
- `w360_bootstrap_staff` no longer exists. `w360_staff_identity` (designating further staff identities) is unchanged.

## 3. Configuration

**Environment settings** (defaults in `backend/config/settings/base.py`; commented placeholders in `.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `W360_FRONTEND_URL` | `http://localhost:3000` | Base of every emailed link (verification and reset). **Must be the public HTTPS origin in production.** |
| `W360_ACCOUNT_VERIFY_TTL_HOURS` | 24 | How long a verification link works |
| `W360_PASSWORD_RESET_TTL_MINUTES` | 60 | How long a reset link works |
| `W360_PENDING_ACCOUNT_TTL_DAYS` | 7 | When a never-verified signup is deleted, freeing the address |
| `W360_ORDER_TTL_HOURS` | 24 | When an unpaid order expires and its invoice is voided |
| `W360_PUBLIC_CATALOG_CURRENCY` | `INR` | The public catalog's currency before Platform settings override it |
| `W360_RL_SIGNUP_IP` / `_ACCOUNT` | 10/min, 5/h | Signup rate limits (per client address, per hashed inbox) |
| `W360_RL_VERIFY_EMAIL_IP`, `W360_RL_VERIFY_RESEND_IP` / `_ACCOUNT` | 20/min; 10/min, 5/h | Verification limits |
| `W360_RL_PASSWORD_RESET_IP` / `_ACCOUNT`, `W360_RL_PASSWORD_RESET_CONFIRM_IP` | 10/min, 5/h | Reset limits |
| `W360_RL_PUBLIC_CATALOG_IP` | 120/min | Anonymous catalog reads |
| `W360_RL_ORDER_PLACE_USER` / `_IP` | 20/min, 60/min | Order placement |

**Email is required for signup.** Verification and reset emails go through the platform's notification email channel, anchored on the platform organization. If that channel is not configured, signups cannot complete. In that case, close signup in Platform settings until it is configured.

**Platform settings** (Super Admin → Platform settings) apply at runtime and need no deploy:

- the signup switch;
- the support and sales addresses shown on the site;
- the public catalog currency.

Changing them is dangerous (AAL 2), versioned (a stale edit gets 409) and audited as `platform.settings_updated`. The public site caches them for 30 seconds and the public catalog for 60 seconds, per currency.

**Product presentation** (Super Admin → Products → Edit):

- `slug`, tagline, marketing URL (HTTPS only), sort order, and listing, which is one of:
  - `listed`
  - `coming_soon`
  - `hidden` (private)
- **Changing a slug breaks old product-page links.** The code never changes.
- Deprecating or retiring a product removes it from the site and Explore. Existing holders keep it.

## 4. The clock

| Beat entry | Cadence | What it does |
|---|---|---|
| `payments-process-due` | 5 min (`W360_BEAT_PAYMENTS_S`) | Existing dunning, plus: expires unpaid orders past their TTL (voiding their invoices), and re-drives orders paid more than a minute ago but not yet fulfilled. Its totals include `expired_orders` and `fulfilled_orders`. |
| `administration-prune-activity` | daily (`W360_BEAT_IDENTITY_PRUNE_S`) | Existing pruning, plus: deletes used or expired account links older than 30 days, and never-verified signups older than `W360_PENDING_ACCOUNT_TTL_DAYS`. It skips an account something already references. |

If the worker is down, paid orders still fulfil straight after payment, because fulfilment runs after commit. The clock is the backstop for a lost after-commit hook.

## 5. Monitoring

| Metric | Watch for |
|---|---|
| `identity_signups_total{outcome}` | A spike of `existing` or `resent` suggests address probing. The answers are uniform, but the rate limits should be firing. |
| `identity_email_verified_total`, `identity_verification_resent_total` | A low verified-to-signup ratio suggests email delivery trouble. |
| `identity_password_reset_requests_total{outcome}`, `identity_password_resets_total` | Abuse of the reset flow |
| `commercial_orders_placed_total{outcome}` | Checkout health |
| `commercial_orders_fulfilment_total{outcome}` | **Any `failed` outcome**: a customer paid and did not get the product. The payment is refunded automatically; check why. |
| `payments_automatic_refund_failures_total` | **Page on any.** Money was taken and the automatic refund could not be requested. Refund it by hand (Super Admin → Payments). |

## 6. Playbooks

- **"I paid but the product isn't there."**
  1. In Super Admin → Orders, find the order by organization.
  2. `paid`, not `fulfilled`: the clock re-drives it within about 5 minutes. If it stays paid, check the worker and the `payments.order.fulfilment` log line.
  3. `failed`: the payment was refunded automatically, and `failure_reason` says why (usually the product was acquired another way meanwhile).
- **"I never got the verification email."**
  1. They can use *Resend* on the sign-in or verify page.
  2. Check the platform organization's notification outbox for delivery failures.
  3. Links expire after 24 hours; a resend issues a fresh one and retires the old.
- **Someone registered with another person's address.** The pending account cannot sign in, and the real mailbox owner is not blocked:
  - *Forgot password* replaces the password and activates the account;
  - otherwise the signup is pruned after 7 days.
- **A partner-provisioned owner cannot sign in.** They use *Forgot password* at `/forgot-password` with the address the partner entered, then accept the owner invitation. The acceptance is API-only today; see D-020 in the debt register.
- **Locked out of the control plane.** The last permanent security administrator cannot be disabled or stripped of the role, from the console or the command line. With a working administrator account, use the console. Otherwise, run `bootstrap_superadmin` for a new address only when no security administrator remains. This is an operator action and it is audited.

# Phase 8 extension — consoles, access controls, communication and payments

Status: see [extension-status.md](extension-status.md). The Phase 8 brief's
AI/RAG/analytics work is complete and unchanged; this extension was requested
afterwards and widens the phase:

- a Zoho-One-style **organization console** (left sidebar + dashboard) that owners
  and administrators land on; ordinary members land on their apps;
- the **Super Admin console** rebuilt around the agency-panel layout (Audience,
  Billing, Panel customization), including a **plan builder**;
- four new shared platform capabilities: **auth configuration**, **communication**,
  **payments**, **devices & network**.

Constraints carried from the request: shared platform capabilities only, reuse the
existing authentication, billing, notification, audit, entitlement and security
architecture, no product-domain logic, payments through provider adapters with no
raw card data and secrets only behind the secret boundary, device/network controls
*add to* (never replace) authentication, RBAC, tenant isolation and MFA; payment
operations, auth-policy changes, IP allow-list changes and session revocation are
**dangerous (AAL 2) and audited**.

Decisions: [ADR-033](../../architecture/adrs/ADR-033-session-registry-and-organization-access-policy.md)
(sessions and access policy), [ADR-034](../../architecture/adrs/ADR-034-payments-hosted-checkout-and-provider-adapters.md)
(payments). Operations: [01-operations.md](01-operations.md).

## 1. Identity: sessions, account events, passwords

| Piece | Where | Notes |
|---|---|---|
| Session registry | `platform/identity/sessions.py` (custom DB session engine, `identity_session`) | Extends Django's DB backend with `user`, `created_at`, `last_activity_at`, `ip_prefix` (/24 or /48), `device_label` (closed vocabulary). Revoking a session deletes its row, so the next request is anonymous with **no extra query per request**. Enforces `SESSION_ABSOLUTE_TIMEOUT` on load. Deploying it signs everyone out once (old `django_session` rows are not read). |
| Account events | `identity.AccountEvent` | sign-in, failed sign-in (known accounts only), password changed, session revoked, other sessions revoked, signed out everywhere, disabled / enabled / unlocked by staff. Masked prefix and device label, never the raw IP or user agent. Pruned after `ACCOUNT_EVENT_RETENTION_DAYS` (400). `User.last_login_at` is now written. |
| Password change | `POST auth/password` | current + new; platform validators **plus** the highest minimum length of the user's organizations; a wrong current password counts as a failed sign-in; bumps `session_epoch` (every other session ends) and re-establishes the current one. |
| Own sessions | `GET auth/sessions`, `POST auth/sessions/<id>/revoke`, `POST auth/sessions/revoke-others`, `GET auth/activity` | revocation is **AAL 2**; the current session cannot be "revoked" (sign out instead). |

## 2. Organization console backend (`platform/administration`, P4)

- `GET orgs/<slug>/access` — the caller's membership, roles, effective
  permissions and console (`admin` when they hold `platform.audit.view` or
  `platform.member.invite`); records one `MemberSignIn` per (membership,
  session) — **tenant-scoped**, so an admin sees only sign-ins *into their
  organization*, never a member's activity elsewhere.
- `GET orgs/<slug>/dashboard?window=7|30|90` — members by status, added in the
  window, pending/expired invitations, daily distinct signed-in members,
  most-active members, apps; plus the subscription summary for holders of
  `commercial.subscription.view`. Each section is present only when the caller
  holds its permission.
- Members: directory with every status, role and last sign-in; change role
  (`platform.role.assign`, dangerous), suspend / reactivate
  (`platform.member.update`; suspension releases seats), sign out of this
  organization (`platform.member.sign_out`, **new, dangerous**: sets
  `Membership.sessions_valid_after`). The owner and oneself are protected; only
  the owner can sign the owner out.
- Invitations (the Phase 1 `Invitation` model, now written): invite by email with
  a role (inviting as admin or billing manager also needs `platform.role.assign`),
  list, resend (rotates the token), revoke; public preview + accept, which either
  joins a signed-in invitee (the invited address only) or creates the account
  (password set here). Bad, expired and revoked tokens get the same answer. The
  token is stored hashed; the email link travels in the notification's **sealed
  context** (Fernet, `W360_NOTIFICATION_SEAL_KEYS`, bound to organization and
  notification, wiped after the final delivery attempt), so a database dump
  yields no working link.
- `GET orgs/<slug>/audit` (`platform.audit.view`, declared since Phase 1, now
  enforced) and `GET orgs/<slug>/sign-ins`.
- Organization rename (`PATCH orgs/<slug>/organization`,
  `platform.organization.update`) with projection sync.
- The **Billing Manager** role ("billing and subscriptions only") now also holds
  `commercial.subscription.view` (`authz.0008`), so the billing page shows the
  subscription beside the invoices the role pays. Buying, changing and
  cancelling (`commercial.subscription.manage`) stay the owner's.

## 3. Auth configuration + devices & network (per organization)

Stored on `Organization` (the row the tenant resolver already loads — no extra
query per request): `require_mfa`, `session_max_age_minutes`,
`password_min_length`, `ip_allowlist_enabled`, `ip_allowlist`,
`security_policy_version`. Evaluated by a pure function in
`contracts/values/access_policy.py` from the tenancy middleware **and** the OIDC
authorize path, after membership resolution (so it never widens access):

| Rule (checked in this order) | Denial |
|---|---|
| client IP outside an enabled allow-list | 403 `network_not_allowed` |
| session older than `session_max_age_minutes`, or signed in before `Membership.sessions_valid_after` | 401 `reauthentication_required` |
| `require_mfa` and the session has not verified a code | 403 `mfa_required` (enrol or verify, then retry) |
| password below the organization's minimum (evaluated at sign-in) | 403 `password_change_required` |

To a product the same denials arrive as the OIDC error `access_policy_denied`
([Phase 3 OIDC contract](../../phase-3/04-oidc-contract.md) §7).

Allow-lists: at most 50 entries, IPv4 no wider than /8, IPv6 no wider than /16,
normalised. Password policy is **length only** (no composition rules, per NIST
SP 800-63B). Changes need `platform.security.update` (dangerous, AAL 2, audited,
optimistic version). An allow-list that would exclude the caller's own address is
refused. Break-glass: a Super Admin with `platform.staff_customer.manage` can
reset a customer's network policy (AAL 2, reason, audited). Product
server-to-server calls (client credentials) are not user traffic and are not
IP-filtered.

## 4. Communication (Super Admin)

Template overrides (`notifications.TemplateOverride`) for the code-registered
templates: subject/body with **only** that template's declared `{variables}` (no
attribute or index access, no format specs — validated with `string.Formatter`),
required variables kept, one-line subject, plain text only. A broken override
falls back to the code default at delivery. Sender display name and reply-to
(`CommunicationSettings`); the from-address stays the deployment's. Preview with
sample values and send-test (to the caller's own address only). Audit records
which template changed, never its text. `platform.staff_communication.view/manage`
(manage dangerous).

## 5. Plan builder (Super Admin, reuses the catalog)

"Plans" in the agency layout = **bundles** (a package of product packages priced
per billing cycle); "product packages" = catalog **plans**. Added: `caption`,
`billing_type` (paid | free_trial; frozen once the item is on sale), plan `kind`
(standard | add_on, with the product an add-on requires; add-ons never join a
bundle), bundle-version `trial_days` (frozen on publication by trigger), the
**semi-annual** interval (6 months), list/read endpoints for plans, bundles,
price books and prices, and metadata updates. A free trial opens a new
subscription once per organization. Status mapping: Draft = draft, Private =
available + not public, Public = available + public, Deactivated =
grandfathered (existing customers keep it). The org **marketplace** lists public
available plans and bundles with prices in the organization's currency.

The agency panel's "hard limit on inclusions" was not built: inclusions are
already exact (a bundle version lists its plans and quantities), so the flag
would have had no behaviour.

## 6. Payments (`commercial/payments`, beside provisioning)

- Provider adapters in `commercial/payments/providers/` — **sandboxed like AI
  providers** (no DB, settings, network or credentials; build an HTTP call, parse a
  response, verify a webhook). Adapters: `stripe` (Checkout Sessions), `razorpay`
  (Payment Links), `fake` (development/test only; pinned off in production).
- Credentials are **references** (`W360_PAYMENT_CREDENTIAL_<REF>`,
  `W360_PAYMENT_WEBHOOK_SECRET_<REF>`) read live per call, never stored, logged or
  returned. Hosted checkout only: card data never reaches Workeasy360.
- Invoices per (subscription, period) from the price snapshots on the subscription
  items, issued by the commercial-change listener and repaired by the payments
  clock; zero totals are not invoiced; `partner_billed` is skipped.
- Pay (`platform.billing.manage`, dangerous, `Idempotency-Key`): creates a
  `PaymentAttempt` and a due-work timer and returns 202; the provider call runs
  **outside any transaction** in a task; the browser polls the attempt and is
  redirected to the hosted page. One attempt in flight per invoice.
- Webhooks (`POST public/payments/webhooks/<provider key>`): size-limited,
  signature-verified, de-duplicated by provider event id, applied under row
  locks, idempotent and order-tolerant; a mismatched amount or currency never
  settles. A paid invoice restores a past-due or grace subscription through
  `subscriptions/system.py`, and lifts a suspension only if dunning caused it.
- Dunning (clock, every 5 minutes): unpaid past due → `past_due`; after
  `PAYMENT_GRACE_AFTER_DAYS` (7) → `grace`; after `PAYMENT_SUSPEND_AFTER_DAYS`
  (21) → `suspended`. Stale checkouts expire after `PAYMENT_ATTEMPT_TTL_MINUTES`.
  Notices: invoice issued, payment received, payment failed.
- Staff (`platform.staff_payment.view/manage`, manage dangerous): providers
  (enabling one needs both secrets present), per-customer invoices and attempts,
  webhook log, collections summary, refunds (async, idempotent, never above the
  captured amount), manual mark-paid / void with a reason and version.

## 7. Super Admin extras

User directory (`platform.staff_user.view`) with sessions and account activity,
never credentials; account actions (`platform.staff_user.manage`, dangerous, with
a reason): sign out everywhere, unlock (both lockouts), disable / enable (not
oneself). The dashboard adds users and collections.

## 8. Frontend

One `SidebarShell` (grouped, collapsible, permission-gated, keyboard and 320 px
safe, drawer below 900 px) used by the org console, the Super Admin console and
the partner console. Landing: staff → `/admin`; partner-only accounts →
`/partner`; otherwise the org flow, where owners/admins land on
`/o/<slug>/dashboard` and members on `/o/<slug>/apps`. A `PolicyGate` turns the
four access-policy denials into the right next step (verify a code, change the
password, sign in again, or the network message). Charts are small accessible
SVG components with a table equivalent (no chart library).

## 9. Tests

`backend/tests/extension/` (sessions, org console, invitations incl. a
transactional acceptance race, access policy incl. OIDC, communication, payments
incl. transactional webhook and pay-click races, plan builder, staff users and
cross-cutting security, failure injection); the security matrix and
architecture suites extended for every new permission, table, allow-listed
module and deferred foreign key; `scripts/rehearse_extension_upgrade.py` (fresh
database, Phase 8 → extension upgrade with byte-level checksums, rollback and
re-apply); frontend unit and axe tests per state.

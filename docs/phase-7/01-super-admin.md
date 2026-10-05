# Super Admin control plane

## Who is staff

A staff member is a user holding one or more live `StaffGrant` rows (table
`authz_staff_grant`). A grant names a permission, the person who issued it, a
**reason**, and optionally an **expiry** (`expires_at`; a grant confers nothing
from that instant, and the terms - expiry included - are frozen by trigger: to
change them, grant anew). Ending a grant is a state change, never a delete, so
the history of who could do what is retained. Permissions are read with no
cache: revoking or expiry takes effect on the next request.

Staff authority is two separate things, held by different people on purpose:

- **Being a staff identity** (`User.is_staff`) is an operator decision:
  `manage.py w360_staff_identity --email <existing account> --enable --reason
  "..." --confirm` (audited; `--disable` ends every grant's effect at once and
  keeps the grant history). It confers nothing by itself.
- **Holding a permission** is a `StaffGrant`, issued in the Super Admin UI by a
  security administrator, with a reason, an optional expiry and AAL 2. Grants
  can only be issued to staff identities, so a compromised security
  administrator cannot hand power to a fresh account it controls.

**Neither `is_staff` nor `is_superuser` is an authority anywhere.** The older
organization-scoped routes for the catalog, price books and the product
integration registry check the same live grants as `/admin` (final audit A-01:
before, `is_staff` plus any organization's owner role sufficed).

| Permission | Allows | Dangerous (AAL 2) |
|---|---|---|
| `platform.staff_customer.view` | Customer directory, detail | |
| `platform.staff_customer.manage` | Suspend / reactivate an organization | yes |
| `platform.staff_catalog.view` / `.manage` | Read / change products, plans, bundles | manage |
| `platform.staff_pricing.manage` | Price books and prices | yes |
| `platform.staff_subscription.manage` | Suspend, resume, cancel, change plan or quantity; entitlement overrides | yes |
| `platform.staff_billing.view` | Billing profile summary | |
| `platform.staff_partner.view` / `.manage` | Read / administer partners, capabilities, members, products, links | manage |
| `platform.staff_commission.view` / `.manage` | Read / change rules, reversals, adjustments | manage |
| `platform.staff_payout.manage` | Prepare, ready, record-paid, cancel payouts | yes |
| `platform.staff_payout.approve` | Approve a payout | yes |
| `platform.staff_support.view` | Access diagnostics | |
| `platform.staff_audit.view` | Read the audit trail | |
| `platform.staff_integration.view` / `.manage` | Read / register, change redirect URIs, lifecycle and credentials of product integrations | manage |
| `platform.staff_security.view` / `.manage` | Read / issue / revoke staff grants | manage |

Dangerous permissions imply the underlying `commercial.*` permission the
existing service requires, so the existing service performs the existing check
under a staff context - there is no parallel implementation.

**The first administrator.** The UI grants staff permissions only to someone who
already holds them, so the first grant is an operator action:
`python manage.py w360_bootstrap_staff --email <existing account> --confirm`. It
names an **existing, active** account (nothing is created), grants
`platform.staff_security.manage` only, records the grant as made by the
control-plane system identity with a reason, audits it (`staff.bootstrap_admin`),
and **refuses once any security administrator exists**, so it is not a back door.
Without `--confirm` it only reports. Everything else is then granted through the
UI, with a reason and a second factor (after an operator has designated each
further person with `w360_staff_identity`). Django's `is_superuser` confers
nothing. The check and the grant run under an advisory lock, so two operators
running it at once cannot both succeed.

**Last security administrator.** `end_grant` locks every holder of
`staff_security.manage` in id order before deciding, so two concurrent
revocations cannot leave the platform with no one able to grant access. Only
**permanent** holders count: a time-boxed administrator lapses on its own, so
the last permanent one cannot be removed while only temporary ones remain.

## Screens and routes (`/admin`)

Overview, Customers (+ detail), Products, Partners (+ detail), Commissions,
Payouts, Diagnostics, Audit, Security. Navigation is derived from the caller's
permissions; the routes are authorized again by the backend.

The API lives under `/api/v1/admin/...` (see `interfaces/api/v1/urls.py`). Every
unsafe route requires CSRF and, when dangerous, AAL 2 - a missing step-up is a
401 `step_up_required` that the UI turns into the existing MFA prompt and then
retries.

## Customer directory

`GET /admin/customers` is keyset-paginated and reads **one page of organizations
plus one batched read of `CustomerSummary`** - never a per-row lookup. The
summary (subscription status, products, seat capacity) is a projection written
by provisioning after each reconciliation and is labelled "as of the last
reconciliation" on screen. A failure to write the projection is isolated: it
never fails the provisioning that triggered it (it is counted and logged), and
the next reconciliation repairs it. Directory query count is independent of
page size (asserted).

## Lifecycle

Suspend and reactivate go through `organizations.set_lifecycle_status`, which
records who, why and when, audits, and emits the existing organization event. A
suspended organization keeps its data and its audit trail; writes are blocked by
the existing R-10 write block.

## Subscription administration

Staff can suspend, resume, cancel, activate, change plan and change quantity.
Each goes through the existing subscription service; the staff-only ones
(`suspend`, `resume`) are new services in the same module, with the same
state-machine and version checks. **Changing a customer's bundle is deferred**
(D-021).

## Entitlement overrides

A staff override is an entitlement grant issued **through provisioning** (the
one entitlement writer), with a required reason and a hard cap of 90 days. It is
visible on the customer detail with its reason and expiry, and revocable. It
never edits a subscription.

## Support diagnostics

See [04-security-and-authorization.md](04-security-and-authorization.md) for the
authorization; the behaviour is:

`POST /admin/customers/<org>/diagnose` answers *"why can user X not launch
product Y?"* by walking the same chain the launcher walks - organization
status, membership, product availability, entitlement, seat, integration - and
returning each step with `ok`, a stable `code`, and the first failing step. It
is **read-only** (asserted: the request writes nothing, audit aside), rate
limited per user and per IP, and returns no secrets, tokens or personal data
beyond the email the caller supplied.

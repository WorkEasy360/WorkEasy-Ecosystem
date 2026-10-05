# Phase 8 extension — Status

**STATUS: PASS** (2026-09-26)

The extension adds the organization console (Zoho-One-style sidebar and
dashboard for owners and administrators), the Super Admin console in the
agency-panel layout with a plan builder, and four shared platform capabilities:
auth configuration, devices & network, communication and payments. Everything
is a platform capability reusing the existing identity, authorization,
notification, audit, commercial and tenancy architecture; nothing is
product-specific. Design: [00-design.md](00-design.md). Operations:
[01-operations.md](01-operations.md). Decisions: ADR-033, ADR-034. Debt:
D-026 … D-030.

## Gates

| Gate | Result |
|---|---|
| backend `pytest` (full suite) | **2,303 passed, 0 failed** (40 min; the 2 warnings are structlog's pre-existing `format_exc_info` notice) |
| `ruff check` / `ruff format --check` | pass / pass (570 files) |
| `mypy` (contracts, strict) | pass (29 files) |
| `bandit -ll` | no issues |
| `pip-audit --strict` | no known vulnerabilities |
| `lint-imports` | 18 contracts kept, 0 broken (new: `payment-providers-are-sandboxed`) |
| `manage.py check --fail-level WARNING` (as the application role, incl. RLS verification) | no issues — fresh and upgraded databases |
| `makemigrations --check` | no changes — fresh and upgraded databases |
| frontend: clean `npm ci`, `tsc`, `eslint`, `vitest`, `next build`, `npm audit` | pass; 178 tests (4 consecutive clean runs); every route dynamic (CSP nonce); 0 vulnerabilities |
| production build in a real browser | 34 page states: 0 CSP violations, 0 axe (WCAG 2.1 A/AA) violations, focus visible everywhere, exactly one `h1` per page; 30 URLs with no horizontal scroll at 320px; 6 flows pass (below) |
| fresh database | pass (`scripts/rehearse_extension_upgrade.py`) |
| Phase 8 → extension upgrade, rollback, re-apply | pass — no pre-existing column of any row changed; only the three new role grants added; new columns hold safe defaults; 55/55 checks overall |

## What was built

**Organization console** (`/o/<slug>/…`): dashboard (member counts,
invitations, sign-ins per day, most-active members, apps, subscription);
members (roles, suspend/reactivate, sign out of this organization);
invitations (invite with role, resend, revoke; public acceptance page that
signs in or creates the account); profile; subscription & billing (billing
account, invoices, pay online with step-up); marketplace (public plans and
bundles in the organization's currency); authentication policy (two-step
verification, session age, password length); network access (IP allow-list
that cannot lock out its editor); my security (password, own sessions,
activity); audit log; sign-in activity. Owners and administrators land on the
dashboard, members on their apps.

**Super Admin console** (`/admin/…`), grouped as in the agency panel:
dashboard (users, collections, partners, payouts); Audience (users with
sessions and account actions, customers with billing and break-glass network
reset, agencies & partners); Billing (plans & add-ons builder with
Draft/Private/Public/Deactivated, free-trial or paid, monthly / quarterly /
semi-annual / annual cycles and package inclusions; currency & pricing;
payments: providers, webhook log, invoices, refunds, manual settlement;
commissions; payouts); Panel customization (product manager, communication
templates and sender, AI); Security & support.

**Partner portal** (`/partner/…`) moved into the same sidebar shell (covered by the
frontend unit and axe tests; the browser run above did not seed a partner).

## Security properties (each with tests)

- Access policy only narrows access and is evaluated in the tenancy
  middleware **and** OIDC authorize; product sign-ins get `access_policy_denied`.
- Dangerous (AAL 2, refused under impersonation, audited): security-policy and
  allow-list changes, session revocation, member sign-out, role changes, every
  payment operation, staff account actions, provider configuration, template
  changes.
- Sessions: registry with revocation by row delete, absolute lifetime
  enforced, masked network prefix and coarse device label only.
- Invitation links: hashed tokens; the link travels sealed in the notification
  and is wiped after delivery; bad/expired/revoked tokens are one answer;
  acceptance races admit a person once.
- Payments: hosted checkout only; secrets by environment reference; sandboxed
  adapters; signature-verified, de-duplicated webhooks settled under row locks;
  a payment lifts only a dunning suspension; refunds bounded and idempotent;
  production pins the test provider off.
- Tenant isolation: every new tenant table has forced RLS and composite foreign
  keys; the dashboard never counts another organization's activity; a webhook
  cannot reach another tenant's payment.

## Browser flows (production build, real backend)

| Flow | Result |
|---|---|
| owner signs in → organization dashboard | pass |
| member signs in → My apps | pass |
| staff sign in → Super Admin dashboard | pass |
| drawer below 900px (org console and Super Admin): Menu opens and moves focus in, Escape closes and returns focus, navigating closes | pass |
| Pay an invoice → step-up with an authenticator code → hosted test checkout → webhook → invoice paid | pass |

The only console entry across the run is the `401 step_up_required` answer to
the first Pay click — the protocol that raises the verification prompt.

## Findings fixed during verification

| Finding | Fix |
|---|---|
| The audit redactor blanked policy change values (keys containing `mfa`, `password`, `session`) | audit change keys renamed (`two_step_verification`, `minimum_length`, `sign_in_max_age_minutes`, …) |
| Payment return address pointed at a page that does not exist | `/o/<slug>/settings/billing`, pinned by the Stripe checkout test |
| Billing Manager ("billing and subscriptions only") could pay invoices but not see the subscription | `commercial.subscription.view` granted by `authz.0008`; buying stays with the owner |
| After step-up, a retried action's failure was lost and the form was not busy | `useStepUp({onRetry})`; `useAction` runs the retry through its own path |
| Sidebar group titles below 4.5:1 contrast | muted text colour |
| Wide tables could not be scrolled by keyboard | the table wrapper is a named, focusable region |
| Visually hidden labels in table cells widened the page to 550px at 320px | the table wrapper is their containing block |
| The small-screen drawer did not take focus when opened; reduced-motion did not apply to it | visibility switches immediately on open; reduced-motion rule last |

## Open decisions for the owner

- Should the Billing Manager also **buy, change and cancel** subscriptions
  (`commercial.subscription.manage`)? Today only the owner (or a custom role)
  can; the role's documented scope suggests it could.
- D-028 (no stored payment method / automatic renewal charge) and D-029 (no
  tax calculation) must be decided before self-serve renewals and before
  selling in taxed markets.
- D-020 (partner-provisioned owner cannot set a credential) remains the
  reseller go-live blocker; the invitation flow built here supplies every
  piece it needs.

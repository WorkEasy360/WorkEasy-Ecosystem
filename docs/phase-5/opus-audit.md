# Phase 5 — Final adversarial audit

**Result: PASS**, after fixing three P1s and six smaller defects found
during it. No P0. Nothing left open blocks Phase 6.
**Date:** 2026-09-23
**Scope:** the 68-item Phase 5 freeze brief: onboarding, idempotency, owner
bootstrap, the access-directory projection, MFA/AAL, migrations, deferred
FKs, degradation, CSP/browser, performance. No Phase 6 work, no product
domain, no Super Admin, reseller, white-label, affiliate or payments.

**Baseline before any change:** 1,121 backend / 41 frontend, per
[phase-5-status.md](phase-5-status.md).
**After:** 1,182 backend (0 failed, 0 skipped, fresh test database; 61
added by this audit), 41 frontend, every gate green (§6).

This file is the audit's evidence. Design stays in
[00-overview.md](00-overview.md); the evidence for the completion pass stays
in [phase-5-status.md](phase-5-status.md).

---

## 1. The headline

The three P1s all kept 1,121 tests green. None was visible from inside the
suite, because each needed an input or a timeline no test used:

1. **An attacker holding only a password could reach AAL2 in weeks.** The
   only limit on step-up guesses was a per-minute bucket (8/min/account).
   It resets every minute, so it allowed ~11,500 guesses a day, forever.
   Against a 6-digit code with a three-step window (p = 3×10⁻⁶ per guess),
   that is a 50% chance of AAL2 in about 20 days. With AAL2 comes enrolling
   the attacker's own authenticator, removing the victim's, and every
   `dangerous` permission. Reproduced: 200 consecutive wrong codes all
   answered 400, and the correct code was then still accepted.
2. **Removing a member leaked their seats for good.** `remove_member` left
   the member's `SeatAssignment` rows active. The seat screen lists active
   members only, so nobody could free those seats from the UI. Each removal
   of a seated member shrank the organization's usable, paid-for seats, and
   the next member was refused with a 402. Reproduced: with one seat,
   remove its holder and assigning it to anyone else raises
   `SeatLimitReached`.
3. **Any organization name containing `_` produced an organization its
   owner could never open.** `slugify` keeps underscores, but the tenancy
   router only accepts `[a-z0-9-]`. "Acme_Corp" was created (201, slug
   `acme_corp`), and every `/orgs/acme_corp/...` request then ran with no
   tenant at all: 403 `missing_tenant_context`, or a 500. It failed closed,
   so no data leaked, but the organization was dead on arrival.
   Reproduced over HTTP.

## 2. Findings

### P0

None.

### P1 — all fixed, each with a regression test that failed before the fix

| # | Defect | Fix |
|---|---|---|
| P1-1 | Step-up guessing bounded only per minute (above) | Durable per-account backoff in PostgreSQL: `User.mfa_failed_attempts` and `mfa_locked_until` (`identity.0002`). One conditional `UPDATE` counts the attempt *before* the code is evaluated, and refuses it while locked. Ten free consecutive failures, then a 1-minute lock doubling to a 24-hour cap. A success clears it. A locked attempt is refused without evaluating the code, so the lock is not an oracle. It is per account and in the database, so it survives new sessions, other instances and a Redis outage (D-012 does not apply). An attacker now gets ~20 guesses on day one and ~1 a day after: about 0.1% per year. 429 + `Retry-After`. `identity/services.py`, `mfa_views.step_up_view`. |
| P1-2 | Member removal leaked seats (above) | `remove_member` releases every seat the member holds in the same transaction (`entitlements.services.seats.release_all_seats`) and records `seats_released` in the audit entry. The race this opened is closed too: seat assignment now locks the membership row (`active_membership_exists(lock=True)`). A removal committing between the "is active" check and the insert used to leave a seat on a removed member (reproduced deterministically). Now the removal waits and then releases it. |
| P1-3 | Underscore names made unroutable organizations (above) | `generate_unique_slug` reduces the slug to the router's alphabet, `[a-z0-9-]`. A 15-name sweep checks every generated slug, first and colliding, against `organization_slug_from_path` itself. The names cover underscores, Unicode, CJK, emoji, reserved words, 255 characters and the truncation boundary. |

### P2

| # | Item | Status |
|---|---|---|
| P2-1 | MFA changes left **no trail at all**: no audit row, no log line, only aggregate counters. Enrolment, removal and step-up could not be traced to an account. | **Fixed.** Structured security events: `identity.mfa.enrollment_started`, `factor_enrolled`, `factor_removed`, `step_up_succeeded`, `step_up_failed`, `step_up_locked`. They carry identifiers only (`user_id`, `factor_id`), never a secret, a code or a recovery code; the existing full-flow log-capture test still passes. They are logs, not `audit_event` rows: that table is tenant-scoped and a factor belongs to no organization. |
| P2-2 | Trusted client IP: the docs never mention the frontend's Next.js hop, which every browser API call crosses. Verified against the production build: the `/api` rewrite **appends nothing** to `X-Forwarded-For` and passes a client-sent value through unchanged. So Next.js must never be the public edge, because then no count is right. With `0`, every user shares one bucket and 30 bad logins a minute from anyone throttle everyone. With `1`, the caller picks the bucket. Behind an ALB the right answer is `1`, not `2`. | **Fixed (documentation).** `client_ip.py` docstring and `.env.example`. The code was already right. Per-account limits, including P1-1's, never depended on the IP. |
| P2-3 | The code claimed a 4xx domain error rolls back the tenant request transaction (`DomainErrorMiddleware`, `settings/base.py`, a `TenancyMiddleware` comment). It does not: Django calls `process_exception` inside that transaction, and only a >= 500 response rolls back. | **Fixed (claims) + D-016.** Every customer-reachable unsafe tenant route was traced. None commits a business write alongside a 4xx. The launcher's DENIED seat audit relies on the commit, which is why the semantics stay. Two staff-only races can leave an unpublished draft plan or bundle version behind. |
| P2-4 | Rotating `MFA_ENCRYPTION_KEY` invalidates every TOTP secret and recovery code. | **Kept as P2** (§4). |
| P2-5 | Step-up is per session, not per organization. | **Kept as P2** (§5). |
| P2-6 | Staff-only commercial endpoints answer an unknown id with a 500. Bare `.get()` calls: `catalog/services.py` lines 115, 171, 230, 271, 300, 338, 434, 549, 658, 724 (product, feature, plan, plan version, bundle, bundle version); `pricing/services.py` lines 192, 294 (price book, price). | **Kept as P2.** Platform staff only, and nothing is written, because the lookup comes first. Not customer-reachable. Fix when `commercial/` is next touched. |
| P2-7 | `notifications.notify` does not validate `recipient_membership_id`. | Kept (latent, no caller). |

### P3

Fixed: a crafted chooser or member-list cursor (JSON-number id) raised an
uncaught `AttributeError` → 500 for any signed-in user · the MFA responses
that carry the raw secret and the recovery codes had no `Cache-Control:
no-store` (now `never_cache`, like the token endpoint) · production accepted
`W360_MFA_ENCRYPTION_KEY` equal to `W360_SECRET_KEY` or the file-signing
key · status screens and the 404 had no `<main>`, so the layout's skip link
pointed at nothing (the unit test wrapped the component in its own `<main>`,
which hid it) · the chooser's `id` tie-break ran against the index's
direction (an incremental sort on page 1; now a plain index scan of 26 rows
at 1,000 memberships) · `ensure_system_role_catalog`'s docstring claimed the
migration calls it.

Open, none blocking:

- `authz.0002` imports the live `SYSTEM_ROLES` and permission registry.
  Renaming either breaks a fresh install. Freeze the spec into the migration
  when it is next touched.
- The `post_migrate` role sync only adds. Removing a permission from a
  system role's spec does not revoke it from an existing database; that
  needs a migration.
- Rolling `authz` back past `0002` is refused safely, but as a raw
  foreign-key traceback rather than a clear message.
- Organizations that were already ownerless before the upgrade stay
  ownerless. No check flags them (the rehearsal's "orphan" organization).
- There is no soft delete yet. Rebuild and drift skip deleted
  organizations, so when soft delete is added it must remove projection
  rows itself.
- Step-up does not rotate the session key (OWASP re-key on privilege
  change). It only matters to someone who already holds the cookie.
- The ID token's `aal` carries no `auth_time`. An `aal=2` assertion can
  outlive the 15-minute window by up to the ID-token TTL (5 min) plus the
  code TTL (60 s). Products that need freshness should get `auth_time`.
- `page-has-heading-one` (axe best-practice) on the "no access" status
  screen.
- One frontend test (`MfaSettings`, a chain of 1-second `findBy` waits)
  failed once while the machine was saturated. It passed 41/41 in four runs
  since.
- Recovery codes can be regenerated only by enrolling another
  authenticator.
- The P3s in [phase-5-status.md §26](phase-5-status.md#26-unresolved-items)
  are unchanged.

## 3. Audit by audit

**Creation atomicity, tenant scope, idempotency (1–3, 14–16).** Failure was
injected after each of the six stages, with real commits: membership, role,
audit, projection, outbox and idempotency completion. Every time, nothing
was left behind, no tenant variable was left bound, and a retry with the
same key built exactly one complete organization. Created from inside
another tenant's scope, the caller's scope was restored and B's never
leaked. Idempotency held with 8 threads on one key and with distinct keys
under a same-name race. In the production browser, two submits fired in one
tick produced one organization. Every protected field sent by the client is
ignored. Slug: see P1-3.

**Owner bootstrap and last owner (4, 59–60).** The owner gets exactly the
`owner` role in their own organization and is not staff (existing tests).
`uq_one_owner_per_org` allows one owner, the owner cannot be removed, and
there is no transfer path, so "last owner" is "the owner". Real threads
removing every member, the owner included, all at once: the owner's removal
was refused every time and the owner kept the role.

**Projection authority, IDOR, staleness (6–8).** Every reference to
`UserOrganizationAccess` was classified. It is read only by the chooser
selector (filtered by the session's own `user_id`) and by the
rebuild/drift tools. Nothing on an authorization path reads it; resolution
re-reads `Membership`/`Organization`. A cursor taken from another user's
chooser returned nothing of theirs. Existing staleness tests cover removed,
suspended, locked and poisoned rows.

**Rebuild races, idempotency, failure (9–11).** New real-thread tests:
rebuild against suspend/reactivate flapping; two rebuilds and a prune at
once; rebuild against eight concurrent creations. All converge with drift
clean. A rebuild made to fail midway rolls that organization back as a
unit. The others finish, and a re-run restores it exactly with no
duplicates. Three rebuilds of a converged table write 0 rows and leave it
byte-identical.

**Chooser scale and plan (12–13, 52–54).** One query at 10–1,000
memberships over 21,610 rows. After the P3 order fix it is a pure
`ix_user_org_access_user` index scan reading 26 rows. Cursor pages use a
range on the same index. The per-page cost depends on the user's own
memberships, never on platform size. Paging while the directory changes
never repeats an organization. A removal mid-traversal drops only that
organization. An addition sorts first and appears on the next traversal. A
suspension moves nothing, because the status sync is a bulk `update()` that
leaves `updated_at` alone.

**MFA enrolment, secrets, TOTP, replay, recovery codes (18–27, 56).**
Re-verified from scratch:

- Adding a factor to a protected account needs a recent step-up, checked
  when confirming as well as when starting, so a race at start gains
  nothing.
- Secrets are 160-bit `os.urandom`, stored Fernet-encrypted under a key
  derived from `W360_MFA_ENCRYPTION_KEY`, and never logged.
- TOTP follows RFC 6238: SHA-1, 6 digits, 30-second steps, ±1 step.
- Replay is refused by a forward-only `sign_count` `UPDATE`, which holds
  across sessions and instances.
- A recovery code is spent by `DELETE` row count; 8 threads with one code →
  accepted once.
- Rate limits: P1-1.

**AAL (28–31).** The window boundaries are exact: at 14:59 and 15:00 the
session is AAL2; at 15:01 it is AAL1. A step-up timestamp from the future
(clock skew between instances) reads as AAL1. Login, logout, revocation and
expiry end AAL2 (existing tests). The session, the tenant context, the
`dangerous`-permission check and the OIDC authorize flow all read
`session_aal()`; nothing hardcodes an AAL.

**MFA UI and caching (32–33).** In the production build, web storage stayed
empty throughout. The secret and the recovery codes live in React state
only, are dropped on "I've saved these codes", and did not come back after
navigating away and pressing Back. HTML is `private, no-store`, and the API
responses that carry secrets are now `no-store` too.

**CSP, XSS, CSRF (34–36).** Production build (`next start`) in real
Chrome, 13 page states (anonymous and signed-in):

- **0** `securitypolicyviolation` events.
- Every script in the server-sent HTML (11–14 per page) carries the
  header's nonce. Runtime-inserted chunks run under `'strict-dynamic'`.
- `X-Frame-Options: DENY` on every page.
- All 11 routes are dynamic.
- An organization name containing `<b>` rendered as text.
- No `dangerouslySetInnerHTML`.
- Each Phase 5 write refused a request without the CSRF token (403):
  organization creation, enrol, confirm, step-up, factor removal and member
  removal.

**Trusted client IP (37).** Onboarding, step-up and confirmation all use
`client_ip()`. Rate limits: P2-2.

**Deferred FKs (38–41).** Read from the live catalog of a database migrated
from zero: 68 FKs, all `DEFERRABLE INITIALLY DEFERRED`, 12 of them
composite. No new FK. `files_file.uploaded_by_id` is always
`ctx.membership_id`; `register_upload` has no route, and the composite FK
`(uploaded_by_id, organization_id)` makes a cross-tenant value
unrepresentable. It is not client-controlled, so no new test was needed.
Staff 500s: P2-6.

**Migrations (42–45).** A real rehearsal, using **Phase 4 code** (a
worktree at `da5b1e2`) on its own database:

- Seeded through Phase 4's own services and the app role: 7 organizations
  (active, suspended, locked, soft-deleted, ownerless, and an owner holding
  a custom role), memberships in every state, custom tenant roles, a `crm`
  grant and two seats.
- Upgraded with current code. All 43 tables that existed before are
  byte-identical (`identity_user` compared without its two new columns).
- 5 owner-role backfills, none for the soft-deleted organization, and
  permission versions bumped.
- 10 projection rows, equal to the active memberships in live
  organizations, with no duplicates and drift clean. Chooser, permission
  and denial outcomes match expectations for every seeded user.
- Re-running both backfills changed nothing.
- `organizations` and `identity` roll back and re-apply cleanly.
- Rolling back `authz` past `0002` is refused by the database FK, with the
  data unchanged.

A fresh database migrated from zero applies 42 migrations in 4 s:

- 21 tables have RLS enabled and forced, with 22 policies.
- Exactly the 6 documented RLS exemptions.
- Neither role is superuser or `BYPASSRLS`.
- 6 system roles; `owner` holds all 38 permissions.

The data migrations use historical models and touch no cache, broker or
network. The exception is the live-code import noted in §2 P3.

**PgBouncer cleanup (46).** Test-only (`conftest.py`). It evicts only pools
named `test_workeasy360*` through the admin console, and nothing like it
exists in runtime code.

**Redis and RabbitMQ (47–49).** With the **real containers stopped**, the
full flow ran through the whole middleware stack on the real database and
PgBouncer: login, create and replay, chooser, context, members, apps,
foreign 404, enrol, confirm, replay refused, step-up, AAL2. 14/14 steps
behaved identically at baseline, with Redis down (`cache.get` raised
`TimeoutError`), with RabbitMQ down, and after recovery. Each creation left
one `organization.created` event pending. Once the broker was back, one
relay run published all four (4 claimed, 4 published, 0 failed). The
payload is the organization id, slug, display name and creator id: no email
and no secret.

**Audit and observability (50–51).** Organization creation and member
removal write audit rows (removal now includes `seats_released`). MFA: see
P2-1. Metric labels stay bounded (a bucket name, never an id).

**Accessibility, Lighthouse, bundle (62–64).**

- axe in the production build: 0 WCAG A/AA violations on all 13 states.
  One best-practice item is left (§2 P3).
- Lighthouse 12, production build:
  - Desktop 100/100/100 (performance, accessibility, best practices) on
    `/login`, `/onboarding`, `/organizations/select`, the launcher and
    security settings.
  - Mobile performance 98–99, accessibility and best practices 100.
  - SEO is 63 by design (`robots.txt`).
- First-load JavaScript, measured as every `<script src>` in the served
  HTML, gzip -9: 175–182 KB per route, 194 KB across all routes. Runtime
  dependencies are only `next`, `react` and `react-dom`, with no QR or OTP
  library.
- Manual screen-reader testing: **NOT EXECUTED.**

**Product domain, lean code (65–66).** No product models, views or
frontend. Nothing dead was found beyond the docstring corrected above.

## 4. MFA key rotation — kept as P2, with the procedure

Why it is not a P1: rotation is a deliberate operator action, never
something an attacker can trigger. Production refuses to start without the
key, or with a key equal to another secret (P3 above). Nothing rotates it
implicitly. The failure is loud: a step-up answers with an MFA error and
never falls back to AAL1-is-enough. Supporting two keys at once
(`MultiFernet` plus a dual HMAC check for recovery codes) is real work, so
it waits for a real rotation need.

**If the key must be rotated** (suspected compromise):

1. Treat it as a factor reset for every enrolled user, because every TOTP
   secret and recovery code becomes unverifiable at once.
2. Deploy the new `W360_MFA_ENCRYPTION_KEY`.
3. In the same maintenance window, as the migrator, delete every
   `identity_auth_factor` row, and set `mfa_failed_attempts = 0` and
   `mfa_locked_until = NULL` on `identity_user`.
4. Bump every user's `session_epoch` (`User.revoke_sessions()`), so no
   session keeps an AAL2 earned under the old key.
5. Tell users to re-enroll. Until they do, their accounts are password-only
   (AAL1), and a password alone can enroll the first factor. That is the
   window the reset opens, and why step 4 matters.

Do not rotate by redeploying the new key alone. Every enrolled user would
be stranded: they could not step up, and removing a factor requires
stepping up.

## 5. Step-up across organizations — kept as P2

Could a step-up in organization A authorize a dangerous action in
organization B? **Yes, within the same browser session for 15 minutes, and
that is correct for this trust model.** AAL is a property of how the
*person* authenticated, not of an organization. The factor belongs to the
global `User`, so proving it for A proves exactly the same fact for B.
Every other check still runs per request against B: membership, B's status,
and B's own role grant (`authz.check` with B's `TenantContext`). Step-up
elevates nothing a user lacks in B, and it never crosses sessions or users.
What is missing is policy, not isolation: an organization cannot demand a
shorter window or a re-prompt for itself. That needs an
`OrganizationSecurityPolicy`, which is out of scope.

## 6. Quality gates (final code)

| Gate | Result |
|---|---|
| `pytest` (fresh test database, full suite) | **1,182 passed, 0 failed, 0 skipped** (16 min 20 s; 2 warnings, both the known structlog `format_exc_info` notice) |
| `ruff check` / `ruff format --check` | clean / all files formatted |
| `mypy` | no issues (21 files) |
| `bandit -ll` | 0 medium, 0 high |
| `pip-audit --strict` | no known vulnerabilities |
| `lint-imports` | 12 contracts kept, 0 broken |
| `manage.py check` (development, and against a fresh database with `--database default`) | no issues |
| `manage.py check --deploy` (production settings) | every Django deploy check passes; only the two environmental `W360.W002` notes (placeholder database unreachable). A key equal to `SECRET_KEY` refuses startup. |
| `makemigrations --check` | no changes |
| Frontend clean `npm ci`, `typecheck`, `lint` | pass, 0 errors |
| Frontend tests | 41 passed (see the P3 flake) |
| `next build` + `assert-dynamic-rendering` | 11 routes dynamic, pass |
| `npm audit` | 0 vulnerabilities |
| Production CSP, axe, Lighthouse | §3 |
| Upgrade rehearsal, fresh database | §3 |

## 7. The silent-failure question (68)

*What could keep 1,121 backend and 41 frontend tests green while still
causing account takeover, organization access loss or tenant leakage in
production?*

**Before this audit, these things did, all found with those tests green:**

- A password-only attacker reaching AAL2 by patience (P1-1). No test ran
  for longer than a minute's bucket.
- Paid seats silently lost on every member removal (P1-2). No test
  combined removal with seats.
- An organization its owner could never open (P1-3). No test used an
  underscore.

**Still possible, and watched for rather than prevented:**

- **Deployment topology.** With Next.js as the public edge, or with the
  Next.js hop counted, IP buckets either collapse into one (a platform-wide
  login DoS) or become caller-chosen (P2-2). No test can see the proxy
  chain in front of production.
- **Key operations.** Redeploying a new MFA key without the §4 procedure
  strands every enrolled user.
- **Clock drift.** AAL2 is time-boxed on each instance's own clock. Skew
  shortens the window (fail closed) and never lengthens it; the MFA lock is
  compared on the app clock.
- **Migration-only states.** Ownerless organizations inherited from before
  Phase 5 stay ownerless.
- **4xx transaction semantics (D-016).** A future service that writes and
  then raises a 4xx will commit the write. The docstrings no longer say
  otherwise.
- **Cache and proxy.** A CDN configured to cache `/api` would break
  everything the `no-store` headers protect. HTML and secret-bearing API
  responses are `no-store`, but that header is advisory to intermediaries
  that ignore it.
- **The frontend's own flakiness under load** (P3). One real failure, not
  reproducible at normal load.

## 8. Decisions frozen for Phase 6

In addition to [phase-5-status.md §28](phase-5-status.md#28-decisions-proposed-to-freeze-for-phase-6):

1. MFA guessing has a durable, per-account, database-decided limit.
   Per-minute buckets are a first line only. Any new code-verification path
   goes through `verify_step_up_code`, or something with the same
   reservation.
2. A membership leaving an organization releases its seats in the same
   transaction. A write that points at a membership locks it
   (`active_membership_exists(lock=True)`).
3. Slugs use only `[a-z0-9-]`, the router's alphabet. Any new
   slug-generating path must be checked against
   `organization_slug_from_path`.
4. A 4xx domain error does not roll back the request transaction (D-016).
   Validate before the first write.
5. `W360_TRUSTED_PROXY_COUNT` counts only hops that append to the chain,
   never the Next.js rewrite, and Next.js is never the public edge.

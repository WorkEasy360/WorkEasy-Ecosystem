# Phase 5 — Design overview

Phase 5 closes the two gaps that stopped a new customer from using the
platform: there was no way to create an organization, and the organization
chooser scanned the whole platform on every sign-in (D-013). It also makes
the platform's AAL2 enforcement usable by adding TOTP MFA.

This document explains how the pieces work and why. The evidence (test
counts, measurements, gate results) is in
[phase-5-status.md](phase-5-status.md). The documentation is consolidated
into these two files on purpose; the code's docstrings carry the detail.

Contents: [architecture](#architecture) · [onboarding](#organization-onboarding) ·
[projection](#the-access-directory-projection) · [MFA and step-up](#mfa-and-step-up) ·
[security model](#security-model) · [concurrency](#concurrency) ·
[failure behaviour](#failure-behaviour) · [upgrade](#upgrading-a-phase-4-database) ·
[testing](#testing) · [known limits](#known-limits)

## Architecture

| Piece | Where | Layer |
|---|---|---|
| Organization creation, member removal (use cases) | `platform/onboarding/services.py` | P3 (ADR-023) |
| Organization/membership write primitives, creation idempotency ledger | `platform/organizations/services.py`, `models.OrganizationCreationRequest` | P1 |
| Access-directory projection (read model) | `platform/organizations/projection.py`, `models.UserOrganizationAccess` | P1 (ADR-022) |
| Chooser query | `platform/organizations/selectors.organizations_for_user` | P1 |
| System role catalog and owner role | `platform/authz` (`0002_seed_system_roles`, `assign_system_role`) | P2 |
| TOTP and recovery codes | `platform/identity/mfa.py`, `identity/services.py` | P1 |
| Session assurance (AAL) | `platform/tenancy/resolution.py` (`session_aal`, `record_step_up`, `reset_session_aal`) | P0 |
| HTTP | `interfaces/api/v1/auth_views.py` (create/list), `mfa_views.py`, `views.py` (member removal) | interfaces |
| Frontend | `/onboarding`, `/organizations/select`, `/o/[orgSlug]/settings/security` | Next.js |

`platform/onboarding` exists because creating an organization writes to four
modules that may not import each other (`organizations`, `identity`, `authz`,
`audit`). It is the same composition pattern `platform/ecosystem` already
uses (ADR-023).

## Organization onboarding

`POST /api/v1/organizations` with `{"name": ..., "idempotency_key": ...}`.

**One transaction** (`onboarding.services.create_organization`) writes the
organization (status `active`), the creator's owner membership, the owner
`RoleAssignment`, the audit entry, the projection row and the
`organizations.organization.created` outbox event. Any failure rolls back all
of it. No partial bootstrap is possible, and none was produced under
concurrency (see [concurrency](#concurrency)).

**Input.** Only `name` (a string, 1–255 characters) and an optional
`idempotency_key` (a string of at most 128 characters) are read. The
organization id, slug, status, owner and roles are all decided by the
server. Anything else in the body is ignored. A wrong type or an over-long
key is a 400, never coerced or truncated.

**Idempotency.** The key is claimed in `OrganizationCreationRequest`
(unique per user and key) inside the same transaction, via a savepoint. The
same key with the same name replays the original result (200). The same key
with a different name is refused (`idempotency_conflict`). Concurrent
requests with one key block on the unique index and then replay.

**Slugs** are generated from the name, restricted to `[a-z0-9-]` (the
tenancy router's alphabet: `slugify` alone keeps underscores, which made
an "Acme_Corp" unreachable by its own owner until the final audit), avoid
reserved words, and get a random suffix on collision. Two concurrent creations of the same name race
on the slug's unique constraint. The loser retries inside a savepoint with a
fresh suffix, up to three attempts. (Before the completion pass, the loser
got a 400; found by the thread tests.)

**Who may create.** Any signed-in, active account. There is no permission
check, because permissions belong to memberships and the caller has none
yet. It is rate-limited per account (10/min) and per trusted client IP
(20/min). This is abuse protection, not a business limit on how many
organizations a customer may own. CSRF applies as for every other write.

**Member removal.** `DELETE /api/v1/orgs/<slug>/memberships/<id>` requires
`platform.member.remove`. The membership row is locked and must not already
be removed, so concurrent removals record one state change and one audit
entry. The owner cannot be removed this way. Every seat the member held is
released in the same transaction (the seat screen lists active members only,
so a seat left on a removed member could never be freed), and seat
assignment locks the membership row so a concurrent removal waits for it. This is the minimal lifecycle
mutation the projection needs; there is still no invitation system.

## The access-directory projection

`UserOrganizationAccess` answers one question: which organizations should
appear in this user's chooser. It holds display data only: organization id,
slug, display name, status, membership id and the owner flag. It holds no
permissions, subscription, entitlement or billing data. It is **never** read
on an authorization path. Every tenant-scoped request re-resolves the real
`Membership` and `Organization` (`platform.tenancy.resolution`), whatever the
projection says (ADR-022).

**Consistency: synchronous, same transaction.** Every membership write path
syncs the projection in the transaction that changes the membership:
`create_organization` and `remove_member` are the only two in the codebase.
`Organization.suspend()`/`reactivate()` refresh the denormalised status.
There is no rename path yet, so a rename would currently not propagate.
`sync_access_for_membership` locks the row and writes only if something
changed. A converged row keeps its `updated_at`, which is the chooser's sort
key.

**Chooser query.** One keyset-paginated query ordered exactly as
`ix_user_org_access_user` is, `(user_id, updated_at DESC, id)`, so the index
serves the order and a page reads `limit + 1` rows. The
page size is clamped to 1–100, and any non-numeric `limit` falls back to the
default. Search is a bounded (100-character) case-insensitive match on the
display name. Cost does not grow with memberships or platform size: 1 query
and about 2 ms from 10 to 1000 memberships (measured; see status).

**Reconciliation.**

- `manage.py check_access_projection_drift [--fail-on-drift]` is read-only.
  It reports *missing* rows (active membership, no row), *stale* rows (row,
  no active membership) and *wrong-metadata* rows.
- `manage.py rebuild_access_projection [--prune-stale]` runs one transaction
  per organization. It reads the organization and its active memberships
  `FOR NO KEY UPDATE`, so a removal or suspension that commits mid-rebuild is
  never overwritten by stale data. It is idempotent: two runs leave the table
  byte-identical, `updated_at` included. Per-organization failures are counted
  and logged, the run continues, and a retry is safe. Stale rows are deleted
  only with `--prune-stale`, and only where the authoritative membership is
  not active.

A stale row cannot grant anything. If a removed member still has a row, the
organization appears in their chooser, and selecting it returns the uniform
404. If a suspended organization is still shown as active, the tenancy
middleware blocks writes with 402 using the real status. A poisoned row for
a foreign organization appears in the chooser and cannot be acted on. All
three are proven over HTTP (`tests/integration/test_projection_staleness.py`).

## MFA and step-up

**TOTP** (RFC 6238): HMAC-SHA1, 6 digits, a 30-second step, accepting one
step either side for clock drift. It is implemented on stdlib `hmac`/`struct`
and checked against the RFC test vector. Secrets are 160 bits from
`os.urandom`, stored Fernet-encrypted (`cryptography`, AES-128-CBC and
HMAC-SHA256) under `MFA_ENCRYPTION_KEY`. Production refuses to start
without `W360_MFA_ENCRYPTION_KEY`: it is never derived from `SECRET_KEY`
there, so rotating one does not silently break the other.

**No code is accepted twice** (RFC 6238 §5.2). Accepting a code is a
conditional `UPDATE` that moves the factor's last-accepted step forward
(`AuthFactor.sign_count`, the same forward-only counter WebAuthn uses). Of
two concurrent requests with one code, the database lets exactly one
through. The code that confirms enrollment is spent too.

**Recovery codes.** 10 codes of 40 bits each (`xxxxx-xxxxx`) come from
`secrets`. They are shown once and stored as HMAC-SHA256 keyed with the MFA
key, so a database dump alone cannot brute-force them. They are single-use:
acceptance is a `DELETE` whose row count decides, so concurrent use lets one
request through. Adding a factor issues a new set and invalidates the old
one. Removing the last factor removes the codes.

**Enrollment.**

- `POST auth/mfa/totp/enroll` returns the key and an `otpauth://` URI once.
  No QR image is rendered; the key is entered manually.
- `POST auth/mfa/totp/confirm` requires proof of possession. Confirmation is
  a conditional update, so exactly one concurrent confirm issues codes.
- On an account that already has a factor, both endpoints require a
  **recent step-up**. Otherwise a password alone would be enough to enroll an
  attacker's authenticator and replace the victim's recovery codes.

**Step-up and session assurance.**

- `POST auth/mfa/step-up` verifies a TOTP or recovery code and records
  `aal=2` and the time in the session.
- `session_aal()` reports AAL2 only for `STEP_UP_MAX_AGE_SECONDS` (15
  minutes; 12-security.md §2: "aal ≥ 2 and re-auth within 15 min") and AAL1
  otherwise. Every reader uses it: the tenant resolver (so `dangerous`
  permissions see it), factor removal, and the OIDC authorize flow.
- Sign-in resets to AAL1, including a second sign-in on the same session.
  Logout, session revocation (`User.revoke_sessions`) and session expiry all
  end the assurance with the session.

**Throttling.** Two layers. First, step-up and enrollment confirmation
share one per-account budget (8/min) and one per-IP budget (20/min), keyed
on the trusted client IP (D-009), so alternating endpoints does not double
a guesser's attempts. Second, and the one that matters over time: every
step-up attempt is counted on the account in PostgreSQL *before* its code
is evaluated (`User.mfa_failed_attempts`, one conditional `UPDATE`). After
10 consecutive failures the account's step-up locks for 1 minute, doubling
per further failure to a 24-hour cap; a success clears it; a locked attempt
is refused (429) without looking at the code. The per-minute bucket alone
allowed ~11,500 guesses a day, AAL2 in weeks for a password holder. Neither
layer has a global bucket, and only someone already holding the account's
password can move its counter.

**Logging.** Secrets, provisioning URIs, TOTP codes and recovery codes are
never logged. A test captures every log line of a full MFA flow and checks.
What *is* logged, as the account-security trail, is who changed or used a
factor and when: `identity.mfa.enrollment_started`, `factor_enrolled`,
`factor_removed`, `step_up_succeeded`, `step_up_failed` and
`step_up_locked`, carrying `user_id` and `factor_id` only. The MFA endpoints
answer `Cache-Control: no-store`.

## Security model

- **Tenant isolation is unchanged.** Every `/orgs/<slug>/...` request
  resolves the membership from the database. A foreign or non-member
  organization gets a uniform 404.
- **Owner is a role, not a superpower.** The owner gets the system `owner`
  role: every permission registered at migrate time. It is not staff.
  Control-plane operations (catalog, pricing, integrations) also require
  `is_platform_staff` whatever the role, and signing keys have no HTTP
  surface at all (`manage.py w360_signing_key`). These are verified for a
  freshly created owner at AAL2.
- **System roles** are global rows (`organization_id IS NULL`), writable only
  by the migrator (D-001). They are seeded by `authz.0002` and kept current by
  a `post_migrate` receiver (`ensure_system_role_catalog`). They change only at
  migrate time, never from request-time code.
- **Deferred FKs.** Every FK in the schema is `DEFERRABLE INITIALLY DEFERRED`,
  so a bad client-supplied id fails only at COMMIT, as a 500 after the view
  built its response. Client-supplied ids are validated before the write.
  The inventory test reads the live `pg_constraint` catalog, not a
  hand-kept list. See status §13 for the full audit.

## Concurrency

Real-thread tests (`tests/integration/test_phase5_concurrency.py`, 8
workers from a barrier, real commits) cover:

- one idempotency key, which creates one organization with a complete
  bootstrap;
- distinct keys, including same-name slug races;
- many users creating at once;
- rebuild racing removals, with no resurrected rows;
- concurrent syncs of one pair, which converge on one row;
- concurrent removals, with one state change;
- recovery-code and TOTP double use, with one acceptance;
- concurrent enrollment confirmation, with one issuance.

## Failure behaviour

| Dependency down | Effect | Correctness |
|---|---|---|
| **Redis** | Sessions are in PostgreSQL, so sign-in, onboarding, the chooser, selection and MFA keep working. Permission sets are recomputed from the database (slower). Rate limits fall back to per-process counters, so N workers allow up to N× the limit (D-012). Connect/operation timeouts are 50/100 ms, so nothing hangs. | Unchanged. No authorization falls back to "allow": dangerous-at-AAL1, stale-row and foreign-org denials were re-verified with the cache down. |
| **RabbitMQ** | No request path in these flows touches the broker. Events are written to the outbox inside the request's transaction, and the relay drains them once Celery beat and the workers can reach the broker again. | Unchanged. Organization creation commits its outbox row; nothing is lost and nothing blocks. |

Both were verified with dead-port tests in the suite
(`tests/integration/test_phase5_degradation.py`) and by stopping the real
containers under a live server (status §16).

## Upgrading a Phase 4 database

Five migrations: `authz.0002` (seed the system roles), `authz.0003` (give
each existing owner membership the owner role, bump the permission-set
version), `organizations.0002` (new tables), `organizations.0003`
(backfill the projection from active memberships) and `identity.0002` (the
two step-up backoff columns on `identity_user`, additive with defaults, and
reversible). Both backfills use historical models, bind the tenant variable
per organization under FORCE RLS, and are idempotent. The final audit
rehearsed the upgrade on a database built and seeded by the Phase 4 code
itself ([opus-audit.md §3](opus-audit.md#3-audit-by-audit)).

Without the two backfills, the upgrade rehearsal showed every existing user
routed to onboarding (empty chooser) and every existing owner unable to
list their own members.

Rolling back past `authz.0002` is refused by the database once any owner
holds the owner role (`PROTECT`). That is deliberate: roll back code, not the
role catalog.

## Testing

Where each claim is proven:

| Claim | Test |
|---|---|
| New customer, sign-in to launcher to AAL2, over a real socket | `tests/integration/test_phase5_acceptance_http.py` |
| MFA storage, replay, throttle, logging, session assurance, owner limits, creation abuse | `tests/security/test_phase5_security.py` |
| Stale/poisoned projection never grants; 500-membership paging over HTTP | `tests/integration/test_projection_staleness.py` |
| Real-thread races | `tests/integration/test_phase5_concurrency.py` |
| Redis/broker outages | `tests/integration/test_phase5_degradation.py` |
| Query plan and latency at 10/100/500/1000 memberships | `tests/performance/test_chooser_query_plan.py` |
| Deferred-FK inventory and commit-phase refusals | `tests/integration/test_deferred_fk_commit_safety.py` |
| Service-level onboarding, projection, MFA | `platform/{onboarding,organizations,identity}/tests/` |
| Browser: CSP, axe, keyboard, Lighthouse, UI acceptance | production build in real Chrome (status §2–6); `frontend/src/accessibility.test.tsx`, `MfaSettings.test.tsx` |

## Known limits

Tracked in [phase-5-status.md §26](phase-5-status.md#26-unresolved-items),
the final audit ([opus-audit.md](opus-audit.md), which also holds the MFA
key-rotation procedure) and the [debt register](../architecture/debt-register.md).
The most notable:

- There is no rename path, so the projection has no rename sync yet.
- There is no QR code rendering, email-based MFA recovery, or WebAuthn.
- The step-up window is a platform-wide setting, not a per-organization
  policy.
- Rotating `MFA_ENCRYPTION_KEY` invalidates every TOTP secret and recovery
  code. There is no `MultiFernet` rotation; follow the procedure in
  [opus-audit.md §4](opus-audit.md#4-mfa-key-rotation--kept-as-p2-with-the-procedure).
- There is no self-service account recovery: a user who loses every factor
  and every recovery code needs an operator to delete their factors (after
  verifying them out of band), which returns the account to password-only.
- A write made before a 4xx domain error in a tenant request commits (D-016).
- D-010 (`style-src 'unsafe-inline'`) is still open.

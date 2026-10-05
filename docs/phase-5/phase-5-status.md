# Phase 5 — Status

**PHASE 5 STATUS: PASS** (completion pass, 2026-09-23).

> **Final adversarial audit, 2026-09-23: PASS** - see
> [opus-audit.md](opus-audit.md). It found and fixed three P1s this report
> did not (step-up guessing bounded only per minute; member removal leaking
> seats; organization names with `_` producing unroutable slugs) plus
> smaller defects. Its test counts and open-item list supersede §1 and §26
> below; everything else here remains the completion pass's evidence.

Every gate in the completion brief was run against real PostgreSQL 16,
PgBouncer (transaction mode), Redis 7 and RabbitMQ 3.13, and the Next.js
production build in real Chrome. The gates found **22 defects in Phase 5
backend and test-harness code** (one P0-class account-takeover path, four
P1) and **6 in the Phase 5 frontend**. They also found 2 pre-existing 500s
(a commit-phase FK path and a `limit` parse) and 1 dev-config defect. All
are fixed and covered by regression tests. What remains open is listed in
§26. None of it is a P0 or blocks Phase 6.

Design and rationale are in [00-overview.md](00-overview.md). This file is
the evidence.

## 1. Final backend test run

A fresh database from zero (volume removed, `init.sql` re-applied, every
migration from an empty schema), one run, with no other test process,
server or database session active:

| | |
|---|---|
| Command | `.venv/Scripts/python.exe -m pytest -p no:cacheprovider -q -rs` |
| **Passed** | **1,121** |
| Failed | 0 |
| Skipped | 0 |
| Warnings | 1: the structlog `format_exc_info` notice, harmless (§13). The teardown warning did not recur. |
| Duration | 947 s (15 min 47 s) |

The count went from 1,051 to 1,121: 69 new verification tests (acceptance
3, concurrency 9, security 39, staleness and 500-membership 9, query plan 1,
degradation 6, deferred-FK +2), plus 1 new case in the pre-existing
production-settings test, since `W360_MFA_ENCRYPTION_KEY` is now required.
No test was skipped, deleted or weakened. The four MFA unit tests that used
the removed `verify_totp` helper were rewritten against `match_totp_step`
(same count). One Phase 5 unit test that expected a TOTP code to be reusable
now asserts the opposite, correct behaviour.

## 2. Frontend

The frontend was verified from a clean install (`rm -rf node_modules .next`,
then `npm ci`, 440 packages).

| Gate | Result |
|---|---|
| `npm run typecheck` (`next typegen && tsc --noEmit`) | 0 errors |
| `npm run lint` | 0 errors, 0 warnings |
| `npm test` (vitest) | **41 passed** / 5 files (was 29) |
| `npm run build` | success, all 11 routes dynamic (`ƒ`) |
| `node scripts/assert-dynamic-rendering.mjs` | pass |
| `npm audit`, `npm audit --audit-level=moderate` | 0 vulnerabilities |

The full sequence was re-run on the final source after the last copy change.
The results were identical.

## 3. CSP, verified against the production build

Setup: `next start` with the production build, the live Django backend, and
headless installed Chrome (playwright-core, a scratch install that is not a
project dependency). 16 route states were checked: anonymous and signed-in
`/login`, `/`, `/onboarding`, `/organizations/select`, the launcher, all
five settings pages including security, a non-member organization and a 404.

- Every page hydrated and worked: login submits, onboarding creates an
  organization, the chooser lists it, and the security page loads factors.
- **0** `securitypolicyviolation` events and **0** CSP console messages.
- Every `<script>` carried the response header's nonce (0 mismatches over
  12–14 tags per page), and the nonce changed on every request.
- HTML is served with `Cache-Control: private, no-cache, no-store, max-age=0,
  must-revalidate`, so no per-request nonce is ever cached. `X-Frame-Options:
  DENY`, `nosniff` and `Referrer-Policy` were on every page.
- Inline `style` attributes render (D-010, `style-src 'unsafe-inline'`, is
  still open and unchanged). The build assertion against static prerendering
  is preserved and passes.

For comparison, the Phase 4 build (commit `da5b1e2`, rebuilt in a separate
worktree) statically prerendered `/`, `/login`, `/_not-found` and
`/organizations/select`, which is the nonce defect Phase 4's audit found.
Phase 5's build prerenders nothing.

## 4. Accessibility

- axe-core in vitest: 11 → **18** tests. New coverage: onboarding (with and
  without an error), the chooser, security settings, the enrollment steps,
  the error state, the recovery codes, the step-up challenge and its error
  state, and the shell.
- axe-core injected into the rendered production pages: **0 WCAG 2.x A/AA
  and 0 best-practice violations across 12 page states**. Colour contrast was
  "incomplete" on the shell pages; Lighthouse scores contrast as passing.
- Tab reaches every control, focus is visible (`:focus-visible`), Enter
  submits, the skip link comes first, and each page has one `h1` with
  ordered headings.
- Defects fixed:
  - Enrollment errors were unhandled promise rejections that showed nothing.
  - Focus was dropped to `<body>` between steps.
  - Error text was not linked to its input (`aria-describedby`).
  - The account-menu button's accessible name was a single initial.
  - The active navigation item was shown by colour only (WCAG 1.4.1).
- **MANUAL SCREEN-READER TEST: NOT EXECUTED.**

## 5. Lighthouse

Lighthouse 12.8.2 on a local Windows 11 machine, against `next start` in
headless Chrome. Signed-in routes used the session cookie.

| Route | Desktop P / A / BP | Mobile P / A / BP |
|---|---|---|
| /login | 100 / 100 / 100 | 99 / 100 / 100 |
| /onboarding | 100 / 100 / 100 | 99 / 100 / 100 |
| /organizations/select | 100 / 100 / 100 | 93 / 100 / 100 |
| /o/<slug>/apps | 100 / 100 / 100 | 80 (repeats 91, 66) / 100 / 100 |
| /o/<slug>/settings/security | 100 / 100 / 100 | 97 / 100 / 100 |

SEO is 63 on every route only because `robots.txt` deliberately disallows
crawling. The mobile launcher score varies with local total blocking time;
no console or CSP errors occurred in any run. Nothing needed fixing.

## 6. Real-HTTP acceptance

**API, over a real socket** (`tests/integration/test_phase5_acceptance_http.py`,
`live_server` with a urllib cookie jar and the double-submit CSRF token):

1. A new user signs in with zero organizations.
2. They create an organization while also sending a forged id, owner, status,
   role and slug; all are ignored.
3. An idempotent retry gets the same organization back.
4. The source of truth holds one organization, one owner membership, one
   owner role assignment and nothing for the forged owner.
5. The chooser lists the organization; selection works at AAL1; the owner can
   list members.
6. The launcher opens with **no launchable app** and entitlements are `[]`.
7. The dangerous permission `platform.organization.delete` is refused at AAL1.
8. Enrollment: confirm returns 10 recovery codes. Re-enrollment and factor
   removal need step-up; the confirming code cannot be replayed.
9. Step-up with the next code: the context reports AAL2 and the dangerous
   permission is allowed.
10. Logout, then a new sign-in: AAL1 again.
11. A recovery code works once. Reusing it after re-login is refused.

Also over HTTP: an existing customer gets their organization from the
chooser (not onboarding), and a POST without the CSRF token is refused
with 403.

**Browser** (production build, real Chrome, 18 screenshots):

1. New user → `/onboarding` → create an organization in the UI.
2. The launcher shows "No products are available yet" (0 Open buttons).
3. The switcher opens the chooser; Enter opens the organization.
4. Enrollment: a wrong code shows a linked error; a correct code shows 10
   `xxxxx-xxxxx` codes, which are not shown again.
5. "Add another app" asks for step-up; a replayed code is rejected; a fresh
   code is accepted.
6. In a new session, Remove asks for step-up and then succeeds.
7. An existing user lands on `/o/<slug>/apps`, not onboarding.

## 7. 500-membership acceptance

Over HTTP, a user with 500 memberships paged through `?limit=100`: 5 pages,
**500 unique organizations, none lost or repeated**, and an identical query
count on every page. Search finds a single organization. Selecting the last
one works; after its membership is suspended in `Membership`, selecting it
is refused although the projection still lists it. A `limit` of `abc`, `-5`,
`0` or `100000` is clamped to 1–100 (`abc` used to be a 500).

## 8. Projection staleness

Each case is proven over HTTP (`test_projection_staleness.py`):

| The projection says | The truth | Selection | After reconciliation |
|---|---|---|---|
| member | membership removed | 404 | `--prune-stale` removes the row; drift clean |
| organization active | suspended | writes 402, context reports `suspended` | rebuild converges the status |
| organization active | locked | 404 | — |
| a foreign organization (poisoned row) | never a member | 404 on every endpoint | — |

The same denials were re-verified with Redis unreachable (§16).

## 9. Projection rebuild

- **Twice:** two runs compared row by row, `updated_at` included, are
  identical, with 0 rows written on the second pass. The first pass also
  writes nothing on a converged table (previously every run rewrote every
  row and reshuffled the chooser order).
- **Bounded:** one transaction per organization, batched organization ids.
- **Retry:** a per-organization failure is counted and logged, the rest
  continue, and a re-run redoes only what is still wrong.
- **No lost access:** a prune removes only the stale row; the legitimate row
  survives.
- **Concurrency:** a rebuild racing removals leaves no resurrected row and a
  clean drift report.
- **Backfill:** `organizations.0003` on a Phase 4 database gives 5/5 rows
  and a clean drift report (§20).

## 10–11. Concurrency

Real threads (8 workers, barrier, `transaction=True`, real commits); every
test asserts zero worker errors, so no deadlock or unhandled
`IntegrityError` occurred:

| Race | Result |
|---|---|
| One idempotency key × 8 | 1 organization, 1 created and 7 replays, 1 ledger row, complete bootstrap |
| Distinct keys, same name × 8 | 8 organizations, 8 distinct slugs, each fully bootstrapped, role catalog not duplicated. **Found a defect** (below). |
| 8 users creating at once | each owns exactly its own organization |
| Rebuild vs 35 member removals | no resurrected row, drift clean |
| 8 syncs of one (user, org) pair | 1 insert, 1 row |
| 8 removals of one member | 1 success, 7 × 400, **1** audit entry |
| One recovery code × 8 | accepted **once** |
| One TOTP code × 8 | accepted **once** |
| One enrollment confirmation × 8 | one caller gets the codes; exactly 10 stored |

## 12. Full backend quality gates

All run on the final code:

| Command | Result |
|---|---|
| `pytest` (final, fresh database) | see §1 |
| `ruff check backend` | All checks passed |
| `ruff format --check backend` | 299 files formatted |
| `mypy` (project config: strict on `contracts/`) | no issues, 21 files |
| `bandit -c pyproject.toml -r backend/workeasy360 backend/config -ll` | **no issues** at the project's `-ll` policy (0 medium, 0 high; 870 low are excluded by that policy) |
| `pip_audit -r requirements.txt -r requirements-dev.txt --strict` | no known vulnerabilities |
| `lint-imports` | 12 contracts kept, 0 broken |
| architecture fitness tests | pass (in the suite) |
| `manage.py check --fail-level WARNING` (migration and development settings) | no issues |
| `manage.py check --deploy` with `config.settings.production` | every Django deploy check passes. Two `W360.W002` notes: the RLS self-check could not connect with placeholder credentials, which is environmental; the same checks pass against the real database above. A missing `W360_MFA_ENCRYPTION_KEY` refuses startup. |
| `makemigrations --check` | No changes detected |

## 13. Fresh database from zero

`docker compose down -v`, `up -d` (so `init.sql` re-ran), then `migrate`
from an empty schema: **41 migrations, 0 unapplied**, in 9 s.

- Roles: `w360_app` and `w360_migrator` are neither superuser nor BYPASSRLS.
- **21/21** tenant tables have RLS enabled and forced, with 22 policies. The
  6 tables that carry `organization_id` without RLS are the documented
  exemptions: D-008 (2), D-014, D-015, and the event outbox and dead-letter
  tables.
- Projection, creation-ledger and MFA tables and their indexes are present.
- 6 system roles; `owner` has all 38 permissions. 68/68 FKs are deferred.
- A bootstrap smoke test over HTTP on the fresh database: 0 organizations,
  create, owner listed in the chooser, selection at AAL1, members list 200.

Warning B (test-database teardown) is gone, and its real cause was not
concurrency. `conftest._terminate_test_db_sessions` ran
`pg_terminate_backend` as `w360_migrator`, which PostgreSQL refuses for
`w360_app` backends ("permission denied to terminate process"), and a bare
`except` swallowed the refusal. So PgBouncer's pooled app connection
survived whenever the drop came within its 10 s idle timeout. The fix evicts
the pool through PgBouncer's admin console (`KILL`/`RESUME`; the migrator is
already an `admin_user`), with no new database privilege. Verified: 0
sessions at drop time, and three consecutive clean runs.

Warning A (structlog "remove `format_exc_info`") is **harmless dev/test
noise**. Production uses the JSON renderer, which needs `format_exc_info`.
Keeping it ahead of `_redact` means tracebacks are redacted, whereas the
console renderer's own traceback path would bypass redaction. Logging was
not changed.

## 14. Phase 4 → Phase 5 upgrade

This is separate from §13. A Phase-4-shaped database was built by migrating
fully, reversing the Phase 5 migrations and removing the system roles. It
was seeded with 3 organizations (one suspended), 6 users, and memberships
that were active, suspended, removed or invited, with no role assignments.
Then it was migrated forward.

| | Before the fix | After the fix |
|---|---|---|
| Organizations and memberships | preserved (byte-identical dump) | preserved |
| Projection | **empty** (drift: 5 missing), so every existing user, owners included, was **routed to onboarding** | 5 rows, 0 duplicates, drift clean |
| Existing owners | **403 on their own member list** (no role assignment) | owner role assigned (3/3); member list 200 |
| Non-owner members | no role (as in Phase 4) | unchanged |
| Re-run of the backfills | — | idempotent: 5 rows, 3 assignments, versions unchanged |

The fixes are `organizations.0003_backfill_access_projection` and
`authz.0003_backfill_owner_role_assignments`. The latter also bumps the
permission-set version so a pre-upgrade cached empty permission set cannot
linger.

**Rollback:** `authz.0002` failed to reverse at all ("pending trigger
events"); that is fixed. Rolling back past it after owners hold the owner
role is then refused by PostgreSQL (`PROTECT`), which is the safe outcome
and is documented.

## 15. Deferred-FK commit-safety audit

Every one of the schema's 68 FKs is `DEFERRABLE INITIALLY DEFERRED`. All of
them were traced from write path back to value origin:

| Classification | Columns |
|---|---|
| **NOT CLIENT-CONTROLLED** | all `organization_id` columns (tenant-stamped, RLS `WITH CHECK`); `auth_factor.user_id`; `organization.created_by_id`; `membership.*`; `role_assignment.{membership,role,granted_by}`; `role_permission.*`; `seat_assignment.{assigned_by,seat_grant}`; `seat_grant.*`; `entitlement*.organization_id`; `subscriptions_item.{subscription,parent_item}`; `pricing_price_tier.plan_price_id`; `catalog_plan_feature.plan_version_id`; `catalog_bundle_item.bundle_version_id`; all three `ecosystem_*.integration_id` (server lookup by client_id); `creation_request.*`; `user_access.*`; `idempotency_record.organization_id` |
| **APPLICATION-VALIDATED** | `seat_assignment.membership_id` (`active_membership_exists`: existence and same organization); `subscriptions_item.{plan_version,bundle_version}`; `catalog_plan_feature.feature_id`; `catalog_bundle_item.plan_version_id`; `catalog_{feature,plan}.product_id`, `plan_version.plan_id`, `bundle_version.bundle_id` and `plan_price.price_book_id` (looked up first, but a miss is an immediate 500, see §26) |
| **NOT WRITTEN** | `organization.parent_id`, `invitation.*`, `entitlements_grant.*`, `notifications_preference.*`, `files_file.*` (no route yet), `notifications_*` and `pricing_contract_price.*` (no caller) |
| **NEEDS FIX → FIXED** | `pricing_plan_price.plan_version_id` and `.bundle_version_id` (`POST .../commercial/prices`, platform staff). They were never looked up, so an unknown id got a 201 and then **a 500 at COMMIT**. They now go through `catalog.require_{plan,bundle}_version`, which returns 400. A commit-phase test proved it fails without the fix. |
| **DB-ONLY (latent)** | `notifications.recipient_membership_id` and `pricing_contract_price.{plan,bundle}_version_id` are unvalidated but have no caller (§26) |

`files_file.uploaded_by_id` is not client-controlled: it is always
`ctx.membership_id`, and `register_upload` has no route. The inventory test
now reads composite FKs from **`pg_constraint`**. The old version read one
migration's list and missed five composite FKs (`invitation.invited_by`,
`role_assignment.granted_by`, `seat_assignment.assigned_by`, and
`subscriptions_item.parent_item`/`subscription`). No global
`IntegrityError` → 400 handler was added.

## 16. Redis and RabbitMQ failure

Two methods were used: dead-port tests in the suite
(`test_phase5_degradation.py`, 6 tests), and **stopping the real
containers** while a live `runserver` served the same HTTP flow. That flow
covered login, creation plus replay, the chooser, selection, members, apps,
enroll, confirm, a replay attempt, step-up, AAL2 and a foreign organization
(a 404). Timings were taken while the checkpoint suite ran in parallel.

| | Baseline | Redis stopped | RabbitMQ stopped | Recovered |
|---|---|---|---|---|
| Every step's expected status | ✔ | ✔ | ✔ | ✔ |
| Replay refused / AAL2 reached / foreign 404 | ✔ / ✔ / ✔ | ✔ / ✔ / ✔ | ✔ / ✔ / ✔ | ✔ / ✔ / ✔ |
| Slowest request | 335 ms (login, Argon2) | 352 ms | 254 ms | 217 ms |
| Throttled/permission paths | ~60–90 ms | ~175–255 ms (cache connect failures) | ~60–140 ms | normal |

**Redis:** a direct `cache.get` during the stop raised `TimeoutError`, which
proves the server really used Redis. The loss is **capacity/latency only**:
rate limits become per-process (D-012) and permission sets are recomputed
from the database. **No correctness change.** A dangerous permission at
AAL1, a stale-projection selection and a foreign organization were all still
denied, and replay protection and throttles still held (per-process).

**RabbitMQ:** nothing on these paths touches the broker. Each creation wrote
exactly one `organizations.organization.created` outbox event, durably
`pending` for the relay, and nothing hung. A suite test also makes every
Celery enqueue raise, and creation and MFA still complete with zero enqueue
attempts.

## 17. MFA security audit

| Item | Finding |
|---|---|
| Secret randomness | `os.urandom(20)` (160 bits) |
| Secret storage | Fernet (AES-128-CBC and HMAC-SHA256) under `MFA_ENCRYPTION_KEY`. **Fixed:** production silently derived this from `SECRET_KEY`; `W360_MFA_ENCRYPTION_KEY` is now required, at least 32 characters. |
| RFC 6238 parameters | SHA-1, 6 digits, 30 s, ±1 step. Matches the RFC vector; the window is tested as exactly ±1. |
| Code reuse | **Fixed (P1):** a code could be replayed for about 90 s, the confirming code included. Now the last accepted step is recorded and advanced by a conditional `UPDATE`, so a code is accepted once, also under concurrency. |
| Enrollment | Authenticated only (401 otherwise). **Fixed (P0-class account-takeover path):** a password-only session could enroll the attacker's own authenticator on an MFA-protected account, step up with it, and replace the victim's recovery codes. Adding a factor now requires a recent step-up when one exists. |
| Activation | Proof of possession required. **Fixed:** concurrent confirms could both issue recovery codes; now exactly one does. A non-UUID `factor_id` was a 500 and is now a 404. |
| Recovery codes | `secrets`. **Fixed:** they were 32 bits with unkeyed SHA-256 (offline brute force in seconds from a database dump). Now 40 bits and HMAC-SHA256 keyed with the MFA key. |
| One-time use | **Fixed (P1):** `.first()` then `.delete()` let concurrent requests double-spend a code. Now a `DELETE` row count decides. |
| Regeneration | Re-enrollment issues a new set and the old ones are rejected (tested). **Fixed:** removing the last factor left codes that still reached AAL2; they are now removed with it. |
| Logging | Secrets, URIs, codes and recovery codes never appear in logs (full-flow log capture test). |
| AAL update | Only after a successful verification (`record_step_up`). |

Custom crypto: none. HMAC, SHA-1/SHA-256, Fernet and the RFC truncation are
standard primitives.

## 18. MFA rate limits

Step-up (TOTP and recovery codes) and enrollment confirmation (**previously
unthrottled**) now share one budget: 8 per minute per account and 20 per
minute per trusted client IP (D-009 resolution). There is no global bucket;
another account was verified unaffected. With Redis down the limit becomes
per-process rather than disappearing (tested). Starting enrollment is not
throttled: it creates at most one pending factor per user and no secret
guess is possible through it.

**Superseded by the final audit (P1-1).** A per-minute bucket resets every
minute, so this alone still allowed ~11,500 guesses a day per account. Step-up
now also carries a durable, database-decided backoff: 10 free consecutive
failures, then a lock from 1 minute doubling to 24 hours, cleared by a
success ([opus-audit.md](opus-audit.md)).

## 19. MFA session behaviour

**Fixed (P1):** AAL2 used to last the whole session, up to 30 days.
12-security.md §2 requires "aal ≥ 2 and re-auth within 15 min". Step-up now
records a time and `session_aal()` reports AAL2 only for
`STEP_UP_MAX_AGE_SECONDS` (900). The tenant resolver, factor removal and
the OIDC authorize flow all use it.

Tested:

- sign-in gives AAL1;
- step-up gives AAL2, and a dangerous permission is then allowed;
- the window lapses back to AAL1;
- a second sign-in on the same session is AAL1;
- logout, `revoke_sessions()` and session expiry each end the session and
  its assurance.

## 20. Owner role security

A freshly created owner, even at AAL2:

- gets a uniform 404 for another organization's context, members,
  organization and member-removal endpoints, and the chooser never shows it;
- is not staff (`is_staff = is_superuser = False`);
- gets 403 on the commercial catalog, price books and integration
  management;
- has **no HTTP route to signing keys** (`w360_signing_key` is a management
  command).

The owner can list members and remove a member (the projection row goes
with it), but cannot remove themselves (400).

## 21. Organization-creation abuse

- Anonymous: 401, and 403 when CSRF is enforced.
- Disabled account (with an existing session): 401 and no organization.
- Duplicate retry: a replay.
- Malformed input returns 400 with no organization created. Cases: `{}`, an
  empty name, whitespace, 256 characters, a list, an object, an integer, a
  boolean, a non-string key, a 129-character key, and a non-JSON body.
  **Fixed:** an object name used to be `str()`-coerced into a real
  organization named `{'name': 'Acme'}`, and long keys were silently
  truncated, which could collide.
- Mass assignment: client-supplied id, owner, status, role and slug are all
  ignored.
- Rate limiting is per account (tested: the 4th of 3 gets 429, another user
  gets 201) and per IP.
- **Fixed:** concurrent same-name creation gave the loser a spurious 400.
- No organization-count limit was invented.

## 22. Projection data minimization

`UserOrganizationAccess` holds user, organization, slug, display name,
status, `membership_id`, `is_owner` and `updated_at`. It has no
permissions, subscription, entitlement, billing, product or other
organization payload. It is cheap to rebuild (§9).

## 23. Query plan and performance

The chooser's own SQL (taken from the selector) was run under
`EXPLAIN (ANALYZE, BUFFERS)` on a table of **21,610 rows** (padded with 20,000
rows belonging to 400 other users) after `ANALYZE`:

| Memberships | Queries | Plan | DB execution | Selector wall time (via PgBouncer) |
|---|---|---|---|---|
| 10 | 1 | Index Scan `ix_user_org_access_user` | 0.023 ms | 1.72 ms |
| 100 | 1 | same | 0.029 ms | 2.01 ms |
| 500 | 1 | same | 0.027 ms | 2.02 ms |
| 1000 | 1 | same | 0.025 ms | 1.87 ms |

Phase 4's scan needed 56, 506 and 2,506 queries (D-013, closed). No index
was added. Chooser pages over HTTP have a constant query count (§7).

## 24. Frontend bundle

First-load JS (gzip), from the manifests:

| Route | Phase 4 | Phase 5 |
|---|---|---|
| `/login` | 136.4 KB | 136.4 KB |
| `/organizations/select` | 136.3 KB | 140.0 KB |
| `/o/[slug]/apps` | 142.1 KB | 142.2 KB |
| `/onboarding` | — | 136.8 KB |
| `/o/[slug]/settings/security` | — | 143.2 KB |
| all chunks | 190.3 KB | 199.6 KB |

There are no new runtime dependencies. The only package change is
`axe-core` as a devDependency.

## 25. Cleanup performed

These were proven waste (grep-verified, no references):

- `contracts.errors.OrganizationCreationRateLimited`: never raised.
- The `sync_permissions` management command and the now-empty
  `authz/management` package: no callers, and `migrate`'s `post_migrate`
  receiver already performs the same sync. Its docstring also contradicted
  that receiver.
- `mfa.verify_totp`: replaced by `match_totp_step`.
- Frontend `removeMember`: no call sites.
- References to non-existent Phase 5 docs (`01`–`09`) in 6 source files, 2
  ADRs and the debt register. The debt register also cited a
  1000-membership measurement that had never been taken; it has now been
  taken (§23).
- A stale layout comment. The `ARCHITECTURE.md` layer list was missing
  `onboarding`.

No temporary debug logging was found. Dependencies added: **none**.
Dependencies removed: **none** (none was unused).

## 26. Unresolved items

There is no P0 and no Phase-6-blocking P1. **The current open list is
[opus-audit.md §2](opus-audit.md#2-findings)**; the table below is the
completion pass's, kept as it was.

| Pri | Item |
|---|---|
| P2 | Staff-only commercial endpoints answer an unknown `product_id`, `plan_id`, `bundle_id` or `price_book_id` with an immediate 500 (a bare `.get()`), and some malformed staff input with a 500 (`declined_plan_version_ids`, bundle items, `valid_days`). This is Phase 2 code, not commit-phase, and not customer-reachable. |
| P2 | `notifications.notify` does not validate `recipient_membership_id`. It is latent: there is no caller. The first caller must pass a resolved membership. |
| P2 | Rotating `MFA_ENCRYPTION_KEY` invalidates every TOTP secret and recovery code; there is no `MultiFernet`. |
| P2 | The step-up window is a platform setting, not a per-organization `OrganizationSecurityPolicy`. |
| P3 | No organization rename path exists, so the projection has no rename sync. It must be added with the rename feature. |
| P3 | A cancelled second enrollment leaves a pending factor listed. It grants nothing and is replaced on the next start, but removing it needs AAL2. |
| P3 | The account menu uses `role="menu"` without arrow or Escape handling (a Phase 4 pattern). |
| P3 | There is no QR code (manual key entry only), no email-based MFA recovery, and no WebAuthn. |
| P3 | `all_organization_ids` caps at batch × 1000 (500k organizations by default) for rebuild and drift. |
| P3 | The projection's implicit slug indexes are unused (write overhead only). |
| P3 | The price-books view validates the request body before authorization, so a customer gets 400 rather than 403 for an incomplete body. Nothing is written. |
| P3 | D-010 (`style-src 'unsafe-inline'`) is still open. |
| — | Manual screen-reader testing has not been done. |

## 27. Architecture deviations and ADRs

There are no new ADRs. ADR-022 §4 is amended (dated) to record the
row-locked rebuild, the opt-in prune and the migration backfill.
ARCHITECTURE.md now lists `onboarding` at P3 (ADR-023). Every change stays
inside the existing layer contracts (12/12 kept); the pricing fix uses the
permitted catalog < pricing edge.

## 28. Decisions proposed to freeze for Phase 6

1. `platform/onboarding` is P3; it is the composition point for multi-module
   use cases below it (ADR-023).
2. The access directory is discovery only, never authority (ADR-022). Every
   new membership write path must sync it in the same transaction.
3. Session assurance goes only through `session_aal`, `record_step_up` and
   `reset_session_aal`. AAL2 is time-boxed.
4. MFA code acceptance is always a conditional database write (a forward-only
   step or a `DELETE` row count), never a read followed by a write.
5. The system role catalog changes only at migrate time (a migration or the
   `post_migrate` sync), never from request-time code. An upgrade that
   changes role semantics ships a backfill migration and is rehearsed on a
   seeded older-schema database.
6. Client-supplied ids are validated before the write. New tables' FKs are
   inventoried from `pg_constraint`, and new write paths get
   `transaction=True` tests.
7. Test-database hygiene: evict PgBouncer pools through its admin console,
   never with `pg_terminate_backend` from the migrator.

## Answer

**Can a completely new Workeasy360 customer now securely create their
organization, become its owner, discover it through a scalable organization
directory, satisfy MFA/step-up requirements, and enter the existing
ecosystem without relying on unsafe tenant projection state? — YES.**

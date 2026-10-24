# Second factor removed from the whole platform: status

Date: 2026-10-24. Decision: [ADR-050](../architecture/adrs/ADR-050-remove-second-factor.md). **Not committed, not pushed, not deployed.** It sits in the working tree beside the uncommitted partner-upgrade work ([docs/partner-upgrade](../partner-upgrade/status.md)).

## Result

**Nobody is asked for a second factor anywhere, and nothing requires one to exist.** Sign-in is a password. A `dangerous` action needs the permission (and is still refused under impersonation and in a read-only organization) but no code. The enrolment, challenge, recovery-code, step-up and "require two-step verification" features are gone from the backend and the frontend.

## What changed

Backend: the assurance level (`aal`) and the step-up gate in `authz.check`; the `auth/mfa/*` routes and views; `identity.mfa` and the enrolment / step-up / factor services; the org `require_mfa` policy and `mfa_required` denial; the factor requirement for staff designation and the `w360_staff_mfa_reset` command; ending another session without a code; the `aal` claim of product ID tokens; `mfa_enrolled` / `mfa_enabled` / `with_second_factor` / `aal` payload fields; the settings `MFA_ENCRYPTION_KEY`, `STEP_UP_MAX_AGE_SECONDS`, `MFA_STEP_UP_RATE_LIMITS` (and the production requirement for `W360_MFA_ENCRYPTION_KEY`, in `make_env.py`, `.env.example` and the env template).
Frontend: `features/security/*` (authenticator screen, step-up prompt and hook), `lib/api/mfa.ts`, the policy toggle and gate, 2FA columns and copy; `useAction` now simply runs the action.
Tests: about 78 deleted, about 37 rewritten, 11 added (`backend/tests/security/test_no_second_factor.py`). Harness seeds under `scripts/` no longer enrol a factor.

**Deliberately left:** the database rows and columns (`AuthFactor` table with enrolled secrets and recovery-code hashes, `User.mfa_failed_attempts` / `mfa_locked_until`, `Organization.require_mfa`, `AuthorizationCode.aal`) - marked dormant, read and written by nothing. Dropping them is irreversible and needs your go-ahead. The audit and log redactors still mask `mfa|otp|totp` values.

## Evidence

| Gate | Result |
|---|---|
| Full backend suite (bare `pytest`, run by the test helper) | **4,682 passed, 9 failed** in 2 h 22 min (the machine was busy). The 9 were old tests that expected a step-up (API keys, team management, product editing, sales-led terms, platform settings, role grants, one authz case); each was rewritten to assert the action now just succeeds |
| After fixing those | their 8 files re-ran green (226), then 609 more across the files that read frontend types, architecture and the upgrade package, then my own pass over security, architecture, partner upgrade, partner menu / panel / experience, phase 9, phase 11 and authz: **1,111 passed, 0 failed** |
| The whole suite was **not** run a second time end to end after those 9 fixes | the fixes were test-only |
| ruff, ruff format, mypy, lint-imports, bandit, `manage.py check`, `makemigrations --check` | clean; 18 import contracts kept; **no new migration** |
| Source scan for the removed identifiers | only the dormant `Organization.require_mfa` column remains |
| Frontend: eslint, `tsc`, vitest | clean / clean / **865 passed in 47 files** (was 876 in 48: the authenticator screen's tests and the step-up tests are gone) |
| Real browser | **not re-run** after this change; the partner-upgrade journeys (password sign-in only) were verified earlier, before the removal |

## Your risk, plainly

The Super Admin console, the partner portal and every customer organization are now protected by a password alone. A stolen staff password opens everything that account's grants allow, including payout approval, refunds and commission adjustments. What still limits it: the password policy, lockout and rate limits, session lifetimes, per-permission `StaffGrant`s, the impersonation block, the audit log and the different-person rule for payout approval. Use long, unique staff passwords. If you want a second barrier without code in this application, restrict the `admin.` host at the edge (an IP allow-list or an access proxy).

## Owner steps

1. Commit when satisfied (this and the partner-upgrade work are separate concerns in one working tree).
2. In production nothing needs changing: `W360_MFA_ENCRYPTION_KEY` in the env file is simply no longer read; delete it whenever convenient. Organizations that had required two-step verification stop being enforced, and their members are not told.
3. Decide whether to drop the dormant tables and columns later.

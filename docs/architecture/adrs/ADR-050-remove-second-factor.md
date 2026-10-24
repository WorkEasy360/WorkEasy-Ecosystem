# ADR-050: Remove the second factor (2FA / MFA) from the whole platform

Status: accepted, 2026-10-24, on the owner's instruction ("remove 2fa from entire software"). Supersedes the second-factor decisions of Phase 5 (enrolment, TOTP, recovery codes, step-up, tasks 37-39), Phase 8's organization "require two-step verification" policy and Phase 12 D-037 (a staff identity must hold a factor first). It follows the same day's withdrawal of the staff sign-in code (admin simplification, 2026-10-24).

## Context

The platform asked for a second factor in four places: a person could enrol an authenticator and recovery codes; `dangerous` permissions demanded "AAL 2" (a verified code within 15 minutes) and answered `step_up_required` otherwise; an organization could require every member to verify a code (`require_mfa`, `mfa_required`); and a person could only be designated staff after enrolling one. The owner asked for all of it to go.

## Decisions

1. **Nobody is ever asked for a code, and nothing requires that one exists.** Removed:
   - the **step-up gate**: `authz.check` no longer reads an assurance level. `dangerous` keeps its other two meanings - refused under impersonation, refused while the organization is read-only - and a `dangerous` action now succeeds for any signed-in principal that holds the permission;
   - the **assurance level itself**: `TenantContext.aal`, `platform_context(aal=)`, `partner_actor/guest_actor(aal=)`, `TenantResolution.aal`, the session keys (`w360_aal`, `w360_step_up_at`, `w360_mfa_verified`) and their helpers (`session_aal`, `record_step_up`, `reset_session_aal`), `StepUpRequired`;
   - **enrolment and challenge**: the five `auth/mfa/*` routes and their views, `identity.mfa` (TOTP, recovery-code hashing, field encryption), the service functions (begin/confirm enrolment, step-up verification and its lockout, factor listing and removal), `lifecycle.reset_second_factors`, the `w360_staff_mfa_reset` command, and the factor requirement of `w360_staff_identity`;
   - **the organization policy**: `AccessPolicy.require_mfa`, `SessionFacts.mfa_verified`, the `mfa_required` denial, and `require_mfa` in the security-policy API;
   - **account sessions**: ending another session no longer needs a verified code;
   - **payload fields**: `mfa_enrolled` (sign-in session, staff users, partner team), `mfa_enabled`, `with_second_factor`, `aal` (admin and organization context, account sessions) and the `aal` claim of product ID tokens (and discovery). The sibling products read that claim tolerantly (EasyBook `claims.get("aal")`, Project-Module defaults to 1, Help Desk records `?? null`), so its absence breaks none of them;
   - **settings**: `MFA_ENCRYPTION_KEY` / `W360_MFA_ENCRYPTION_KEY` (no longer read, no longer required in production), `STEP_UP_MAX_AGE_SECONDS`, `MFA_STEP_UP_RATE_LIMITS`;
   - **the frontend**: the authenticator settings screen, the step-up prompt and its retry flow, the "require two-step verification" control and gate, and every 2FA column and line of copy.
2. **Nothing stored is destroyed.** The `AuthFactor` table (with the secrets and recovery-code hashes people enrolled), `User.mfa_failed_attempts` / `mfa_locked_until`, `Organization.require_mfa` and `AuthorizationCode.aal` are left in the database, defined in the models and marked dormant; no code reads or writes them. Dropping them is an irreversible contract migration and needs its own go-ahead. Until then the stored secrets stay encrypted under the key in each environment's env file; that key may be deleted from the env files whenever the rows are dropped or no longer matter.
3. **The audit and log redactors keep their `mfa|otp|totp` patterns**: they cost nothing and still protect any such value that might appear.

## Consequences (the owner's risk, stated)

- **The Super Admin console, the partner portal and every customer organization are protected by a password alone.** A phished or reused password of a Super Admin account now opens everything that account's grants allow, including the money actions (payout approval, refunds, commission adjustments, capability and partner grants). What still limits that: the password policy and its common-password check, the failed-sign-in lockout and rate limits, the session lifetime and idle window, `StaffGrant` per permission, the impersonation block on `dangerous` actions, the append-only audit log, and a different-person rule for payout approval. Staff passwords should be long and unique; restricting the `admin.` host at the edge (an IP allow-list or an access proxy) would restore a second barrier without any code in this application.
- Organizations that had turned on "require two-step verification" are no longer enforced; their members are not told.
- The browser-check harnesses under `scripts/` that answered second-factor prompts still run: their seeds no longer enrol a factor, `STAFF_TOTP` is empty, and the prompt-answering helpers find no prompt. Their READMEs describe the old prompts.
- Historical docs (Phase 5, ADR-025-era notes, earlier status documents) describe the factor as it was; this ADR is the current word.

## Alternatives

- **A switch that turns enforcement off.** Rejected: the owner asked for removal, and a dormant switch leaves the enrolment and challenge surface (and its rate limits, keys and tests) in the product.
- **Remove the code and drop the tables now.** Not done: it destroys every enrolled secret and recovery-code hash for no functional gain, and cannot be undone. Listed as a follow-up.
- **Keep it optional for people who want it.** Not done: a factor that nothing can require protects nothing the platform can rely on, and the owner asked for none.

## Reinstating it

Git history holds the removed modules (`platform/identity/mfa.py`, `interfaces/api/v1/mfa_views.py`, the identity service block, the frontend `features/security/*`). The dormant tables mean a reinstatement needs no schema work for enrolment; the context field, the gate, the session keys and the policy flag would be re-added.

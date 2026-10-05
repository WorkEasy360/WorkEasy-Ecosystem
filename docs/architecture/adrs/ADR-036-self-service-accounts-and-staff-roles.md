# ADR-036: Self-service accounts verify with the link *and* the password; staff roles are named bundles of the one grant model

**Status:** Accepted
**Date:** 2026-09-26
**Deciders:** Architecture, Security
**Depends on:** [ADR-003](ADR-003-shared-identity.md), [ADR-027](ADR-027-control-plane-principals.md), [ADR-033](ADR-033-session-registry-and-organization-access-policy.md)

---

## Context

The platform had no public signup, no email verification and no password reset. Accounts came only from:
- invitations;
- partner provisioning, which creates an account with no credential (D-020);
- `createsuperuser`.

The brief asks for:
- signup with email verification;
- one permanent identity classified as Explorer or Customer;
- named administrator roles with least privilege;
- an idempotent super-admin bootstrap driven by deployment secrets.

## Decision

### Self-service accounts

- **One identity from the first minute.** Signup creates the `User` the person keeps, in the new status `pending`. A pending account cannot authenticate, join or buy. The Explorer and Customer classifications are derived from what the person's organizations hold (`CustomerSummary.access_class`); an account never moves between tables.
- **Answers are uniform.**
  - Signup always returns 202.
  - A registered address receives a "you already have an account" email instead of a link, at most one per hour.
  - Validation, including the password policy, runs identically for every address.
  - Every path hashes the offered password once, so response time does not reveal which path ran.
  - Resend and password reset are silent for unknown addresses.
- **Verification needs the emailed link and the password together.**
  - The link proves the mailbox; the password proves the person who chose it.
  - Someone who signs up with another person's address cannot get their password activated by that person clicking an unsolicited link (pre-account-hijacking).
  - The mailbox owner's way in is a password reset, which replaces the password and activates the account.
  - A repeated signup for a pending address resends the link; it never replaces the password.
- **Links** (`identity.AccountToken`):
  - 256-bit, stored only as SHA-256, single use;
  - the latest link of each purpose retires earlier ones;
  - sealed in the notification;
  - read from the URL fragment and posted in a request body.
- **Account email with no organization.** Verification and reset emails are queued under the platform organization by `notifications.notify_account`, which uses a routing context with no actor.
- **Reset** proves the mailbox, so it also:
  - verifies the address;
  - activates a pending account;
  - clears both lockouts;
  - ends every session.

  A notice follows. It gives a partner-provisioned owner a way to set a credential (D-020).
- **Sign-in** refuses a pending account with `email_not_verified` only after the password matched. Anyone else sees `invalid_credentials`.
- **Pruning.** Never-verified signups are pruned after 7 days, so an address cannot be squatted. Partner provisioning may name a pending address.
- **Signup switch.** Signup can be closed at runtime (Platform settings, dangerous, audited). Every write is rate-limited per client address and per hashed inbox.

### Staff roles and bootstrap

- **A role is a code-declared set of `platform.staff_*` permissions:**
  - `SUPER_ADMIN`
  - `USER_ADMIN`
  - `PRODUCT_ADMIN`
  - `BILLING_ADMIN`
  - `SUPPORT_ADMIN`
  - `READ_ONLY_ADMIN`
- **Holding a role *is* holding each grant.** Each is an ordinary `StaffGrant`, created, expired and revoked like any other, and audited once per role. A person "holds" a role when they hold all of its permissions; this is derived, never stored. There is no role table and no second authorization path. `authz.check` still reads grants (H1).
- **Platform-wide dangerous permissions are `SUPER_ADMIN` only:** staff grants, payout approval, partners, settings, communication and AI governance.
- **Revoking a role** ends only the grants no other held role needs. The last-permanent-security-administrator guard applies.
- **`bootstrap_superadmin`** replaces `w360_bootstrap_staff`. It reads `PLATFORM_SUPERADMIN_EMAIL` and `PLATFORM_SUPERADMIN_PASSWORD` from the deployment's secret store.
  - It creates the account if absent: verified, active, source `operator`.
  - It never changes an existing account's password and never prints one.
  - It is idempotent for the same address, so it is safe on every deploy.
  - It refuses once a *different* security administrator exists, so it is no back door.
  - It is serialised by an advisory lock and audited as a system action.
- **Account deactivation** (the staff "disable" action) now refuses to remove the last permanent security administrator. The command-line path already had this rule; the HTTP path did not.
- **Staff sign-in and sign-out** are recorded in the control-plane audit log.

## Alternatives considered

- **Sign in straight from the verification link.** Rejected: it lets an unsolicited click activate a stranger's password.
- **Create the `User` only at verification (a signup-request table).** Rejected: it duplicates identity state, and the same attack applies at the moment of verification.
- **A staff-role table with role assignments.** Rejected: it creates a second authorization model to keep consistent with grants, and grants already carry expiry, audit and revocation.

## Consequences

- A person must enter their password once more to finish signing up. That same step signs them in.
- `UserStatus.PENDING` is a new value in the contracts enum. Nothing that authorises reads it except `can_authenticate`, which is false for it.
- The billing-manager payment step no longer requires a second factor. This is the owner decision recorded in ADR-035.

# ADR-033: Sessions are a registry the platform owns; an organization's access policy only narrows access

**Status:** Accepted
**Date:** 2026-09-25
**Deciders:** Architecture, Security
**Depends on:** [ADR-021](ADR-021-launcher-access-decision-and-seat-projection.md), [ADR-022](ADR-022-access-directory-projection.md)

---

## Context

The organization console needs what every business-suite admin panel shows:
who is signed in, from what kind of device, and the ability to end a session;
an owner needs to require two-step verification, cap session age, set a
minimum password length and restrict sign-in to the company's networks.

Until now a session was an opaque row in Django's `django_session`: no owner,
no creation time, no way to list or revoke one person's sessions short of
bumping their `session_epoch` (which ends *all* of them). `SESSION_ABSOLUTE_TIMEOUT`
had been declared since Phase 4 and never enforced. Nothing recorded a sign-in.

The request constrained the design: device and network controls must **add
to** authentication, RBAC, tenant isolation and MFA, never replace them; policy
changes, allow-list changes and session revocation are dangerous (AAL 2) and
audited.

## Decision

**A session registry, not a second session system.** `SESSION_ENGINE` points at
`platform/identity/sessions.py`, a subclass of Django's database backend whose
model (`identity_session`) adds the owning user, creation and last-activity
times, a masked network prefix (/24 or /48) and a coarse device label from a
closed vocabulary - never the raw address or user agent. Django still loads and
saves the session exactly as before, so there is **no extra query per request**.
Revoking a session deletes its row; the next request is anonymous. The absolute
lifetime is enforced when the session loads.

**Account events are the person's; sign-ins into an organization are the
organization's.** `identity.AccountEvent` (control plane) records sign-in,
failed sign-in, password change and revocations for the person's own security
page and for staff. `organizations.MemberSignIn` (tenant table, RLS) is written
when a session *enters* an organization - once per (membership, session) - so
an administrator's dashboard counts only activity in their organization and
can never learn where else a member works.

**The policy lives on the organization row.** `require_mfa`,
`session_max_age_minutes`, `password_min_length`, `ip_allowlist_enabled`,
`ip_allowlist` and a version are columns of `Organization`, which the tenant
resolver already loads; `Membership.sessions_valid_after` implements "sign this
member out of this organization". One pure function,
`contracts/values/access_policy.evaluate`, decides - network first, then
session age and sign-out stamp, then two-step verification, then password -
and it is applied in **two** places: the tenancy middleware (every
organization-scoped request) and the OIDC authorize path (every product
sign-in), both *after* membership, organization status and RBAC have allowed
the request. It can only refuse. An empty policy refuses nothing.

**Denials say what to do.** `network_not_allowed` (403), `mfa_required` (403,
the console offers enrolment or verification), `reauthentication_required`
(401), `password_change_required` (403). To a product the same set arrives as
the OIDC error `access_policy_denied` with the code as its description.

**Changes cannot lock the editor out.** Every change needs
`platform.security.update` (dangerous: AAL 2, refused under impersonation,
audited with the before/after values but never a secret) and the current
policy version. An allow-list that would exclude the caller's own address is
refused with the address shown. If an organization still locks itself out,
staff with `platform.staff_customer.manage` reset the network policy with a
reason (AAL 2, audited).

**Passwords: length, not composition.** The organization may raise the
minimum length; it may not demand character classes (NIST SP 800-63B). The
strictest minimum across a person's organizations applies to a new password,
and a password below an organization's minimum is flagged at sign-in so that
organization asks for a change before admitting the session.

## Consequences

- Deploying the registry signs everyone out once: `django_session` is not read.
- Product server-to-server calls (client credentials) are not user traffic and
  are not filtered by the allow-list; the allow-list governs people.
- The policy is read from the row already loaded per request; a change applies
  to the next request of every session.
- A person's sessions are listed and revoked by the person; staff can sign a
  person out everywhere (`platform.staff_user.manage`, dangerous); an
  organization administrator can sign a member out of *their* organization
  only (`platform.member.sign_out`, dangerous) and never out of the platform.

## Rejected alternatives

- **A policy table joined per request** - one more query on every request for
  data the resolver already has a row for.
- **Revocation lists checked per request** - a lookup per request to express
  what deleting the session row already expresses.
- **Recording organization sign-ins at platform login** - the login does not
  know which organization will be entered, and recording all of them would
  show one organization's administrator a member's activity in another.

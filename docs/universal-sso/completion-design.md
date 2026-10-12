# Universal SSO completion: Easy Project, Easy Help Desk, and the shared rules

**Written for: the engineers implementing and reviewing the last two OIDC clients, and the operators deploying them.**

Brief: "UNIVERSAL SSO FINAL COMPLETION" (2026-10-12). Builds on ADR-044 and `design.md`; nothing there is redesigned. The record of what was done and proven is `status.md`.

## 1. Where the product code is

| Product | Repository (cloned 2026-10-12) | Stack |
|---|---|---|
| Easy Project | `github.com/WorkEasy360/Project-Module` → `Desktop/Project-Module` | Spring Boot 3.5 (Java 21) API serving a React/Vite SPA from one jar, at `ops.workeasy360.com` |
| Easy Help Desk | `github.com/WorkEasy360/helpdesk-workeasy` → `Desktop/helpdesk-workeasy` | NestJS 11 API + BullMQ worker + Prisma 6; Next.js 15 frontend; nginx in front, at `help.workeasy360.com` |

`WorkEasy360-HelpDesk` (same account) is an older copy of the Help Desk; the live site runs `helpdesk-workeasy`.

## 2. Registration (Workeasy360)

| | Easy Project | Easy Help Desk |
|---|---|---|
| Product code (unchanged) | `easy_project` (slug `easy-project`) | `easy_helpdesk` (slug `easy-helpdesk`) |
| Launch URL | `https://ops.workeasy360.com/` | `https://help.workeasy360.com/` |
| Redirect URI (exact, the only one) | `https://ops.workeasy360.com/api/auth/callback/workeasy360` | `https://help.workeasy360.com/api/auth/callback/workeasy360` |
| Post-logout URI | `https://ops.workeasy360.com/` | `https://help.workeasy360.com/login` |
| `sign_in_mode` | `sso_only` (it never had a sign-in of its own) | `sso_only` (production runs with `WORKEASY360_SSO_ONLY` on, like Easy CRM and EasyBook) |
| Scopes | `openid email profile organization` | same |

The product codes stay what the registry, entitlements and tests already use; the brief's `easy-project`/`easy-helpdesk` are the slugs.

## 3. The client contract both products implement (the one Easy CRM and EasyBook proved)

1. **Configuration** from the environment: `WORKEASY360_ISSUER`, `WORKEASY360_CLIENT_ID`, `WORKEASY360_REDIRECT_URI`, `WORKEASY360_KEY_ID`, `WORKEASY360_PRIVATE_KEY_FILE` (or `WORKEASY360_PRIVATE_KEY`, literal `\n` allowed), `WORKEASY360_PRODUCT_CODE`, optional `WORKEASY360_POST_LOGOUT_REDIRECT_URI`. Production refuses to start while SSO is the only way in and any of the first five is missing.
2. **Discovery** from the issuer only; the document's `issuer` must equal the configured one exactly; cached for an hour; JWKS cached, one refresh on an unknown `kid`, then fail.
3. **Start** (`GET /api/auth/workeasy360/start?organization=<uuid>&next=<path>`): `state` and `nonce` (32 random bytes each), PKCE S256 (32-byte verifier), `organization` forwarded only when it is a UUID. The pending flow (state, nonce, verifier, organization, next, time) is kept **server-side, single use, 10 minutes**, bound to this browser.
4. **Callback** (`GET /api/auth/callback/workeasy360`): the flow is consumed first, whatever happens next. Provider `error` → mapped code. State compared in constant time; missing state or no pending flow → `expired`. Code exchanged with `private_key_jwt` (iss = sub = client id, aud = token endpoint, `jti`, 60 s). ID token verified offline: key by `kid` from our cached JWKS; algorithm from the key (RS256/ES256 only); `aud` = our client id; `iss` = issuer; `exp`/`nbf`/`iat` with 30 s leeway; `iss sub aud exp iat jti typ` required; `typ` = `id_token`; nonce in constant time; `organization_id` present; `product_code` = ours; when the launcher named an organization, the token's must equal it.
5. **Federated identity** is `(issuer, sub)`, never the email. The email is profile data. An existing local account is linked by address only when Workeasy360 says `email_verified: true`, only inside the organization bound to that Workeasy360 organization, never onto an account already linked to another subject, and the local password is retired and its sessions revoked at the link (EasyBook's rule).
6. **Organization binding**: the product session is pinned to the organization the verified token named. Nothing the browser sends afterwards can change it.
7. **Session**: the product's own, created fresh at sign-in (new session id: no fixation). A sign-in by a different person in the same browser ends the previous session server-side first (account switching).
8. **Redirects**: `next` must be a same-site path: starts with `/`, not `//`, no whitespace, control character or backslash, at most 2,048 characters, not an auth page. Anything else lands on the home page.
9. **Logout**: "Sign out of <product>" ends the product session only. With a post-logout URI configured, "Sign out of <product> and Workeasy360" ends it, then sends the browser to the discovered `end_session_endpoint` with `client_id` and that URI. No front- or back-channel logout (ADR-044, accepted P3).
10. **Launcher**: `/?organization=<uuid>` always starts a sign-in for that organization, even with a session (integration guide §6a). The product's sign-in page never auto-starts one.
11. **Errors** go to `/login?sso_error=<code>` (Easy Project: its sign-in route) from a fixed vocabulary: `cancelled`, `expired`, `unavailable`, `refused`, `rate_limited`, `not_entitled`, `no_seat`, `provider_membership_inactive`, `organization_inactive`, `access_policy`, `email_missing`, `email_unverified`, `account_conflict`, `account_disabled`, `membership_inactive`, `wrong_product`, `wrong_organization`. Logs carry codes and ids, never a code, token, state or nonce.

## 4. Entitlement while a session lasts (both new products, and Easy CRM and EasyBook)

Sign-in checks the person, the organization, the entitlement and the seat. A product session then lives on, so the product keeps asking the entitlement API (`workeasy360_entitlement_endpoint`):

- **Per person** for a signed-in request: `organization_id` + `subject` (the `sub` it signed the person in with). **New in this brief** (ADR-044 addendum): Workeasy360 then also answers `deny` with `membership_inactive` (account disabled or locked by an administrator, membership suspended or removed) or `no_seat` (per-seat product, seat unassigned). Without it, a removed member kept working until their product session ended.
- **Per organization** for background work (no person): `organization_id` only.
- **Cadence**: an `allow` is trusted for `min(revalidate_after, 900 s)` (at least 30 s). A denial is re-asked after 60 s on a person's request (a renewal takes effect quickly) and after 900 s for background work (an expired organization's jobs must not cost a question a minute each). A refusal of the product's own client is re-asked after 60 s everywhere, background included.
- **Outage** (5xx, 429, timeout, network, misconfigured discovery, unreadable key): never a denial. The last answer stands; with none, access continues. The next attempt waits 60 s.
- **Refusal of the product's own client** (400/401/403: revoked credential, suspended integration): not an outage, and **client-wide**, not per person: for the next 60 s every question (person, organization, background) fails closed with `workeasy360_access_unverified` without asking, and a 429 within 5 minutes of the last refusal counts as a refusal too (Workeasy360's client-authentication failure limit answers 429 after about ten failures a minute, which would otherwise read as an outage and let most people keep access). One question is asked again after 60 s; a 429 with no refusal in the last 5 minutes is an outage. Logged as an error.
- **SSO-only means every person is federated**: with the product's SSO-only switch on, a session in an organization linked to Workeasy360 from an account with no Workeasy360 identity (a session opened before the switch, a product-only account) is refused (`workeasy360_sign_in_required`) and the person signs in through Workeasy360, which links the account by verified address. Otherwise such a session could only ever be checked per organization and would survive the person's removal. Machine credentials (API keys) stay organization-level.
- **A known denial never lapses into access**: kept without expiry; one request asks while others use the last answer; a first check is never waved through because another request is asking.
- **Answers**: `deny` → 403 `workeasy360_access_ended` (with the reason); `allow` without `write_allowed` → writes 403 `workeasy360_read_only`; refused → 403 `workeasy360_access_unverified`. The session stays valid, so the person sees one "access has ended" screen and can sign out.
- **Exempt**: sign-in, sign-out, the session/me read, the methods read, health.

The worst case after access ends is one window (15 minutes), for every cause the brief lists: entitlement denial, organization suspension, membership removal, account disabled, seat removed, trial expiry.

## 5. Background work (all four products)

A job that produces a customer business effect must re-check the organization's entitlement before producing it. Classes:

| Class | Rule | Examples |
|---|---|---|
| **A. Requires active access** | Org-level `allow` **and** `write_allowed`, checked when the job runs. Denied: the effect is skipped, logged (`workeasy360.background_skipped`, org, job, reason), not retried in a loop, and the schedule is left so the work resumes after renewal. Outage: last answer stands, none → run. | automations and rules, outbound e-mail/messages/channel replies, outbound webhooks, recurring invoices/bills, external syncs, reminders, SLA escalations and notifications, AI processing |
| **B. Allowed after access ends (cleanup)** | No check. | session and token expiry, finishing an export the customer already asked for, survey expiry, temporary-file cleanup, deletion and purge |
| **C. Retention, legal, system maintenance** | No check. | metric rollups, audit retention, backups, index maintenance, permission catalogue sync |

Only organizations linked to Workeasy360 are checked; a product-only organization has no Workeasy360 entitlement to lose. A sweep **looks for due work first** and asks only about the organizations that have some: asking about every linked organization first would make every expired trial cost a question every 15 minutes, forever (`capacity.md` §1). Skipped work must resume after renewal - a sweep picks it up again - never be failed or dropped for having been skipped. Each product's own document lists its jobs by class.

## 6. Capacity of the entitlement API

See `capacity.md`. Summary: per product client `W360_RL_VERIFY` 1,200/min (was 600); new per (client, organization, person) bucket `W360_RL_VERIFY_ORG` 6/min.

## 7. What is not done here

- D-035 (sign-in on a partner's custom domain) is untouched.
- No front- or back-channel logout.
- No new business features in any product.

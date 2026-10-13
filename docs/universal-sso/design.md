# Universal, user-controlled product sign-in: design

Status: design frozen 2026-10-12, before implementation. Builds on the Phase 3 OIDC provider (docs/phase-3/), ADR-021 (launcher), ADR-033 (session registry), ADR-038 (four products) and ADR-043 (multi-portal hosts and central sign-in). The decision record is ADR-044.

## 1. What already exists (reused, not rebuilt)

- **One identity provider.** `platform/ecosystem` is an OpenID Connect provider: authorization code + PKCE (S256 only), exact-match redirect URIs, single-use 60 s codes stored as SHA-256, `private_key_jwt` / client-secret client authentication, ID tokens verified offline against the JWKS, no refresh tokens. Products are OIDC clients registered as `ProductIntegration` rows whose validated **manifest** is the single source of launch URL, redirect URIs, scopes and post-logout URIs.
- **Live checks at sign-in.** The authorization endpoint and the token endpoint both check, live: active user, active membership in the named organization, organization status, the organization's access policy (IP allow-list, required MFA, session age), entitlement (trial or paid, with the trial's `ends_at`), and a seat for per-seat products. A preference can never skip these: they run before any preference is read.
- **Parked requests.** An authorization that needs the platform's UI (sign-in, organization chooser) is parked as a single-use `PendingAuthorization` row and resumed with a token.
- **The launcher** decides `active / no_seat / no_entitlement / integration_suspended / product_unavailable` with one function, re-run at launch, and sends the browser to the registered `launch_url?organization=<uuid>`; the product then starts its own OIDC request.
- **Two products have OIDC clients** (2026-10-12, in their own repositories): EasyBook and Easy CRM. Both start Workeasy360 sign-in from `?organization=<uuid>` even when a product session exists, verify state, nonce, issuer, audience, signature and expiry, and map people by `(issuer, sub)`. Easy Project and Easy Help Desk have no client; their code is not available to this work.

**No generic preference mechanism exists.** The only preference table, `notifications.NotificationPreference`, is tenant-scoped per membership and per notification template. A sign-in preference belongs to the *person* across organizations, so it cannot live there. One small table is added (section 4).

## 2. The model

```
                 user's preference             product's declaration
                 (per product, or default)     (ProductIntegration manifest)
                          \                         /
                           \                       /
                      effective sign-in for (person, product)
                          |                |                 |
                       auto_sso           ask           product_login
                          |                |                 |
   launcher (portal)   launch_url       launch_url        login_url
                          |                |
   authorize (IdP)     silent code     access chooser  (also for product_login
                                        on the auth     when the product itself
                                        host            sent the person to SSO)
```

### 2.1 What the product declares (registry, never the user)

Manifest version `2026-10-12` adds two optional fields:

| Field | Values | Meaning |
|---|---|---|
| `sign_in_mode` | `sso_only` (default when absent) · `sso_optional` · `local_only` | Whether the product signs people in with Workeasy360 only, offers its own sign-in as well, or does not use Workeasy360 sign-in at all |
| `login_url` | https URL (loopback http outside production), same validation as `launch_url` | The product's own, approved sign-in page. Required for `sso_optional`; optional for `local_only` (the launch URL is used otherwise) |

`post_logout_redirect_uris` (already in the manifest) becomes meaningful: the exact URIs Workeasy360 may return a browser to after an RP-initiated sign-out.

A manifest without `sign_in_mode` is an OIDC client registration by construction (redirect URIs are required), so it reads as `sso_only` with no product sign-in page: exactly how every existing registration behaves today.

**SSO readiness** is a server fact: the integration is `active`, its mode is not `local_only`, and it holds a usable credential (active, unrevoked, unexpired). The registry reports `sso`: `enabled` / `integration_required` (SSO declared, no usable credential yet) / `disabled` (`local_only`). Products that are not SSO-ready are labelled "Separate product sign-in required" and are never routed through Workeasy360 sign-in. A credential proves the token endpoint would accept the product, not that the product's own client works; per-product PASS is reported only from a real sign-in.

### 2.2 What the person chooses

`SignInPreference` values: `inherit`, `auto_sso`, `ask`, `product_login` (enum `contracts.enums.SignInPreference`; labels are frontend copy only).

- A **default** for every product, including products added later. No stored default means `auto_sso` (since the one-login brief, `one-login.md` §1 and ADR-044 item 14; it meant `ask` before).
- An optional **override per product**. `inherit` is not stored: choosing it deletes the override, so a product added in six months needs no migration and no per-product row.

### 2.3 Effective sign-in (one pure function, `contracts.values.sign_in.resolve`)

1. Product not SSO-ready (`local_only`, no credential, not active) -> **`product_login`**, constraint `sso_unavailable`. The card says "Separate product sign-in required"; nothing pretends SSO works.
2. Preference = product override if set, else the person's default, else `auto_sso` (the platform default).
3. Preference `product_login` but the product offers no sign-in of its own (`sso_only`, or no `login_url`) -> **`ask`**, constraint `product_requires_sso`. The person's wish not to be signed in automatically is still honoured; the product's constraint is too.
4. Otherwise the preference.

Organization policy over user preference: the organization access policy (MFA, networks, session age) is already enforced on every product sign-in and is unaffected by any preference. A future "require Workeasy360 sign-in" organization policy would be applied as a constraint between steps 1 and 2. It is not built (the brief: only if the architecture supports it, and user choice stays the default).

## 3. Where each choice takes effect

### 3.1 The launcher (customer portal, `app.`)

`POST /orgs/<slug>/apps/<code>/launch` re-runs the access decision first (unchanged: entitlement, seat, integration). Only an `active` decision reaches sign-in routing:

| Effective | Browser goes to |
|---|---|
| `auto_sso`, `ask` | `launch_url?organization=<uuid>` - the product starts OIDC; the authorization endpoint applies the preference |
| `product_login` | the registered `login_url` (or `launch_url` for a `local_only` product without one). No organization hint is added: the product's own page must not auto-start SSO |

The response says which (`sign_in: "workeasy360" | "product"`); the destination is still built server-side from the registry and never from the request.

### 3.2 The authorization endpoint (identity provider, `auth.`)

After every existing check passes (so a refusal is never hidden behind a chooser):

| Effective, `prompt` | Result |
|---|---|
| `auto_sso`, no prompt | Code issued silently: the existing Workeasy360 session is reused, no password |
| `ask` or `product_login` | Request parked; browser sent to the **access chooser** |
| any, `prompt=select_account` or `consent` | Access chooser |
| non-auto, `prompt=none` | `interaction_required` to the product (no UI) |
| no session, `prompt=none` | `login_required` to the product (no UI) |
| any, `prompt=login` | Parked for sign-in; resumed only by a session that authenticated after the request arrived |
| `local_only` product | `unauthorized_client` to the product: its registration says it does not use Workeasy360 sign-in |

The chooser is shown only where the request would otherwise complete with no interaction. A person who has just typed their password (sign-in parked) or picked an organization (organization chooser) in this flow has already acted, and is not asked again.

Why the chooser lives at the identity provider, not in the portal: it is the one place every sign-in passes, whether it started from the launcher, from the product's own landing page, or from a product that redirects to Workeasy360 on its own. Enforcing the preference there means no product can sign a person in automatically against their choice. It also knows which account is signed in, which the product does not.

### 3.3 The access chooser (`/product-access?resume=<token>`, auth host)

```
[icon] Easy CRM
How would you like to access Easy CRM?
Signed in to Workeasy360 as  user@example.com
Organization                 ABC Ltd.

[ Continue with Workeasy360 ]   Use your existing Workeasy360 account.
[ Use Easy CRM sign-in ]        Use the product's separate sign-in page.   (only when offered)
[ ] Remember my choice for Easy CRM
Not you? Use another Workeasy360 account      Cancel      Sign-in preferences
```

- Data: `GET /integrations/oauth/pending?resume=` returns only display facts (product name and icon, organization name, the signed-in address, which choices exist). The parked row is bound to the person who was signed in when it was parked; another session gets the uniform "invalid or expired" answer.
- Answer: `POST /integrations/oauth/pending/choose {resume, choice, remember}` (session + CSRF):
  - `workeasy360`: the row is released for resumption (conditional update), the browser is sent to `resume`, which re-runs every check and issues the code. `remember` stores `auto_sso` for this product.
  - `product`: the row is spent; the browser goes to the product's registered `login_url`. `remember` stores `product_login`. Offered only when the product has its own sign-in.
  - `switch_account`: the row is spent; the same verified request is re-parked unbound; Workeasy360 signs out this sign-in (every portal session of it, ADR-043) and opens the sign-in page with the new token. Whoever signs in next gets the code, after every check runs for *them*. Nothing of the previous person's session survives (logout rotates and deletes the session).
  - `cancel`: the row is spent; the product receives `access_denied`.
- The resume endpoint refuses a row still awaiting a choice (a hand-typed resume URL cannot skip the chooser), sending the browser back to the chooser with the same token.

### 3.4 Silent authentication and the second factor

A silent code reuses the Workeasy360 session and carries that session's `aal` and the access-policy facts, exactly as today: an organization that requires MFA still refuses a session that has not verified it, with or without a preference. `prompt=login` requires a fresh authentication. The ID token gains `auth_time` (when the session authenticated) so a product can check how recent the authentication is instead of trusting the round trip. Additive claim; existing products ignore it.

## 4. Data

```
ecosystem_sign_in_preference
  id            uuid pk
  user_id       fk identity_user, on delete cascade
  product_code  varchar(32), '' = this person's default for every product
  mode          auto_sso | ask | product_login      (never inherit: inherit = no row)
  updated_at
  unique (user_id, product_code); check mode in (...)
```

Control-plane-adjacent like `identity.User`: no organization column, no RLS, never read by an organization administrator. `PendingAuthorization` gains `awaiting_choice` (bool), `user_id` (who it is bound to, when known) and `authenticated_after` (`prompt=login`).

Why one table, not two: the default and the overrides have the same shape and the same owner; a second table would add a join and a second write path for one row per person.

## 5. Settings (`/o/<slug>/settings/sign-in-preferences`)

`GET/PUT/DELETE /api/v1/auth/sign-in-preferences` (served on every portal host, like the other account endpoints). Lists every registered product that is not retired, with its sign-in mode, SSO state, the person's override and the effective result; products that are not SSO-ready show "Separate product sign-in required" and no control. `PUT` changes only the keys sent; an override of `product_login` for a product without its own sign-in is refused (it could never take effect). `DELETE` is "Reset all to default". A change takes effect on the next launch; nothing is signed out.

Privacy and audit: a preference is the person's own. It is recorded in their account-security trail (`AccountEvent sign_in_preference_changed`, retained like other account events), never in an organization's audit log. Changes to a product's sign-in configuration (mode, login URL, redirect and post-logout URIs, scopes, credentials) are Super Admin acts: dangerous (second factor), audited in the immutable control-plane log, exactly like today's manifest edits.

## 6. Super Admin (`/admin/products`, Authentication section)

Per product: Workeasy360 SSO (Enabled / Disabled / Integration required), mode (Optional SSO / SSO only / Local sign-in only), client ID, redirect URIs, post-logout (sign-out return) URIs, product sign-in URL, launch URL, scopes, status, credentials (metadata only). Actions: register an integration for a catalog product that has none; edit the above; add a public key or generate a client secret (**shown once**, never readable again); revoke a credential; activate / suspend. A new product therefore joins universal sign-in through the console alone: Add product -> register integration -> credential -> activate. No code path names a product.

## 7. Sign-out

| Action | Effect |
|---|---|
| Sign out of a product | The product ends its own session. Workeasy360 is untouched, so the next "Continue with Workeasy360" is silent or asks, per preference |
| Sign out of Workeasy360 (portal) | Every Workeasy360 session of that sign-in ends (ADR-043). Product sessions are **not** ended: no product implements a logout contract Workeasy360 could call, so none is pretended. The next product sign-in through Workeasy360 needs the password |
| Sign out of a product **and** Workeasy360 | RP-initiated logout: the product ends its session and sends the browser to Workeasy360's `end_session_endpoint` with its `client_id` and a registered `post_logout_redirect_uri`. Workeasy360 asks "Sign out of Workeasy360 too?" (a GET cannot sign anyone out: logout CSRF), signs out the sign-in on confirmation, then returns the browser to the exact registered URI |

No front-channel or back-channel logout to other products: they keep their sessions until they expire or the person signs out of them. Stated in the product docs and the final report.

## 8. Sign-in once, across portal hosts

In multi-portal mode the identity provider runs on `auth.` with its own host-only session, while the person works on `app.`. Each session has an 8-hour idle window, so a long day on `app.` could let the `auth.` session lapse and ask for a password on the next product launch, although the person never stopped using Workeasy360. A session that slides its idle window now slides the other live sessions of the same sign-in too (one UPDATE at most once a minute, bounded by each session's own absolute lifetime). Sign-out still ends them all together.

## 9. Observability

Structured log events (each line already carries the correlation id): `ecosystem.sso.started`, `ecosystem.sso.chooser_shown`, `ecosystem.sso.choice`, `ecosystem.authorize.granted` (now with `organization_id` and `silent`), `ecosystem.sso.failed` (category = the OAuth error code), plus the existing token-endpoint events and metrics. Product code, organization id, prompt, choice and category only: never a code, token, password, nonce, state or claim value. A test captures the logs of a full flow and asserts none of those values appear.

## 10. Not done, and why

- No organization override of preferences (not required; the access policy already outranks convenience).
- No front/back-channel logout (no product implements a contract for it).
- No product SSO for Easy Project or Easy Help Desk: their code is not available here; both are registered `local_only` and reported BLOCKED.

## 11. After sign-in: what the products now do (added during implementation)

The browser journeys showed that a product session opened through SSO outlived the access behind it: Easy CRM keeps its own session up to 14 days, and EasyBook's refresh token rotates, so an active user was never cut off when a trial ended. Workeasy360 itself refused new launches and sign-ins, but "SSO must never become an entitlement bypass" was not yet true end to end. The Phase 3 contract already required products to re-check entitlement ([09 §6-7](../phase-3/09-product-integration-guide.md)); neither product did. Both now do:

- **Entitlement re-check** (Easy CRM `apps/accounts/workeasy360/entitlement.py`, EasyBook `sso/entitlement.py`): the existing entitlement API, authenticated with the product's own `private_key_jwt` credential. One cached answer per Workeasy360 organization for `revalidate_after` (900 s); a denial is re-asked after 60 s; a platform failure is never a denial (the last answer stands, or access continues if there is none) and backs off for 60 s; one request asks at a time. Every organization-scoped request is refused with `workeasy360_access_ended` once the answer is `deny`, and every write with `workeasy360_read_only` while `write_allowed` is false. The session stays valid, so the person sees one "access has ended" page and can sign out. Organizations not linked to Workeasy360 are never checked.
- **"Confirm it's you" bound to the person** (Easy CRM): the `prompt=login` flow names the current organization (no chooser) and the callback requires the ID token's `sub` to be the account signed in, as well as a fresh `auth_time`. Another person's password is refused with `reauthentication_wrong_account`, and the session is left as it was.
- **Relative redirects** (EasyBook): the SSO route handlers built absolute redirects from `request.url`, which Next.js resolves to its own listening address. Behind the production proxy that sent every sign-in to `localhost:3000`. They now send a relative `Location`.

The worst case after access ends is now one window (15 minutes) inside a product, instead of the product session's lifetime.

## 12. Hardening from the independent audit

An independent review of all three codebases found nothing critical or high. These findings were fixed:

- **Workeasy360:** an active integration with no usable credential is refused at the authorization endpoint (`unauthorized_client`) for every preference, instead of reaching the chooser and a code the product could never redeem. The chooser also re-checks before releasing a request.
- **Both products' entitlement re-check:**
  - A refusal by Workeasy360 of the product's own client (400/401/403: a revoked credential, a suspended integration) is not an outage. It fails closed (`workeasy360_access_unverified`, "cannot confirm", re-asked every 60 s), because serving on would let every expired organization in for as long as nobody noticed.
  - A known denial is kept without expiry, and each process keeps its own copy of the last answers. A cache outage, eviction or flush can no longer hand access back.
  - A first check is not waved through while another request is asking.
  - Any unexpected failure (an unreadable key file, a broken HTTP reply) counts as an outage, not a 500.
  - Reads made with POST stay allowed for a read-only organization: Easy CRM exports, EasyBook document downloads and Ask Books.
- **Easy CRM:**
  - "Confirm it's you" needs a linked Workeasy360 account; with none it is refused at the start, so an unbound confirmation can never sign in whoever answers.
  - A Workeasy360 record without `auth_time` no longer counts as recent.
  - An account linked to Workeasy360 cannot set a local password or receive a reset link, even with password sign-in on. A local password would be a second way in that skips Workeasy360's MFA and access policy.
- **EasyBook:**
  - Every redirect target (`next` after sign-in included) refuses whitespace, control characters and backslashes anywhere and must resolve on the same site. Before, `/<tab>/evil.example` became `//evil.example` in the browser.
  - The sign-out route forwards the browser's address to the API throttle.

**Accepted and recorded, not changed:**
- Background jobs in both products (recurring invoices and bills, automations and outbound webhooks, e-mail and CRM sync) still run for an organization whose access has ended. No person reaches its data through them, but they keep writing and sending. Stopping them is each product's lifecycle policy.
- Workeasy360's entitlement-verify rate limit: sized 2026-10-12 from a capacity model (`capacity.md`): 1,200/min per client and 6/min per (client, organization, person).
- The access-ended pages offer no organization switcher. A person in several organizations signs out and opens the other one from Workeasy360.

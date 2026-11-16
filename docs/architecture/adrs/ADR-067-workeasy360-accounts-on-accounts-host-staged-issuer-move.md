# ADR-067: WorkEasy360 Accounts - the central sign-in moves to accounts.workeasy360.com, its issuer moves in stages, and auth.workeasy360.com stays reachable while products move

Date: 2026-11-16. Status: accepted; built on `feat/partner-admin-panel`, not yet committed or deployed. Amends ADR-043 (the central sign-in's host and the issuer rule) and ADR-044 (where the OIDC browser endpoints live). Keeps ADR-059 unchanged: the Admin, Partner, customer and Store portals still sign their people in on their own hosts.

## Context

The owner's brief "WorkEasy360 - Rename Existing Auth Service to Accounts Portal" (16 Nov 2026) asks for the existing authentication service on `auth.workeasy360.com` to become the **WorkEasy360 Accounts** portal on `https://accounts.workeasy360.com`. The brief allows no new project, no new user database and no duplicated identities, and it must not break existing integrations. It asks for a staged OIDC issuer move: keep the old issuer at first, move the products, then change the issuer, with no wrong issuer ever given to a product and no weakening of issuer validation.

The audit (`docs/accounts-portal/status.md`, section 1) found:

- The "auth service" is not a separate service. It is one of six hosts served by the same Django and Next.js deployment (ADR-043): the `auth` portal role, set by `W360_AUTH_ORIGIN`.
- Since ADR-059 it serves only the product sign-in: `/login?resume=`, the access chooser, the product sign-out, Switch Account's second step, and the organization choice for a product sign-in. Customer login, sign-up, password reset and the account pages are on `app.`.
- Production required `W360_OIDC_ISSUER == W360_AUTH_ORIGIN`, so the host and the issuer could not move separately.
- The four products are Easy CRM, EasyBook, Easy Project and Easy Help Desk. Each pins exactly one issuer, discovers everything else from it, compares the discovery document's `issuer` and the ID token's `iss` by exact equality, and **keys its stored identities on `(issuer, sub)`**. Changing a product's issuer re-links its users by verified email in three of them and creates new, empty users in Easy Project.
- None of the four completed a sign-in against production on 2026-11-16:
  - Easy CRM answers `sso_error=unavailable`.
  - EasyBook's SSO route does not exist in production.
  - Easy Project and Help Desk are not reachable.
  - Only Easy CRM has its SSO code on its default branch.

The owner chose (2026-11-16) **rename + rebrand**: everything the auth host does moves to `accounts.`, branded "WorkEasy360 Accounts". Customer login and the account pages stay on `app.` (ADR-059). The cross-host hand-off ADR-059 removed does not come back.

## Decision

1. **One portal role, two names.**
   - `W360_AUTH_ORIGIN` is now `https://accounts.workeasy360.com`. The central sign-in's pages and its session live there, and every link the platform builds with `identity.portals.auth_url()` names it.
   - A new optional `W360_AUTH_LEGACY_ORIGIN` (`https://auth.workeasy360.com`) keeps the previous name reachable while products move.
   - No Django app, migration, table, package, cookie name or portal code is renamed. The role is still `auth` inside the code, and only the configured origin changes.
2. **On the previous name, products are served and browsers are forwarded** (`interfaces/api/portal_surface.legacy_auth_answer`):
   - **Served unchanged:** the discovery document, the key set, and the server-to-server product API under `/api/v1/integrations/` (token, userinfo, entitlement verification, events, usage, intelligence). None of these reads a session.
   - **Forwarded:** every browser navigation (`GET`/`HEAD`) - the authorization request, the parked request's resume, the RP-initiated sign-out, and any page - gets a temporary 302 to the same path and query on `W360_AUTH_ORIGIN`. The destination comes from configuration only.
   - **Refused (404):** everything else - the rest of the API, every portal's API, the access chooser's and sign-out's data calls, every write to a page.
   - So **no session is ever read or set on the previous name**, and the session that products sign in with lives on one host only. Logout, Switch Account and the Ask-once record (ADR-066) therefore keep their meaning.
   - The edge sends the whole previous name to the API (`auth_legacy_on` in `deploy/edge/Caddyfile`, switched by `W360_AUTH_LEGACY_EDGE`), so the web app never runs there.
3. **Issuer by host, from a closed list of two** (`platform/ecosystem/tokens.py`):
   - `W360_OIDC_ISSUER` is the canonical issuer, and production accepts only `W360_AUTH_ORIGIN` or `W360_AUTH_LEGACY_ORIGIN`.
   - During the move, `W360_OIDC_TRANSITION_ISSUERS` may name **the other of those two** (checked at start-up). A request whose host *is* that origin is answered as that issuer (`IssuerMiddleware`, a context variable read by `tokens.issuer()`). That covers the discovery document's `issuer` and endpoints, the `iss` of tokens minted there, and the client-assertion audience. Every other host gets the canonical issuer.
   - The Host can therefore only **choose between** the platform's own configured issuers, never name one. A forged Host selects another of our own issuers, with the same keys, for a request whose sender already holds that client's credentials.
   - A product that discovers the platform at `auth.` keeps receiving `iss=https://auth.workeasy360.com`, and one that discovers it at `accounts.` receives that. **No product is ever handed an issuer it was not configured to trust**, and each product moves by changing its own configuration on its own schedule.
   - Verification of the platform's own tokens (userinfo) and of client assertions (the token endpoint's URL under each configured issuer) accepts the configured list. Each token is signed with our keys either way, and an assertion made for any other service is still refused.
4. **Stages, each a complete state, written by one command** (`deploy/single-host/make_env.py accounts --stage`):

   | Stage | Sign-in host | Previous name | Canonical issuer | Also issued (own host only) |
   |---|---|---|---|---|
   | off | auth. | - | auth. | - |
   | a | accounts. | kept | auth. | - |
   | b | accounts. | kept | auth. | accounts. |
   | c | accounts. | kept | accounts. | auth. |
   | d | accounts. | kept | accounts. | - |
   | e | accounts. | retired | accounts. | - |

   - Stage a is the brief's Stage A: the new domain is live, the issuer is unchanged, and machine endpoints are not redirected.
   - Stage b is the brief's Stage B, in which products move one at a time.
   - Stages c and d are the brief's Stage C: the issuer is canonical on `accounts.`, with a time-limited window for the old one.
   - In stage e the old name is retired.
   - Rollback is the earlier stage. Production refuses to start on a mix of two stages, and the tests prove that every stage the tool writes is accepted (`backend/tests/accounts_portal/test_configuration.py`).
   - A new deployment is rendered at stage e (`accounts.` from the start).
5. **Customer-facing name "WorkEasy360 Accounts"** on the pages the accounts host serves:
   - the product sign-in heading (`LoginScreen`, portal `auth`);
   - a shared mark above the access chooser and the product sign-out card (`features/signin/AccountsBrand.tsx`);
   - the page titles of `/login` on that host, `/product-access`, `/sign-out` and `/switch-account`.
   - The chooser's "Welcome to WorkEasy360" heading (ADR-066 copy) is kept.
6. **Migration observability.** Each token exchange logs `ecosystem.token.issued` with the product and the issuer that answered, so the operator can tell when no product is left on the old name. `accounts_legacy_host_requests_total{kind=served|forwarded|refused}` counts what still reaches the old name. Neither ever records a path or a query, because a resume token or a code may be in it.
7. **Sessions.**
   - Cookies stay host-only, with `__Host-` names, the `Secure` and `HttpOnly` flags (CSRF readable, by design) and no `Domain` attribute.
   - A session on the old `auth.` host is simply never read again. People sign in once on `accounts.` the next time a product asks (the brief allows a one-time reauthentication).
   - No session identifier or token travels in a URL.
   - Sessions on admin., partner., app. and store. are untouched, and so is every product's own session.

## Consequences

- Nothing a product sees changes at stage a: the same issuer, keys, endpoints, `sub` and claims. The only visible change is that the browser hops from `auth.` to `accounts.` during a product sign-in, and the person signs in once more on `accounts.`.
- **A product's move needs one data step in the product itself.** A product changing its configured issuer must re-point its stored identities from the old issuer to the new one in the same release (`UPDATE ... SET issuer = 'https://accounts.workeasy360.com' WHERE issuer = 'https://auth.workeasy360.com'`). Otherwise it creates new identity rows, which re-link by email in three products and produce duplicate, empty users in Easy Project. `sub` is the same Workeasy360 user UUID on both names. The per-product checklist is in `docs/accounts-portal/status.md`. Products that are not yet live should be configured with `accounts.` from their first release, which needs no data step at all.
- Stage d is irreversible for any product still configured with `auth.`: it fails closed at discovery, which is the intended behaviour, never a token with the wrong issuer. The runbook waits for 30 days with no token issued as `auth.` before stage d.
- Going back past stage b strands only the products that already moved to `accounts.`, and they fail closed. The runbook forbids it while any product uses `accounts.`.
- Rejected:
  - Changing `W360_OIDC_ISSUER` in one step: every product pins one issuer, so it is a coordinated cutover across four repositories, plus an identity re-link.
  - Accepting both issuers inside each product: a code change in four repositories, and each product would still need the identity data step.
  - Routing the old name to the web app: the frontend would take it for a partner's custom domain (ADR-039).
  - A permanent 301/308: a browser would cache it past a rollback.
  - Moving the customer login to `accounts.`: the owner chose against it; it would need ADR-043's hand-off again.

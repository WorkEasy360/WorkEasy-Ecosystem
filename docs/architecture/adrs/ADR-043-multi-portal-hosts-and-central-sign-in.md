# ADR-043: Multi-portal hosts, central sign-in hand-off, and time-exact entitlement ends

- Status: accepted (2026-10-11)
- Context: the multi-portal + four-product trial brief (record: `docs/portals/status.md`, design: `docs/portals/design.md`)
- Builds on: ADR-003 (shared identity), ADR-018 (OIDC for products), ADR-033 (session registry), ADR-038 (four products and per-product trials), ADR-039 (white-label domains keep sign-in canonical), ADR-041 (sign-in modes)

## Decision

1. **Five hosts, one application.** `admin.`, `partner.`, `app.`, `auth.` and `api.workeasy360.com` are origins of the same Next.js frontend and the same Django API. The host decides which UI is shown (the frontend proxy's routing table, `frontend/src/lib/portals.ts`). It never decides what anyone may do: StaffGrant, PartnerMember and Membership are read live on every request.
2. **Privileged API surfaces are narrowed by host.** `PortalSurfaceMiddleware` returns 404 for `/api/v1/admin/*` off `admin.`, `/api/v1/partner/*` off `partner.`, `/api/v1/orgs/*` off `app.`, and the sign-in, hand-off issue and OIDC authorize endpoints off `auth.`. It only refuses and never admits. The reason is that an XSS on the customer portal must not be able to drive a staff member's customer-portal session against the admin API.
3. **Host-only sessions, joined by a one-time hand-off.** No cookie is scoped to `.workeasy360.com`, because four separately deployed product applications live on sibling subdomains. The central sign-in issues a single-use, 60-second code bound to the target portal's host and to that portal's binding cookie (`state`, which defeats login CSRF). The code is delivered in the URL fragment and redeemed by the portal for its own session (key rotated). The session inherits the sign-in's AAL and step-up time verbatim, so it is never more elevated or elevated for longer. Only hashes are stored (`identity_session_handoff`).
4. **`__Host-` cookies in production.** Session, CSRF and binding cookies use the `__Host-` prefix, so the browser enforces Secure, `Path=/` and no Domain attribute.
5. **One sign-in, one sign-out.** Each password sign-in gets a `sign_in_id`, which every session handed off from it inherits. Signing out on any host deletes all of them.
6. **The issuer moves to `auth.`.** The OIDC authorize endpoint needs the central session. Account links (verify, reset, invitations) go to `auth.`; billing and launcher links go to `app.`.
7. **Entitlements end at their end, by server time.** Snapshot entries now carry `ends_at`, the source's `effective_to` (merge: open-ended wins, otherwise the later end). The single decision function `decide()` denies once that moment has passed. Before this, a trial granted access past `ends_at` until the sweeper next ran, contrary to ADR-038's own claim.
8. **Trial terms from the operator file, once.** `register_products` applies each product's `trial` entry only when no configuration exists, and switches it on only when an eligible plan is named. The four products ship 7 days, off, because no plan exists yet. Staff switch it on in the Product Manager once a plan is published.

## Consequences

- Production refuses to start unless the five origins are set, distinct and https; ALLOWED_HOSTS lists them with no wildcard or leading dot; CSRF_TRUSTED_ORIGINS is exactly the four web origins; the issuer is the auth origin; and cookie domains are unset.
- A portal visit with no session costs two redirects (portal → auth → portal). Moving between portals once signed in costs one redirect and no second password.
- Session listings show one row per host for the same sign-in.
- Single-host mode (no portal origins) is unchanged, so development and every earlier suite run as before.
- Rejected: a shared parent-domain cookie (exposed to the product subdomains), wildcard CSRF origins, OIDC between the platform's own portals (heavier, with no gain over a host-bound one-time code), and an injectable clock (the entitlement end is enforced by data the writer already had).

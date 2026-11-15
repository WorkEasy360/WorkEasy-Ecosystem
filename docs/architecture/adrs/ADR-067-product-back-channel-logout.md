# ADR-067: Product back-channel logout, one reusable Django connector, and no second identity provider

- Status: proposed (2026-11-15). On branch `feat/sso-connector-backchannel-logout`; not deployed.
- Context: the brief "WORKEASY360 - COMPLETE UNIFIED SSO & AUTOMATIC PRODUCT ONBOARDING" (15 November 2026), and the owner's follow-up "UNIVERSAL PRODUCT-INDEPENDENT SSO REQUIREMENT".
- Builds on: ADR-018 (OIDC for products), ADR-043 (hosts and sign-ins), ADR-044 (universal sign-in), ADR-066 (switch account).
- Amends: ADR-044's consequence "Signing out of Workeasy360 does not end product sessions (no product implements front- or back-channel logout)", and its rejection of "front- or back-channel logout without a product contract". There is now a product contract, and a connector that implements it.

## Context

The brief asked for a new identity service at `accounts.workeasy360.com`. The platform has been the identity provider since Phase 3; it was live with real users at the time of the brief, and four products key their users on `(issuer, sub)`. A second provider would change the issuer, and every product would lose or mis-match its users. Item 1 below records the decision not to build one.

The brief also asked for things the platform did not have:

1. **Logout from every product.** Only Workeasy360's own sessions ended. A product kept serving until its entitlement re-check (up to 15 minutes), and "sign out everywhere" never reached product sessions.
2. **One reusable connector.** Each product wrote its own client of 1,300 to 1,600 lines.
3. **A starter template** for future products.

## Decision

1. **No second identity provider.** `auth.workeasy360.com` stays the one provider. The issuer, `sub`, organization ids and the product contract are unchanged. A rename or new host would need a migration ADR of its own.
2. **Back-channel logout follows OpenID Connect Back-Channel Logout 1.0, and products opt in.**
   - **Manifest.** Manifest `2026-11-15` adds `backchannel_logout_uri`. It is validated like a redirect URI, and it must be on the same origin as one of the product's registered redirect URIs, so a manifest cannot point the platform's outbound calls at a third party. `local_only` products cannot set it. `MANIFEST_VERSION` stays `2026-10-12`; a console write stamps `2026-11-15` only when the field is used.
   - **ID token.** The ID token carries `sid` (the ADR-043 `sign_in_id`) **only** for an integration that registered the URI. Every other product's token is unchanged. Discovery advertises `backchannel_logout_supported` and `backchannel_logout_session_supported`.
   - **Recording sign-ins.** The token endpoint records a `ProductSignIn` (integration, user, sign-in) for opted-in integrations.
   - **Ending sign-ins.** Identity reports ended sign-ins through `contracts.ports.sign_ins`, the same port pattern as `contracts.ports.events`, so no import edge is added. The ecosystem's listener writes one `BackchannelLogout` row per affected product **in identity's own transaction**, which makes it a transactional outbox. The reporting call sites are:
     - `handoff.end_sign_in`, which covers sign-out and account switching;
     - `account.revoke_session` and `revoke_other_sessions`, which spares the caller's own sign-in;
     - `sign_out_everywhere`, which names only `sub`, so products end every session of the person;
     - `change_password`;
     - `lifecycle.set_temporary_password` and `reset_credentials`.
   - **Delivery.** Delivery lives in `integration/backchannel`, because the ecosystem module makes no outbound HTTP. It POSTs through the SSRF-guarded egress client.
     - The worker is woken by task name on commit. A 15-second beat sweep is the guarantee if the kick is lost.
     - Rows are leased with `SKIP LOCKED`.
     - Outcomes: 2xx is delivered. Any other 4xx, or an egress refusal, is final ("failed"). 408/425/429/5xx and transport failures are retried at 0 s, 15 s, 1 min, 5 min, 15 min, 1 h, 6 h and 24 h, then the row is "dead".
     - The logout token is minted fresh for every attempt and never stored: header `typ: logout+jwt`; claims `typ: logout_token`, `events`, `sub`, optional `sid`, no `nonce`, valid for 120 s.
   - **Visibility.**
     - `GET /api/v1/admin/products/<code>/integration/backchannel` shows staff the last day's deliveries.
     - `POST /api/v1/auth/sessions/sign-out-everywhere` ends every Workeasy360 session of the person. It then waits up to 4 s and reports each product as `delivered`, `pending` or `failed`, with `complete: true` only when every product confirmed. It does not claim a completion it has not seen.
3. **One Django connector, one starter template.** Both live outside this repository (`workeasy360-sso`, `workeasy360-django-product-template`) and import nothing from it.
   - The connector implements completion-design sections 3-5 and the back-channel receiver once. PyJWT handles signing and verification, as in `tests/factories/reference_product_client.py`.
   - Its `w360sso_check` command is a readiness self-test that proves the product's own credential against the live provider.
   - Non-Django products keep their own clients. Any standards-compliant client can implement the receiver from the contract.

## Consequences

- **What changes for products.** A product that registers a back-channel URI is signed out within seconds of a Workeasy360 sign-out, an account switch, a revoked session, a password change or "sign out everywhere". Products that do not register one behave exactly as before.
- **Duplicates.** Delivery is at least once. Every token has its own `jti`, and ending an already-ended session is a no-op, so receivers must tolerate a repeat.
- **What still waits for the re-check window.** A product that is unreachable for longer than about 31 hours (the retry schedule) is marked dead and visible to staff. Its sessions still end at its entitlement re-check or session expiry. The per-person entitlement re-check (ADR-044 item 11) is unchanged and remains the bound for removal, suspension and seats.
- **The four live products are not yet opted in.** Each needs the receiver (or the connector), and then a manifest update in the console.

## Rejected

- **A separate `accounts.workeasy360.com` provider:** it changes the issuer and duplicates live identity (item 1).
- **Front-channel logout (hidden iframes):** browsers block third-party cookies, and it fails silently.
- **Storing logout tokens for retry:** a retry an hour later must not carry an hour-old assertion.
- **Delivering from `platform/ecosystem`:** that module is forbidden outbound HTTP.
- **Always issuing `sid`:** it would change the claim set of products that never asked.
- **A shared parent-domain cookie:** rejected again, for the reasons in ADR-043.

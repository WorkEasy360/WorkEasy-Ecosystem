# Onboarding a product to WorkEasy360 Accounts

Written for: product teams connecting a product, and the Super Admin staff who register it.

Every WorkEasy360 product signs people in through **WorkEasy360 Accounts**, the one identity provider. The product never sees a password. It receives an ID token naming:

- the person (`sub`, their central UUID);
- the organization they chose (`organization_id`);
- the product it is for (`product_code`).

It then keeps **its own** session, authorizes on organization, role and entitlement (never on `sub` alone), and ends that session when Accounts says so (back-channel logout).

## The ten steps

| # | Who | Where | What |
|---|---|---|---|
| 1 | Super Admin | Products → Add product | Create the product (code, name). The catalog row exists; nothing is sold yet. |
| 2 | Super Admin | Product → Integration | Register the integration: launch URL, exact redirect URIs (https, no wildcard), post-logout URIs, back-channel logout URI (same origin as a redirect URI), scopes, sign-in mode. |
| 3 | Super Admin + product owner | Integration → Domains | Prove each hostname with its DNS TXT record (ADR-070). Activation is refused until every hostname is proven. |
| 4 | Product team | the product's machine | Generate a key pair (`python manage.py w360sso_keygen`, or `openssl genpkey`). Add the **public** key as a credential in Super Admin; the private key goes only into the product's secret manager. |
| 5 | Super Admin | Integration → **Integration kit** | Choose the framework and generate the kit: `.env.example`, the adapter's configuration and wiring, and the checks. It holds no secret, and each generation is audited. |
| 6 | Product team | the product's repository | Apply the kit (adapters below), deploy to a test environment, run `VERIFY.md`. |
| 7 | Super Admin | Customers / Products | Grant the product to a test organization (entitlement), and set the plan mapping customers will buy. |
| 8 | Product team | test environment | Sign in once and sign out everywhere. Run the conformance run, `Accounts/e2e/run_global_logout.py`, extended with the product. |
| 9 | Super Admin | Integration | Activate. **SSO readiness** turns **Sign-in verified** and **Logout verified** to OK only from real evidence: a redeemed code, and an acknowledged logout token. |
| 10 | Super Admin | Product → Publish | Publish. The checklist refuses a product that signs people in with WorkEasy360 until a sign-in has completed. |

## Adapters

| Framework | Adapter | What the product still writes | Reference |
|---|---|---|---|
| Django | `workeasy360-sso` connector (private), installed by pinned commit over a read-only deploy key | the organization adapter (WorkEasy360 organization to its tenant) | the template `WorkEasy360/workeasy360-django-product-template`; Easy CRM; EasyBook |
| Spring Boot | Spring Security OAuth 2.0 client: `private_key_jwt` and PKCE | the claim checks, the logout receiver, the shared sign-in record checked per request | Easy Project (`com.projectmodule.security`) |
| NestJS / Express / Next.js | `openid-client` 6 and `jose` | the claim checks, the logout receiver, the per-request session check | Easy Help Desk (`backend/packages/workeasy360`, `session-liveness.service.ts`) |
| anything else | the contract in the kit's `CONTRACT.md` | all of it, with a maintained OIDC library; never hand-written token verification | - |

## The contract, for every adapter

1. **Authorization Code with PKCE S256.** State and nonce are kept server-side and used once.
2. **The code exchange authenticates the client with `private_key_jwt`.** The assertion has `iss = sub = client id`, `aud` = the token endpoint, a 60 s lifetime and a fresh `jti`.
3. **The ID token is verified:**
   - signature by `kid` from `jwks_uri`;
   - `iss` is **exactly** the configured issuer;
   - `aud` is the client id;
   - `exp`, `iat` and `nonce` are checked;
   - `product_code` matches, `organization_id` is present and `email_verified` is true.
4. **People are linked on `(issuer, sub)`.** When the issuer moves, the identity-link move runs in the same release.
5. **Authorization comes from organization, role and entitlement,** using the entitlement endpoint the integration guide describes.
6. **The product keeps its own session** in an httpOnly, Secure, host-only cookie. There is never a `.workeasy360.com` cookie.
7. **Back-channel logout is handled:**
   - verify the logout token: `typ` is `logout+jwt`, the event is present, there is no nonce, and each `jti` is used once;
   - end the named sessions in a **shared** store that is checked on **every** request, so all instances stop at once;
   - answer 200, including for a duplicate, or 400 for an invalid token.
8. **Product sign-out ends the product session only.** WorkEasy360 sign-out goes through `end_session_endpoint`.

## Issuer and rollback

- The kit names the issuer a new product should trust today: WorkEasy360 Accounts once it is an issuer (stage b on), otherwise the current one. A product trusts exactly one issuer.
- Moving an existing product is a release of that product: its issuer setting, plus the identity-link move.
- Rolling back is the same release pointed the other way. The platform stays at stage b, which serves both names (`release-runbook.md`).

## The record

Everything in the onboarding flow is in the audit log and in the product's **Activity**:

- registration;
- manifest changes;
- credentials added and revoked;
- status changes;
- domain proofs;
- kit generations.

The readiness card shows the evidence: when the last sign-in completed, under which issuer, and how many logouts the product has acknowledged.

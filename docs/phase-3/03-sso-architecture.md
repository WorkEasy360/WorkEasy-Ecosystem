# 03 — SSO Architecture

## 1. The shape

```
   Workeasy360 launcher                        CRM (separate application,
   (platform UI, Phase 4)                       separate repository, own DB)
            │                                             │
            │  user picks org A, clicks "Open CRM"         │
            ├─────────────────────────────────────────────▶│
            │     GET crm.example.com/launch?organization=A│
            │                                             │
            │◀────────────────────────────────────────────┤
            │  302 to /oauth/authorize                     │
            │    ?client_id&redirect_uri&scope&state       │
            │    &nonce&code_challenge&organization=A      │
            ▼
   ┌─────────────────────────────────────────────┐
   │ Workeasy360 authorization endpoint          │
   │                                             │
   │  1. client registered?          ─▶ render   │
   │  2. redirect_uri exact match?   ─▶ render   │   ← until both hold, the
   │  ─────────────────────────────────────────  │     server does not know
   │  3. response_type = code        ─▶ redirect │     where the browser may
   │  4. integration active?         ─▶ redirect │     go (RFC 6749 4.1.2.1)
   │  5. PKCE present, S256?         ─▶ redirect │
   │  6. scopes registered?          ─▶ redirect │
   │  7. user authenticated?         ─▶ redirect │
   │  8. organization named?         ─▶ redirect │
   │  9. ACTIVE membership in it?    ─▶ redirect │
   │ 10. organization allows login?  ─▶ redirect │
   │ 11. ENTITLED to the product?    ─▶ redirect │
   │ 12. mint single-use 60s code                │
   └─────────────────────┬───────────────────────┘
                         │ 302 ?code=…&state=…
                         ▼
            ┌─────────────────────────────┐
            │ CRM callback                │
            │  verifies state             │
            └─────────────┬───────────────┘
                          │ POST /oauth/token  (server to server)
                          │   code, redirect_uri, code_verifier,
                          │   client_assertion (signed with CRM's key)
                          ▼
   ┌─────────────────────────────────────────────┐
   │ Workeasy360 token endpoint                  │
   │   authenticate client                       │
   │   code bound to: client, redirect_uri, PKCE │
   │   spend it ONCE (conditional UPDATE)        │
   │   sign id_token + access_token              │
   └─────────────────────┬───────────────────────┘
                         │ id_token, access_token
                         ▼
            ┌──────────────────────────────────────┐
            │ CRM verifies OFFLINE against JWKS:   │
            │   kid → key, alg from the key set    │
            │   iss, aud, exp, nbf, typ, nonce     │
            │ → its own session, its own cookie    │
            │ → stores rows keyed by organization  │
            └──────────────────────────────────────┘
```

## 2. Why the step order is fixed

Steps 1 and 2 come first because **until both hold, the platform does not
know where the browser is allowed to go.** Redirecting to an unverified
location *is* the open redirect. Everything before that boundary is rendered;
everything after it is delivered to the product where its client library can
render the right call to action.

That distinction is structural in the code rather than a comment: it is the
split between `authorization.resolve_client()` (returns a `ClientResolution`;
errors rendered) and `authorization.parse_request()` (takes one; errors
redirected). The same error code — `invalid_request` — is rendered when raised
for a missing `redirect_uri` and redirected when raised for a missing
`code_challenge`, which is exactly what RFC 6749 §4.1.2.1 requires and is why
redirectability is not modelled as a property of the code.

## 3. Organization context is verified, never trusted

The product supplies an `organization` parameter. It has to: it learns the
organization from its launch URL and cannot enumerate a user's memberships.

**The claim is a hint; the membership row is the authority.** Step 9 resolves
the membership through `platform/organizations` — literally the same function
the tenancy middleware resolves through, keyed by id rather than slug — and
refuses unless it is `active`.

So a product naming an arbitrary organization gets nothing. Proven by
`test_a_user_cannot_authorize_into_an_organization_they_do_not_belong_to`.

**The organization is named by its opaque UUID, never its slug.** A slug is a
display handle: human-chosen, visible in URLs, changeable in principle. Using
it as security identity would make the tenant a product can name depend on a
string a customer picked (P-6).

**There is no "pick their only organization" fallback.** An implicit choice is
one the user did not make and cannot see, and it silently changes behaviour
the day they join a second organization. Absent, the endpoint returns
`organization_required`.

## 4. What the code is bound to, and why

An authorization code is a bearer credential that travels in a URL, through a
browser, into history and possibly into a referrer header. Everything bound
into it narrows the circumstances in which a stolen one is worth anything:

| Binding | Closes |
|---|---|
| `integration` | Books redeeming a code minted for CRM, even with a valid Books credential |
| `redirect_uri` | Completing a code obtained via one registered callback against another |
| `code_challenge` | Anyone but the browser that started the flow finishing it |
| `organization_id` | Re-deciding the tenant at exchange time |
| `expires_at` (60s) | A code stolen from a log or a referrer an hour later |
| `redeemed_at` | Using it twice |

Together they reduce a stolen code to "the attacker is the browser that
started the flow, within sixty seconds, and got there first".

The code itself is **never stored** — only its SHA-256 — so a database dump
yields nothing redeemable.

## 5. Trust boundaries

| Boundary | Authenticated by | Notes |
|---|---|---|
| `/.well-known/*` | Nothing | Public, credential-free, cacheable. `Access-Control-Allow-Origin: *` is safe **because** of that, and `test_the_public_documents_carry_no_credential_and_no_personal_data` asserts it against real values |
| `/oauth/authorize` | Platform **session** | The only browser-facing endpoint. GET only |
| `/oauth/token` | Client credential | No session is ever read. Rate-limited |
| `/oauth/userinfo` | Access token | No session is ever read |
| `/integrations/entitlements/verify` | Client credential | No session, no user |
| `/orgs/<slug>/integrations/*` | Session + staff + AAL 2 | Control plane, CSRF-protected, tenant-path scoped |

### CSRF

The global `CsrfViewMiddleware` is **untouched**. Disabling it to make OAuth
work would expose every other endpoint in the platform.

The three credentialed endpoints are `@csrf_exempt`, and the exemption is
safe *because of a second rule enforced alongside it*: they authenticate by
credential and **never** by session cookie. CSRF protects requests carrying
ambient credentials; a request with no ambient credentials has nothing to
forge. `test_credentialed_endpoints_ignore_session_authentication` asserts
that a fully signed-in browser session authenticates nothing there.

The authorization endpoint is GET, so there is nothing for CSRF to protect.
`state` — the client's own CSRF defence — is echoed verbatim and not
interpreted.

## 6. Offline verification, and what it costs

A product verifies the ID token's signature against the cached JWK Set. It
does **not** call Workeasy360 per request.

That is the deliberate anti-cascading-failure property: if the platform is
unreachable, existing product sessions keep working. It is also the source of
the one real trade-off in this design — revocation is **bounded, not
instant**. See [09 §6](09-product-integration-guide.md) for the full
statement and the numbers.

## 7. What is minted

Two tokens, and they are not interchangeable:

- **ID token** — the identity assertion. 300s. What the product verifies and
  turns into its own session.
- **Access token** — the credential for `/oauth/userinfo` and nothing else.
  900s. It exists because RFC 6749 §5.1 requires the token response to carry
  one; rather than emit a decorative value no endpoint accepts, it was given
  the one job that is genuinely its own.

A `typ` claim distinguishes them. Both verify against the same key with the
same audience, so without it an identity assertion would be usable as a
credential — `test_an_id_token_is_not_accepted_as_an_access_token`.

**No refresh tokens.** See [05 §6](05-token-claims.md).

## 8. Signing

Asymmetric, RS256 by default (OpenID Connect Core requires every
implementation to support it, so it is the one algorithm an independently
written product is guaranteed to verify). ES256 is available per key.

The **private half is never in the database** — see
[08](08-key-management.md) and ADR-020.

## 9. Where the browser UI lives

**It does not live in this repository, and this phase did not build it.**

The authorization endpoint needs three screens that are platform UI:

| Screen | Signalled by |
|---|---|
| Login | `login_required` — the standard OIDC signal to send the user to the provider |
| Organization chooser | `organization_required` |
| Consent (if ever required) | Not signalled; consent is not currently collected |

Until those exist, a product integrating against this platform drives the
flow with an already-authenticated session — which is exactly what the
reference client and the acceptance test do, and exactly what the launcher
will do when it is built.

This is a stated gap, not an oversight: the phase brief excluded the app
launcher and every frontend. The endpoints return the protocol-correct signals
so that building the UI later requires no change to them.

# 04 — The OIDC Contract

The machine-readable version of this document is served at
`{issuer}/.well-known/openid-configuration`. **Read that, not this** — a
product should construct no URL by hand, and
`test_every_endpoint_the_discovery_document_names_resolves` guarantees every
endpoint it advertises exists.

This document explains the parts a discovery document cannot: what each
parameter means here, and what each error means for the product.

## 1. Discovery

`GET /.well-known/openid-configuration` — public, no credentials,
`Cache-Control: public, max-age=300`.

```json
{
  "issuer": "https://app.workeasy360.com",
  "authorization_endpoint": ".../api/v1/integrations/oauth/authorize",
  "token_endpoint":         ".../api/v1/integrations/oauth/token",
  "userinfo_endpoint":      ".../api/v1/integrations/oauth/userinfo",
  "jwks_uri":               ".../.well-known/jwks.json",
  "end_session_endpoint":   ".../api/v1/integrations/oauth/logout",
  "scopes_supported": ["openid", "profile", "email"],
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code"],
  "code_challenge_methods_supported": ["S256"],
  "prompt_values_supported": ["none", "login", "consent", "select_account"],
  "id_token_signing_alg_values_supported": ["RS256", "ES256"],
  "token_endpoint_auth_methods_supported": [
    "private_key_jwt", "client_secret_basic", "client_secret_post"
  ],
  "subject_types_supported": ["public"],
  "claims_supported": [...],
  "workeasy360_entitlement_endpoint": ".../api/v1/integrations/entitlements/verify",
  "workeasy360_entitlement_revalidate_after": 900,
  "workeasy360_error_codes": [...]
}
```

The `workeasy360_*` entries are not OIDC. They are advertised because a
product needs the entitlement endpoint and the revalidation cadence to
integrate correctly, and discovering them beats hard-coding them in nine
repositories.

**Notable absences**, each a decision:

| Absent | Why |
|---|---|
| `registration_endpoint` | Dynamic client registration would let anyone create a client |
| `refresh_token` in `grant_types_supported` | There are none ([05 §6](05-token-claims.md)) |
| `request_uri` / `request` parameter support | Advertised as `false`; unused surface is unbuilt surface |

## 2. Key set

`GET /.well-known/jwks.json` — public, `max-age=300`.

Returns every non-retired key: `pending`, `active` and `retiring`. Each entry
carries `kid`, `alg` and `use: "sig"`.

**Select the key by `kid` and take the algorithm from the key set entry, never
from the token header.** Trusting the token's `alg` is the
algorithm-confusion attack.

**Cache it, and refresh on an unknown `kid`** — once, not unconditionally.
That single refresh is what makes platform key rotation invisible to you; an
unconditional refresh turns any garbage token into a request-amplification
lever against the platform.

## 3. Authorization

`GET /api/v1/integrations/oauth/authorize`

| Parameter | Required | Notes |
|---|---|---|
| `response_type` | ✅ | `code` |
| `client_id` | ✅ | |
| `redirect_uri` | ✅ | Must match a registered URI **exactly** |
| `scope` | ✅ | Must include `openid`; every scope must be registered |
| `state` | recommended | Echoed verbatim, including on errors. **Your** CSRF defence — generate it, store it, compare it |
| `nonce` | recommended | Echoed into the ID token. Compare it |
| `code_challenge` | ✅ | BASE64URL(SHA256(verifier)), unpadded |
| `code_challenge_method` | ✅ | `S256`. `plain` is refused, not defaulted |
| `organization` | ✅ | The organization's UUID, from your launch URL. Without it the platform shows its own organization chooser |
| `prompt` | optional | Space-separated, OIDC Core 3.1.2.1 (ADR-044). `login`: the person must authenticate again, and only a session that signed in after your request arrived can complete it; check the ID token's `auth_time` and `sub` yourself. `none`: never show UI - `login_required` or `interaction_required` instead. `select_account` / `consent`: show the access chooser. Unknown values, and `none` combined with another, are `invalid_request` |

**Success:** `302` to your exact registered `redirect_uri` with `code` and
`state`.

**Failure:** either a rendered JSON error (client or redirect URI could not be
verified) or a `302` to your `redirect_uri` with `error`, `error_description`
and `state`.

### 3.1 The person's sign-in preference (ADR-044)

Every person chooses how products they open are signed in: automatically with
their Workeasy360 account, asked each time (the default), or with the
product's own sign-in. It is applied **here**, after every membership,
organization, access-policy, entitlement and seat check has passed - so a
refusal is never hidden behind a question, and no preference ever grants
access.

| Effective preference | What your request gets |
|---|---|
| Automatic | A code at once: the existing Workeasy360 session, no password |
| Ask each time | The platform's access chooser first ("How would you like to access ...?"). Continue: a code. Your own sign-in (only if your manifest offers one): the browser goes to your registered `login_url` and your request is spent. Cancel: `access_denied` |
| Product sign-in | The chooser too, if the browser reached you anyway: the person asked not to be signed in automatically |

A product registered `local_only` gets `unauthorized_client`: its registration
says it does not use Workeasy360 sign-in.

### 3.2 RP-initiated logout

`GET /api/v1/integrations/oauth/logout?client_id=...&post_logout_redirect_uri=...&state=...`
(OpenID Connect RP-Initiated Logout 1.0). End your own session first, then
send the browser here. A GET cannot sign anyone out (logout CSRF): the
platform asks "Sign out of Workeasy360 too?", signs the person out on
confirmation, and returns the browser to `post_logout_redirect_uri`, which must
be registered for your client **exactly** (`post_logout_redirect_uris` in the
manifest), with `state` appended. Anything unregistered is refused, never
followed. Other products' sessions are not ended: there is no front- or
back-channel logout.

## 4. Token

`POST /api/v1/integrations/oauth/token` — `application/x-www-form-urlencoded`,
client-authenticated, `Cache-Control: no-store`.

```
grant_type=authorization_code
code=<the code>
redirect_uri=<the same one, exactly>
code_verifier=<43-128 chars, the one you hashed>
+ client authentication (see 07)
```

```json
{
  "access_token": "eyJ…",
  "token_type": "Bearer",
  "expires_in": 900,
  "id_token": "eyJ…",
  "scope": "openid"
}
```

## 5. UserInfo

`GET /api/v1/integrations/oauth/userinfo` with
`Authorization: Bearer <access_token>`.

Returns `sub`, plus the claims your granted scopes permit. An ID token
presented here is refused (`typ`).

## 6. Entitlement verification

See [06](06-entitlement-api.md).

## 7. Error codes

Standard RFC 6749 / OIDC Core codes, plus six Workeasy360 codes. The extra
codes exist because `access_denied` alone would collapse six genuinely
different remedies — pay, be invited, ask an admin, wait, contact support,
satisfy the organization's sign-in policy — into one message, and a product
cannot render the right call to action from that.

| Code | HTTP | Delivery | What it means for your product |
|---|---|---|---|
| `invalid_request` | 400 | Rendered **or** redirected | Malformed. If rendered, your `client_id` or `redirect_uri` is wrong — check your registration |
| `invalid_client` | 401 | Rendered | Client authentication failed. Deliberately does not say *why* |
| `invalid_grant` | 400 | Response | The code is invalid, expired, already used, not yours, **or the user's membership, organization status or entitlement changed since it was issued**. Deliberately does not say which — start the flow again and the authorization endpoint will tell you |
| `unsupported_grant_type` | 400 | Response | Only `authorization_code` is served |
| `invalid_scope` | 400 | Redirected | You asked for a scope you are not registered for |
| `access_denied` | 302 | Redirected | The person chose Cancel on the access chooser (ADR-044). Nothing failed: offer to try again |
| `unauthorized_client` | 302 | Redirected | Your registration is `local_only`: Workeasy360 does not sign people in to your product |
| `interaction_required` | 302 | Redirected | Only to `prompt=none`: the person's preference requires the chooser, or no organization was named. Start an interactive flow |
| `login_required` | 302 | Redirected | No Workeasy360 session. Send the user to the platform to sign in |
| `organization_required` | 302 | Redirected | No `organization` parameter. You lost the launch context |
| `membership_inactive` | 302 | Redirected | This user has no active membership in that organization. **Also returned when the organization does not exist** — the two are indistinguishable by design |
| `organization_inactive` | 302 | Redirected | The organization cannot be signed in to (locked, deleted) |
| `product_not_entitled` | 302 | Redirected | Show a purchase or upgrade path. This is a sales conversation, not an error |
| `integration_suspended` | 302 / 403 | Redirected / response | Your integration is not active. Contact Workeasy360 |
| `access_policy_denied` | 302 | Redirected | The user is a member, but this organization's sign-in policy refuses this session (added in the Phase 8 extension). `error_description` is one of a closed set: `network_not_allowed` (outside the organization's IP allow-list), `mfa_required` (two-step verification required and not verified in this session), `reauthentication_required` (the session is older than the organization allows, or an administrator signed the member out), `password_change_required`. Send the user back to the Workeasy360 console, which explains the remedy; retrying the flow unchanged will be refused again |
| `invalid_token` | 401 | Response | The access token is invalid or expired |
| `temporarily_unavailable` | 429 / 503 | Response | Rate-limited (honour `Retry-After`) or the platform cannot answer. **Retry — do not treat as a denial** |
| `server_error` | 503 | Response | Something failed on our side. You did nothing wrong |

Every error body carries `correlation_id`. Quote it to support: it finds the
exact request without the response having disclosed anything.

### What errors never say

They never name the value you sent, never say whether an organization or user
exists, and never distinguish "no such client" from "wrong secret". Each of
those is an oracle. The description is a **fixed string per condition**.

## 8. Versioning

| Thing | Versioned by | Changes when |
|---|---|---|
| Endpoints | The `/api/v1/` path | The transport changes incompatibly |
| Manifest | `manifest_version` | The shape you submit changes |
| Token claims | Additive only | A new claim is added; existing ones are never removed or repurposed without a new API version |
| Entitlement reason codes | A closed, documented set | Mapped from an internal enum, so the platform's internals can change without breaking your switch |
| Signing keys | `kid` | Rotation — handled automatically if you follow §2 |

Discovery is unversioned, at the spec-mandated location, and describes
whatever versioned endpoints are current.

# 09 — Product Integration Guide

**Written for: engineers building a Workeasy360 product in a separate
repository.**

You need nothing from the Workeasy360 codebase. No package, no import, no
database credential, no shared table. You need an issuer URL, a client id, a
credential, and a JWT library.

The executable version of this guide is
`backend/tests/factories/reference_product_client.py` — a working client in
~300 lines that imports nothing from `workeasy360`, enforced by a test. If
this document and that file ever disagree, the file is right.

## 1. What you will be given

| | |
|---|---|
| Issuer | `https://app.workeasy360.com` |
| Client id | Random, e.g. `kQ7xW2p…`. Public |
| Credential | A `credential_id` (your `kid`) for the public key you supply, or a client secret shown once |
| Product code | Your ecosystem identity, e.g. `crm` |

## 2. Register once

Submit a manifest ([02](02-product-manifest.md)). The important fields:

```json
{
  "manifest_version": "2026-10-12",
  "product_code": "crm",
  "launch_url": "https://crm.example.com/launch",
  "redirect_uris": ["https://crm.example.com/auth/callback"],
  "scopes": ["openid"],
  "token_endpoint_auth_method": "private_key_jwt",
  "sign_in_mode": "sso_optional",
  "login_url": "https://crm.example.com/login",
  "post_logout_redirect_uris": ["https://crm.example.com/login"]
}
```

**Say how people sign in to you** (manifest `2026-10-12`, ADR-044).
`sign_in_mode` is `sso_only` (the default: Workeasy360 is the only way in),
`sso_optional` (you also have your own sign-in page - then `login_url` is
required and must be that page), or `local_only` (you do not use Workeasy360
sign-in at all; the launcher opens `login_url` or `launch_url` and the
authorization endpoint answers `unauthorized_client`). People's sign-in
preferences can only send them to a page you declared here.

**Register every callback you use.** Redirect URIs are compared by exact
string equality — not prefix, not normalised, not host-only. A URI that
differs by a trailing slash, a port, or a query string will not match.

**Ask only for the scopes you need.** `openid` gives you the user's stable id,
their organization and your product code, which is enough for most products.
`profile` and `email` are personal data and must be registered. `organization`
releases `organization_name`, a display label for a tenant you provision on a
first sign-in. Never key on it.

**The operator's step.** A platform operator finishes a registration with one
command. It replaces the redirect URIs and scopes, registers your **public**
key as a `private_key_jwt` credential, activates the integration and prints
your issuer, client id and key id:

```
manage.py connect_product_sso --actor <staff email> --product <code> \
    --redirect-uri https://<your host>/api/auth/callback/workeasy360 \
    --public-key-file <your public key PEM>
```

Running it again with a new key adds a credential and keeps the old one, so you
can rotate. Revoke the old credential once you no longer use it.

The same can be done in the Super Admin console (Products -> Authentication,
ADR-044): register the integration, set the sign-in mode and URIs, add your
public key (or have a client secret generated - shown once, never again), and
activate. A product added this way needs no code change in Workeasy360.

## 3. Discover everything else

```
GET {issuer}/.well-known/openid-configuration
```

**Hard-code the issuer and nothing else.** Every endpoint comes from this
document. Verify that the `issuer` field matches what you configured — a
document naming a different issuer is describing somebody else's
authorization server.

Cache it. Cache the JWK Set it points at.

## 4. The flow

```
   user arrives at your launch_url?organization=<uuid>
                 │
                 │  keep that organization id
                 ▼
   generate: state, nonce, code_verifier (43-128 chars)
   compute:  code_challenge = BASE64URL(SHA256(code_verifier)), unpadded
   store all three in the user's session
                 │
                 ▼
   302 → {authorization_endpoint}
         ?response_type=code
         &client_id=…&redirect_uri=…&scope=openid
         &state=…&nonce=…
         &code_challenge=…&code_challenge_method=S256
         &organization=<the uuid from your launch URL>
                 │
                 ▼
   302 back to your redirect_uri?code=…&state=…
                 │
                 │  ✋ COMPARE state. Constant-time. If it does not match,
                 │     stop — this callback is not yours.
                 ▼
   POST {token_endpoint}   (server to server)
        grant_type=authorization_code
        code=…&redirect_uri=<the same one>&code_verifier=<the original>
        + client authentication
                 │
                 ▼
   { id_token, access_token, token_type, expires_in, scope }
                 │
                 ▼
   verify the id_token OFFLINE   ← section 5
                 ▼
   create YOUR session. You are done with the platform for now.
```

Exchange the code **within 60 seconds** and **exactly once**. A second
exchange fails, and is logged as a replay.

## 5. Verify the ID token — all seven steps

Every step closes a named attack. Skipping any one of them is a finding at
certification ([10](10-product-certification-checklist.md)).

```python
header = jwt.get_unverified_header(id_token)     # untrusted; kid only

# 1. Select the key by kid from your cached JWKS.
#    Unknown kid → refresh the JWKS ONCE, then fail. Not unconditionally:
#    that turns any garbage token into request amplification against us.
key = find_key(header["kid"]) or refresh_and_find(header["kid"])

claims = jwt.decode(
    id_token,
    jwt.PyJWK(key).key,
    algorithms=[key["alg"]],   # 2. From the KEY SET, never the token header
    audience=MY_CLIENT_ID,     # 3. Audience-confusion control
    issuer=ISSUER,             # 4. Token-substitution control
    leeway=30,                 # 5. exp/nbf, ±30s
    options={"require": ["iss", "sub", "aud", "exp", "iat", "jti", "typ"]},
)

# 6. It must be an identity assertion, not an access token.
assert claims["typ"] == "id_token"

# 7. It must answer YOUR request.
assert claims["nonce"] == session["nonce"]
```

| Step | Closes |
|---|---|
| 1 | A token pointing at a key you fetch from wherever it says |
| 2 | **Algorithm confusion** — an attacker presenting our public key as an HMAC secret |
| 3 | **Audience confusion** — a token minted for another product presented to you |
| 4 | Token substitution from another issuer |
| 5 | Expired-token replay |
| 6 | An access token used as an identity assertion |
| 7 | A token captured from another session |

You now have:

```python
claims["sub"]              # stable, opaque, permanent. Use as a primary key.
claims["organization_id"]  # the ecosystem tenant identity. Key EVERY row on it.
claims["product_code"]     # should equal your own
claims["aal"]              # 1 or 2. Gate your own sensitive actions on it.
```

## 6. Sessions, revocation, and the trade-off you are accepting

**Exchange the ID token once for your own session.** Do not present it per
request, and do not call Workeasy360 per request — that would make your
availability depend on ours and is exactly the cascading failure this design
avoids.

The consequence, stated plainly:

> Because you verify offline, **we cannot reach in and invalidate a token you
> already hold.** A user disabled ten seconds ago still holds a valid ID token
> for up to 300 seconds.

That is bounded by two things you control:

| Control | Window |
|---|---|
| ID token lifetime | 300 s (+30 s skew) — the maximum staleness of *identity* |
| Your entitlement revalidation | `revalidate_after`, default 900 s — the maximum staleness of *access* |

The 300 seconds starts when the token is **issued**, not when the user began
signing in, and the code you are about to exchange does not extend it: the
token endpoint re-checks the membership, the organization status and the
entitlement before it will spend a code. A code that was valid when the
browser was redirected can therefore come back `invalid_grant` a few seconds
later. That is not an error on your side — start the flow again and the
authorization endpoint will tell you what changed.

**Your obligations:**

- Keep your own session no longer than your product's risk warrants.
- Re-check entitlement on the advertised cadence ([06](06-entitlement-api.md)).
- Re-check entitlement before a privileged or irreversible operation, with
  `write=true`.
- Consume `organization.suspended` and `organization.purged` platform events
  when event delivery exists, to stop serving and honour deletion.

The emergency lever is ours: retiring a signing key invalidates every
unexpired token at once. If you follow §5 you will handle it automatically —
the `kid` stops resolving and you reject the token.

## 6a. What the person's sign-in preference means for you (ADR-044)

- **Start sign-in from `?organization=<uuid>`** whenever the launcher opens
  you with it, even if you already have a session: the person picked that
  organization. The authorization endpoint then applies their preference -
  silently, or after its access chooser. You never see the preference and
  must not guess it.
- **Your own login page must not auto-start Workeasy360 sign-in.** A person
  who prefers your sign-in is sent there with no organization hint; offer
  "Already a Workeasy360 user?" as a choice, not a redirect.
- **"Confirm it's you"** for a sensitive action: start a flow with
  `prompt=login` and the organization the person is in, then require the ID
  token's `auth_time` to be no older than your request (allow 30 s of skew)
  **and** its `sub` to be the account signed in to you. A different account
  confirms nothing about the person at the keyboard; refuse it and keep the
  existing session as it was. Do not switch accounts mid-confirmation.
- **Sign out of you and Workeasy360**: end your own session, then send the
  browser to the discovered `end_session_endpoint` with your `client_id` and a
  registered `post_logout_redirect_uri` ([04 §3.2](04-oidc-contract.md)).
- **Error codes** `access_denied` (the person cancelled), `unauthorized_client`
  and `interaction_required` are new ([04 §7](04-oidc-contract.md)); map each to
  a message.

## 7. Check entitlement

```
POST {workeasy360_entitlement_endpoint}
Content-Type: application/x-www-form-urlencoded

organization_id=<from the token>&write=false&subject=<the token's sub>
+ client authentication
```

**Send `subject` for a signed-in person** (optional field, 2026-10-12): the
answer then also covers that person - `deny` with `membership_inactive` (account
disabled, membership removed or suspended) or `no_seat` - so removing someone in
Workeasy360 reaches their open session in your product within one window, not
only at their next sign-in. Cache the answer per (organization, subject). Leave
`subject` out for background work, which has no person, and ask per
organization ([06 §7](06-entitlement-api.md), and
`docs/universal-sso/completion-design.md` §4-5 for the background-job rule).

```json
{"decision": "allow", "reason": "entitled",
 "product_code": "crm", "write_allowed": true,
 "revalidate_after": 900}
```

**Reference implementations** (ADR-044): Easy CRM
(`apps/accounts/workeasy360/entitlement.py`) and EasyBook
(`sso/entitlement.py`) keep one cached answer per organization for
`revalidate_after`, re-ask a denial after 60 s so a renewal takes effect
quickly, let one request ask while the others use the last answer, keep the
last answer through an outage (a 503 is never a denial; with no answer yet,
access continues) and back off for 60 s after a failure. A refusal of your
own client (400/401/403: a revoked credential, a suspended integration) is
not an outage: they fail closed (`workeasy360_access_unverified`) for the
whole client, not per person - every question for 60 s, and a 429 within 5
minutes of the refusal counts as one - and then ask once again. A known denial never lapses into access - it is kept without
expiry, and each process keeps its own copy, so a cache outage cannot hand
access back. Every
organization-scoped request is refused with `workeasy360_access_ended` once the
answer is `deny`, and every write with `workeasy360_read_only` while
`write_allowed` is false. The person's session itself stays valid, so they
see one "access has ended" page and can sign out.

Note there is no `product_code` to send — we determine it from your
credential, which is why nobody can ask about your product but you.

**Handle `503 temporarily_unavailable` by retrying and honouring your last
known answer.** It means we could not determine an answer, not that the
customer has no licence. A product that treats 503 as `deny` turns our
incident into its customers' lockout.

Handle `deny` by reason:

| `reason` | Show the user |
|---|---|
| `not_entitled` | A purchase path |
| `expired` | A resubscribe path |
| `suspended` | "Contact your administrator" |
| `read_only` | Your read-only mode — they can still see and export |
| `membership_inactive` | "Your access has ended - ask an administrator" (only with `subject`) |
| `no_seat` | "Ask an administrator for a seat" (only with `subject`) |

Never branch on anything else. You will not be told what plan they are on,
and you must not need to know ([ADR-005](../architecture/adrs/ADR-005-product-entitlement-model.md)).

## 8. Your own data

You own your store, your schema and your migrations. We never see them.

**Put `organization_id` on every row and scope every read by it.** You do not
get a foreign key to our `Organization` — you cannot, across a database
boundary — so referential integrity is yours:

- treat an unknown `organization_id` as **not found**, never as "allow";
- never let a request for organization A read a row belonging to B, even for
  the same user;
- do not cache an entitlement answer indefinitely.

Our five-layer isolation model protects **our** data. You must implement
equivalent isolation in yours. That is the subject of
[10](10-product-certification-checklist.md), and it is a certification
requirement rather than an assumption — a product that leaks tenant data
damages the ecosystem's trust, not only its own.

## 9. Things that will not work, and why

| | |
|---|---|
| Importing anything from `workeasy360` | It is not published, and it would weld your release to ours |
| Connecting to our PostgreSQL | Bypasses RLS, tenancy and every guarantee above it |
| A redirect URI that differs by one character | Exact matching |
| `code_challenge_method=plain` | Refused, not defaulted |
| Requesting a scope you did not register | `invalid_scope` — not a silent narrowing |
| Exchanging a code twice | `invalid_grant`, and logged as a replay |
| Asking about another product's entitlement | There is no field for it |
| A refresh token | There are none. Re-run the flow or use your own session |
| Choosing an organization the user is not in | `membership_inactive` |

## 10. Local development

`http://localhost:*` and `http://127.0.0.1:*` redirect URIs are accepted
**outside production**. Production refuses them, so your development manifest
and your production manifest will differ in exactly that respect — which is
the intended friction.

# 02 — The Product Manifest

The manifest is the boundary contract an external product submits. It lives
in `contracts/values/manifest.py`: pure Python, no Django, usable by a
documentation generator or an external conformance harness without booting
the platform — asserted by
`test_the_manifest_contract_lives_in_contracts_and_stays_pure`.

## 1. The document

```json
{
  "manifest_version": "2026-09-01",
  "product_code": "crm",
  "display_name": "CRM",
  "launch_url": "https://crm.workeasy360.com/launch",
  "redirect_uris": ["https://crm.workeasy360.com/auth/callback"],
  "post_logout_redirect_uris": [],
  "scopes": ["openid"],
  "capabilities": ["offline_token_verification", "entitlement_revalidation"],
  "token_endpoint_auth_method": "private_key_jwt",
  "health_url": "https://crm.workeasy360.com/healthz",
  "contact_email": "crm-team@example.com"
}
```

| Field | Required | Meaning |
|---|---|---|
| `manifest_version` | ✅ | The manifest's own version, separate from the API version in the URL. The path changes when the *transport* changes; this changes when the *shape a product submits* changes. |
| `product_code` | ✅ | Must be a registered SKU in the catalog. |
| `display_name` | | The product's own preferred name. Not a copy of the catalog's — a product may present itself differently from its invoice line. |
| `launch_url` | ✅ | Where the launcher sends the user. Stored; never fetched. |
| `redirect_uris` | ✅ | Compared by **exact string equality**, always. Max 10. |
| `post_logout_redirect_uris` | | Registered now, unused until RP-initiated logout exists. See §5. |
| `scopes` | | Default `["openid"]`. `profile`, `email` and `organization` must be registered to be grantable. |
| `capabilities` | | Statements about the product. None grants anything. |
| `token_endpoint_auth_method` | | `private_key_jwt` (default) or `client_secret_basic`. |
| `health_url` | | Stored for future ecosystem display. Never fetched. |
| `contact_email` | | Who to tell when their integration breaks. |

## 2. Validation is strict, and rejects rather than repairs

A manifest describes **where a browser will be redirected carrying an
authorization code**. Every lenient parse is a potential open redirect.

### Unknown fields are rejected, not ignored

A product sending `redirect_uri` when the field is `redirect_uris` must find
out at registration time. Ignored, it would leave the integration with no
callbacks at all, and the product would discover it when its first user could
not log in.

### URLs pass an allowlist, not a denylist

Only `https`, plus `http` on a loopback host outside production. An allowlist
because a denylist of `javascript:`, `data:` and `file:` is a list somebody
can eventually find an entry missing from.

| Refused | Because |
|---|---|
| `https://crm.example.com/auth/*` | A wildcard could never match exact comparison, so accepting it registers a callback that silently never works — or, under a future looser comparison, one that matches anything |
| `javascript:alert(1)` | Executes in the platform's own origin |
| `data:text/html;base64,…` | Renders attacker HTML with the code in the URL |
| `file:///etc/passwd` | A non-http scheme has no business being a browser redirect |
| `//evil.example.com/cb` | Protocol-relative: no scheme, no host of its own |
| `https://good.example@evil.example/cb` | Reads as `good.example` to a human, resolves to `evil.example` |
| `https://crm.example.com/cb?next=x` | A pre-existing query makes appending `code` and `state` ambiguous |
| `https://crm.example.com/cb#frag` | RFC 6749 §3.1.2 forbids a fragment |
| `http://crm.example.com/cb` | Plaintext delivery of an authorization code is a disclosed credential |

### The loopback exemption is for loopback

`http://127.0.0.1:9000/cb` is permitted outside production, because OAuth 2.1
permits it for native and development clients.
`http://crm.example.com/cb` is **not**, even with the flag on — otherwise a
development setting would permit a plaintext callback to any host on the
internet, which is the exemption becoming the hole.

Production sets `OIDC_ALLOW_INSECURE_REDIRECT_URIS = False` and
`test_production_refuses_insecure_configuration` refuses to boot otherwise.

### Everything is bounded

10 redirect URIs, 512-character URLs, 128-character display name. Registration
is authenticated, but an authenticated caller submitting a million redirect
URIs is still an outage.

## 3. Exact matching, and only exact matching

`ProductManifest.permits_redirect` is `candidate in self.redirect_uris`.
Nothing else. Not a prefix test, not a normalising comparison, not a host
test.

Every weakening of this predicate that has ever shipped anywhere has become an
account-takeover advisory. `test_exact_matching_is_the_only_comparison` pins
seven of them:

```
https://crm.example.com/cb/../../evil     normalising comparison
https://crm.example.com/cb/extra          prefix matching
https://crm.example.com/cb?x=1            query-insensitive comparison
https://crm.example.com.evil.net/cb       prefix matching on the host
https://crm.example.com:443/cb            port normalisation
HTTPS://CRM.EXAMPLE.COM/cb                case normalisation
https://crm.example.com/cb#               fragment stripping
```

The cost is that a product registers every callback it uses. It should be
doing that anyway.

## 4. Scopes are the PII gate

`openid` is mandatory and yields the subject, organization and product
binding — everything the SSO contract promises.

`profile` and `email` add personal data and are therefore separate, denied by
default, and granted **per integration** rather than per request. A product
that only needs to know *which* user is not handed *who* the user is.

An unregistered scope is an **error**, not a silent drop. Narrowing quietly
means a product believes it asked for `email`, receives a token without one,
and debugs its own parsing for an afternoon.

Scope order is normalised to the declared order, so two manifests requesting
the same scopes in different orders compare equal.

## 5. Two fields that are registered but not yet used

Stated plainly rather than left for a reader to discover:

- **`post_logout_redirect_uris`** — RP-initiated logout (OIDC Session
  Management) is not implemented. The field is validated to the same standard
  as `redirect_uris` so that when logout is built, existing registrations do
  not all need updating, and so a product cannot register an unsafe value
  today that becomes reachable later.
- **`health_url`** — stored for ecosystem display. Nothing fetches it
  ([11 T-11](11-security-threat-model.md)).

Both are single validated fields on a document the product submits anyway.
They were kept rather than dropped because removing a field from a versioned
external contract and adding it back is a version bump for every product;
adding an unused validated field is not.

## 6. Versioning

`SUPPORTED_MANIFEST_VERSIONS` is a frozenset, currently holding one value. A
manifest declaring an unrecognised version is **refused**, not best-effort
parsed — guessing at a future shape is how a security-relevant field gets
silently dropped.

The set exists rather than a single constant because accepting two during a
migration window must not require changing the type of the constant.

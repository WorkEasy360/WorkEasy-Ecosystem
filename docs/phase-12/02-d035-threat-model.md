# D-035 — threat model for partner custom domains (Phase 12, Step 6)

Written **before** any change. The decision it led to is [ADR-039](../architecture/adrs/ADR-039-white-label-domains-keep-authentication-canonical.md) (decision B: authentication stays canonical).

**Assets:** passwords, session cookies, CSRF tokens, OIDC authorization codes, and the partner's brand (reputation).
**Adversaries:**
- a malicious partner;
- an attacker who gains control of a partner's DNS or of a lapsed partner domain;
- a network attacker;
- an attacker who spoofs headers.

Each row gives the concern, what goes wrong under **option A** (authentication on custom domains), and how **option B** answers it.

| Concern | Under A (authenticate on custom domain) | Under B (as built) |
|---|---|---|
| **TLS certificate issuance** | A certificate is needed per partner host, and the private key sits on our edge for a name we do not control. | A certificate is still needed for the branded landing page. On-demand issuance goes only to names `/public/edge/tls-allowed` approves: canonical, or a domain that is active, verified, belongs to an active partner, and holds both capabilities. An arbitrary name pointed at the edge gets no certificate. |
| **Custom host verification** | Verification becomes part of the authentication perimeter. | Unchanged: DNS TXT proof, then separate activation, re-checked every 6 h (`recheck_domains`), with repeated misses disabling the domain. A verified domain only presents a brand. |
| **Host validation** | `ALLOWED_HOSTS` would need to include partner hosts dynamically. | `ALLOWED_HOSTS` is a static list with no partner host. Django answers 400 to a partner `Host` (pinned by test). On a custom host the edge forwards only the branding lookups, with `Host` rewritten to canonical. |
| **CSRF trusted origins** | Needs a dynamic, database-backed trusted-origin list, or a wildcard (forbidden). | No change: no partner origin is trusted. A login POST with a partner `Origin` gets 403 (pinned by test). |
| **Session cookie scope** | A cookie per partner host; revocation and logout must span hosts. | Cookies exist only on the canonical host (host-only, `Secure`, `HttpOnly`, `SameSite=Lax`). A partner host never receives one. |
| **SameSite / Secure** | Cross-host flows complicate `SameSite`. | Unchanged, canonical only. |
| **OIDC redirect URIs** | The authorize, resume and login redirects would have to follow the originating host. | Unchanged. Product redirect URIs come from the registered manifest. Login and resume redirects use `W360_FRONTEND_URL` (now required and https in production, G-06). |
| **Callback host** | A per-host callback. | None on partner hosts. |
| **Logout** | Must clear every host's cookie. | Canonical only. |
| **Domain revocation** | Must end sessions on that host. | Disabling a domain, revoking either capability or terminating the partner takes the brand off air at once, and stops certificate approval. Nothing to revoke beyond that. |
| **DNS takeover / lapsed domain** | An attacker controlling the domain could collect passwords and cookies on a page that looks like ours. | The attacker points the name at their own server and gets nothing from us: no certificate (we stop approving it once verification lapses), no API and no cookie. What they could have without us they still have: a lookalike page on a domain the partner lost. |
| **Stale domains** | A stale session host. | A stale CNAME to our edge serves the unbranded landing, whose hand-off goes to canonical, and no certificate after control lapses. |
| **Proxy forwarding headers** | `X-Forwarded-Host` must be trusted to know the host. | `USE_X_FORWARDED_HOST` stays off. `Host`, `X-Forwarded-Host`, `Forwarded`, `Origin` and `Referer` never select a brand (Phase 10 test). The frontend reads `Host` only to choose between two public pages. |
| **Caching** | Per-host caching of authenticated pages. | Brand responses are `no-store` with `Vary: Host`. The logo is `private`. Pages render dynamically, since the CSP nonce forces dynamic rendering. |
| **Tenant/partner lookup** | The host would identify a tenant for authentication. | The host is never an identity. The brand lookup returns presentation only: no partner ID, tenant or capability. |

**Result:** B removes every custom-domain authentication risk and keeps branding. The residual is user experience: sign-in happens on the canonical host, which shows the partner's brand. Recorded as the design, not as debt.

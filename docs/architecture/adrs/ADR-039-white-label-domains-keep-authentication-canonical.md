# ADR-039 — White-label custom domains keep authentication on the canonical host (D-035, decision B)

**Status:** Accepted (Phase 12, 2026-09-30)
**Supersedes:** nothing. **Closes:** D-035 (by decision; see "Consequences").
**Related:** ADR-028 (partner capability model), ADR-037 (partner consoles), `docs/phase-12/02-d035-threat-model.md`.

## Context

Since Phase 10 a partner with `branding.manage` + `domain.manage` can prove control of a custom domain (DNS TXT), activate it, and have it serve their brand. Sign-in on that domain failed: Django's CSRF origin check does not trust the partner's hostname, so the login POST was refused ("Something went wrong"). D-035 recorded this as P2 for the white-label channel.

The brief for Phase 12 offered two choices: **A** — securely support authentication on custom domains; **B** — keep authentication canonical and hand the visitor over. It explicitly forbids solving D-035 with `CSRF_TRUSTED_ORIGINS = ["https://*"]` or wildcard hosts, and says security wins over branding.

## Decision

**B. Authentication stays on the canonical Workeasy360 host.** A partner's custom domain is a *branded landing surface*:

1. On a host other than `W360_CANONICAL_HOST` the frontend serves only `/login`, rendered as a branded landing page with no credential field and no form. `/` goes to that page; every other path is redirected (302) to the same path on the canonical host (`frontend/src/proxy.ts::offCanonicalRedirect`).
2. "Continue to sign in" links to `https://<canonical>/login?brand=<this host>`. The URL is built on the server from the configured canonical origin and the request host; nothing in the request URL can change its destination (`features/auth/handoff.ts`).
3. The canonical sign-in page presents that partner's brand. `brand` is only a lookup key, and the server returns a brand only for an active, verified domain of an active partner holding both capabilities. An unknown or revoked host renders unbranded.
4. After signing in, the person is on the canonical host. Their organization's console is branded through its live partner link (`brand_for_organization`, Phase 11). That console is the branded portal they "return" to.
5. The edge issues TLS certificates on demand only for names the platform approves: `GET /api/v1/public/edge/tls-allowed?domain=` answers 200 for the canonical host and for a domain that would serve a brand right now, and 404 otherwise. The edge refuses to proxy that path from outside.
6. The API is never served on a partner hostname. Django still answers 400 for such a `Host`. On a custom host the edge forwards only the two public branding lookups, rewriting `Host` to the canonical name.

## Why not A

Custom-domain authentication would need all of the following, each a new way to fail:

- `CsrfViewMiddleware` trusting a dynamic, database-driven origin list.
- Session cookies issued per partner host.
- OIDC `/authorize` and resume redirects bound to the host the flow started on.
- Logout and session revocation across hosts.
- Per-host rate-limit identity.
- A partner's DNS becoming part of our authentication perimeter: a lapsed or hijacked partner domain could then capture passwords and session cookies.

Branding is achieved without any of that. Decision B leaves no credential, cookie or authorization code on a host a partner can repoint. Choosing A later remains possible; nothing in B precludes it.

## Consequences

- **D-035 is closed as "by design."** The limitation still exists, and it is now the architecture: sign-in on a partner's own domain is **NOT SUPPORTED**. Sign-in from it is supported by hand-off.
- Custom domains need TLS at the edge (Caddy on-demand TLS gated by the endpoint above, `deploy/edge/Caddyfile`), not in Django.
- `CSRF_TRUSTED_ORIGINS`, `ALLOWED_HOSTS`, session cookie scope and OIDC redirect URIs are unchanged and contain no partner host. Tests pin this: `tests/phase12/test_12_d035_canonical_auth.py` and `frontend/src/features/auth/d035.test.tsx`.
- A visitor sees the canonical host name in the address bar while signing in. That is the intended trust signal.

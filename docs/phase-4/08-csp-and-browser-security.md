# 08 — CSP and Browser Security

## The trigger

Phase 3's audit recorded CSP as "trigger NOT REACHED": "no HTML surface
exists - zero templates in the repository". Phase 4 introduces a real,
browser-facing frontend. **Trigger reached.**

## Two layers, deliberately not duplicated (Task 35)

**Django** (`platform.observability.middleware.SecurityHeadersMiddleware`,
last in `MIDDLEWARE` so it stamps every response including one built
entirely by an earlier middleware): a static, maximally strict policy -
`default-src 'none'`, `frame-ancestors 'none'`, `base-uri 'none'`,
`form-action 'none'` - appropriate for a JSON API that renders no HTML of
its own. `Permissions-Policy` disables camera/microphone/geolocation/
payment, none of which this platform ever needs.

**Next.js** (`frontend/src/proxy.ts`): a nonce-based policy -
`script-src 'self' 'nonce-<random>' 'strict-dynamic'`, `style-src` likewise
in production (`'unsafe-inline'` only in development, matching Next.js's own
documented guidance, since React's dev-mode error reconstruction needs
`eval`) - because this app genuinely renders HTML and genuinely needs
`script-src`/`style-src` to allow its own bundles. A fresh nonce is
generated per request; every page in this app is already dynamically
rendered (it fetches session/org state client-side after hydration, so
static generation was not a goal to trade away here - see
[01](01-frontend-architecture.md)).

Each layer is set exactly once, on the response it actually owns; neither
overrides or conflicts with the other because they answer for different
origins (the API origin vs. the frontend origin behind the same reverse
proxy in production).

## Other headers (Task 35), both layers

`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: strict-origin-when-cross-origin` - set by both the Django
middleware and the Next.js proxy, redundantly and harmlessly (a browser
applies the stricter of two identical values without conflict). HSTS is a
production-only, TLS-terminating-proxy concern
(`SECURE_HSTS_SECONDS`/`SECURE_SSL_REDIRECT`, already configured in
`config/settings/production.py` since Phase 1) and is not duplicated here.

## What was not built

No CSP violation-reporting endpoint (`report-uri`/`report-to`). Worth adding
once this ships behind a real domain where reports are actionable; not
useful against a local/staging setup where every dev-mode script triggers
noise.

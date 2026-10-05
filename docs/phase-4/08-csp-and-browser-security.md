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

**Next.js** (`frontend/src/proxy.ts`): a nonce-based policy for scripts -
`script-src 'self' 'nonce-<random>' 'strict-dynamic'`, with `'unsafe-eval'`
in development only, since React's dev-mode error reconstruction needs it.
A fresh nonce is generated per request.

### The two corrections the Phase 4 audit made here

Both were shipped, both left the production application broken, and neither
was visible to any test - the frontend suite runs components in jsdom, where
there is no CSP at all, and CI had no frontend job.

**1. Every route must be dynamically rendered, and four were not.** Next.js
injects the nonce while *server-rendering a request*; a page prerendered at
build time has no request and so no nonce. `'strict-dynamic'` makes a CSP3
browser ignore `'self'` and every host-source in `script-src`, so a script
tag without the nonce does not load - and `next build` was emitting
`/`, `/login`, `/organizations/select` and `/_not-found` as `○ (Static)`.
The production login page therefore shipped twelve script tags, none with a
nonce, and every one of them would have been blocked: an inert shell with a
sign-in form that could not submit. This document previously asserted the
opposite ("every page in this app is already dynamically rendered"), while
[09-performance.md](09-performance.md) recorded "four are static" from the
same build output.

Fixed by `export const dynamic = "force-dynamic"` in `app/layout.tsx`, which
covers the whole route tree. Held by two checks, because the source-level
one is not enough on its own: `src/proxy.test.ts` asserts the line still
exists, and `scripts/assert-dynamic-rendering.mjs` - a CI step after
`npm run build` - reads `.next/prerender-manifest.json` and fails on any
prerendered route, which is what catches a future per-route
`revalidate`/`dynamic` or a Next.js default change.

**2. `style-src` cannot carry a nonce in this app.** CSP3's "does a source
list allow all inline behavior?" algorithm returns *Does Not Allow* for
inline style as soon as the list contains **any** nonce or hash, and a nonce
cannot be attached to a `style` attribute. `style-src 'self' 'nonce-X'`
therefore blocked all ~70 inline `style` attributes this app renders, and
adding `'unsafe-inline'` next to the nonce would not have rescued them -
the nonce's mere presence disqualifies the list. `style-src-attr` states it
precisely but is not implemented in every target browser.

`style-src 'self' 'unsafe-inline'` is the honest policy for an app that
renders inline style attributes, and it is recorded as
[debt D-010](../architecture/debt-register.md) rather than presented as
strict. `script-src` is unaffected and remains nonce-gated. The way to earn
a strict `style-src` back is to move those attributes into the CSS modules
this app already uses.

### Verified, not asserted

Against `next start` on a production build: every `<script>` on `/login`
carries the request's nonce (12 of 12), `Cache-Control` is
`private, no-cache, no-store` rather than the `s-maxage=31536000` a
prerendered page was being served with, and Lighthouse best-practices scores
100.

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

It is worth saying what a report endpoint would have cost, given the two
corrections above: both were policy-versus-page mismatches that a browser
would have reported on the first page load, and neither was findable from
inside a test suite. That is an argument for adding one sooner rather than
later, and for the first staging deployment being opened in a real browser
with the console visible before anything else is signed off.

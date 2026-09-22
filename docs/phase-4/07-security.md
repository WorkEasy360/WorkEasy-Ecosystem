# 07 — Security

## Session (Task 33)

Unchanged cookie posture from Phase 1 (`SESSION_COOKIE_HTTPONLY = True`,
`SameSite=Lax`, `Secure` in production, 8h idle / 30d hard cap). New in
Phase 4:

- **A real login/logout exists** (`platform/identity/services.py`,
  `interfaces/api/v1/auth_views.py`). Failed-attempt lockout reuses fields
  the model already carried (`failed_login_count`, `locked_until`) rather
  than adding new ones - 10 attempts, 15-minute lockout, no password check
  attempted at all against a locked or disabled account (so a lockout never
  doubles as a password-guessing oracle).
- **Session rotation on login.** `django.contrib.auth.login()` cycles the
  session key internally (session-fixation protection) - this was already
  Django's behaviour, now actually exercised for the first time.
- **`SessionEpochMiddleware`**: the first thing that reads
  `User.session_epoch` back. A session established through the login view
  carries the epoch it was issued under; a mismatch (a password reset, a
  forced sign-out, an account disable bumping the epoch) calls
  `django.contrib.auth.logout()`, which flushes the session outright.
  Sessions predating this control (or a test's `force_login`) carry no
  recorded epoch and are treated as current, deliberately - see the
  middleware's own docstring for why treating "no epoch recorded" as
  "revoked" would be a self-inflicted mass sign-out on deploy day.
- **CSRF.** Django's double-submit cookie, genuinely exercised for the first
  time: `session_view` carries `@ensure_csrf_cookie` specifically because a
  pure-JSON API never renders `{% csrf_token %}`, so nothing would otherwise
  ever issue the cookie. Verified with `enforce_csrf_checks=True` (the
  default Django test client does not enforce it, so this property was
  otherwise invisible to every other test in the suite) -
  `test_login_without_a_csrf_token_is_refused`,
  `test_login_with_the_issued_csrf_token_succeeds`.
- **No token in browser storage.** Nothing this phase adds touches
  `localStorage`/`sessionStorage` for anything security-relevant; the
  session lives entirely in the HttpOnly cookie, and the frontend never
  reads or stores one.

## Launch-target integrity (Task 55)

`launch_view` builds the redirect target from `platform.launcher.services
.launch()` - which reads the registered `launch_url` through
`platform/ecosystem`'s `launch_target` selector - and appends only
`?organization=<the caller's own verified organization_id>`. Nothing the
request supplied (`product_code` in the URL selects *which* decision to
re-run, not where a successful one redirects to) reaches the constructed
URL. A denial redirects back to the launcher, not to any product-controlled
location (see [06](06-sso-ui-integration.md) for the identical property on
the OIDC side).

## Rate limiting / trusted-IP review (Task 37, D-009)

**Re-evaluated, not changed.** D-009 (Phase 3: ecosystem rate-limit buckets
keyed on `REMOTE_ADDR`, blind to a reverse proxy) is unaffected by anything
Phase 4 adds: the OIDC endpoints are unchanged, and the new
frontend-facing endpoints (`login`, `resume`, the launcher) are not
rate-limited at all yet - a gap, not a regression, since they did not exist
before. Implementing trusted-IP extraction (Task 37's own instruction:
"before relying on rate limits") requires knowing the actual deployment
topology (which proxy, how many hops, whether `X-Forwarded-For` is
rewritten) - information this phase does not have, since no production
topology has been specified. Blindly trusting `X-Forwarded-For` without that
knowledge would be strictly worse than the current gap, per the same
reasoning D-009 already recorded. **Carried forward as a Phase 5
prerequisite, alongside D-009 itself**, rather than guessed at now.

Login is protected only by the per-account lockout above, which is
IP-independent and therefore unaffected by this gap either way.

## Audit (Task 44)

Seat assignment and release are audited
(`launcher.seat.assigned`/`launcher.seat.released`/`launcher.seat.assign_denied`),
through the existing `platform.audit.services.record` - the same append-only,
hash-chained log every other control-plane write in this codebase uses.
Opening an app is treated as operational logging (the `launcher_launch_*`
metrics, plus the standard `http.request` line every endpoint already
produces) rather than a heavyweight audit entry - a deliberate choice per
the brief's own framing (Task 44: "may be operational/security logging
rather than heavyweight business audit").

## What Phase 4 did not add

No new MFA/step-up UI. Login establishes AAL 1; nothing this phase's UI
does is a `dangerous` permission (`platform.seat.assign` is not), so AAL 2
is never required by anything reachable through the new frontend. Recorded
as a gap, not a silent omission - AAL 2 machinery (`AuthFactor`,
`satisfies_aal2`) already exists from Phase 1 and is simply not exercised by
any Phase 4 UI.

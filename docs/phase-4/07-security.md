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

## Rate limiting and trusted client IP (D-009 — closed by the Phase 4 audit)

Phase 4 shipped this as "re-evaluated, not changed", on the reasoning that
implementing trusted-IP extraction needs a known deployment topology. The
audit reversed that, for a reason the original assessment did not weigh:
Phase 4 is precisely the phase that made `/auth/login` a public,
unauthenticated endpoint, and it shipped with **no rate limit at all**. The
deployment topology is not something the application can discover - it is
something a deployment must *state*, and a setting is how you state it.

### What is implemented

`platform/observability/client_ip.py`. A forwarded chain is trustworthy only
from the right: the right-most entry was written by the hop that spoke to
us, the one before it by the hop before that. With
`W360_TRUSTED_PROXY_COUNT` hops of our own infrastructure, the address that
infrastructure observed is `chain[-count]`, and everything to its left is
attacker-supplied and never read. Reading the *left-most* entry - the common
shortcut - lets a caller name their own rate-limit bucket, which deletes the
limit rather than weakening it.

| Deployment | `W360_TRUSTED_PROXY_COUNT` |
|---|---|
| Directly exposed (local, CI, a bare server) | 0 - the header is not read at all |
| One reverse proxy / ALB / ingress | 1 |
| CloudFront in front of an ALB | 2 |

`required=True` in production: both wrong answers fail silently, and 0 is a
legitimate answer that should be stated rather than defaulted into. A chain
shorter than the configured depth means the request did not traverse the
expected infrastructure, and falls back to `REMOTE_ADDR` - over-strict, and
never forgeable. Unparseable entries are discarded rather than used, because
these values become cache keys.

`SECURE_PROXY_SSL_HEADER` is now derived from the same setting. It was
unconditional, which meant that with nothing in front of the process any
caller could send `X-Forwarded-Proto: https` and have Django believe the
request arrived over TLS - defeating `SECURE_SSL_REDIRECT` and making
`request.is_secure()` answer to the client rather than to the transport.

### What is now throttled

| Bucket | Key | Default |
|---|---|---|
| `login_ip` | caller address | 30 / min |
| `login_account` | SHA-256 of the normalised email | 20 / min |
| `resume` | caller address | 60 / min |

`login_ip` is the one that matters most: it catches one address spraying a
single password across thousands of accounts, which no per-account counter
can see, and it is the CPU guard - Argon2id is tuned to ~250 ms of
memory-hard work and Django hashes a dummy password even for an unknown
email (its own timing-attack defence), so an unthrottled login endpoint is a
workload anyone on the internet can schedule. `login_account` is what is
left when the attacker has a botnet.

The account key is a hash, never the address itself: cache keys reach Redis,
and Redis reaches backups and dashboards.

**What this does not fix, stated rather than implied.** `identity.services`
locks an account for fifteen minutes after ten failures. Ten requests per
fifteen minutes is under any throttle worth having, so an attacker who wants
a chosen account locked can still manage it; the lockout is time-boxed
precisely because it is a denial-of-service lever, and removing that lever
means changing the lockout policy rather than tuning a limiter.
`login_account` is therefore deliberately set **above** `MAX_FAILED_ATTEMPTS`
- below it, the throttle would mask the lockout and a genuinely locked-out
person would be told "too many requests" instead of being told to wait,
which is worse for them and no harder for an attacker. Pinned by
`test_the_account_throttle_does_not_mask_the_lockout`.

The counter itself moved to `platform/observability/throttle.py` so the OIDC
limiter and this one cannot drift on the Redis-outage behaviour
([debt D-012](../architecture/debt-register.md)).

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

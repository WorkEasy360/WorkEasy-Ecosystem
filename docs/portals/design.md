# Multi-portal domains + central auth + four-product 7-day trials: design

Status: design frozen 2026-10-11, before implementation. Builds on ADR-033 (session registry), ADR-038 (four products and per-product trials), ADR-039 (white-label domains keep sign-in canonical) and ADR-041 (sign-in modes). The decision record is ADR-043.

## 1. What already exists (reused, not rebuilt)

- **One Next.js app and one Django API.** The browser calls `/api/*` on its own origin, and Next rewrites that to Django server-side. Session and CSRF cookies are therefore **host-only** cookies on whatever host the browser is on. There is no credentialed CORS anywhere.
- **The four products** (`easy_crm`, `easybook`, `easy_project`, `easy_helpdesk`) are registered from `infra/ecosystem/products.json` by `register_products`. Their hyphenated slugs match the brief's codes. The `ProductCode` regex forbids hyphens, so the brief's `easy-crm` style is the slug and `easy_crm` the code (Phase 11 frozen decision).
- **Per-product trials.** `ProductTrial` and `ProductTrialConfig` feed provisioning as a TRIAL source. The trial entitlement carries `effective_to = ends_at`, and every read checks that window. Access therefore stops at the server's `ends_at`, whether or not the sweeper (`settle_product_trials`) has marked the trial EXPIRED yet. One trial per organization per product, backed by a unique key. Starts are idempotent.
- **Launch.** It is server-owned (manifest `launch_url`), returned as JSON, and navigated with `window.location.assign` (Phase 11 CSP fix).
- **Authorization.** `staff_view` requires `is_staff`, a session that verified a second factor, and a StaffGrant in force. `partner_view` checks an active PartnerMember of that partner. Org endpoints use membership plus RBAC. None of these reads the Host header.

## 2. Hosts

| Host | Serves | API surface on that host |
|---|---|---|
| `admin.workeasy360.com` | `/admin/**` | shared + `/api/v1/admin/**` |
| `partner.workeasy360.com` | `/partner/**` | shared + `/api/v1/partner/**` |
| `app.workeasy360.com` | the customer portal (`/dashboard`, `/o/**`, `/organizations/**`, `/onboarding`, `/billing/**`) and the public site (`/`, `/products`, `/pricing`, `/services`, `/contact`) | shared + `/api/v1/orgs/**` |
| `auth.workeasy360.com` | `/login`, `/signup`, `/verify-email`, `/forgot-password`, `/reset-password`, `/invitations/accept`, `/owner-invitations/accept`, `/organizations/select` (OIDC org choice) | shared + `auth/login`, `auth/handoff` (issue), `integrations/oauth/authorize|resume`, `/.well-known/*` |
| `api.workeasy360.com` | no web UI | product and machine endpoints (`integrations/**`, payment webhooks). Browser portal APIs are refused there (404) |

"Shared" means `auth/session`, `auth/logout`, `auth/mfa/**`, `auth/password`, `auth/sessions`, `auth/activity`, `organizations`, `public/**` and `invitations`. Every portal host also serves `/auth/continue` (the hand-off landing page).

**The host decides which UI is shown, never what anyone may do.** The Next proxy routes by host. A path that belongs to another portal is redirected to that portal's origin, which only changes the URL. On the backend, `PortalSurfaceMiddleware` refuses (404) a privileged API prefix on a host that does not own it, so it can only **deny**. Every grant still comes from StaffGrant, PartnerMember and Membership, re-checked per request. Why narrow at all: if `/api/v1/admin/*` were served on `app.`, an XSS on the most-exposed portal could drive a staff member's app-host session against the admin API. With the narrowing, an admin session exists only on the admin origin.

**Single-host mode.** When no portal origins are configured (development, the test suite, the browser checks of earlier phases), every host serves everything, exactly as today. Production refuses to start without all five origins.

## 3. Central sign-in and the hand-off

Cookies stay host-only, so a session set on `auth.` is invisible to `admin.`. Sharing a `Domain=.workeasy360.com` cookie would also expose it to the four product applications on sibling subdomains, which this platform does not control. Instead:

1. A portal host with no session (or a `/login` visit there) redirects to `https://auth.workeasy360.com/login?mode=<admin|partner|user>&next=<path>&state=<s>`. The proxy sets `__Host-w360_portal_state=<s>` (development: `w360_portal_state`) on the portal host: HttpOnly, SameSite=Lax, 10 minutes. `state` is a random binding value, not a credential.
2. The person signs in on `auth.`. For `mode=admin` with an enrolled factor, the auth page completes the TOTP step-up there, on the auth session.
3. The auth page calls `POST /api/v1/auth/handoff {mode, next, state}`. The server:
   - checks the account may enter that portal: `admin` needs `consoles.staff` (is_staff + a StaffGrant in force); `partner` needs an active PartnerMember; `app` only needs to be signed in, because membership is per organization and checked by every org request;
   - refuses `admin` for an enrolled staff member whose session is not AAL2 (`mfa_required`);
   - validates `next` against that portal's path allowlist, falling back to the portal root;
   - creates a `SessionHandoff` row: SHA-256 of a 256-bit code, user, portal, target origin, next, SHA-256 of `state`, a snapshot of the auth state (AAL, step-up time, MFA-verified flag, sign-in time, password-policy flags, sign-in id), 60-second expiry, single use;
   - returns `redirect_url = <portal origin>/auth/continue#code=<code>`. The code travels in the **fragment**, so it never reaches a server log, a proxy or a `Referer`.
4. `/auth/continue` on the portal reads the fragment, removes it from history, then calls `POST /api/v1/auth/handoff/redeem {code}`. CSRF applies: the page first loads `auth/session` to get the cookie. The server:
   - requires the request Host to equal the code's portal host;
   - requires the state cookie to match the stored hash (constant-time). This blocks login-CSRF, where an attacker hands a victim the attacker's own code;
   - claims the row with one conditional UPDATE (`consumed_at IS NULL AND expires_at > now`);
   - requires the user to be active and unchanged since issue (`session_epoch`), and the originating auth session to still be live (signed out means no redemption);
   - calls `django_login`, which rotates the key (session fixation), copies the snapshot verbatim, clears the state cookie and returns `{next}`.

   AAL2 keeps its original `step_up_at`, so a hand-off never extends elevation (15-minute window, `STEP_UP_MAX_AGE_SECONDS`). Any failure gives 400 `handoff_invalid`, the same for every cause.
5. Already signed in on `auth.`: step 3 runs immediately, so moving between portals costs one redirect, not a second password.

The mode (`admin|partner|user`) is navigation intent only. It picks which portal to try. The server's eligibility check reads identity, not the mode, and the portal re-checks on every request. Choosing "Administrator" without a StaffGrant leads to a refusal page and exposes no admin data.

**Return-URL allowlist.** `next` is only ever a path, validated by a per-portal prefix list: no scheme, no `//`, no backslash, no control characters, no `..` escape, at most 512 characters. The origin always comes from configuration (`W360_*_ORIGIN`), never from the request. `javascript:`, `data:`, external URLs, `localhost` and unknown subdomains are therefore impossible destinations. OIDC redirect URIs stay exact-match per integration (unchanged).

## 4. Sessions, cookies, logout

- Production cookie names get the `__Host-` prefix (`__Host-w360_session`, `__Host-w360_csrf`, `__Host-w360_portal_state`). The browser then enforces Secure, `Path=/` and **no Domain**, so a sibling host such as a product on `crm.workeasy360.com` cannot plant a `.workeasy360.com` cookie of the same name (cookie tossing and session fixation). `SESSION_COOKIE_DOMAIN` and `CSRF_COOKIE_DOMAIN` must be unset, which production asserts.
- Sessions are HttpOnly and SameSite=Lax. The CSRF cookie is readable by JS (double submit). AAL2 is time-boxed at 15 minutes (unchanged).
- **Sign-in id.** Each password sign-in gets a new `sign_in_id`. Sessions created by hand-off inherit it, and it is stored in the new indexed `identity_session.sign_in_id` column.
- **Logout** on any host deletes every session row of that user with the same `sign_in_id`: the auth session and every portal session from that sign-in. The next request on any host is anonymous, so no elevated admin session survives a sign-out from `app.`. The existing "sign out everywhere" (`session_epoch`) and per-session revocation are unchanged.

## 5. CSRF, ALLOWED_HOSTS, CSP, edge

- `CSRF_TRUSTED_ORIGINS` is exactly the four web origins. Production asserts that every entry is one of them, all four are present and none contains `*`. Partner custom domains and product hosts are not trusted: a partner domain serves only the branded landing (ADR-039), and products call the API server-to-server.
- `ALLOWED_HOSTS` is the five hosts. Production asserts there is no `*`, no leading-dot (subdomain-wildcard) entry, and that every configured portal host is present. A spoofed Host is a 400 before any view runs, and no view derives tenant or authority from Host.
- The CSP is unchanged: `default-src`, `script-src`, `frame-ancestors` and `form-action` keep their values. The cross-origin hand-off is a top-level `location.assign`, which `form-action` does not govern, the same mechanism launch already uses.
- Edge (Caddy): one site block per portal host (`/api/*` and, on `auth.`, `/.well-known/*` to Django, the rest to Next). `api.` gets `/api/*` and `/.well-known/*` only. HTTP to HTTPS is a 308. HSTS with includeSubDomains and preload. ACME certificates per host with automatic renewal. Partner custom domains keep the on-demand block. Internal paths return 404 everywhere.
- `W360_OIDC_ISSUER` becomes the auth origin, because the authorize endpoint needs the auth-host session. Emailed account links (verify, reset, invitations, owner activation) and the OIDC login and org-choice redirects use the auth origin. Billing and launcher links use the app origin.

## 6. Products and trials (gaps closed here)

- `products.json` gains `"trial": {"enabled": true, "duration_days": 7}` per product. `register_products` applies it through the console's own `set_trial_config` service, **only when no configuration exists** (staff edits are never overwritten). The duration is the brief's commercial decision. Eligible plans stay empty: no plan or price is invented (Phase 11 E-2), so a trial becomes startable only once staff publish a plan and mark it eligible. The browser check seeds test plans.
- Explore shows all four products: owned gets "Open product", an active trial gets "Trial active, N days remaining" (days computed by the server), an expired trial gets "Trial expired" plus Buy, never used gets "Start 7-Day Free Trial" (duration from the server), and every card has "Learn more".
- My Products lists held products (ACTIVE, TRIAL, SUSPENDED) as before, then the remaining catalog products as EXPIRED or AVAILABLE cards with the matching action.

## 7. Not done, and why

- No `AdminUser`, `PartnerUser` or `CustomerUser` model, no second auth backend, no second trial system, no second entitlement writer.
- No `.workeasy360.com` cookie and no wildcard CSRF origin.
- No real DNS, TLS or deployment: the platform has never been deployed (Phase 12). `scripts/verify_portal_dns_tls.py` checks the five hosts and its live result is reported as-is.
- No product SSO: none of the four products has an OIDC client (Phase 11 and 12 findings). Each is reported as "SSO INTEGRATION REQUIRED".

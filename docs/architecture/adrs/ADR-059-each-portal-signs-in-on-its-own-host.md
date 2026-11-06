# ADR-059: Each portal signs in on its own host

Date: 2026-11-06. Status: accepted (built locally, not deployed). Changes section 3 of ADR-043 (central sign-in and the hand-off).

## Context

Under ADR-043, the Admin, Partner and customer portals had no sign-in form of their own. A signed-out visit to `admin.`, `partner.` or `app.workeasy360.com` was sent to `auth.workeasy360.com/login` with a binding `state` cookie. After the password, a one-time hand-off code went back to the portal's `/auth/continue`. That chain had several ways to fail: the state cookie was spent or expired after 10 minutes, the code lived only 60 seconds, a second tab hit "Sign in again", the auth session ended, or Cloudflare or the browser dropped something. When any step failed, the person was stuck on "That didn't work" or "Sign-in couldn't be completed".

On 2026-11-06 the owner asked for each portal to have its own login and sign-up pages, "so it never redirects to auth".

## Decision

1. **Every web portal serves its own `/login`, `/forgot-password` and `/reset-password`** (`frontend/src/lib/portals.ts`, `ALSO_ON`). The form posts to the same single endpoint, `POST /api/v1/auth/login`. It has the same rate limits, lockout and CSRF as before, and the server sets the session cookie on that host directly. The flow uses no hand-off code, no state cookie and no central host.
   - A signed-out visit goes to that portal's own `/login?next=<page>` (`portalSignInPath`).
   - `?mode=admin|partner|user` on a sign-in address only picks which portal's sign-in page to show. On another portal's host it redirects there. Old `auth.…/login?mode=…` links land on the portal they named.
   - On a portal host, the host decides the mode (`LoginScreen` `modeFor`). After sign-in, the browser goes to `next` when it is a page of that same portal (`safeNext`), and to the portal's root otherwise.
2. **Sign-up is the customer portal's.** `/signup`, `/r`, `/verify-email`, `/invitations` and `/owner-invitations` are owned by `app.`. The Admin and Partner sign-in pages show no "Create an account" link. Administrators are staff, and partner accounts are created by the Super Admin (ADR-047/048). A public form for either would let anyone apply for those roles, so neither was built.
3. **Backend.** `PortalSurfaceMiddleware` now serves `/api/v1/auth/login` on all four web hosts. The API host still refuses it with a 404.
   - Account links name the portal they belong to through `identity.portals.account_url(portal)`. Verification, invitation and owner-activation links go to `app.`.
   - A password-reset link goes to the portal the request was made on (`request_portal(request)`). Staff-sent partner resets and partner set-up links go to `partner.`.
4. **The central host keeps only the product sign-in** (OIDC, ADR-044): `/login?resume=…`, `/organizations/select`, `/product-access` and `/sign-out`. Its other pages redirect to `app.`, so an emailed link from before this change still opens, and the browser carries the `#token` fragment across the redirect.
5. **The hand-off is kept, not used.** `/api/v1/auth/handoff` and `/auth/continue` still work, so a sign-in in flight at deploy time completes. No page starts a hand-off any more. The frontend `PortalHandoff` was removed.

## Consequences

- One password entry signs in to one portal. Someone who uses both `admin.` and `app.` signs in on each, because the cookies stay host-only exactly as in ADR-043 (no `.workeasy360.com` cookie).
- **Products:** opening a product (OIDC) needs a session on `auth.`. Signing in on `app.` no longer creates one, so the first product launch after a sign-in asks for the password once on the central host. Later launches reuse that session.
- Sign-out on a portal ends that portal's sign-in (its own `sign_in_id`). It no longer signs the person out of the other portals at the same time.
- Security is unchanged: the same endpoint and lockout apply. Admin access is still decided per request by StaffGrant, and Partner access by PartnerMember. The per-portal API narrowing and the per-portal Origin check are still in place.

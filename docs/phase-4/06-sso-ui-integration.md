# 06 — SSO UI Integration

## The gap

ADR-018 accepted, as a stated consequence: "an unauthenticated request
returns `login_required` ... until [a login page] does [exist], an
unauthenticated request returns `login_required`" - the product was expected
to catch that OAuth error and re-drive the user to Workeasy360 itself. That
is standards-adjacent but not standard: an OpenID Provider is conventionally
expected to interrupt the flow with its *own* login page and resume
afterward, not delegate that UX to every relying party.

## The fix: park and resume

`platform/ecosystem/models.py`'s new `PendingAuthorization` - a verified
request (client, redirect URI, PKCE challenge, scope, nonce, and the
organization if one was named) parked while the browser completes login or
organization selection, single-use by the identical conditional-UPDATE
mechanism `AuthorizationCode` already uses.

```
Browser → GET /authorize (unauthenticated)
        → 302 {W360_FRONTEND_URL}/login?resume=<token>
Browser → POST /api/v1/auth/login (session established)
        → (full navigation) GET /api/v1/integrations/oauth/resume?resume=<token>
        → 302 back to the product's redirect_uri, with a code
           — or, if the request named no organization —
        → 302 {W360_FRONTEND_URL}/organizations/select?resume=<new token>
        → (user picks an org, full navigation)
        → GET /api/v1/integrations/oauth/resume?resume=<token>&organization_id=<id>
        → 302 back to the product's redirect_uri, with a code
```

Every failure `authorize()` already produced for other reasons
(`membership_inactive`, `product_not_entitled`, `product_no_seat`, ...) is
unchanged - still redirected straight to the product. Only `login_required`
and `organization_required` are now intercepted.

## Task 32's properties, each with a regression test

(`backend/tests/integration/test_authorization_resume.py`)

- **Single-use.** A resume token clicked twice resumes at most once; the
  second attempt is redirected to a *fresh* `/login`, not the original URL
  replayed (`test_a_resume_token_is_single_use`).
- **No product-supplied URL on failure.** An unknown or expired token is
  sent to the platform's own `/login`, never to any product-registered
  redirect URI - there is no verified destination to redirect to yet
  (`test_an_unknown_resume_token_is_sent_to_login_not_to_any_product`,
  `test_an_expired_resume_token_cannot_be_completed`).
- **No cross-organization substitution.** A request the product named an
  organization for cannot have that organization overridden by a
  browser-supplied `organization_id` at resume - the parked row is replayed
  verbatim; a browser value is accepted only when the *original* request
  named none at all (`test_a_product_supplied_organization_cannot_be_substituted_at_resume`).
- **Re-parked, not failed, when still undecided.** If resume is reached with
  no organization decided (the chooser reloaded, a race), a *fresh* token is
  minted and the browser is sent back to the chooser - never failed out to
  the product for something the user is still signed in and able to resolve
  (`test_resuming_with_no_organization_choice_re_parks_at_the_chooser`).

## Why a row, not a signed stateless token

See `PendingAuthorization`'s own docstring and
[ADR-021](../architecture/adrs/ADR-021-launcher-access-decision-and-seat-projection.md)'s
Decision §3-4 for the adjacent reasoning on the launcher side. In short: a
stateless (signed JWT-shaped) token would need no database write and would
work identically across instances - but cannot give revocation on first use,
which is Task 32's single-use requirement. `AuthorizationCode`'s
conditional-UPDATE mechanism already solves exactly this, correctly, under
concurrency; reusing it was cheaper and more auditable than inventing a
stateless variant with an equivalent property.

## What the frontend never does

Never handles a code, a token, or a product's client credential (Task 17).
The only thing that crosses into frontend JavaScript is the opaque resume
token itself, and even that is used exactly once, as a query parameter on a
full top-level navigation the frontend triggers via `window.location.href`
- never a `fetch`, which would silently follow the eventual redirect in the
background instead of moving the browser (Task 54).

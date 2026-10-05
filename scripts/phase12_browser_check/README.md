# Real-browser verification of Phase 12 (staging)

A real Chrome session driven by puppeteer-core against the **production-shaped staging stack**, not a dev server. The stack runs production settings behind the Caddy edge (TLS from Caddy's internal CA) with the TEST payment provider. Mailpit receives email. CoreDNS is authoritative for the test partner domain `wl-brand.com`.

It follows the same rules as `../phase10_browser_check/`:
- nothing is added to the repository (`puppeteer-core` lives in a scratch directory; the browser is the installed Chrome);
- the check is not part of CI.

## What it checks

**On every page it audits:**
- axe (WCAG 2.1 A/AA);
- a visible keyboard focus indicator (40 Tab presses);
- exactly one `h1`.

**Across every page and journey:**
- CSP violations, counted through a page binding so that a violation just before a navigation (the product launch) is still recorded;
- console errors. Expected refusals inside a journey are listed separately:
  - 401 step-up;
  - 402 suspended;
  - 403 for `mfa_required`, CSRF or a terminated partner;
  - 409 denied;
  - a 400 from a refused logo upload, if the file store refuses it.

**The real product sites are never loaded.** Every request to a host other than the four staging names is intercepted and aborted. Its URL is recorded: that is how the launch target is verified.

## Journeys (all through the UI; each `flow` in the report is one step)

| Step | What is driven |
|---|---|
| setup, D-037 | **The Super Admin signs in with a password only, and the control plane stays closed.** `/admin` shows "Two-step verification is required"; the customer list is not shown; `GET /api/v1/admin/customers` returns 403 `mfa_required`. The TOTP code is entered in the gate's own form, and then the list and the API both work. A second session, and a sign-out followed by a new sign-in, each need their own verification, while the first session stays verified. |
| 0 | Public pages signed out, including the home page's "How it works" copy. The Super Admin product console's launch URL and single sign-on status for each product. |
| 8 | **Per product, a new customer each time.** The product in the `/products` store and on its own page. "Start free trial: Team" on the product page. Sign up, verify through the Mailpit link, check the organization exists and the trial shows (badge "Trial"). Follow the card's "Assign a seat: <product>" link to Product access and assign yourself a seat. My products -> Open: the launch is intercepted; it must be the registered launch URL with `organization=<org uuid>`. SSO (what the API and the UI say). Back to the dashboard. Buy in Explore (the card's Buy, or "See plans" -> Product packages) -> checkout -> the TEST provider's hosted page -> Active. Super Admin suspends the organization (Customers console, step-up): the customer sees the read-only banner and Open is refused. Super Admin reactivates: Open works again. The customer cancels in Subscription & billing: "Cancel subscription", then the in-place confirm, then "Yes, cancel at the end of the period" (keyboard only for the first product). The page must then show "Ends on", and the product must still open ("Active", "Ending - won't renew."). |
| 8x | A separate visitor follows the `/products` store card's own "Start free trial" end to end. |
| 9 | "Organization A" holds Easy CRM paid (the checkout done keyboard-only), EasyBook paid, Easy Project on trial, and Easy Help Desk not purchased; the dashboard, My products and Explore are asserted. Then the Project trial is converted to paid and Help Desk is bought: all four are Active. |
| 37 | CSRF from a signed-in page: `POST /api/v1/orgs/<slug>/trials` without `X-CSRFToken` must return 403 JSON `{code: "csrf_failed"}`. With the token, any refusal is a business one. |
| 10 | Add Reseller wizard: ONLY Easy CRM + EasyBook, partner prices, 15% commission, an administrator. The administrator sets a password from the emailed link and enrols TOTP. The reseller provisions a customer; the owner activates from the emailed invitation. The reseller sells CRM and EasyBook. Selling Easy Project must be denied (it is not in the form, and the server refuses it). Commission is checked on the partner ledger and the staff ledger (15% of the partner price). Super Admin terminates the reseller; the customer still signs in and still holds its products. |
| 11 | White-label partner A: Add Reseller wizard, all four products, then Enable white-label. Its administrator runs the portal wizard: brand, PNG logo, colour, preview, save, domain `app.wl-brand.com`, the TXT record published in the CoreDNS zone file, verify, activate. TLS is probed before activation (see Re-runs). Partner B (`other.wl-brand.com`) goes through the **staff** wizard. Both logos must display. Each logo is a solid square in its own partner's colour, and the pixel colour of the displayed image identifies whose logo is shown. Partner A provisions a customer and sells it all four products. The customer's path, **D-035 decision B**: `https://app.wl-brand.com/` shows a branded landing with no form and no password field. "Continue to sign in" leads to the canonical `/login?brand=app.wl-brand.com`, which shows the same brand, and the customer signs in there. The console is branded, and all four products launch. Other paths on the custom host redirect (302) to the canonical host. The second partner's domain shows only its own brand. `shop.wl-brand.com` (never approved) gets no certificate. |
| 12 | Keyboard only: sign in; the in-place "Cancel subscription" confirm (step 8); the navigation drawer (390 px) and the account menu open with Enter and close with Escape, focus returning to their button; the Add Reseller wizard with Tab/Enter; ARIA tabs with the arrow keys, Home and End; the checkout (step 9). An axe/focus/h1 sweep of the organization, Super Admin and partner portal pages not already audited in a journey. |

The report also holds:
- a per-product matrix (Catalog … Browser E2E), with evidence for every cell;
- every launch target;
- every aborted external request;
- the platform or environment bugs found, with evidence (URL, request, status, body, screenshot).

## NOT EXECUTED

- **A manual screen-reader pass.** Axe and the keyboard checks are automated proxies only.
- **Loading a real product site.** Launches are intercepted by design.
- **Product-side single sign-on.** No product has an OIDC client (`integration_required`). No credential is ever registered by this check.
- **The D-037 enrolment path.** A staff member with no factor would see the enrolment form. It is not driven because the only staff account already has a factor.

## Running it

**Prerequisites:**
- The staging stack is up (`deploy/staging`, edge on `127.0.0.1:58443`, Mailpit API on `127.0.0.1:58026`).
- The scratch directory holds:
  - `stg/superadmin_password`;
  - `stg/caddy-intermediate.crt.spki`, `stg/caddy-root.crt.spki` and `stg/caddy-root.crt`;
  - `stg/coredns/wl-brand.com.zone`.

```
cd <scratch>/pptr && npm install puppeteer-core@23      # once; the scratch copy's axe-core/axe.min.js -> <scratch>/pptr/axe.min.js
NODE_PATH=<scratch>/pptr/node_modules SP=<scratch> OUT=<scratch>/stg/browser-report.json \
  node scripts/phase12_browser_check/browser_check.js > <scratch>/stg/browser-run.log 2>&1
```

**Chrome is launched with:**
- `--host-resolver-rules=MAP <host> 127.0.0.1:58443` for `app.staging.workeasy360.com`, `app.wl-brand.com`, `other.wl-brand.com` and `shop.wl-brand.com`;
- `--ignore-certificate-errors-spki-list=<intermediate>,<root>`. This pins the staging CA's keys; it is not a blanket disable.

**The Super Admin's TOTP:**
- It is enrolled on the first run, right after a fresh sign-in.
- From rc3 on (D-037), a code is also needed once per sign-in before any control-plane page loads. A code is refused if reused within its 30-second step, so the harness waits for the next step.
- It is kept in `<scratch>/stg/superadmin_totp` for later runs. Delete that file only together with the factor.

**Optional switches, for work on the harness itself:**
- `ONLY=0,8,9,10,11,12` runs a subset (setup always runs).
- `PRODUCTS_ONLY=CRM` limits step 8 to one product.
- `SKIP_STORECARD=1` skips step 8x.
- `RUN_ID=` fixes the unique suffix used in emails and names.

**Re-runs:**
- Every run creates new rows: customers, partners, domains. Emails and names carry a per-run suffix.
- Step 11 first disables live `app.wl-brand.com` / `other.wl-brand.com` domains left by an earlier run (staff API; recorded as housekeeping), so it can claim those names again.
- It rewrites that name's `_w360-verify` TXT line in the zone file and bumps the SOA serial. The zone reaches CoreDNS through a bind mount, which can take tens of seconds, so verification is retried for up to four minutes.
- **The TLS check before activation only proves refusal the first time a hostname is used.** Once a hostname has been active, the edge keeps its certificate: `tls-allowed` stops issuance and renewal, not serving a cached certificate. On later runs, the pre-activation probe therefore reaches the page. It then asserts that the page is the unbranded landing (no brand, no form, no password field) and records this in `tls_note`. `shop.wl-brand.com` is never activated, so it always proves the refusal.
- **If the file store refuses the logo upload,** the wizard records the refusal as an environment bug, saves the brand without a logo and continues. The logo checks are then reported as BLOCKED. This happened on rc2, whose SeaweedFS had no writable volume; on rc3 the upload works.

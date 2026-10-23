# Real-browser verification: simplified Super Admin partner management (ADR-048)

Same harness and rules as `../partner_experience_browser_check/` (whose plumbing it reuses): the production build, a real backend, a scratch database (`w360_pmgmt_fresh`), no dependency added to the repository (`puppeteer-core` lives in a scratch directory; the browser is the installed Chrome).

**Checks on every audited page** (the Partners dashboard, All Partners, Add Partner, a new partner's page, every step of Complete Your Profile for the three types, the three partner workspaces): CSP violations and console errors; axe WCAG 2.1 A/AA; a visible keyboard focus indicator; exactly one `h1`; reflow (no horizontal scroll) at 1440, 1024, 768, 390 and 320 px.

**Journeys, driven through the UI** (the Super Admin's second factor is answered from the seed's TOTP secret; each partner administrator's password is generated per run and typed by the script):

- **N.** The Partners navigation is exactly Dashboard, All Partners, Add Partner; the dashboard shows its seven counts and Recent partners and no MRR/ARR; eleven old addresses redirect (type lists to All Partners filtered on the type, the cross-partner screens to All Partners, the old add pages to Add Partner with the type).
- **A.** Add Partner asks for no business, product, price or commission field; a **Reseller** is created (name, email, password, confirm, type, country) and lands on All Partners (`?created=<id>`) with "Partner created successfully." and its profile marked incomplete; its page (View Details) shows PROFILE INCOMPLETE; **Complete Your Profile** shows the eight reseller steps; business details and contacts, a product, a commission rate; the customer and sales switches are on; Review → **Activate partner**; the administrator signs in at `/login?mode=partner` with the typed password and lands on `/partner/reseller/dashboard`.
- **B.** The same for **White-label**: brand identity (saved through the brand command), product, commission, the custom-domain step's honest "custom-domain branding / Workeasy360 secure sign-in" statement, communication, customer portal → `/partner/white-label/dashboard`.
- **C.** The same for **Affiliate**: seven steps, "It is granted no product", a commission rate, a **referral code made by staff** with its link → `/partner/affiliate/dashboard`.
- **F.** All Partners: the twelve columns of the agency-style table (type badges and setup state sit in the Name cell); the Partner type filter (Affiliate, Reseller - which includes the white-label partner, whose bundle sells - and White-label), the Profile filter and the search by administrator email are all answered by the server (the request carries the filter).
- **E.** An email that already has an account: the message, **Cancel** clears it, **Link Existing User as Partner Administrator** links it; the password typed for that address does not sign the person in.
- **D.** Isolation: an affiliate cannot read customers; a reseller cannot request a custom domain; partner A cannot read partner B; a partner cannot reach the staff referral-code command.
- **E2.** The typed passwords travelled only to the create request and the sign-in; no answer repeated one; nothing in storage, a cookie or an address; the create answer is `no-store`.

Not part of this check: a manual screen-reader pass (**NOT EXECUTED**). Not part of CI. The journeys **create** rows: re-create and re-seed before every run.

**How to run it** (Windows, from the repository root; in Git Bash set `MSYS_NO_PATHCONV=1` and give paths as `C:/...`):

```
# 1. a scratch copy of frontend/ (never next to a running `next dev`): npm ci, then
#    BACKEND_ORIGIN=http://127.0.0.1:8011 npx next build, then `next start -H 127.0.0.1 -p 3999`
# 2. CHECK_PASSWORD=<a fresh random value> python scripts/partner_management_browser_check/prepare.py
#    (drops, migrates and seeds w360_pmgmt_fresh; prints STAFF_TOTP)
# 3. backend on 127.0.0.1:8011:
#    PYTHONPATH="backend;scripts/extension_browser_check" DJANGO_SETTINGS_MODULE=w360_extension_browser_settings \
#    PYTHONUNBUFFERED=1 W360_DB_NAME=w360_pmgmt_fresh W360_DB_HOST=127.0.0.1 W360_DB_PORT=5442 \
#    W360_DB_DIRECT_HOST=127.0.0.1 W360_DB_DIRECT_PORT=5442 W360_DB_POOL_MODE=direct \
#    BACKEND_ADDR=127.0.0.1:8011 DNS_FILE=<a json file containing {}> python scripts/phase10_browser_check/run_backend.py
# 4. CHECK_PASSWORD=<the same value> STAFF_TOTP=<from the seed> NODE_PATH=<scratch>/pptr/node_modules \
#    AXE_PATH=<axe.min.js> OUT=<report.json> node scripts/partner_management_browser_check/browser_check.js
python scripts/partner_management_browser_check/prepare.py --drop
```

`ONLY=N,A` runs only the journeys whose names start with those codes (F, E, D need A-C first). Do not run it alongside the full backend suite or within a couple of minutes after it (Windows ephemeral ports; see the partner-experience README).

# Real-browser verification: admin-created partners and the type-specific workspaces (ADR-047)

Same harness and rules as `../partner_panel_browser_check/` and `../partner_setup_browser_check/`:
- it runs against the production build and a real backend;
- it uses a scratch database (`w360_pexp_fresh`);
- it adds no dependency to the repository (`puppeteer-core` lives in a scratch directory; the browser is the installed Chrome).

**Checks on every audited page** (the three dashboards and their pages, the refusals, the admin list and detail, every Add Partner step): CSP violations and console errors; axe WCAG 2.1 A/AA; a visible keyboard focus indicator; exactly one `h1`. **Reflow** (no horizontal scroll) at 1440, 1024, 768, 390 and 320 px for the workspace pages, the admin pages and every Add Partner step.

**Journeys, driven through the UI** (the Super Admin's second factor is answered from the seed's TOTP secret; every partner administrator's password is generated per run and typed by the script, never fixed in the repository):

- **A.** Super Admin → Partners → **+ Add Partner**: seven steps for a **Reseller**; the review shows "Configured", never the password; create; sign out; the same email and password sign in at the partner portal and land on `/partner/reseller/dashboard` with exactly the 13 approved Reseller entries, no workspace switcher, and the server-resolved landing. **A2.** A second sign-in; the nine pages open; `customers` → `users`, the short address `/reseller/dashboard`, and an affiliate-only page is "not part of the Reseller workspace".
- **B.** The same for a **White-label** partner: the White-label dashboard with brand-setup and custom-domain status, the 15 entries (brand, domain, website branding, pricing, orders and billing; no sub-resellers), its pages.
- **C.** The same for an **Affiliate** (six steps, no pricing): the Affiliate dashboard with "no referral link yet", the 12 entries, a tracking link created for a campaign, **Copy Link**, the honest "no sign-up step" and "no marketing library" statements, every affiliate page.
- **D.** Isolation: the affiliate's API calls to customers, catalog, plans, price book, orders, sub-partners, branding, domains, every customer command and every staff pricing / commission / capability / experience command are refused (never 2xx); another partner's data is 404; the Reseller and White-label workspaces and commercial pages typed by hand are refused; the reseller cannot open the affiliate workspace.
- **E.** The password: too short, mismatched and common are refused (the last by the platform's own policy); Show / Hide; the inputs are `type=password`, `autocomplete=new-password`; an email that already has an account says so, **Cancel** clears it, **Link existing user as Partner Admin** links it without a password; the linked person signs in with the password they already had (the one typed for the taken address does not work); nothing in cookies, local/session storage or any address. **E2.** The typed passwords reached only the credentials check, the create request and the sign-in; no answer repeated one; both admin endpoints are `no-store`.
- **F.** The Partners list: the columns Partner, Admin email, Primary experience, Capabilities, Products, Customers, Status, Created, Last login, Actions; each partner's administrator, experience and last sign-in; the row actions.
- **G.** Suspend (reason recorded) → the partner sees the notice, keeps its history and loses every provision / sell / manage permission; reactivate → they return.
- **H.** Add the Reseller experience to the affiliate later: a workspace switcher appears, the affiliate menu and its referral history are unchanged, the new primary experience is where the chooser lands.
- **I.** Reset password on the administrator: a reset link is sent, nothing is revealed, and the typed password keeps working until the person uses the link.
- **R.** Reflow sweep. **S.** Axe, CSP, focus and reflow for each Add Partner step.

`ONLY=A,B,C` runs only the journeys whose names start with those codes; later ones need the partners an earlier one created, so run A-C first. The journeys **create** rows, so re-create and re-seed before every run.

Not part of this check: a manual screen-reader pass (reported as **NOT EXECUTED**). Not part of CI.

**How to run it** (Windows; run from the repository root; in Git Bash set `MSYS_NO_PATHCONV=1`, and give paths as `C:/...`):

```
# 1. a scratch copy of frontend/ (never next to a running `next dev`): npm ci, then
#    BACKEND_ORIGIN=http://127.0.0.1:8011 npx next build, then `next start -H 127.0.0.1 -p 3999`
# 2. CHECK_PASSWORD=<a fresh random value for this run> python scripts/partner_experience_browser_check/prepare.py
#    (drops, migrates and seeds w360_pexp_fresh; prints STAFF_TOTP)
# 3. backend on 127.0.0.1:8011:
#    PYTHONPATH="backend;scripts/extension_browser_check" DJANGO_SETTINGS_MODULE=w360_extension_browser_settings \
#    PYTHONUNBUFFERED=1 W360_DB_NAME=w360_pexp_fresh W360_DB_HOST=127.0.0.1 W360_DB_PORT=5442 \
#    W360_DB_DIRECT_HOST=127.0.0.1 W360_DB_DIRECT_PORT=5442 W360_DB_POOL_MODE=direct \
#    BACKEND_ADDR=127.0.0.1:8011 python scripts/phase10_browser_check/run_backend.py
# 4. CHECK_PASSWORD=<the same value> STAFF_TOTP=<from the seed> NODE_PATH=<scratch>/pptr/node_modules \
#    AXE_PATH=<axe.min.js> OUT=<report.json> node scripts/partner_experience_browser_check/browser_check.js
python scripts/partner_experience_browser_check/prepare.py --drop
```

Running this alongside the full backend suite - or within a couple of minutes after it - can exhaust Windows' ephemeral ports (the scratch backend opens a connection per request, and a test run leaves thousands of sockets in TIME_WAIT); the symptom is a 500 with `connection failed ... Address already in use (10048)` in the backend log. Run them one after the other and let the sockets drain (about two minutes).

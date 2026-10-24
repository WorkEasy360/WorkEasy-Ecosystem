# Real-browser verification: partner upgrade (ADR-049)

Same harness and rules as `../partner_experience_browser_check/`:
- it runs against the production build and a real backend;
- it uses a scratch database (`w360_pup_fresh`);
- it adds no dependency to the repository (`puppeteer-core` lives in a scratch directory; the browser is the installed Chrome).

**Checks on every audited page**: CSP violations and console errors; axe WCAG 2.1 A/AA; a visible keyboard focus indicator; exactly one `h1`. **Reflow** (no horizontal scroll) at 1440, 1024, 768, 390 and 320 px for the dashboard offer, the upgrade intro, the Business, Products and Review steps and the Super Admin Upgrade panel.

**Seed** (`seed.py`, through the existing commands): the Super Admin; two products with a plan and a price; five active partners made by the Add Partner command with an administrator email and password - `affiliate.one` and `affiliate.four` with a referral code, a captured referral and a ledger row (the history the upgrade must keep), `affiliate.two`, `affiliate.three`, and `reseller.one`. Every partner administrator signs in with `CHECK_PASSWORD` (a fresh random value per run, never committed).

**Journeys, driven through the UI** (the partner uses its own sign-in; the Super Admin signs in with a password only - ADR-050):

- **A.** Affiliate -> **Reseller** on the same login: the Affiliate dashboard shows *Grow Your Partnership* with both buttons; the upgrade page asks for no password; the five-step wizard (Business, Products, Pricing, Commercial, Review) is walked and sent; a pending request grants nothing (still an Affiliate, `/partner/reseller/dashboard` refused, customers API refused); staff approve in the console with a commercial reference; **the same email and password** sign in and land on `/partner/reseller/dashboard` with exactly the Reseller menu, no Affiliate entries in the sidebar, and only *Upgrade to White-label* offered.
- **B.** Affiliate -> **White-label** directly (six steps incl. Brand and Domain); staff ask for more information; the partner sees the note, edits and resends; staff approve; the same login lands on `/partner/white-label/dashboard` with exactly the White-label menu and no upgrade offer.
- **C.** Before approval an Affiliate is refused `/partner/reseller/...`, `/reseller/...`, `/partner/white-label/...`, a reseller page typed inside its own workspace, the same after editing `localStorage` / `sessionStorage` (`partner_type`, `primary_partner_workspace`), the reseller APIs, the staff grant / experience / decision routes, a forged request section and an ineligible target; an open request still grants nothing.
- **D.** The Affiliate's referral links, clicks, conversions, balances, commissions and payouts are identical before and after the Reseller upgrade; the Affiliate workspace is one click away with its menu and its link.
- **E.** The same for an Affiliate with history upgrading to **White-label**.
- **F.** **Reseller -> White-label** on the same login; no *Upgrade to Reseller* offered beforehand, no upgrade offered afterwards.
- **S.** The console shows level, status, the approved request and its commercial reference; the review-queue API lists the approved requests.

`ONLY=A,C` runs only the journeys whose names start with those codes; later journeys need the partners an earlier one used, so run A first. The journeys **change** rows, so re-create and re-seed before every run. Afterwards `verify_db.sh`-style counts (users, partners, members) must equal the counts before: nothing in the run may create a user or a partner.

Not part of this check: a manual screen-reader pass (reported as **NOT EXECUTED**). Not part of CI.

**How to run it** (Windows; run from the repository root; in Git Bash set `MSYS_NO_PATHCONV=1`, and give paths as `C:/...`):

```
# 1. a scratch copy of frontend/ (never next to a running `next dev`): npm ci, then
#    BACKEND_ORIGIN=http://127.0.0.1:8011 npx next build, then `next start -H 127.0.0.1 -p 3999`
# 2. CHECK_PASSWORD=<a fresh random value for this run> python scripts/partner_upgrade_browser_check/prepare.py
#    (drops, migrates and seeds w360_pup_fresh; prints STAFF_TOTP)
# 3. backend on 127.0.0.1:8011:
#    PYTHONPATH="backend;scripts/extension_browser_check" DJANGO_SETTINGS_MODULE=w360_extension_browser_settings \
#    PYTHONUNBUFFERED=1 W360_DB_NAME=w360_pup_fresh W360_DB_HOST=127.0.0.1 W360_DB_PORT=5442 \
#    W360_DB_DIRECT_HOST=127.0.0.1 W360_DB_DIRECT_PORT=5442 W360_DB_POOL_MODE=direct \
#    BACKEND_ADDR=127.0.0.1:8011 DNS_FILE=<a file containing {}> python scripts/phase10_browser_check/run_backend.py
# 4. CHECK_PASSWORD=<the same value> STAFF_TOTP=<from the seed> NODE_PATH=<scratch>/pptr/node_modules \
#    AXE_PATH=<axe.min.js> OUT=<report.json> node scripts/partner_upgrade_browser_check/browser_check.js
python scripts/partner_upgrade_browser_check/prepare.py --drop
```

Running this alongside the full backend suite - or within a couple of minutes after it - can exhaust Windows' ephemeral ports; the symptom is a 500 with `connection failed ... Address already in use (10048)` in the backend log. Run them one after the other and let the sockets drain (about two minutes).

# Real-browser verification: complete reseller + white-label setup

Same harness and rules as `../partner_center_browser_check/` and `../phase10_browser_check/`:
- it runs against the production build and a real backend;
- it uses a scratch database;
- it adds no dependency to the repository (`puppeteer-core` lives in a scratch directory; the browser is the installed Chrome).

**Checks run on every audited page** (the wizard's every step, the partner's tabs, the customer-facing pages):
- CSP violations and console errors;
- axe WCAG 2.1 A/AA;
- a visible keyboard focus indicator;
- exactly one `h1`.

**Reflow** (no horizontal scroll) at **1440, 1024, 768, 390 and 320 px**: the Add Reseller wizard is walked step by step at each width, and so is the white-label wizard, then the partner's Overview, Business Details, Contacts, Products, Users and Activity tabs.

**Journeys, driven through the UI** (the second factor is answered by computing the TOTP from the seed's secret):

- **R. Add reseller.** Partners → Resellers → Add reseller. Business details (with address and country), Contacts (primary and a separate billing contact), Capabilities, Products and trials, Custom partner pricing (the discount is asserted to be the server's figure), Commission and payouts (the worked example is asserted to come from the server: £10,000 / £2,000 / £8,000), Administrator (no password field anywhere; invitation after activation) and support, White-label "Not now", Review, Activate. Then the partner's page: badges, the Business Details, Contacts, Products & Pricing and Users tabs show what was typed (the administrator reads "Invitation sent" because activation sent the deferred link), and the Activity tab lists the setup in words.
- **D. Save draft.** After step 1 only: the partner page says "Setup incomplete", lists what is missing, and Activate is disabled.
- **W. White-label for an existing partner.** Partners → White-label → Set up → choose Seed Reseller. Brand name, short name, a real PNG logo through the real upload, colour, support details; the four preview modes; products (one hidden from the portal); welcome message and footer; sender identity; a custom domain (the DNS TXT record is shown as Type / Host / Value with Copy buttons, "published" by writing the backend's file-backed DNS, verified, and only then **separately** activated); the honest sign-in statement; derived labels; Review; Enable. The same partner now wears Reseller and White-label and there is still exactly one row for it.
- **C. Combined.** The reseller made in R gets white-label, then an affiliate capability. One account, three badges, one row in All, Resellers and Affiliates.
- **A.** An existing affiliate becomes white-label: same account, no second row.
- **U. The partner's customer.** Signs in as User and sees the brand, the welcome message, the footer and the support details; the product the partner hid is not offered on Explore, the other one is; nothing is stored in the browser.
- **S.** A customer who chooses Administrator, and who then opens either wizard's address, is refused with no form and only 403s from the admin API.

**Not part of this check:** a manual screen-reader pass (reported as **NOT EXECUTED**).

**How to run it** (Windows; `;` separates `PYTHONPATH` entries; run from the repository root; in Git Bash set `MSYS_NO_PATHCONV=1`, and give `NODE_PATH` as a `C:/...` path):

```
python scripts/partner_setup_browser_check/prepare.py       # w360_psetup_fresh: drop, migrate, seed; prints the ids and the staff TOTP
# backend on 127.0.0.1:8011 with a file-backed DNS resolver (the check "publishes" a TXT record by writing DNS_FILE):
#   PYTHONPATH="backend;scripts/extension_browser_check" DJANGO_SETTINGS_MODULE=w360_extension_browser_settings \
#   PYTHONUNBUFFERED=1 DNS_FILE=dns.json W360_DB_NAME=w360_psetup_fresh W360_DB_HOST=127.0.0.1 W360_DB_PORT=5442 \
#   W360_DB_DIRECT_HOST=127.0.0.1 W360_DB_DIRECT_PORT=5442 W360_DB_POOL_MODE=direct \
#   BACKEND_ADDR=127.0.0.1:8011 python scripts/phase10_browser_check/run_backend.py
# frontend: a production build with BACKEND_ORIGIN=http://127.0.0.1:8011 (in a scratch copy with its own `npm ci`,
#   never next to a running `next dev`), then `next start -H 127.0.0.1 -p 3999`
NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> STAFF_TOTP=<from the seed> RESELLER_ID=<…> \
  AFFILIATE_ID=<…> CUSTOMER_ORG=customer-company DNS_FILE=<dns.json> OUT=<report.json> \
  node scripts/partner_setup_browser_check/browser_check.js
python scripts/partner_setup_browser_check/prepare.py --drop
```

`ONLY=R,D` (any of `R D W C A U S X`, `X` being the reflow sweep) runs part of it, but the journeys **create** rows, so re-create and re-seed before a full run.

This check is not part of CI. Its results are recorded in `docs/partner-setup/status.md`.

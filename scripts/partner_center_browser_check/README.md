# Real-browser verification: Partner Control Center + sign-in modes

Same harness and rules as `../phase10_browser_check/`:
- it runs against the production build and a real backend;
- it uses a scratch database;
- it adds no dependency to the repository (`puppeteer-core` lives in a scratch directory; the browser is the installed Chrome).

**Checks run on every audited page:**
- CSP violations and console errors;
- axe WCAG 2.1 A/AA;
- a visible keyboard focus indicator;
- exactly one `h1`;
- no horizontal scroll at **1440, 1024, 768, 390 and 320 px**.

**Journeys, driven through the UI:**

- **A. Administrator.** Sign in, choosing "Administrator" with the keyboard (Tab, then Enter). Answer the second factor. Then: Admin dashboard, the single **Partners** sidebar entry, the Partner dashboard, Resellers, and a reseller's page. The check asserts:
  - the old per-screen sidebar links are gone;
  - the Control Center navigation has nine sections;
  - the reseller page shows the "Reseller" badge;
  - after sign-in, nothing about a mode or role sits in `localStorage`, `sessionStorage` or a script-readable cookie.
- **B. A normal customer chooses Administrator.** The check asserts:
  - every `/api/v1/admin/*` answer is 403;
  - the page shows "You don't have administrator access.";
  - no console navigation, table or staff permission name appears;
  - **Continue as User** leads to the customer's organization.
- **C. A normal customer signs in as User.** The organization dashboard opens, then My products, which shows the held product.
- **D. An administrator signs in as User.** They land in their own organization and are never routed through `/admin`.
- **E. A combined partner.** One partner holds reseller, white-label and affiliate capabilities:
  - its page shows all three badges, and the Branding, Domains and Referrals tabs;
  - it appears as exactly one row in All Partners, Resellers, Affiliates and White-label.
- **Deep link.** A signed-out visit to `/admin/partners/affiliates` goes to `/login?mode=admin&next=…`. After sign-in and the second factor, the administrator returns to that page. A hostile `next` is ignored.
- **Old addresses.** `/admin/commissions`, `/admin/payouts` and `/admin/partners/resellers?scope=all` land on their Control Center pages, with their filters kept.
- **Narrow screens.** At 390 and 320 px the local navigation scrolls inside itself, the current section stays in view, and the page never scrolls sideways.

**Not part of this check:**
- a manual screen-reader pass (reported as **NOT EXECUTED**);
- revoking a StaffGrant, which is proved at the HTTP edge in `backend/tests/partner_center/test_admin_sign_in.py`.

**How to run it:**

```
python scripts/partner_center_browser_check/prepare.py      # w360_pcc_fresh: migrate + seed; prints the ids
# backend (Windows: ';' separates PYTHONPATH entries), unbuffered, on 127.0.0.1:8011:
#   PYTHONPATH="backend;scripts/extension_browser_check" DJANGO_SETTINGS_MODULE=w360_extension_browser_settings \
#   PYTHONUNBUFFERED=1 DNS_FILE=dns.json W360_DB_NAME=w360_pcc_fresh W360_DB_HOST=127.0.0.1 W360_DB_PORT=5442 \
#   W360_DB_DIRECT_HOST=127.0.0.1 W360_DB_DIRECT_PORT=5442 W360_DB_POOL_MODE=direct \
#   BACKEND_ADDR=127.0.0.1:8011 python scripts/phase10_browser_check/run_backend.py
# frontend: a production build with BACKEND_ORIGIN=http://127.0.0.1:8011 (in a scratch copy, never next to a
#   running `next dev`), then `next start -H 127.0.0.1 -p 3999`
NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> STAFF_TOTP=<from seed> RESELLER_ID=<…> \
  AFFILIATE_ID=<…> COMBINED_ID=<…> CUSTOMER_ORG=customer-company STAFF_ORG=staff-own-company \
  OUT=<report.json> node scripts/partner_center_browser_check/browser_check.js
python scripts/partner_center_browser_check/prepare.py --drop
```

The journeys create no partner, so one seed serves repeated runs.

This check is not part of CI. Its results are recorded in `docs/partner-center/status.md`.

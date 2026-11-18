# Partner Panel UI audit (2026-11-11)

Measures what the owner's "Professional Partner Panel UI/UX redesign" brief asks to be consistent, on every page a partner can reach, in a real browser on the production build. Signs in as each kind of partner from the scratch database of [partner_admin_panel_check](../partner_admin_panel_check/README.md): Reseller, Affiliate, White Label and a partner still in KYC (draft). Walks the sidebar, every section tab and every page tab. Not part of CI.

For each page it records:

- the h1 (exactly one), where it sits and its size;
- the h2 sizes, the button and input heights, and the font sizes in use;
- page-level sideways scrolling, and elements pushed off screen outside a scroll region;
- clipped text, touch targets under 24 px, axe (WCAG 2.2 AA) and console errors.

Every page is measured at 1440, 1024 and 390 px. The main pages (Dashboard, Eligible Products, Customers, Customer Subscriptions, Platform Wallet, Commissions, Help, Business & KYC) are measured at all eight widths the brief names: 1920, 1440, 1366, 1024, 768, 390, 360 and 320. Each measurement has a full-page screenshot.

```
# Prepare the database and start the backend, the production frontend and the edge
# as scripts/partner_admin_panel_check/README.md says, then:
NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> CHECK_PASSWORD=<seed's> \
  EDGE=127.0.0.1:8088 OUT=<dir> LABEL=before node scripts/partner_ui_check/audit.js
# ... change the code, rebuild, restart the frontend ...
LABEL=after node scripts/partner_ui_check/audit.js            # same variables
node scripts/partner_ui_check/summarize.js <dir>/before/report.json <dir>/after/report.json
```

The audit only reads; it signs in and opens pages. The clipped-text figure also counts text hidden on purpose for screen readers ("compared with the previous period", "(opens in a new tab)"), so read those entries before treating them as faults.

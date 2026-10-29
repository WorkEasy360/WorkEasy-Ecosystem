# Real-browser verification: the Product Manager (ADR-051)

Same harness rules as the earlier checks: it runs against the **production build** and a real backend on a **scratch database** (`w360_pm_fresh`), adds no dependency to the repository (`puppeteer-core` lives in a scratch directory; the browser is the installed Chrome), and is not part of CI.

**prepare.py is also the migration rehearsal.** The committed code (`git archive HEAD`) migrates the scratch database and registers the four live products from `infra/ecosystem/products.json`; the working tree then applies `catalog.0005_product_manager`; the products are compared column by column, the new columns must hold their defaults, and the migration is reversed and re-applied. It prints `REHEARSAL PASS`.

**Checks on every audited page**: CSP violations and console errors; axe WCAG 2.1 A/AA; a visible keyboard focus indicator; exactly one `h1`. **Reflow** (no horizontal page scroll) at 1440, 1366, 1024, 768 and 390 px for the list, each wizard step and the detail page. Screenshots for a manual review are written to `screens/` next to `OUT`.

**Journeys** (the Super Admin signs in with a password - ADR-050):
- **L** the list shows Easy CRM, EasyBook, Easy Project and Easy Help Desk as Active and Published; search and filters work.
- **W** Add Product: *Workeasy Test App* / `workeasy-test-app`, logo, description, screenshot with caption, store preview, an unsafe launch URL refused, launch URL + SSO callback, trial 7 days - **Save Draft**.
- **D** the draft is Draft + Hidden and absent from the public catalog; logo, screenshot, integration (draft) and trial are stored; every tab renders and passes the audit.
- **P** **Publish**: Active + Published and in the public catalog; the preview of the stored product; Plans and Pricing open filtered to the product.
- **U** **Unpublish**: hidden again; Activity lists every step with who did it.
- **X** the test product has an integration, so **Delete is refused** ("Archive it instead") and it is archived; a draft nothing uses is deleted from its own page.
- **M** 390 px screenshots of the list, the wizard and the detail page.

**How to run it** (Windows, from the repository root; never alongside a backend pytest run - it can exhaust ephemeral ports):

```
# 1. a scratch copy of frontend/ (never next to a running `next dev`): npm ci, then
#    BACKEND_ORIGIN=http://127.0.0.1:8011 npx next build, then `next start -H 127.0.0.1 -p 3999`
# 2. CHECK_PASSWORD=<fresh random value> python scripts/product_manager_browser_check/prepare.py
# 3. backend on 127.0.0.1:8011 (as in ../partner_upgrade_browser_check/README.md, W360_DB_NAME=w360_pm_fresh)
# 4. CHECK_PASSWORD=<same> NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> OUT=<report.json> \
#    node scripts/product_manager_browser_check/browser_check.js
python scripts/product_manager_browser_check/prepare.py --drop
```

A manual screen-reader pass is not part of this check: NOT EXECUTED.

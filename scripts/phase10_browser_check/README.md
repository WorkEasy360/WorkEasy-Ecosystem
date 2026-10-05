# Real-browser verification of Phase 10

Same harness and rules as `../phase9_browser_check/`:
- the production build and a real backend;
- a scratch database;
- no dependency added to the repository (`puppeteer-core` lives in a scratch directory; the browser is the installed Chrome).

**Checks run on every page:**
- CSP violations and console errors;
- axe WCAG 2.1 A/AA violations;
- a visible focus indicator;
- exactly one `h1`;
- no horizontal scroll at **1440, 1024, 768, 390 and 320 px**.

**Journeys, run end to end through the UI:**
1. **Reseller creation.** Staff use the Add Reseller wizard (details, the Standard Reseller preset, products, a partner price with server-computed figures, a custom commission rate, the administrator, a server-backed review, a confirmation and a step-up prompt), then see the partner in the list and the overview. The administrator opens the emailed setup link, chooses a password, signs in and enrols an authenticator.
2. **Customer provisioning -> activation -> product access** (the one that closes D-020). The partner provisions a customer (step-up on that click); staff see "Owner activation pending"; the owner's email carries one link in the URL fragment and no password; the owner opens it in their own browser, chooses a password and becomes the owner; the same link is then refused; the partner sells a plan; the owner holds the product; staff see "Owner activated".
3. **White-label, with AAL2.** A partner with branding and domain capability saves a brand with a PNG logo (no second factor), previews it, requests a domain (second factor **on this step**), publishes the DNS record (by writing `DNS_FILE`, which the scratch backend's resolver reads), verifies, and activates - a separate, explicit step. The brand then serves on the partner's own hostname (Chrome maps it to the frontend), and only there.
4. **A combined partner.** Staff grant the reseller white-label capabilities: it gains a label, not an account, appears in both lists, and brands and serves a domain of its own alongside its customers.
5. **Termination.** Staff terminate the reseller: its domain goes off the air, its portal offers only what it is owed and refuses everything else, and the customer keeps their sign-in, their organization and their product.

**Not part of this check:** a manual screen-reader pass (reported as **NOT EXECUTED**), and any load or capacity measurement (see `backend/tests/phase10/test_10f_performance.py`).

Emailed links are read from the backend's console email output. The backend must run unbuffered, with its output in `BACKEND_LOG`.

```
python scripts/rehearse_phase10_upgrade.py --only fresh --keep      # w360_p10_fresh
PYTHONPATH=backend DJANGO_SETTINGS_MODULE=config.settings.development \
  W360_DB_NAME=w360_p10_fresh W360_DB_HOST=127.0.0.1 W360_DB_PORT=5442 \
  W360_DB_DIRECT_HOST=127.0.0.1 W360_DB_DIRECT_PORT=5442 W360_DB_POOL_MODE=direct \
  W360_PAYMENTS_FAKE_PROVIDER=true \
  python scripts/phase10_browser_check/seed.py     # prints the ids and the two authenticator secrets
# backend (Windows: ';' separates PYTHONPATH entries), unbuffered, output to BACKEND_LOG:
#   PYTHONPATH="backend;scripts/extension_browser_check" \
#   DJANGO_SETTINGS_MODULE=w360_extension_browser_settings PYTHONUNBUFFERED=1 DNS_FILE=dns.json \
#   W360_DB_NAME=w360_p10_fresh ... python scripts/phase10_browser_check/run_backend.py   # 127.0.0.1:8011
# frontend: a production build with BACKEND_ORIGIN=http://127.0.0.1:8011,
#   `next start -H 127.0.0.1 -p 3999` (in a scratch copy - never next to a running `next dev`)
NODE_PATH=<scratch>/pptr/node_modules BACKEND_LOG=<backend.log> DNS_FILE=<dns.json> AXE_PATH=<axe.min.js> \
  STAFF_TOTP=<from seed> BRAND_TOTP=<from seed> SEED_RESELLER_ID=<from seed> SEED_BRAND_ID=<from seed> \
  OUT=<report.json> node browser_check.js
```

The journeys create rows (a reseller, a customer, a domain), so **re-create and re-seed before every re-run**.

This check is not part of CI. Its results are recorded in `docs/phase-10/phase-10-status.md`.

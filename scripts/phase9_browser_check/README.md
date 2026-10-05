# Real-browser verification of Phase 9

It uses the same harness and rules as `../extension_browser_check/`:
- the production build and a real backend;
- a scratch database;
- no dependency added to the repository (`puppeteer-core` lives in a scratch directory; the browser is the installed Chrome).

**Checks run on every page:**
- CSP violations and console errors;
- axe WCAG 2.1 A/AA violations;
- a visible focus indicator;
- exactly one `h1`;
- no horizontal scroll at 320px.

**Pages covered:**
- the public site, signed out;
- the new organization pages;
- the new Super Admin pages.

**Journeys, run end to end:**
- **Signup to purchase:**
  1. Choose a plan on `/pricing`.
  2. Sign up.
  3. Confirm that signing in before verification is refused with the reason.
  4. Open the emailed link and confirm with the password.
  5. Land in checkout for the chosen plan and set the billing currency.
  6. Place the order and pay on the test provider's page. No step-up is needed.
  7. The order is fulfilled and the product is held.
- **Password reset:** the emailed link sets the password once, and the new password signs in.
- **Landing:** everyone lands on the dashboard; the bootstrapped Super Admin lands on `/admin`.
- **Paying from Orders:** paying an order from the Orders page needs no step-up.
- **Staff segments:** the staff user segments reflect what each organization holds.
- **Membership (the organizations write boundary, D-032):**
  1. The owner invites someone, who accepts in their own browser.
  2. Suspending shuts the member out, and reactivating lets them back in.
  3. Signing the member out needs a step-up (TOTP), then ends their session for this organization.
  4. A rename reaches the member's organization chooser.
  5. The security policy saves once per version, and a stale save is a 409.

Emailed links are read from the backend's console email output. The backend must run unbuffered, with its output in `BACKEND_LOG`.

```
python scripts/rehearse_phase9_upgrade.py --only fresh --keep            # w360_p9_fresh
PYTHONPATH=backend DJANGO_SETTINGS_MODULE=config.settings.development \
  W360_DB_NAME=w360_p9_fresh W360_DB_HOST=127.0.0.1 W360_DB_PORT=5442 W360_DB_POOL_MODE=direct \
  W360_PAYMENTS_FAKE_PROVIDER=true \
  python scripts/phase9_browser_check/seed.py          # prints the slug, PLAN_BUSINESS and OWNER_TOTP
# backend: the same environment plus PYTHONUNBUFFERED=1 and
# DJANGO_SETTINGS_MODULE=w360_extension_browser_settings
# (../extension_browser_check on PYTHONPATH):
#   runserver 8011 --noreload > backend.log 2>&1
# frontend: a production build with BACKEND_ORIGIN=http://127.0.0.1:8011,
#   `next start -p 3999` (in a scratch copy - never next to a running `next dev`)
ORG_SLUG=<slug> PLAN_BUSINESS=<id> OWNER_TOTP=<secret> BACKEND_LOG=<backend.log> AXE_PATH=<axe.min.js> node browser_check.js
```

This check is not part of CI. Its results are recorded in `docs/phase-9/phase-9-status.md`.

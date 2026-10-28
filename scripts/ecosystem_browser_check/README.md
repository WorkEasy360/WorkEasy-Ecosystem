# Real-browser acceptance: the complete Store + partner ecosystem (ADR-053)

Same harness rules as the earlier checks: the **production build** of the frontend, a **real backend** on a **scratch database** (`w360_eco_fresh`), headless Chrome (`puppeteer-core` in a scratch directory, never in the repository), not part of CI. Results: `docs/ecosystem-complete/status.md`.

**prepare.py is also the migration rehearsal**: the committed code (`git archive HEAD`) migrates the scratch database and writes history (`seed_history.py`); the working tree applies its migrations; 15 tables are fingerprinted before and after, the migrations are reversed and re-applied, and `seed.py` adds the acceptance data (TEST plans and INR prices for the four products, the test payment provider, margins, a set-up fee, three customers, two affiliates, a reseller granted EasyBook and Easy Help Desk only, a white-label partner with its brand). `--skip-rehearsal` only seeds.

Journeys (`ONLY=A,B,...` runs a subset): A Super Admin, B customer trial + purchase, C three-product cart, D affiliate link -> clean browser -> sign-up -> purchase -> commission, E reseller customer + central order + payment + margin, F branded store purchase, G Affiliate -> Reseller -> White-label upgrade, H every menu item, I wrong access and browser prices, S support, R responsive (1920-390 px). Every audited page: axe WCAG 2.1 A/AA, CSP, console errors, visible focus, one h1.

```
# Windows, Git Bash, repository root. Never alongside a backend pytest run.
CHECK_PASSWORD=<fresh random> python scripts/ecosystem_browser_check/prepare.py
# backend (single-host; emails to the console, read from BACKEND_LOG):
PYTHONPATH="backend;scripts/ecosystem_browser_check" DJANGO_SETTINGS_MODULE=w360_ecosystem_browser_settings \
  W360_DB_NAME=w360_eco_fresh W360_DB_HOST=127.0.0.1 W360_DB_PORT=5442 W360_DB_DIRECT_HOST=127.0.0.1 \
  W360_DB_DIRECT_PORT=5442 W360_DB_POOL_MODE=direct W360_REDIS_URL=redis://127.0.0.1:6392/<an unused db> \
  PYTHONUNBUFFERED=1 python backend/manage.py runserver 127.0.0.1:8011 --noreload > backend.log
# frontend: a scratch copy, npm ci, BACKEND_ORIGIN=http://127.0.0.1:8011 next build, next start -H 127.0.0.1 -p 3999
CHECK_PASSWORD=<same> NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> BACKEND_LOG=<backend.log> \
  OUT=<report.json> node scripts/ecosystem_browser_check/browser_check.js
python scripts/ecosystem_browser_check/prepare.py --drop
```

A manual screen-reader pass is not part of this check: NOT EXECUTED.

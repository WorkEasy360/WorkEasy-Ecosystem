# Real-browser acceptance: the full-stack Store upgrade (ADR-056)

Same harness as `scripts/store_console_check/`: the **production build** of the frontend, a **real backend** on a **scratch database** (`w360_su_fresh`), headless Chrome (`puppeteer-core` in a scratch directory, never in the repository), not part of CI. Results: `docs/store-upgrade/status.md`.

**prepare.py is also the migration rehearsal**: the committed code (`git archive HEAD`) migrates the scratch database and writes history (`store_console_check/seed_history.py`); the working tree applies `catalog.0007`, `pricing.0004`, `commissions.0007` and `partners.0014`; nine tables are fingerprinted before and after; new columns are checked at their defaults and the triggers checked to freeze a published price's discount and a rule's `recurring`; the four migrations are reversed and re-applied; then `scripts/ecosystem_browser_check/seed.py` adds the acceptance data (four products with INR prices, margins Reseller 20% / White-label 25%, affiliate `aman@eco.test`, white-label `wl@eco.test` granted Easy CRM and EasyBook, customer `buyer@eco.test`, Super Admin `staff@eco.test`).

Journeys: A Super Admin Store overview and Commercial rules, commission 30% recurring for 12 months, white-label price range; B Plans & Pricing discount and set-up fee; C customer discounted card, product page, plan comparison, checkout with set-up fee, test payment, plan held; D white-label sets its own price inside the range, outside refused; E wrong access refused; R responsive 1920-390 px. Every audited page: axe WCAG 2.1 A/AA, CSP, console errors, visible focus, one h1.

```
# Windows, Git Bash, repository root. Never alongside a backend pytest run that uses the same database.
CHECK_PASSWORD=<fresh random> python scripts/store_upgrade_check/prepare.py
PYTHONPATH="backend;scripts/ecosystem_browser_check" DJANGO_SETTINGS_MODULE=w360_ecosystem_browser_settings \
  W360_DB_NAME=w360_su_fresh W360_DB_HOST=127.0.0.1 W360_DB_PORT=5442 W360_DB_DIRECT_HOST=127.0.0.1 \
  W360_DB_DIRECT_PORT=5442 W360_DB_POOL_MODE=direct W360_REDIS_URL=redis://127.0.0.1:6392/<an unused db> \
  python backend/manage.py runserver 127.0.0.1:8011 --noreload
# frontend: a scratch copy, npm ci, BACKEND_ORIGIN=http://127.0.0.1:8011 next build, next start -H 127.0.0.1 -p 3999
CHECK_PASSWORD=<same> NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> OUT=<report.json> \
  node scripts/store_upgrade_check/browser_check.js
python scripts/store_upgrade_check/prepare.py --drop
```

The journeys change data, so re-run `prepare.py` before every run. A manual screen-reader pass is not part of this check: NOT EXECUTED.

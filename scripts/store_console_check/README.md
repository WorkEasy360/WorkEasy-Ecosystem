# Real-browser acceptance: the affiliate Store Console (ADR-055)

Same harness as `scripts/ecosystem_browser_check/`: the **production build** of the frontend, a **real backend** on a **scratch database** (`w360_sc_fresh`), headless Chrome (`puppeteer-core` in a scratch directory, never in the repository), not part of CI. Results: `docs/store-console/status.md`.

**prepare.py is also the migration rehearsal**: the committed code (`git archive HEAD`) migrates the scratch database and writes history (`seed_history.py`: a live and a revoked promotion grant, a product link with a visit); the working tree applies `partners.0012`; seven tables are fingerprinted before and after; the migration is reversed and re-applied; then `scripts/ecosystem_browser_check/seed.py` adds the acceptance data (four products on the Store with TEST prices, a 10% rule, affiliate `aman@eco.test` with no grant, Super Admin `staff@eco.test`).

Journeys: A affiliate explores and requests (duplicate refused), B Super Admin approves one and rejects one with a reason, C affiliate makes a link and a campaign code for the approved product, D wrong access refused, E revocation stops new links and keeps history, F the old My Products address redirects, R responsive 1920-390 px. Every audited page: axe WCAG 2.1 A/AA, CSP, console errors, visible focus, one h1.

```
# Windows, Git Bash, repository root. Never alongside a backend pytest run that uses the same database.
CHECK_PASSWORD=<fresh random> python scripts/store_console_check/prepare.py
PYTHONPATH="backend;scripts/ecosystem_browser_check" DJANGO_SETTINGS_MODULE=w360_ecosystem_browser_settings \
  W360_DB_NAME=w360_sc_fresh W360_DB_HOST=127.0.0.1 W360_DB_PORT=5442 W360_DB_DIRECT_HOST=127.0.0.1 \
  W360_DB_DIRECT_PORT=5442 W360_DB_POOL_MODE=direct W360_REDIS_URL=redis://127.0.0.1:6392/<an unused db> \
  python backend/manage.py runserver 127.0.0.1:8011 --noreload
# frontend: a scratch copy, npm ci, BACKEND_ORIGIN=http://127.0.0.1:8011 next build, next start -H 127.0.0.1 -p 3999
CHECK_PASSWORD=<same> NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> OUT=<report.json> \
  node scripts/store_console_check/browser_check.js
python scripts/store_console_check/prepare.py --drop
```

The journeys change data, so re-run `prepare.py` before every run. A manual screen-reader pass is not part of this check: NOT EXECUTED.

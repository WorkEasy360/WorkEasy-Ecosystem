# Real-browser acceptance: the Admin Panel programme

Same harness rules as the earlier checks: the **production build** of the frontend, a **real backend** on a **scratch database** (`w360_admin_panel_fresh`), headless Chrome (`puppeteer-core` in a scratch directory, never in the repository), not part of CI. Results: `docs/admin-panel/status.md`.

**prepare.py is also the migration rehearsal**: the live release (`PREVIOUS_RELEASE`) migrates the scratch database and writes history; the working tree applies its migrations; every existing row is fingerprinted before and after; active partners must stay active with no KYC approval invented; the Admin from the live release must hold every new permission; the migrations are reversed and re-applied. `seed.py` then adds the data the journeys use through the real endpoints. `--skip-rehearsal` only seeds.

Journeys (`ONLY=A,B,...` runs a subset): A authentication, B every page in `adminModules.ts` plus detail pages at 1920, 1366, 1024, 768, 390 and 360 px (one h1, no page overflow, nothing clipped, no console or API errors; axe at 1366 and 390), C the dashboard against its API, D search, Create and notifications, E public application to an active partner, F new customer to a plan, G the wallets, H usage pricing and commission rules, I support, J every report with CSV, K settings and permissions, L the phone drawer.

```
# Windows, Git Bash, repository root. Never alongside a backend pytest run.
CHECK_PASSWORD=<fresh random> python scripts/admin_panel_check/prepare.py
# backend:
PYTHONPATH="backend;scripts/admin_panel_check" DJANGO_SETTINGS_MODULE=w360_admin_panel_settings \
  W360_DB_NAME=w360_admin_panel_fresh W360_DB_HOST=127.0.0.1 W360_DB_PORT=5442 W360_DB_DIRECT_HOST=127.0.0.1 \
  W360_DB_DIRECT_PORT=5442 W360_DB_POOL_MODE=direct W360_REDIS_URL=redis://127.0.0.1:6392/<an unused db> \
  PYTHONUNBUFFERED=1 python backend/manage.py runserver 127.0.0.1:8011 --noreload > backend.log
# frontend: a scratch copy, npm ci, BACKEND_ORIGIN=http://127.0.0.1:8011 next build, next start -H 127.0.0.1 -p 3999
CHECK_PASSWORD=<same> NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> OUT=<dir> \
  node scripts/admin_panel_check/browser_check.js
python scripts/admin_panel_check/prepare.py --drop
```

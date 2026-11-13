# Real-browser check: partner categories and Login as Guest (ADR-062)

What only a real browser on the real host layout can show: that Login as Guest started on `admin.` really opens the partner's dashboard on `partner.` (a cross-host form post the CSP must allow, a 303, host-only cookies), stays read-only there, and comes back to the Admin session. Same rules as the earlier checks: the **production build** of the frontend, a **real backend** on a **scratch database** (`w360_guest_fresh`), headless Chrome with `puppeteer-core` from a scratch directory (never the repository), not part of CI.

Chrome maps the five portal names to `edge.js`, a few lines of Node that do what `deploy/edge/Caddyfile` does for routing: `/api/*` to the backend, the rest to the frontend, **the Host header kept**. Plain http; TLS is the edge's and is rehearsed by `scripts/portals_browser_check/edge_rehearsal.py`.

Checks (updated 2026-11-11 for the owner's "Login as Guest beside View" brief; not yet re-run): sign-in on `admin.`; Partners ▼ Affiliate, Reseller, White Label, All Partners; the table's first columns are Partner Name | Partner Type | Status | View / Actions, with no Earnings and no trailing Guest Login column; every row has View and Login as Guest side by side (a draft's shown, explained, unavailable) and both are in view with nothing to scroll at 1920, 1366 and 1280 px, and present at 390 px with no sideways page scroll; each type's page lists exactly its partners with the All Partners table, one h1, the menu entry current, the module tabs unchanged, axe clean; Alt+Up reorders the menu, keeps focus and survives a reload; the row menu keeps Manage Guest Access and no longer has Login as Guest; for each type, Login as Guest (after the reason) lands on that partner's dashboard on `partner.` with the "Guest Session – Viewing Partner:" banner (name, partner type, Return to Super Admin, End Guest Session; held in view while scrolling at 390 px), no ticket in any URL, a host-only HttpOnly session cookie, a write refused (`guest_read_only`), no admin API, another partner unreachable, axe for the reseller; Return to Super Admin returns to the searched list it started from with its search restored, End Guest Session to the partner's own Admin page, the Admin still signed in and nothing left signed in on `partner.`; the partner's page has Back to Partners (to that searched list), Edit Partner and Login as Guest at the top, above the tabs, and its guest session returns there; Manage Guest Access lists the views (axe clean) and Revoke signs the other tab's guest session out on its next request; New partner asks for the Partner Email; no CSP violation and no console error anywhere.

```
# Windows, Git Bash, repository root. Ports 8011, 3999 and 8088 free.
export MSYS_NO_PATHCONV=1
CHECK_PASSWORD=<fresh random> python scripts/guest_login_check/prepare.py     # prints SEED {...ids}
docker exec workeasy360-redis-1 redis-cli -n 11 FLUSHDB
# backend:
PYTHONPATH="backend;scripts/guest_login_check" DJANGO_SETTINGS_MODULE=w360_guest_login_settings \
  W360_DB_NAME=w360_guest_fresh W360_DB_HOST=127.0.0.1 W360_DB_PORT=5442 W360_DB_DIRECT_HOST=127.0.0.1 \
  W360_DB_DIRECT_PORT=5442 W360_DB_POOL_MODE=direct W360_REDIS_URL=redis://127.0.0.1:6392/11 \
  W360_PARTNER_KYC_REQUIRED=false python backend/manage.py runserver 127.0.0.1:8011 --noreload
# frontend: a scratch copy, npm ci, BACKEND_ORIGIN=http://127.0.0.1:8011 next build, then
#   W360_{ADMIN,PARTNER,APP,AUTH}_ORIGIN=http://<portal>.workeasy360.com next start -H 127.0.0.1 -p 3999
node scripts/guest_login_check/edge.js 8088 8011 3999
NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> CHECK_PASSWORD=<same> IDS='<the SEED json>' \
  EDGE=127.0.0.1:8088 OUT=<dir> node scripts/guest_login_check/browser_check.js
python scripts/guest_login_check/prepare.py --drop
```

The journeys start and end guest views, so re-create the database before every run.

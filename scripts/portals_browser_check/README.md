# Real-browser verification: multi-portal hosts and the four products' 7-day trials

ADR-043, `docs/portals/design.md`. The results are recorded in `docs/portals/status.md`.

Same rules as the earlier checks: the production frontend build, a real backend, a scratch database, and no new dependency in the repository (`puppeteer-core` lives in a scratch directory; the browser is the installed Chrome).

**What is different here: the production edge sits in front.** The journeys must carry each portal's real host name (`admin.`, `partner.`, `app.`, `auth.workeasy360.com`). The backend's surface rules and the hand-off's host binding depend on it, and so does the frontend's routing. A Next.js rewrite would replace the Host header, so `run_edge.py` runs the real `deploy/edge/Caddyfile` in front of the scratch backend and frontend. Chrome maps only the five portal names to it (`--host-resolver-rules`). The products' own hosts are not mapped, so **Open reaches the real product sites**.

`run_edge.py` applies these mechanical changes, and every routing rule is otherwise the production file's:
- the two upstreams (`api:8000` and `web:3000`) become the local ports;
- the listeners move to high ports, bound to loopback;
- `skip_install_trust`;
- for this plain-http run only, the portal sites' `tls` issuer and the partner custom-domain block are removed. Caddy refuses a catch-all on-demand TLS policy next to certificate-less sites.

**TLS is rehearsed separately**, with verification on: `edge_rehearsal.py` runs the same file with https origins and Caddy's `internal` CA (`skip_install_trust`, so nothing touches this machine's trust store). It checks every host for:
- a 308 from http to https;
- a certificate that verifies (chain and host name);
- HSTS;
- the original Host header reaching both upstreams;
- per-host routing;
- internal paths hidden;
- OIDC discovery only on `auth.`;
- no UI on `api.`.

**Journeys** (`browser_check.js`, `ONLY=A,N,P,U,T,S,R,X,L` runs a subset):
- **A**: `admin.` signed out goes to `auth.`, then password, then the TOTP on `auth.`, then the console on `admin.`. Cookies are host-only, the binding cookie is spent, nothing is set for the parent domain, and the Product Manager shows four products with a 7-day trial on.
- **N**: a customer who chooses the Admin portal is signed in and refused, with no admin API call. "Continue as User" opens `app.`.
- **P**: `partner.` goes to `auth.`, then the partner portal, which shows only the granted products (not Easy Project). The admin API answers 404 on `partner.`. A customer is refused the partner portal.
- **U**: `app.` serves the public site at `/`. `/dashboard` goes to `auth.`, then the organization, then My Products (four products, AVAILABLE).
- **T**, for each of the four products separately:
  - Explore offers "Start 7-Day Free Trial", Buy now and Learn more; starting gives "Trial active — 7 days remaining", and starting again is refused (`already_used`);
  - My Products shows TRIAL, 7 days left; assigning a seat lets Open reach the product's own site;
  - **controlled clock** (`time_travel.py`: the trial's own moments move back 7 days and a minute; no sweeper runs): the launch is denied, and My Products and Explore say "Trial expired" and offer "Upgrade / Buy now";
  - checkout, then the hosted test payment, gives ACTIVE, and the product is launchable again.
- **S**: staff and a partner member on `app.` see only their own memberships (none) and are refused a customer's organization.
- **R**: hostile `next` values are not carried to the sign-in, and a forged `state` cannot complete a hand-off.
- **X**: reflow at 1440, 1024, 768, 390 and 320 px on the sign-in, admin, partner and customer pages.
- **L**: signing out on `app.` ends the `admin.` and `auth.` sessions of the same sign-in.

On every audited page: CSP violations, console errors, axe WCAG 2.1 A/AA, a visible focus indicator, and exactly one `h1`. **A manual screen-reader pass is not part of this check**; it is reported as NOT EXECUTED.

**How to run it** (Windows, Git Bash, from the repository root, `MSYS_NO_PATHCONV=1`):

```
python scripts/portals_browser_check/edge_rehearsal.py --caddy <caddy.exe> --work <scratch>/edge_work
python scripts/portals_browser_check/prepare.py                      # w360_portals_fresh: drop, migrate, seed
docker exec workeasy360-redis-1 redis-cli -n 9 FLUSHDB                 # the scratch backend's own cache DB
# backend (prepare.backend_env): PYTHONPATH="backend;scripts/portals_browser_check"
#   DJANGO_SETTINGS_MODULE=w360_portals_browser_settings W360_DB_NAME=w360_portals_fresh W360_DB_HOST=127.0.0.1
#   W360_DB_PORT=5442 W360_DB_DIRECT_HOST=127.0.0.1 W360_DB_DIRECT_PORT=5442 W360_DB_POOL_MODE=direct
#   W360_REDIS_URL=redis://127.0.0.1:6392/9  python backend/manage.py runserver 127.0.0.1:8011 --noreload
# frontend: a production build in a scratch copy with its own `npm ci`, then
#   W360_{ADMIN,PARTNER,APP,AUTH}_ORIGIN=http://<portal>.workeasy360.com  next start -H 127.0.0.1 -p 3999
python scripts/portals_browser_check/run_edge.py --caddy <caddy.exe> --work <scratch>/edge_browser --listen 8088
NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> STAFF_TOTP=<seed> CUSTOMER_ORG=customer-company \
  PARTNER_ID=<seed> EDGE=127.0.0.1:8088 OUT=<report.json> PY=<repo>/.venv/Scripts/python.exe \
  TIME_TRAVEL_ENV='<the backend env as JSON>' node scripts/portals_browser_check/browser_check.js
python scripts/portals_browser_check/prepare.py --drop
```

The journeys start trials and buy products, so re-create, re-seed and flush before every run. This check is not part of CI.

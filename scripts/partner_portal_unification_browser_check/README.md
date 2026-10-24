# Real-browser verification: partner.workeasy360.com is the WorkEasy360 partner portal

Results: `docs/partner-portal-unification/status.md`.

**What it proves.** A partner the Super Admin creates (name, email, password, type) signs in at `partner.workeasy360.com` with exactly those credentials, through the central sign-in on `auth.`, and lands in the Reseller, Affiliate or White-label workspace. Guest Login reaches the same workspace from `admin.`, with no partner password.

**The four real host names.** Like `../portals_browser_check/`, it runs behind a local copy of the production edge. That is `deploy/edge/Caddyfile`, which the single host now uses unchanged. Chrome's `--host-resolver-rules` maps `admin.`, `partner.`, `app.`, `auth.` and `api.workeasy360.com` to it, so every request carries its real Host. The backend runs with the multi-portal settings (`../portals_browser_check/w360_portals_browser_settings.py`), so the surface rules, the hand-off and the per-portal CSRF origin check are all in force.

**Reuse.** The plumbing and the Complete Your Profile steps come from `../partner_management_browser_check/`, and so does its seed: the Super Admin with an authenticator, two sellable products and the default commission rule.

**Checks on every audited page:**
- CSP violations and console errors;
- axe WCAG 2.1 A/AA;
- a visible keyboard focus indicator;
- exactly one `h1`;
- reflow at 1440, 1024, 768, 390 and 320 px.

**Journeys** (`H A B C W R M G S L P`, described at the top of `browser_check.js`):
- host routing;
- the three types end to end, including the draft state;
- wrong-workspace refusals;
- the short addresses;
- a second capability with the switcher;
- Guest Login for all three types, with Exit;
- suspension;
- sign-out;
- the password and request audit.

**How to run it** (Windows, Git Bash, from the repository root, `MSYS_NO_PATHCONV=1`; `<S>` is a scratch directory):

```
# 1. a scratch copy of frontend/ (never next to a running `next dev`): npm ci, then
#    BACKEND_ORIGIN=http://127.0.0.1:8011 npx next build, then
#    W360_{ADMIN,PARTNER,APP,AUTH}_ORIGIN=http://<portal>.workeasy360.com NODE_ENV=production \
#      npx next start -H 127.0.0.1 -p 3999
# 2. CHECK_PASSWORD=<a fresh random value> python scripts/partner_portal_unification_browser_check/prepare.py
#    (drops, migrates and seeds w360_ppu_fresh; prints STAFF_TOTP)
#    docker exec workeasy360-redis-1 redis-cli -n 10 FLUSHDB
# 3. backend on 127.0.0.1:8011 with the environment `prepare.py --env` prints:
#    python backend/manage.py runserver 127.0.0.1:8011 --noreload
# 4. python scripts/portals_browser_check/run_edge.py --caddy <caddy.exe> --work <S>/edge --listen 8088
# 5. CHECK_PASSWORD=<same> STAFF_TOTP=<from the seed> NODE_PATH=<S>/pptr/node_modules \
#    AXE_PATH=<S>/fe/node_modules/axe-core/axe.min.js EDGE=127.0.0.1:8088 OUT=<S>/report.json \
#    node scripts/partner_portal_unification_browser_check/browser_check.js
python scripts/partner_portal_unification_browser_check/prepare.py --drop
```

**Rules for running it:**
- The journeys create partners, so re-create, re-seed and flush before every run.
- Do not run it alongside the full backend suite (Windows ephemeral ports).
- It is not part of CI.
- A manual screen-reader pass is not part of it, and is reported as **NOT EXECUTED**.

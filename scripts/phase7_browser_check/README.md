# Real-browser verification of the Phase 7 screens

Not part of CI (it needs a browser and a running stack), and it adds **no
dependency to the repository**: `puppeteer-core` is installed in a scratch
directory, and the browser is the Chrome already on the machine.

What it proves, against the **production build** of the frontend and a real Django
backend: no Content-Security-Policy violation, no console error, no WCAG 2.1 A/AA
axe violation (including colour contrast, which jsdom cannot compute), a visible
keyboard focus indicator on the first 40 tab stops of each page, no horizontal page
scroll at 320px, and that a partner's accent colour is applied through the CSSOM
and a hostile one is not.

```
# 1. a scratch database (never the dev or test database)
python scripts/rehearse_phase7_upgrade.py --only fresh --keep      # creates w360_p7_fresh
#    or create one yourself and: manage.py migrate (config.settings.migration)

# 2. seed it and start the backend on 8010
PYTHONPATH=backend DJANGO_SETTINGS_MODULE=config.settings.development \
  W360_DB_NAME=<db> W360_DB_HOST=127.0.0.1 W360_DB_PORT=5442 W360_DB_POOL_MODE=direct \
  python scripts/phase7_browser_check/seed.py
PYTHONPATH="backend;scripts/phase7_browser_check" DJANGO_SETTINGS_MODULE=w360_browser_settings \
  W360_DB_NAME=<db> W360_DB_HOST=127.0.0.1 W360_DB_PORT=5442 W360_DB_POOL_MODE=direct \
  python backend/manage.py runserver 8010 --noreload

# 3. a production build of the frontend, served on 3999 (a scratch copy: never
#    build in the working tree while `next dev` is running)
npm ci && npx next build && npx next start -p 3999

# 4. the checks (in a scratch dir containing `npm i puppeteer-core`)
node browser_check.js && node brand_check.js
```

**Keyboard-only pass** (final audit): `seed_keyboard.py` (run after `seed.py`,
same environment) enrols TOTP factors with known secrets for the staff and
partner users and prepares a READY payout by a *second* staff member; it prints
the secrets as JSON. Then, with the servers above running:

```
KB_SECRETS='<the "secrets" object it printed>' node keyboard_check.js
```

It signs in, navigates, approves a payout, grants a time-boxed staff permission,
creates a commission rule and requests a custom domain using Tab, typing and
Enter only - completing the real MFA step-up from the keyboard - and checks
where focus lands after each step. Build the frontend with
`BACKEND_ORIGIN=http://127.0.0.1:8010` (the rewrite target is fixed at build
time).

`w360_browser_settings.py` only adds `http://127.0.0.1:3999` to the development
CSRF trusted origins. Adjust `CHROME` and the axe path at the top of
`browser_check.js` for your machine. Results are recorded in
`docs/phase-7/06-frontend.md`.

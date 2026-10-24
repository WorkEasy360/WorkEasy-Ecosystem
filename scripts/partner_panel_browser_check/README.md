# Real-browser verification: the partner business panel and its final menu (ADR-045, ADR-046)

Same harness and rules as `../partner_setup_browser_check/`:
- it runs against the production build and a real backend;
- it uses a scratch database (`w360_ppanel_fresh`);
- it adds no dependency to the repository (`puppeteer-core` lives in a scratch directory; the browser is the installed Chrome).

**Checks on every audited page** (the 37 screens of the final menu and its tabs, plus the forced password change): CSP violations and console errors; axe WCAG 2.1 A/AA; a visible keyboard focus indicator; exactly one `h1`. **Reflow** (no horizontal scroll) at 1440, 1024, 768, 390 and 320 px for all 37 screens (185 checks). The sidebar is asserted to be **exactly the 24 approved entries**, in order.

**Journeys, driven through the UI**

- **A.** A signed-out visit is sent to sign-in; a customer who is nobody's partner member sees "no access" and the API answers 404; the owner reaches the dashboard and the whole navigation; the range selector and the table view of the chart; the **demo partner** signs in with its temporary password, is shown only "Choose your own password" (the API answers 403 `password_change_required`), changes it, reaches its dashboard, and the temporary password no longer signs in.
- **B.** Add a customer (first/last name, email), see it listed with its owner activation, export the list as a CSV download (content checked, another partner's customer absent), the subscriptions page.
- **C.** Create a sub-partner (capabilities and a product), activate it (second factor), set the administrator's password (standing in for the emailed link), sign in as the sub-partner: its customer list is empty, none of its parent's customers appear, it cannot create sub-partners, and its parent's API answers 404.
- **D.** Create a referral link, conversions, settings.
- **E.** Create the brand, apply a colour scheme (the logo is kept), the domains page.
- **F.** Plans show server prices; a product switched off for new sales leaves the sell form, and returns when switched on.
- **G.** Communication: one live channel, the others "not available", no password field, templates not editable.
- **H.** Invite a person, change their role; a finance member sees the money and no customers (API 403).
- **I.** Another partner's address shows "no access" and every one of its API reads answers 404.
- **J.** Every address the old portal served (21 of them) redirects permanently to the page that holds the feature now.
- **K.** API keys: create one (second factor), see its secret once, use it against the read-only partner API (scoped: a missing scope is 403), dismiss it and never see it again (page and list API), rotate it (the old secret then answers 401), revoke it. **K2.** A finance member cannot read the key list (403).
- **L.** Translations: word a string in French, and a markup value is refused by the server (400).
- **M.** Notification Center: unread / important / mark all read; Support Manager links are only https or mailto.
- **N.** Suspend and resume a team member.

`ONLY=A5,H2` runs only the journeys whose names start with those codes.

Not part of this check: a manual screen-reader pass (reported as **NOT EXECUTED**). Not part of CI.

**How to run it** (Windows; run from the repository root; in Git Bash set `MSYS_NO_PATHCONV=1`, and give paths as `C:/...`):

```
# 1. a scratch copy of frontend/ (never next to a running `next dev`): npm ci, then
#    BACKEND_ORIGIN=http://127.0.0.1:8011 npx next build, then `next start -H 127.0.0.1 -p 3999`
# 2. CHECK_PASSWORD=<a fresh random value for this run> DEMO_PASSWORD_FILE=<scratch>/demo-password.txt python scripts/partner_panel_browser_check/prepare.py
#    (drops, migrates and seeds w360_ppanel_fresh; prints PANEL_ID, RIVAL_ID, DEMO_ID, PANEL_TOTP)
# 3. backend on 127.0.0.1:8011 (see ../partner_setup_browser_check/README.md)
# 4. CHECK_PASSWORD=<the same value> NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> PANEL_ID=... RIVAL_ID=... DEMO_ID=... PANEL_TOTP=... \
#    (also export, for set_password.py: PYTHONPATH="backend;scripts/extension_browser_check" DJANGO_SETTINGS_MODULE=w360_extension_browser_settings W360_DB_NAME=w360_ppanel_fresh W360_DB_HOST=127.0.0.1 W360_DB_PORT=5442 W360_DB_DIRECT_HOST=127.0.0.1 W360_DB_DIRECT_PORT=5442 W360_DB_POOL_MODE=direct)
#    DEMO_PASSWORD_FILE=... PYTHON=<venv python> SET_PASSWORD_PY=scripts/partner_panel_browser_check/set_password.py \
#    OUT=<report.json> node scripts/partner_panel_browser_check/browser_check.js
python scripts/partner_panel_browser_check/prepare.py --drop
```

The journeys create rows (a customer, a sub-partner, a brand, a changed password), so re-create and re-seed before every run. The demo partner's temporary password is written to a file in the **scratch** directory only, for the one journey that needs it; it is never in the repository.

Running this alongside the full backend suite can exhaust Windows' ephemeral ports (the scratch backend opens a connection per request); run them one after the other.

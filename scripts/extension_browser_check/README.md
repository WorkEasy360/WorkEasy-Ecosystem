# Real-browser verification of the Phase 8 extension

The Phase 7/8 harness (`../phase8_browser_check/`), pointed at the extension's
screens: the organization console (dashboard, members, invitations, billing,
marketplace, security policy, audit, sign-ins), the Super Admin console (users,
plans & add-ons, pricing, payments, communication and the existing pages in the
new shell) and the public invitation page. Same checks - CSP violations, console
errors, axe WCAG 2.1 A/AA, visible focus, one `h1`, reflow at 320px - plus the
small-screen drawer, the landing rules (owner -> dashboard, member -> apps,
staff -> Super Admin) and one end-to-end payment: Pay -> step-up with a TOTP
code -> the development test checkout -> webhook -> invoice paid.

```
python scripts/rehearse_extension_upgrade.py --only fresh --keep        # w360_px_fresh
PYTHONPATH=backend DJANGO_SETTINGS_MODULE=config.settings.development \
  W360_DB_NAME=w360_px_fresh W360_DB_HOST=127.0.0.1 W360_DB_PORT=5442 W360_DB_POOL_MODE=direct \
  python scripts/extension_browser_check/seed.py          # prints the slug and OWNER_TOTP
# backend: the same environment, DJANGO_SETTINGS_MODULE=w360_extension_browser_settings
# (this folder on PYTHONPATH), runserver on a free port; frontend: a production
# build with BACKEND_ORIGIN pointing at it, `next start -p 3999`; then
ORG_SLUG=<slug> OWNER_TOTP=<secret> AXE_PATH=<axe.min.js> node browser_check.js
```

Not part of CI. Results are recorded in `docs/phase-8/extension/extension-status.md`.

# Real-browser verification of the Phase 8 screens

The Phase 7 harness (`../phase7_browser_check/`), pointed at the three Phase 8
screens: Settings -> AI, Settings -> Analytics, Super Admin -> AI. Same checks,
same rules - production build, real backend, scratch database, no dependency
added to the repository - and the same setup, with this `seed.py`:

```
python scripts/rehearse_phase8_upgrade.py --only fresh --keep          # w360_p8_fresh
PYTHONPATH=backend DJANGO_SETTINGS_MODULE=config.settings.development \
  W360_DB_NAME=w360_p8_fresh W360_DB_HOST=127.0.0.1 W360_DB_PORT=5442 W360_DB_POOL_MODE=direct \
  python scripts/phase8_browser_check/seed.py
# backend with ../phase7_browser_check/w360_browser_settings.py; frontend built
# with BACKEND_ORIGIN pointing at it and served on 3999; then
node browser_check.js
```

Results are recorded in `docs/phase-8/08-frontend.md`.

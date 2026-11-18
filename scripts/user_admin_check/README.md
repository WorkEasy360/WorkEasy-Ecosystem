# Real-browser check: the User Admin Panel (ADR-064)

What a real browser on the real host layout shows about the owner's brief of 11 November 2026: the customer portal's sign-in and dashboard after the two reference images, the seven menus with their sub-menus, the icon rail and phone drawer, the top bar (organization switcher, notifications, account menu), the three wallets kept apart, every User Admin page (one h1, no sideways scroll, axe clean), what a plain member and a Billing Manager see, and the Super Admin's Login as Guest beside View for Affiliate, Reseller and White Label (both exits, the filtered list restored, writes refused, impersonation refused for a partner user and a customer). The **production build** of the frontend, a **real backend** on a **scratch database** (`w360_uap_fresh`), headless Chrome with `puppeteer-core` from a scratch directory (never the repository). Not part of CI.

The seed (`seed.py`) uses the working tree's own services: the organization is created through onboarding, Easy CRM Advanced is bought for 10 users, the INR 10,000 Platform Wallet balance arrives through a real top-up order marked paid by staff (so this month's spend is real), usage is charged against the Consumption Wallet, and the three partners go through the real KYC flow.

```
# Windows, Git Bash, repository root. Ports 8011, 3999 and 8088 free. Never while a backend pytest run is going.
export MSYS_NO_PATHCONV=1
CHECK_PASSWORD=<fresh random> python scripts/user_admin_check/prepare.py      # prints SEED {...ids}
docker exec workeasy360-redis-1 redis-cli -n 12 FLUSHDB
PYTHONPATH="backend;scripts/user_admin_check" DJANGO_SETTINGS_MODULE=w360_uap_settings \
  W360_DB_NAME=w360_uap_fresh W360_DB_HOST=127.0.0.1 W360_DB_PORT=5442 W360_DB_DIRECT_HOST=127.0.0.1 \
  W360_DB_DIRECT_PORT=5442 W360_DB_POOL_MODE=direct W360_REDIS_URL=redis://127.0.0.1:6392/12 \
  W360_PARTNER_KYC_REQUIRED=true W360_PAYMENTS_FAKE_PROVIDER=true python backend/manage.py runserver 127.0.0.1:8011 --noreload
# frontend: a scratch copy, npm ci, BACKEND_ORIGIN=http://127.0.0.1:8011 next build, then
#   W360_{ADMIN,PARTNER,APP,AUTH}_ORIGIN=http://<portal>.workeasy360.com next start -H 127.0.0.1 -p 3999
node scripts/guest_login_check/edge.js 8088 8011 3999
NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> CHECK_PASSWORD=<same> IDS='<the SEED json>' \
  EDGE=127.0.0.1:8088 OUT=<dir> node scripts/user_admin_check/browser_check.js
python scripts/user_admin_check/prepare.py --drop
```

Screenshots land in `OUT`: `login-1440.png`, `login-390.png`, `dashboard-1440.png` (and `-full`, `-1024`, `-390`), the drawer and rail, the main pages, the three partner lists at 1280 px, the partner page header and the guest banner.

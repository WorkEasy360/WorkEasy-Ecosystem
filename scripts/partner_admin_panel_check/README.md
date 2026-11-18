# Real-browser check: the Partner Admin Panel (ADR-063)

What a real browser on the real host layout shows about the specification of 08 November 2026: the eight menus, onboarding before trading, Plan Activation paid from the Platform Wallet, an online top-up, the Store referral link, White Label as an add-on, Login as Guest with its reason, and the Admin's partner list. The **production build** of the frontend, a **real backend** on a **scratch database** (`w360_pap_fresh`) with **KYC required** (as in production), headless Chrome with `puppeteer-core` from a scratch directory (never the repository). Not part of CI.

The seed takes every partner through the real KYC flow (details, submit, review, approve) and activation, except the draft, and records the reseller's INR 1,000 as an offline top-up, as staff do.

Checks (59):

- **R** (reseller): the eight menus in order; KYC and account status side by side; the Platform Wallet beside the earnings; the quick actions; Plan Activation (customer, product, plan, the purchase price 999.00 less 20 %, the wallet after, confirm, the order); the wallet transaction naming the order and the customer; Customer Subscriptions with its four filters and "Your Platform Wallet"; an activation the wallet cannot pay offers Add Money; Add Money through the test provider's page and its signed callback credits the wallet once; phone (390 px) and tablet (768 px) layouts; no White Label settings without approval; Business & KYC; Sign out all other sessions; Help Content.
- **A** (affiliate): six menus; the Store referral link (`/r/<code>?to=store`) opens the Store; customer pages refused; its wallet API 403; another partner's wallet by editing the id 404.
- **W** (White Label only): no Customers, Platform Wallet or referral menus; the White Label tab; customer creation refused by the server.
- **D** (draft): lands on the onboarding dashboard; Dashboard, Help and Settings only; a commercial page says it opens once approved; Business & KYC and support open; the server refuses customer creation.
- **S** (Super Admin): the specification's list columns; Login as Guest asks for the reason, opens the Guest Session banner on `partner.`, refuses a write (`guest_read_only`) and returns to the Admin; New Partner asks for the Partner Email and, for White Label, which base permissions to grant; Settings shows the partner policies.
- No CSP violation, no console error and no unexpected API error anywhere; axe clean on every page it checks.

```
# Windows, Git Bash, repository root. Ports 8011, 3999 and 8088 free.
export MSYS_NO_PATHCONV=1
CHECK_PASSWORD=<fresh random> python scripts/partner_admin_panel_check/prepare.py     # prints SEED {...ids}
docker exec workeasy360-redis-1 redis-cli -n 12 FLUSHDB
# backend (W360_PARTNER_KYC_REQUIRED=true, the test payment provider on):
PYTHONPATH="backend;scripts/partner_admin_panel_check" DJANGO_SETTINGS_MODULE=w360_pap_settings \
  W360_DB_NAME=w360_pap_fresh W360_DB_HOST=127.0.0.1 W360_DB_PORT=5442 W360_DB_DIRECT_HOST=127.0.0.1 \
  W360_DB_DIRECT_PORT=5442 W360_DB_POOL_MODE=direct W360_REDIS_URL=redis://127.0.0.1:6392/12 \
  W360_PARTNER_KYC_REQUIRED=true W360_PAYMENTS_FAKE_PROVIDER=true python backend/manage.py runserver 127.0.0.1:8011 --noreload
# frontend: a scratch copy, npm ci, BACKEND_ORIGIN=http://127.0.0.1:8011 next build, then
#   W360_{ADMIN,PARTNER,APP,AUTH}_ORIGIN=http://<portal>.workeasy360.com next start -H 127.0.0.1 -p 3999
node scripts/guest_login_check/edge.js 8088 8011 3999
NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> CHECK_PASSWORD=<same> IDS='<the SEED json>' \
  EDGE=127.0.0.1:8088 OUT=<dir> node scripts/partner_admin_panel_check/browser_check.js
python scripts/partner_admin_panel_check/prepare.py --drop
```

The journeys activate a plan, spend and add money, so re-create the database before every run. Wait until the backend answers before starting the browser: a 502 from the edge means it is still starting.

**The test provider's page.** The development test provider's "hosted page" is the platform's own page on `app.`, which asks for a sign-in there (sessions are host-only). A real provider's page is on its own domain and confirms by its signed callback. The check therefore confirms the payment the way that page does (the test provider's public completion call, which takes the real signed-webhook path) and then follows the return address.

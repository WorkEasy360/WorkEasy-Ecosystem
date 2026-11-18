# Real-browser check: the Digital Store on its own host (ADR-065)

What a real browser on the real host layout shows about the owner's brief of 12 November 2026: the marketplace at `store.workeasy360.com/` (served, not redirected), the four menus and no cart, the old `app.workeasy360.com/store/...` addresses opening on the Store host, `robots.txt` and `sitemap.xml` per host, the product page (plans, renewal, setup requirements, the official app link, Coming Soon selling nothing), a checkout that pays **₹10,000 from the Platform Wallet and ₹18,320 through the (test) provider** for a ₹24,000 + 18% GST plan, comes back to the Store's confirmation and debits the wallet only once the payment is confirmed, My Account's single-use pass into User Admin, a Consumption Wallet recharge that credits that wallet only, Help, Partners, a published and an unpublished policy, the Store's own sign-in - and on every page one `h1`, axe WCAG 2.1 A/AA, no sideways scroll at 390 and 320 px, no CSP violation, no console error.

The **production build** of the frontend, a **real backend** on a **scratch database** (`w360_store_fresh`), headless Chrome with `puppeteer-core` from a scratch directory (never the repository). Not part of CI. The seed is the User Admin check's (`scripts/user_admin_check/seed.py`: the customer, its two organizations, wallets, Easy CRM in INR, the test provider) plus `store_seed.py` (EasyBook on the Store at ₹24,000 a year and a free plan, Easy Project Coming Soon, a credit pack and the AI-token rate, the Refund & Cancellation Policy published).

```
# Windows, Git Bash, repository root. Ports 8011, 3999 and 8088 free. Never while a backend pytest run is going.
export MSYS_NO_PATHCONV=1
CHECK_PASSWORD=<fresh random> python scripts/store_domain_check/prepare.py      # prints SEED {...} and STORE {...}
docker exec workeasy360-redis-1 redis-cli -n 13 FLUSHDB
PYTHONPATH="backend;scripts/store_domain_check;scripts/user_admin_check" DJANGO_SETTINGS_MODULE=w360_store_settings \
  W360_DB_NAME=w360_store_fresh W360_DB_HOST=127.0.0.1 W360_DB_PORT=5442 W360_DB_DIRECT_HOST=127.0.0.1 \
  W360_DB_DIRECT_PORT=5442 W360_DB_POOL_MODE=direct W360_REDIS_URL=redis://127.0.0.1:6392/13 \
  W360_PARTNER_KYC_REQUIRED=true W360_PAYMENTS_FAKE_PROVIDER=true python backend/manage.py runserver 127.0.0.1:8011 --noreload
# frontend: a scratch copy, npm ci, BACKEND_ORIGIN=http://127.0.0.1:8011 next build, then
#   W360_{ADMIN,PARTNER,APP,AUTH,STORE}_ORIGIN=http://<portal>.workeasy360.com next start -H 127.0.0.1 -p 3999
node scripts/store_domain_check/edge.js 8088 8011 3999
NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> CHECK_PASSWORD=<same> IDS='<the SEED json>' \
  EDGE=127.0.0.1:8088 OUT=<dir> node scripts/store_domain_check/browser_check.js
python scripts/store_domain_check/prepare.py --drop
```

`edge.js` stands in for the production edge: every host on one port with the Host header kept, `/api/*` to the backend, the Store host's `/sitemap.xml` to the API's sitemap (as the Caddyfile's Store site does), everything else to the frontend. The journeys buy and recharge, so re-create the database before every run. Screenshots land in `OUT`: `home-1440`, `home-390`, `home-390-menu`, `product-1440`, `checkout-signed-out-1440`, `checkout-1440`, `checkout-390`, `complete-1440`, `recharge-1440`, `policy-unpublished`, `sign-in-1440`, `my-account-app`.

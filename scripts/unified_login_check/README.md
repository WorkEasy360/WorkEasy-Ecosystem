# Real-browser check: same account or a different one (ADR-066)

What a real browser on the real host layout shows about the unified-login brief (13 November 2026):

1. **Ask once** - signed in for one product, the next product shows "Welcome to WorkEasy360 / How would you like to continue? / Currently signed in as" with the name, email and organization, and Continue with Same Account / Use Different Account / Create New Account; Continue opens it; opening either product again in the same sign-in asks nothing.
2. **Use Different Account** - the central sign-in is signed out, its page opens for the same request, and the product opens as the other person.
3. **Create New Account** - signed out, the Store's sign-up opens with `next=/store/product/easybook`.
4. **Switch Account** from User Admin's account menu (My Profile, Switch Organization, Switch Account, Account & Security, Logout) - User Admin *and* the central sign-in are signed out, person-bound browser storage is forgotten (layout kept), the sign-in page says why, the next product asks for a password, and a forged hand-over only opens the sign-in page.
5. **Changing the sign-in email** - Account & Security names the address and the sign-in methods, the popup sends the link and changes nothing yet; the emailed link's page confirms on a click and the new address signs in.
6. **Sign-in preferences** - the default reads "Ask the first time I open each product after signing in".

On every audited page: one `h1`, axe WCAG 2.1 A/AA, no sideways scroll at 390 and 320 px; across the run: no CSP violation, no console error, no API 5xx.

The **production build** of the frontend, a **real backend** on a **scratch database** (`w360_ulogin_fresh`), every portal host behind `scripts/store_domain_check/edge.js`, headless Chrome with `puppeteer-core` from a scratch directory (never the repository). Easy CRM's and EasyBook's callbacks (`https://crm.workeasy360.com/...`, `https://book.workeasy360.com/...`, their registered addresses) are answered inside Chrome by request interception, so nothing leaves the machine and no product server is needed - the check is about Workeasy360's side of the sign-in. Not part of CI.

The seed is the Digital Store check's (the User Admin seed, then the Store's) plus `unified_seed.py`: both products connected with `connect_product_sso` and a throwaway key, both held by "UAP Beta Labs" with seats for its owner (Manoj Kumar) and a member (Priya Patel), and a change-of-email link for Neha Sharma.

```
# Windows, Git Bash, repository root. Ports 8011, 3999 and 8088 free. Never while a backend pytest run is going.
export MSYS_NO_PATHCONV=1
CHECK_PASSWORD=<fresh random> python scripts/unified_login_check/prepare.py     # prints SEED {...} and ULOGIN {...}
docker exec workeasy360-redis-1 redis-cli -n 13 FLUSHDB
PYTHONPATH="backend;scripts/store_domain_check;scripts/user_admin_check" DJANGO_SETTINGS_MODULE=w360_store_settings \
  W360_DB_NAME=w360_ulogin_fresh W360_DB_HOST=127.0.0.1 W360_DB_PORT=5442 W360_DB_DIRECT_HOST=127.0.0.1 \
  W360_DB_DIRECT_PORT=5442 W360_DB_POOL_MODE=direct W360_REDIS_URL=redis://127.0.0.1:6392/13 \
  W360_PARTNER_KYC_REQUIRED=true W360_PAYMENTS_FAKE_PROVIDER=true python backend/manage.py runserver 127.0.0.1:8011 --noreload
# frontend: a scratch copy, npm ci, BACKEND_ORIGIN=http://127.0.0.1:8011 next build, then
#   W360_{ADMIN,PARTNER,APP,AUTH,STORE}_ORIGIN=http://<portal>.workeasy360.com next start -H 127.0.0.1 -p 3999
node scripts/store_domain_check/edge.js 8088 8011 3999
NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> CHECK_PASSWORD=<same> IDS='<the ULOGIN json>' \
  EDGE=127.0.0.1:8088 OUT=<dir> node scripts/unified_login_check/browser_check.js
python scripts/unified_login_check/prepare.py --drop
```

The journeys sign people out, spend the change-of-email link and change Neha's address, so re-create the database before every run. Screenshots land in `OUT`: `chooser-1440`, `chooser-390`, `account-menu-1440`, `switched-login-1440`, `change-email-popup`, `confirm-email-1440`; `report.json` holds every check and page audit.

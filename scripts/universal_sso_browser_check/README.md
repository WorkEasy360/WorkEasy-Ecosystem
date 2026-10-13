# Real-browser verification: user-controlled universal product sign-in (all four products)

ADR-044, `docs/universal-sso/design.md`. The results are recorded in `docs/universal-sso/status.md`.

Same rules as the earlier checks:
- production builds of every frontend (`next build`, then `next start`);
- real backends and scratch databases;
- the installed Chrome;
- no new dependency in any repository (`puppeteer-core` lives in a scratch directory).

**Six applications, each with its own host.** Workeasy360 and all four products run side by side, each with its real OIDC client. A fifth product is registered during the run.

| Application | Browser origin | Backend | Sign-in mode in this run |
|---|---|---|---|
| Workeasy360 (the identity provider) | `http://127.0.0.1:3021` | Django `127.0.0.1:8021` (the issuer) | - |
| Easy CRM | `http://localhost:4022` | Django `127.0.0.1:8022` | `sso_optional`, so its password sign-in can show "Prefer the product's own sign-in" (production: `sso_only`) |
| EasyBook | `http://127.0.0.2:3023` | Django `127.0.0.1:8023` | `sso_only` |
| Future Product | `http://127.0.0.3:5024` | `future_product.py`, a stdlib relying party | `sso_optional`, registered in the Super Admin console during journey 5 |
| Easy Project | `http://127.0.0.4:8024` | the same jar (Spring Boot serves the SPA and the API) | `sso_only` |
| Easy Help Desk | `http://127.0.0.5:3025` | NestJS `127.0.0.5:4025` + its BullMQ worker | `sso_only` |

Cookies are scoped by host name, so each application has its own jar. The products keep their production hardening; where a production check refuses plain http (Easy Project's startup check, the Help Desk's `NODE_ENV=production`), the run uses the product's own development mode with the security settings set explicitly: Secure cookies (loopback is a secure context to Chrome), SSO-only, the real OIDC client.

**The re-check window is 30 s here.** The scratch Workeasy360 answers `revalidate_after = 30` (`w360_usso_browser_settings.py`). Every product honours the platform's cadence, so "an open session loses access within the window" is observed in real time, with no hook inside any product.

**Journeys** (`browser_check.js`, `ONLY=1,...,15` runs a subset; 5, 6, 10 and 12 need a fresh `prepare.py`; 10 runs after 6, 15 after 10; 12 suspends Ben, so it runs last):
1. **AUTO**: Automatic (the platform default since the one-login brief, `docs/universal-sso/one-login.md`). All four products open signed in with no chooser and no second password, and each keeps its own session.
2. **ASK**: "Reset all to default" stores nothing, which means Automatic; Ben then chooses "Ask me each time". The chooser appears for Easy CRM (both ways in) and for Easy Project and Easy Help Desk (Workeasy360 only), and each opens.
3. **REMEMBER**: "Remember my choice for Easy CRM" means no chooser next time; EasyBook still asks (Workeasy360 only).
4. **CHANGE**: product-only sign-out of Easy CRM; preferring its own sign-in opens its sign-in page; changing back signs in through Workeasy360.
5. **FUTURE**: a product registered in the console follows the person's existing default with no code change.
6. **TRIAL**: all four 7-day trials; each opens through Workeasy360; the controlled clock ends them; no Open, the launch is refused, Explore offers Upgrade / Buy now; each product's open session is refused within the window; a new sign-in from each product is refused (`not_entitled`).
7. **SWITCH**: A signs out of Workeasy360 and B signs in: each of the four products then holds B. "Use another Workeasy360 account" on the chooser.
8. **LOGOUT**: product-only sign-out keeps Workeasy360 signed in; "and Workeasy360" asks at Workeasy360 first, then ends both and leaves the browser on the product's sign-in page (all four).
9. **REAUTH**: Easy CRM's "Confirm it's you".
10. **PAID** (after 6): all four bought through checkout; each opens on the paid plan with the same federated account as during the trial, and serves the organization again within a minute.
11. **OVERRIDE**: global Ask with per-product overrides (Easy CRM, EasyBook, Easy Help Desk Automatic; Easy Project Ask), each verified.
12. **REMOVED**: Ana suspends Ben in Workeasy360; each of the four products refuses his open session within the window (the per-person entitlement question).
13. **PRODUCT_LOGIN**: "Prefer each product's own sign-in": the settings page explains that EasyBook, Easy Project and Easy Help Desk only use Workeasy360 and offers no option they lack; they are asked about; Easy CRM opens its own page.
14. **DIRECT** (the one-login brief's journey B): signed in to Workeasy360 only, with nothing stored, Ana types each product's address. The product starts the sign-in itself, Workeasy360 reuses its session, and the product opens signed in: no product sign-in page (not even for a moment), no password, no chooser. A deep link survives. After "Sign out of <product>" the address shows the sign-in page instead of signing her straight back in, and one click there signs her in. With no Workeasy360 session, the central sign-in comes first. Easy CRM starts by itself only with its own password sign-in off, so it is checked in **pass 2** (below); in pass 1 it shows both ways in.
15. **CROSS-ORG** (after 10): Cara asks each product for Customer Company, which is not hers, and is refused with no session. Her own sessions are bound to Trial Company and show no one from Customer Company.

On every audited page: CSP violations, console errors, axe WCAG 2.1 A/AA, a visible focus indicator, and exactly one `h1`. Reflow is checked at 1280, 768 and 375 px. CSP reports on the products' pages are listed separately from Workeasy360's: they are the products' own policies. **A manual screen-reader pass is not part of this check.**

**How to run it** (Windows, Git Bash, `USSO_WORK` = a scratch directory, `MSYS_NO_PATHCONV=1`; Docker running):

```
python scripts/universal_sso_browser_check/prepare.py     # keys; w360_usso_fresh, keel_usso, easybook_usso; this check's own
                                                          # containers usso-project-pg, usso-helpdesk-pg, usso-helpdesk-redis;
                                                          # project_usso, helpdesk_usso (Prisma migrate deploy); seed
# production builds in copies under $USSO_WORK, never in the working trees:
#   w360-fe (BACKEND_ORIGIN=http://127.0.0.1:8021), crm-fe (API_INTERNAL_ORIGIN=http://127.0.0.1:8022),
#   easybook-fe (API_BASE_URL=http://127.0.0.1:8023/api/v1),
#   helpdesk-fe (NEXT_PUBLIC_API_URL=http://127.0.0.5:4025/api/v1; on Windows without symlink rights drop
#   `output: 'standalone'` in the COPY - the product's Docker image builds standalone on Linux);
#   Easy Project: `mvnw package` in its repository, jar copied to $USSO_WORK/project-module.jar (JAVA_HOME = a JDK 21);
#   Easy Help Desk API and worker: `pnpm -r build` in its backend (dist/main.js)
python scripts/universal_sso_browser_check/serve.py w360   # and crm, easybook, project, helpdesk, helpdesk-worker,
                                                           # w360-fe, crm-fe, easybook-fe, helpdesk-fe: one each
NODE_PATH=$USSO_WORK/pptr/node_modules AXE_PATH=<axe.min.js> PY=<python> REPO=<repo root>   BACKEND_ENV="$(python scripts/universal_sso_browser_check/print_backend_env.py)"   node scripts/universal_sso_browser_check/browser_check.js        # writes $USSO_WORK/report.json
python scripts/universal_sso_browser_check/prepare.py --drop      # afterwards (also removes this check's containers)
```

**Pass 2 (Easy CRM in production's mode).** After pass 1, stop the `crm` server (by its PID), start it again with `USSO_CRM_SSO_ONLY=1` in the environment (`serve.py crm` reads it through `prepare.crm_env`), and run `ONLY=14 USSO_CRM_SSO_ONLY=1 node .../browser_check.js` with `OUT=<another report>`. Journey 14 needs no fresh `prepare.py`.

Scratch databases and this check's own containers only: never a development or test database.

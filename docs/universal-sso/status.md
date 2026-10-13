# One Workeasy360 login for every product: final status

**Written for: the product owner deciding on release, and the engineers and operators who deploy the five applications.**

Brief: "WORKEASY360 - ONE USER LOGIN FOR ALL PRODUCTS / UNIVERSAL PRODUCT SSO" (2026-10-12/13). What this round changed is in `one-login.md`, and ADR-044 has new items 14-16. It builds on the earlier records `status-completion-run.md` and `status-adr044-run.md`.

**Nothing is committed** in any of the five repositories. Each repository's changes sit in its working tree on the branch the earlier work was committed to:

| Repository | Branch |
|---|---|
| Workeasy360 | `master` |
| AI CRM | `feat/android-twa` |
| EasyBook | `p0-remediation` |
| Project-Module | `workeasy360-sso` |
| helpdesk-workeasy | `workeasy360-sso` |

## Verdict

**ONE LOGIN FOR ALL PRODUCTS: PASS**

**Can a user now log into Workeasy360 once using one Workeasy360 account and open every entitled Workeasy360 product without entering those credentials again, while each product independently validates Workeasy360 identity and entitlement through the standard OIDC integration? YES**

## Matrix

|                      | CRM | BOOK | PROJECT | HELPDESK |
|---|---|---|---|---|
| Central login        | PASS | PASS | PASS | PASS |
| Direct product SSO   | PASS | PASS | PASS | PASS |
| No second password   | PASS | PASS | PASS | PASS |
| OIDC validation      | PASS | PASS | PASS | PASS |
| Trial access         | PASS | PASS | PASS | PASS |
| Trial expiry denial  | PASS | PASS | PASS | PASS |
| Paid access          | PASS | PASS | PASS | PASS |
| Entitlement refresh  | PASS | PASS | PASS | PASS |
| Account switch       | PASS | PASS | PASS | PASS |
| Logout               | PASS ¹ | PASS ¹ | PASS ¹ | PASS ¹ |
| Cross-org isolation  | PASS | PASS | PASS | PASS |

1. Both of each product's sign-outs work in the real browser: the product only, and "the product and Workeasy360". Signing out of Workeasy360 anywhere else does not end the product sessions, because there is no front- or back-channel logout. What ends them is removal, suspension or seat loss, within the re-check window (section 11).

Every cell rests on the real-browser run (section 14) and on each product's own tests (section 15). Nothing is marked PASS by association.

## Report

### 1. Workeasy360 central authentication
- One identity provider. The person signs in once at Workeasy360 (password, plus MFA where policy requires it). Every product is an OIDC client of it: authorization code + PKCE S256 + `private_key_jwt`.
- **The default preference is now Automatic** (ADR-044 item 14). Click Open, or type the product's address, and the product opens. "Ask me each time" and "Prefer the product's own sign-in" are still offered, and stored choices are kept. The preference is applied after every access check, so it never grants anything. Reverting is a one-line change.
- **A request that names no organization means the person's only organization** (ADR-044 item 16). The first browser run found that typing a product's address led to Workeasy360's organization picker even for someone with one organization. That organization is now used and checked exactly like a named one. Someone with several organizations is still asked, never guessed for.
- The independent review of that change found one P2, and it is fixed:
  - The implied organization is never stored on a parked request. Otherwise "Use another Workeasy360 account" would have handed the first person's organization to the next person.
  - A resume by a session that was already signed in when the request was parked now applies the person's preference.

### 2-5. Each product's sign-in
| | Easy CRM | EasyBook | Easy Project | Easy Help Desk |
|---|---|---|---|---|
| Client | Django views under `/api/auth/...` | BFF route handlers + Django `sso` app | Spring Security OAuth2 login | NestJS module + `@digisoft/workeasy360` |
| Opened from My Products | silent (journey 1) | silent | silent | silent |
| Opened by typing its address | its own start, then Workeasy360 reuses the session (journey 14, pass 2) | same, server-side in its proxy | same, in the SPA's route guard | same, in the agent app's session bootstrap |
| Its own session | Django session, `__Host-` cookies | `__Host-` token cookies (**renamed this round**) | server HttpSession, `__Host-` | `__Host-` refresh cookie (**renamed this round**) |

### 6. Direct-product login behaviour (integration guide §6b)
- **How it works.** An SSO-only product that gets a page visit with no session of its own goes straight to its own start endpoint. Workeasy360 then does one of two things:
  - completes silently: the person's only organization, the default Automatic;
  - shows its central sign-in when it has no session, and the product opens after it.
- **It never starts by itself:**
  - on the product's own sign-in page (including after a refusal);
  - after a sign-out of the product: a marker cookie holds until the next sign-in;
  - within 60 seconds of a previous automatic start (the loop guard).
- **Unchanged:**
  - API calls are never redirected;
  - a session whose access ended keeps its explanation;
  - a product whose own password sign-in is on still shows both ways in (journey 14, pass 1).
- **Proven in the browser for all four:**
  - typed root address;
  - deep link (`/contacts`, `/sales/invoices`, `/#/settings`, `/settings/profile`);
  - no sign-in page, not even for a moment;
  - after "Sign out of X", the sign-in page (no silent sign-in), then one click to sign in again;
  - with no Workeasy360 session, the central sign-in, then the product.

### 7. Password isolation
- A product never sees a Workeasy360 password: the password is typed only on Workeasy360's origin, and products receive a code.
- One person model holds a password. One endpoint takes a password to sign in (`test_one_login_guards.py`).
- Accounts created or linked through Workeasy360 get no usable local password, or have it retired.
- In SSO-only mode each product refuses password sign-in, reset and registration.

### 8. Entitlement enforcement
- Workeasy360 checks membership, organization status, access policy, entitlement and seat before any code is issued. The token endpoint checks them again.
- Each product re-checks entitlement per person on Workeasy360's cadence (15 minutes in production, 30 seconds in the browser run).
- A known denial never lapses into access during an outage.
- New guards: the default preference and an implied organization each meet every refusal an explicit choice meets (entitlement, seat, another organization, a locked organization, a product not ready).

### 9. 7-day trial enforcement
Journey 6, for each product on its own:
- the trial opens through Workeasy360 with no question;
- after `ends_at`, Workeasy360 offers no Open, refuses the launch, and Explore says "Trial expired" with Upgrade / Buy now;
- the product's open session is refused within the window;
- a new sign-in is refused (`not_entitled`).

Journey 10:
- after checkout, each product opens with **the same federated account** as during the trial;
- it serves the organization again within a minute.

### 10. Account switching
Journey 7, now covering all four products, EasyBook included: A signs out of Workeasy360, B signs in, and each product holds B and nothing of A. "Use another Workeasy360 account" on the chooser gets the next person their own organization (a new test).

### 11. Logout
Journey 8, all four products:
- "Sign out of X" ends only the product's session; Workeasy360 stays signed in.
- "Sign out of X and Workeasy360" asks at Workeasy360 first, ends both, and leaves the browser on the product's sign-in page. Easy Project's sign-out address is its root, and its marker is what holds.

Limits, stated in `one-login.md` §4:
- there is no single sign-out across products;
- a product-only sign-out lasts one browser session;
- a product's own "sign out every device" does not lock out someone still signed in to Workeasy360. Removal in Workeasy360 does.

### 12. Future-product extensibility
- Journey 5: a Super Admin registers "Future Product" in Product Manager, Authentication. The console shows "Integration required" until the product has a key, then "Enabled", with the Client ID, redirect and sign-out URIs and the mode.
- That product (a separate relying party) then signs a person in with their existing default. Nothing in the platform changed for it.
- Guards check that no sign-in source in Workeasy360 names a product.
- The fixture lives only in the scratch database, which was dropped after the run.

### 13. Security findings
- **Independent audits:** five read-only Opus reviews, one per product change plus the Workeasy360 organization change.
- **P0: none. P1: none.**
- **P2 (all fixed and re-tested):**
  - **EasyBook** (already in HEAD): its auth cookies had no `__Host-` prefix. A compromised sibling subdomain could plant its own sign-in flow cookie, which would be a login CSRF past the state check. Every EasyBook auth cookie is now `__Host-` under Secure, and plain-named copies are ignored. Help Desk had the same class of problem: its refresh and flow cookies are now `__Host-` too. Easy CRM and Easy Project already used `__Host-` names or kept the flow server-side.
  - **Workeasy360:** the implied organization travelled with "Use another Workeasy360 account" (section 1).
- **P3 fixed:**
  - the sign-out marker is written before the sign-out request (Easy Project, Easy Help Desk), so a race cannot become a silent sign-in;
  - the marker is kept when a sign-out ends unclearly (Easy CRM, Easy Help Desk);
  - `__Host-` marker names;
  - automatic starts use `location.replace` (no Back-button trap);
  - every "Signing you in" screen gets a way out after 5 seconds and is announced to screen readers;
  - Easy CRM and EasyBook now check `azp`;
  - Easy CRM requires `product_code`;
  - an empty product code stops Easy CRM and EasyBook from starting;
  - EasyBook's start endpoint has its own throttle;
  - Help Desk logs drop the query of every `/api/auth` request;
  - organization source in Workeasy360's success log;
  - a resume re-applies the person's preference.
- **P3 accepted and documented:**
  - several tabs starting at once can show "sign-in expired" once;
  - the marker is a convenience, not a control;
  - a narrow double organization question for someone in several organizations, set to Ask, opening a product that named none;
  - the dev-only `braces` advisory.

### 14. Browser journeys
Production builds of every frontend, real backends, scratch databases, the installed Chrome. Six applications, each on its own host. The scratch Workeasy360 answers `revalidate_after = 30 s`.
- **Pass 1** (fresh scratch state, all 15 journeys, Easy CRM `sso_optional` for the journeys about its own sign-in): **102 of 102 checks passed.**
  - 30 pages audited: 0 axe (WCAG 2.1 A/AA), focus or `h1` findings;
  - 0 reflow overflow at 1280/768/375 px;
  - 0 CSP violations (Workeasy360's and the products' pages);
  - 0 console errors.
- **Pass 2** (Easy CRM restarted SSO-only, production's mode; journey 14): **21 of 21 passed**, Easy CRM's five direct-visit checks included.
- **New this round:**
  - journey 14 DIRECT (the brief's journey B and DIRECT URL JOURNEY);
  - journey 15 CROSS-ORG: Cara asks each product for Customer Company and is refused with no session; her own sessions show no one from Customer Company;
  - EasyBook in journeys 7 and 8;
  - journey 2 starts from an explicit Ask;
  - journey 6 asserts that a person who never chose is not asked.
- **What the first run found:**
  - the organization picker on a typed address (fixed in Workeasy360);
  - the harness not finishing EasyBook's first-time fiscal-year step before using its account menu (fixed in the harness).

### 15. Final test counts (after each repository's last change)
| Repository | Backend | Frontend | Other gates |
|---|---|---|---|
| Workeasy360 | full bare pytest: **4,269 passed, 0 failed, 0 errors, 0 skipped** (1 h 07 min, after the last change) | vitest 774 / 774 (40 files); typegen, tsc, eslint, `next build` | ruff + format (721 files), mypy, import contracts 18 kept, bandit, pip-audit, `check`, `makemigrations --check`; `npm audit --omit=dev` 0 |
| Easy CRM | 1,039 passed, 0 failed, 0 skipped | vitest 327 (50 files); tsc, eslint, `next build` | ruff, format, mypy (363 files), bandit, pip-audit, `makemigrations --check`; `pnpm audit`: `braces` only |
| EasyBook | 1,929 OK; 5 skipped, explained: 1 POSIX-only, 4 DB-bootstrap (run separately: 6 OK) | vitest 549 (50 files); typegen + tsc, eslint `--max-warnings 0`, `next build` | ruff, bandit, pip-audit, `check`, `makemigrations --check`; `npm audit --omit=dev` 0 |
| Easy Project | Maven `package`: 1,116 run, 0 failures, 0 errors, 0 skipped | vitest 348 (37 files); `tsc -b && vite build` | oxlint 0 errors (7 old warnings); `npm audit` 0 |
| Easy Help Desk | unit: api 244, worker 31, `@digisoft/workeasy360` 115, channels 67, analytics 16, ai 19, engine 24; API e2e 364 (24 suites, real PostgreSQL + Redis) | typecheck, lint 0 errors; Playwright 136 against the Docker-built production images | all three Docker images build; `check-migrations.sh` OK; `pnpm audit`: `braces` + moderates |

**Notes:**
- Under the machine's load (four products' gates at once), some runs had transient failures, and each passed on a quiet re-run of the module and then of the full suite:
  - Easy CRM: statement timeouts;
  - Help Desk: a lost connection, and a disabled register button;
  - Workeasy360's previous full run: a PgBouncer "server conn crashed?" error, and a 5.6 s phase-10 latency bound.
- EasyBook's Playwright specs were updated for the cookie names but not run. They need a backend and browsers, and they are not part of its gate set.

### 16. Remaining blockers (outside the code)
- **Deploy configuration:** set the `WORKEASY360_*` variables and register each product with `connect_product_sso`, as in the previous record. Every product refuses to start in production without them.
- **One-time sign-out:** EasyBook and Easy Help Desk users sign in once more after the deploy, because their auth cookie names changed. This happens silently, through Workeasy360.
- **No back-channel logout:** global sign-out across products does not exist (ADR-044). It was not built.
- **My Products:** a product the organization does not have shows "Not activated" with "Explore products", and the trial or purchase starts there (journey 6). The brief's mock shows "Start 7-Day Trial" on My Products itself; that is one click further here.
- **D-035** (sign-in on a partner's custom domain) is unchanged.
- **Nothing is committed.**

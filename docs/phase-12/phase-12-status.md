# Phase 12: production hardening, SSO completion and go-live readiness (status)

**Date:** 2026-09-30. **Scope:** the Phase 12 brief (Steps 1–65). No new business features were built. **Nothing was committed, pushed or deployed to any real environment.** Every result below was produced on this machine against the repository tree, a local test database, and a production-shaped staging stack (`deploy/compose/production.yml` + `deploy/staging/compose.staging.yml`: production settings, TLS edge, PgBouncer, RabbitMQ, Redis, S3-compatible store, SMTP catcher, private DNS, Prometheus). Nothing is claimed that was not observed.

## PHASE 12 PRODUCTION READINESS: **FAIL**

> *Is Workeasy360 now technically ready for controlled production go-live with Easy CRM, EasyBook, Easy Project and Easy Help Desk while preserving the platform's security, isolation, commercial integrity and independent-product architecture?*
>
> **NO.**

**Why not.** Everything found *inside the application code* was fixed and re-verified, including the findings of an independent final audit (§2). The blockers that remain are outside it: the products, the providers, and the operations around a deployment that has never happened.

1. **No product can sign a user in with Workeasy360 (SSO).** None of the four products has an OIDC client. The platform's provider is complete and tested per product; the products' callbacks answer 404.
2. **Easy Project authenticates by client-supplied `X-User-Id` / `X-Org-Id` headers** ("There is no password", from its public bundle). This is P0 for that product.
3. **Easy CRM's host fails the TLS handshake**, and three of the four products live on `<IP>.sslip.io` names that follow whoever next holds the IP address.
4. **Production has never been deployed.** `workeasy360.com` answers `301` to itself forever, and no platform host resolves.
5. **No real email provider and no real payment provider** are configured anywhere. Email readiness is BLOCKED; payments are TEST/MANUAL only.
6. **The operations a deployment needs are not in place.** Nothing schedules the backups or WAL retention, no copy leaves the host, and the production topology runs no Prometheus, Alertmanager or pager route (OPS-2, OPS-3). The scripts and alert rules exist and were proven on staging.

What would change the answer to YES is in §4, in order.

---

## 1. The 42 report items

### 1. Final backend tests
**PASS: 3,306 passed, 0 failed, 0 skipped** (57 min, full suite, one run on the final tree after the last code change). One warning: structlog's notice about exception formatting in one test's console output. No test was skipped or marked expected-to-fail.

Earlier full runs, each followed by fixes, before this one:
- **3,257 tests:** 5 failures, all regressions of Phase 12's own changes (G-45, G-46).
- **3,271 tests:** 1 failure, a test that expected Django's HTML CSRF page (B-06).

After the final audit's fixes, this run was repeated in full; no incremental result is relied on. Static gates on the final tree:

| Gate | Result |
|---|---|
| ruff check / ruff format --check | pass / 659 files formatted |
| mypy (`contracts/`, strict) | no issues, 29 files |
| lint-imports | 18 contracts kept, 0 broken |
| bandit -ll | no issues |
| pip-audit --strict | no known vulnerabilities |
| `manage.py check` / `check --deploy` / `makemigrations --check` | no issues / only the 6 W360.W010 warnings for the three `sslip.io` product hosts (staging, rc5) / no changes |

### 2. Final frontend tests
**PASS**, from a clean install of the final tree, in a scratch copy (CI's order):

| Gate | Result |
|---|---|
| `npm ci` (clean `node_modules`, no `.next`) | 440 packages, exit 0 |
| `npm run typecheck` (`next typegen && tsc --noEmit`) | 0 errors |
| `eslint` | 0 problems |
| `vitest` (full) | **388 / 388 passed**, 23 files; includes axe per screen |
| `next build` (production) | exit 0 |
| `assert-dynamic-rendering` | no route statically prerendered, so every page carries a CSP nonce |
| `npm audit` | 0 vulnerabilities |

### 3–6. Easy CRM, EasyBook, Easy Project, Easy Help Desk

The platform side of every product is verified the same way:
- **Automated:** registration from the real `infra/ecosystem/products.json` manifest; OIDC discovery, authorize, token, userinfo and entitlement verification per product, on 32 tests (`tests/phase12/test_12_sso_per_product.py`).
- **In a real browser:** catalog, trial, purchase, entitlement, dashboard, launch, suspend, resume, cancel, reseller, commission and white-label (item 9).

What differs is the product itself, verified from outside:

| | Easy CRM | EasyBook | Easy Project | Easy Help Desk |
|---|---|---|---|---|
| Registered host | `3-110-142-102.sslip.io` | `65-1-134-3.sslip.io` | `ops.workeasy360.com` | `13-200-218-28.sslip.io` |
| HTTPS reachable | **No: TLS handshake fails** | yes | yes | yes |
| OIDC client (`/api/auth/callback/workeasy360`) | **404** | **404** | **404** | **404** |
| Own authentication | not observable (TLS) | own login | **`X-User-Id` / `X-Org-Id` headers, no password (P0)** | own login |
| Host stable (not wildcard DNS) | no | no | yes | no |
| **Result** | **BLOCKED** (TLS, no SSO, sslip.io) | **BLOCKED** (no SSO, sslip.io) | **FAIL** (header authentication, P0) and BLOCKED (no SSO) | **BLOCKED** (no SSO, sslip.io) |

The platform can launch each product (a navigation to its registered launch URL with `organization=<uuid>`, no token in the URL). It cannot vouch for what the product does after that.

### 7. OIDC/SSO status per product
**Platform provider: PASS.** It covers authorization code + PKCE S256; exact redirect URIs with no wildcards and no query-token authentication; `private_key_jwt` and client-secret authentication; JWKS with key rotation and a safe overlap (item 16); and the organization's session policy (MFA required, IP allow-list, session age, sign-out) judged at `/authorize` **and again at `/token`**.
- The second check at `/token` is **G-10**: before Phase 12, any organization policy made every token exchange fail.
- A suspended organization is read-only for products (G-24, G-45).
- Back-channel logout does not exist yet (D-039). It is required before the first product goes live with SSO.

**Per product: BLOCKED for all four**, since none has an OIDC client (X-01). No bypass was added: no shared cookies, no header trust, no query tokens.

### 8. D-035 final decision
**Decision B: authentication stays on the canonical host** ([ADR-039](../architecture/adrs/ADR-039-white-label-domains-keep-authentication-canonical.md), threat model [02-d035-threat-model.md](02-d035-threat-model.md)).
- A partner's custom domain serves a branded landing page with **no form, no input, no password field**.
- "Continue to sign in" goes to the canonical sign-in page, which wears the brand (`?brand=<host>`, looked up server-side, validated).
- Every other path on the custom host redirects to the same path on the canonical host. The API on a custom host answers only the branding lookup (404 otherwise).
- No wildcard `CSRF_TRUSTED_ORIGINS`, no wildcard hosts. TLS is issued on demand only for an active, verified domain.
- Verified in real Chrome (item 9). D-035 is closed by decision.

### 9. Browser E2E
**PASS: 83 of 83 flows** on release candidate 3, and again on the final release candidate 5. Real Chrome drove staging over HTTPS, with TLS pinned to the staging CA and no certificate override (`scripts/phase12_browser_check/`).
- **Customers, per product (Step 8):**
  - public catalog and the product page's trial, then sign-up and verification by the emailed link;
  - trial badge, "Assign a seat", launch;
  - Explore → Buy → the TEST provider's hosted page → Active;
  - Super Admin suspends (read-only, launch refused with a plain reason), then reactivates;
  - the customer **cancels in the UI** at period end and keeps access until then.
- **Store card:** the trial starts from `/products` with no checkout.
- **Organization with all four products** (Step 9), including a keyboard-only checkout.
- **Reseller (Step 10):** created in the wizard with two products; provisions a customer by owner invitation; sells; a product it lacks is refused; commissions accrue at 15 %; after termination the partner loses access and the customer keeps its products.
- **White-label (Step 11):** two partners on two custom domains; PNG logos uploaded and displayed; DNS verification; D-035 behaviour as in item 8, and no cross-partner bleed.
- **Staff second factor (D-037):** the gate appears, the API refuses before verification and allows after, and a new session is gated again.
- **CSRF and CSP:** item 13.

Release candidate 2 had passed 77 of 82. Its findings (B-01…B-08 in [01-audit-gap-list.md](01-audit-gap-list.md)) were fixed and each re-verified here.

**Not executed:**
- loading the real product sites: navigations are intercepted by design, and only the target URL is checked;
- product-side SSO;
- a staff enrolment from no factor (the only staff account already had one);
- a staff step-up after 15 minutes;
- the organization-scoped integration-registry routes and `/healthz/deep` under D-037. The harness does not visit them; backend tests cover them (G-60).

**Email on release candidate 5:** every message the journeys caused was read back from Mailpit: 23, each once, none missing.

### 10. Accessibility
**PASS at automated-browser level.**
- **axe-core (WCAG 2.1 A/AA):** 89 page audits on release candidates 3 and 5, **0 violations**. They cover public, sign-in, organization, checkout and the hosted payment page, billing with the cancel confirm open, members, the MFA gate, admin, the partner portal, every wizard step and both branded landings.
- **Focus and structure:** 0 pages with invisible focus; exactly one `h1` per page.
- **Keyboard: all 7 journeys pass:**
  - sign-in;
  - the navigation drawer at 390 px (Escape and focus return);
  - the account menu;
  - the Add Reseller wizard;
  - ARIA tabs with arrow keys;
  - checkout including the hosted payment page;
  - the in-place cancel confirm (focus moves to the confirm and returns on Cancel).
- **Unit level:** the frontend suite adds its own axe pass per screen.
- **Not executed:** a manual screen-reader pass (NVDA/JAWS/VoiceOver).

### 11. Tenant isolation
**PASS.**
- The backend suite's cross-organization tests.
- The architecture tests: no product-domain code in the platform, no cross-product database reads, one entitlement writer.
- In the browser: two white-label partners on two domains showed no bleed; a partner's customer list is its own; a reseller denied a product could neither see nor sell it (server 403 `product_not_granted`).
- The staff directory now needs a verified second factor (D-037, item 40).

### 12. RLS
**PASS, live, as the application role** (`w360_app`, NOBYPASSRLS, owns no table) on staging:
- 62 tables carry `organization_id`. 48 have RLS enabled **and** forced, 0 have RLS without FORCE, and 14 are documented exemptions.
- Without the tenant variable the app role reads **0 rows** (a superuser reads 514).
- Every api and worker container runs the **startup gate** before it serves (G-22, G-53). A runtime role that can bypass RLS or owns a table, missing timeouts, or unapplied migrations stop the container. Proven on staging: with `BYPASSRLS` granted, the api never started.
- `check --deploy` verifies the same on every deploy, and in CI.

### 13. CSRF/CSP
**PASS.**
- **CSRF:** a cookie-authenticated write without the token is refused (403), and with it the request reaches the business rule; verified in the browser. The refusal is now JSON `csrf_failed` and never says why (B-06). Trusted origins are the canonical host only.
- **CSP:** the production build is served with a per-request nonce and `strict-dynamic`. There were **0 violations** across every browser flow, every audited page and every launch click. The edge adds HSTS and nosniff; Next.js and Django add CSP, COOP, Permissions-Policy and X-Frame-Options.

### 14. SSRF
**PASS.** 106 tests (`test_12_ssrf.py`).
- Manifest URLs now refuse numeric and alternate loopback forms: `2130706433`, `0177.0.0.1`, `127.1`, fullwidth letters, IPv4-mapped, 6to4, Teredo and NAT64 (G-11).
- Server-side fetches resolve and pin addresses at connect time, refusing private, link-local and metadata ranges; that was already true and is re-tested.
- The final audit found three more forms a browser opens as loopback: percent-encoded hosts, ideographic full stops (`127。0。0。1`) and a backslash in the authority. All refused since (G-56).
- Partner support URLs no longer 500 on malformed input (G-23).

### 15. Rate limiting
**PASS.**
- Login, signup, password reset, owner claim, MFA codes, OIDC endpoints, public catalog and branding are all limited.
- IPv6 is keyed by /64 (G-12).
- With Redis down, a bounded per-process limiter takes over and no longer resets every counter under key-spraying (G-13). Brute force is still stopped with Redis physically down (resilience "redis" scenario).
- The load test raised only the two per-address limits that one shared k6 address trips (item 24) and restored them.
- **Behind a CDN** the edge must trust the CDN's ranges (`W360_EDGE_TRUSTED_PROXIES`, with a proxy count of 2). Otherwise every user shares one address: one rate-limit bucket, and IP allow-lists that see the edge (G-55).

### 16. Secret management
**PASS** ([05-secrets-and-key-rotation.md](05-secrets-and-key-rotation.md)).
- Every secret comes from the environment or the secret store. `production.env.example` holds names only.
- gitleaks is clean over history and tree with a triaged configuration; trivy finds no secrets in either image. The frontend bundle holds none; logs pass through the redaction processor (including standard-library logs, G-14).
- **OIDC signing-key rotation was rehearsed through the edge, 13/13:** overlap, the old key still verifying until retirement, safe retirement after 930 s.
- **Least privilege per container (G-54, final audit):** each service receives only the variables it reads. The internet-facing edge and the frontend hold no secret; the api and workers hold neither the database superuser's nor the owner's password. Verified inside the running staging containers.
- **gitleaks allowlist narrowed** from whole directories to the triaged files (G-61).
- **Open (D-038):** the MFA encryption key cannot be rotated yet.

### 17. PostgreSQL
**PASS** for the database itself. Its backup schedule is P1 (OPS-2).
- **Timeouts:** they were **off on the pooled path** (PgBouncer ignores startup `options`, G-02). They are now role defaults (`statement_timeout` 10 s, `lock_timeout` 5 s, `idle_in_transaction_session_timeout` 15 s), and the startup gate refuses to start without them.
- **Topology:** two roles (the owner migrates directly, the app connects through PgBouncer with SCRAM, pool 40); `max_connections` 200.
- **WAL archiving** fixed and alerted (G-44).
- **WAL retention (G-59):** the archive only grew, reaching 1.2 GB after a day of staging. `wal_retention.sh` takes a base backup, keeps N and prunes the rest: on staging 1.2 GB → 73 MB, and PITR still passes.
- **After a PostgreSQL restart**, PgBouncer retries its login within 2 s instead of refusing clients for 15 s (G-48).
- **Data growth** is inventoried with retention ([09-capacity-and-retention.md](09-capacity-and-retention.md)).
- Query counts are pinned by the performance tests. Measured database load: item 24.
- **Owner action:** partition the audit table before about 50 M rows.

### 18. Redis outage
**PASS**, with Redis physically stopped on staging: 12/12 and recovery 5/5, on release candidates 2, 4 and 5 (final). The whole resilience suite on release candidate 5 passed **178/178**.
- **Before Phase 12:** sign-in took about 8 s and My Products about 15 s, because every cache call waited for its own failure (G-43).
- **Now:** a per-process circuit breaker costs one slow discovery request (about 4 s), then sign-in 66 ms, My Products 40 ms, catalog 37 ms.
- Rate limiting holds, nothing is lost, and service recovers on its own.
- The cascade scenario (Redis, broker and AI provider down together) passes 8/8.

### 19. RabbitMQ outage
**PASS** (14/14, on release candidates 2, 4 and 5). With the broker stopped, sign-in and browsing work.
- Trial start, order and payment start commit.
- **Password reset:** the email is recorded durably, no email leaves during the outage, and it is delivered **exactly once** after recovery.
- **Checkout:** the deferred step completed after recovery and the order was fulfilled.

**Database outage** (not a numbered item, same run):
- PgBouncer down: sign-in fails safely with no partial session; commercial writes are refused; readiness goes not-ready; no email is sent and no partial order is left; the platform recovers.
- PostgreSQL itself down: requests fail fast (under 5 s).
- On release candidate 4 this found **G-48**: PgBouncer kept refusing clients for up to 15 s *after* PostgreSQL was back. Fixed (`server_login_retry = 2`); the database scenarios re-ran 27/27, and all of them pass on release candidate 5, including recovery after a PostgreSQL restart.

### 20. External-product outage
**PASS**: 16 fault cases (5xx, timeout, DNS failure, TLS failure) × 4 products, 112/112 checks, on release candidates 2, 4 and 5.
- The platform stays fast, launch reports the product unavailable, and health probes run on the `heavy` queue, so sign-in email on `critical` is unaffected.
- The live `W360ProductUnreachable` alert is firing on staging for the real Easy CRM (item 28).

### 21. Backup result
**PASS.** The final backup was taken of staging on release candidate 4, after the browser runs ([03-backup-and-restore.md](03-backup-and-restore.md)).
- **Time and size:** 3 s for a 2.0 MB dump plus the files archive, roles without passwords, the manifest and checksums. All 5 checksums verify.
- **Contents:** 625 users, 603 organizations, 533 product trials, 610 entitlements, 20 partners, 71 partner–customer links, 42 orders, 53 invoices, 37 commission accruals, 1 paid payout and 2,262 audit events.
- **Found by the rehearsal:** the script refuses to produce a backup when the files archive is missing. It did, when a shell rewrote the container path.
- **Not scheduled (P1, OPS-2):** nothing runs `backup.sh` or copies its output off the host yet ([04-deployment.md §7](04-deployment.md)).

### 22. Restore result
**PASS.** `restore_verify.sh` into a new, isolated PostgreSQL with new role passwords, re-run with the final script and the release candidate 5 image:
- `pg_restore` in one transaction: 1 s. Verification included: 13 s (20 s wall).
- Row counts identical for all 22 authoritative tables.
- On the restored database, each judged by its exit status:
  - the application's startup gate: RLS role, ownership, timeouts, migrations, registries;
  - `check --deploy`;
  - `migrate --check`.
- The runtime role carries its timeouts as role defaults.
- 95 stored files restored.
- The final audit found that the first version of this script's application check could not fail (G-52). Fixed before this run.

**Point-in-time recovery:** PASS on the live WAL archive. Base backup, then restore and replay to the target instant in 3–4 s (30 s wall); the write before the target is present and the write after it is absent. It passed again after `wal_retention.sh` had pruned the archive.

### 23. RPO/RTO
- **Built and proven on staging:** logical backup (`backup.sh`), isolated restore, point-in-time recovery, and base backup with WAL retention (`wal_retention.sh`).
- **Not built:** the schedule for them and a copy off the host (P1, OPS-2). Until those exist the RPO is **unbounded**: nothing is taken unless an operator runs it. The final audit caught the earlier text claiming a 24 h / 5 min RPO "built today".
- **Once scheduled** (daily logical backup and base backup; WAL shipped off-host): **RPO ≤ 5 min, RTO ≤ 2 h** on a single host.
- The AWS target (RDS Multi-AZ) before general availability.

Details: [03-backup-and-restore.md §3](03-backup-and-restore.md).

### 24. Load test
**Done at 50 / 100 / 250 / 500 concurrent customers** on the final release candidate, k6 over TLS through the edge. Customers sign in, then read session, My Products, the catalog and their subscription, and launch. Partners list customers; staff verify TOTP and read the directory. Everything ran on one laptop, so these are a lower bound ([07-load-test.md](07-load-test.md)).

| Customers | Requests/s | Failed | p50 | p95 | p99 | DB commits/s |
|---|---|---|---|---|---|---|
| 50 | 119 | 0.02 % | 186 ms | 535 ms | 754 ms | 884 |
| 100 | 112 | 0.00 % | 716 ms | 1.26 s | 1.93 s | 822 |
| 250 | 104 | 0.00 % | 2.18 s | 3.96 s | 6.29 s | 795 |
| 500 | 107 | 0.02 % | 4.86 s | 8.17 s | 13.1 s | 765 |

- **Resources:**
  - api 250–285 % CPU (saturated), ~270–300 MiB;
  - PgBouncer 60–75 % of one core;
  - PostgreSQL ~40 % CPU, at most 6 active connections;
  - queue depth 0;
  - memory flat throughout.
- **One deviation:** two per-address rate limits raised for the test, because all VUs share one IP; restored afterwards.
- **An invalid first run is disclosed:** a k6 cookie setting signed every VU out after its first pass. Fixed and re-run.

### 25. Bottlenecks
1. **API compute binds first:** a flat ~105–120 requests/s from 50 to 500 VUs. That is 3 gunicorn processes, GIL-bound, using under 3 of 24 cores. Remedy: more processes and instances.
2. **PgBouncer next:** single-threaded, about 7 database transactions per request, ceiling roughly 1,100–1,200 transactions/s (~40–50 % above today). Remedy: several PgBouncer processes or RDS Proxy, and fewer transactions per request.
3. **Sign-in (Argon2)** queues on a saturated api: p95 14.6 s when 500 sign in at once.
4. **PostgreSQL has headroom.** The staff directory's summary aggregate needs caching before ~100,000 organizations (P3).
5. **Rare 502 on launch** (0.014 %, P3): a POST sent down a connection that a recycled gunicorn worker had just closed. An `Idempotency-Key` on launch, or rarer recycling, would remove it.

### 26. Queue isolation
**PASS** ([06-async-safety.md](06-async-safety.md)).
- Three queues, each with its own worker pool, and every task declares its queue (a deploy check refuses one that does not).
- Sign-in email moved to `critical`; long third-party calls moved to `heavy` (G-31).
- **Notification sends** (G-30, reworked by G-50 after the final audit): a committed claim with a lease, then the provider call outside any transaction, then the outcome. A concurrent copy never sends. A worker that dies mid-send is retried once its lease ends: at-least-once across a crash, bounded by the attempt count.
- Every Beat job is safe to run twice (table in that document), and sweeps honour their soft time limit (G-32, completed by G-57).

### 27. Observability
**PASS** ([08-observability-and-alerts.md](08-observability-and-alerts.md)).
- **Logs:** structured JSON with redaction, including standard-library logs.
- **Correlation ids:** end to end, now including health probes (G-46).
- **Probes:** health endpoints for orchestrators, answered before host/TLS handling, with generic failure bodies.
- **Metrics:** a token-protected Prometheus endpoint covering database, WAL, cache, broker, queues, outbox, due work, products and partner domains. Metric memory is bounded (G-34), and a metric can no longer fail the request it describes (G-47).
- **Not in the production topology (P1, OPS-3):** Prometheus, Alertmanager and node_exporter ([04-deployment.md §7](04-deployment.md)).

### 28. Alerts
**PASS in code and on staging; not deployed in production (P1, OPS-3).** 20 rules, validated by promtool, each with a runbook link ([10-runbooks.md](10-runbooks.md)). **Observed firing on staging:**
- `W360ProductUnreachable` for Easy CRM: real, its TLS is broken.
- `W360CustomDomainFailing`: a test partner domain.
- **Forced:** with RabbitMQ stopped, `W360BrokerUnavailable` was pending at +29 s and firing at +149 s (its 2-minute window). It resolved 30 s after the broker came back, and all three worker pools were consuming again.

**Found by the final audit (G-51):** the 5xx page alert could never fire, because Caddy's per-request metrics were off. They are on now; the series and the rule were checked on staging.

**Not in place (P1, OPS-3):** Prometheus and Alertmanager in production, and a paging route. Log-based alerts to add with them (08-observability-and-alerts.md):
- `identity.mfa.staff_factor_enrolled`;
- `metrics.series_dropped`;
- `healthz.check_failed`.

### 29. Production settings
**PASS.**
- Production refuses to boot on every unsafe fallback found (G-03, G-05, G-06, G-07):
  - SMTP without TLS;
  - a `.test` from address;
  - a non-https frontend URL;
  - a missing broker;
  - plaintext AMQP or S3 without an explicit override;
  - a missing S3 bucket;
  - the development migrator password.
- Every process entrypoint in the image defaults to production settings (the image sets `DJANGO_SETTINGS_MODULE`); `manage.py` outside the image defaults to development.
- The api and every worker run the startup gate before serving (G-53).
- A metrics token shorter than 32 characters is refused (G-63).
- `check --deploy --fail-level WARNING` on staging: 0 issues, except W360.W010 for the three `sslip.io` product hosts, which is deliberately loud.

### 30. Email readiness
**BLOCKED.**
- **The code works.** The SMTP settings are read and TLS is required. On staging (Mailpit), in the final browser run on release candidate 5, all 23 messages arrived once each and none was missing or duplicated:
  - email verification;
  - partner set-up (password link) and password reset;
  - owner activation;
  - payment receipts and invoices.

  Every link in them worked, and each was sent through the Phase 12 claim/send/record path. Reset email was delivered exactly once across a broker outage. (There is no email for custom-domain events; an earlier draft of this report wrongly listed one.)
- **Missing:** no real provider is configured, so no real delivery has been proven (the brief: no PASS without real delivery). SPF, DKIM and DMARC for the sending domain are also still to do.

### 31. Payment readiness
**TEST/MANUAL only.**
- **What works:** the provider-independent flow, verified end to end in the browser with the TEST provider. That covers hosted checkout, signed callback, order fulfilment, invoices, dunning, refunds and manual marking.
- **What is missing:** no real provider (Stripe or another) is configured. `payments.0002` is forward-fix-only after the first order.

### 32. DNS/TLS
**Platform edge: PASS on staging.**
- TLS is issued on demand only when the platform confirms an active, verified domain. An unknown SNI gets no certificate, and a partner domain is refused TLS before activation.
- HSTS is on; internal paths answer 404 from outside.

**Real world: FAIL / owner action.**
- `workeasy360.com` is in a 301 loop, and no platform host resolves (X-04).
- Easy CRM has no working TLS (X-03).
- Three products use `sslip.io` (G-19).

### 33. Deployment process
**PASS for repeatability.** Version-controlled images, compose topology, edge, roles and PgBouncer; an exact build, order and checklist ([04-deployment.md](04-deployment.md)). Release candidates 1→2→3→4 were deployed the documented way (migrate task first, `check --deploy` after).

**Measured on the rc3→rc4 deploy** (a probe through the TLS edge every ~0.5 s): 19 s end to end (migrate task, api, web, three worker pools and Beat replaced). The edge answered 502 for a window of about 9 s for the API and about 10 s for the web front end (3 of 163 probes each); nothing else failed.

**Zero-downtime is not achieved by the single-instance compose topology** (P2): it needs two or more api and web instances behind the edge, rolled by an orchestrator ([04-deployment.md §4](04-deployment.md)). Stop grace periods now let requests (35 s) and tasks (60–95 s) drain instead of being killed at Docker's default 10 s.

**After the final audit** the compose topology also:
- runs the startup gate before the api and every worker (G-53);
- gives each container only its own secrets (G-54);
- enables the edge's request metrics and trusted-proxy setting (G-51, G-55).

It was deployed as **release candidate 5**: every service was recreated; the gate passed in the api and all three worker pools; data intact. The jobs that must run beside it (backups, WAL retention, monitoring) are listed in [04-deployment.md §7](04-deployment.md) and are not in place (OPS-2, OPS-3).

### 34. Migration safety
**PASS.**
- A new CI guard refuses any migration unsafe to run while the previous release serves traffic (non-concurrent index, NOT NULL without a database default, drop, rename). An exception needs a reviewed `overlap_safety`; 76 pre-existing migrations are frozen in a baseline, whose size and fingerprint the test pins (G-62).
- The only Phase 12 migration is overlap-safe.
- Every migration reverses structurally (92/92) and re-applies.

### 35. Rollback/forward-fix
**PASS, documented and rehearsed.**
- **Code rollback:** redeploy the previous tags. The schema stays; the old code ignores new nullable columns.
- **Database rollback:** only where reversible with real data. Phase 12's migration was rolled back over the populated staging copy with no value changed (item 39).
- **Forward-fix only:** `payments.0002` after the first order; the reason and procedure are in [04-deployment.md §6](04-deployment.md).

### 36. Incident runbooks
**PASS** ([10-runbooks.md](10-runbooks.md)): API/database outage, Redis, RabbitMQ, queue backlog, external product, OIDC failure, payment, email, backup failure (now including WAL retention), **staff member locked out of the control plane** (the D-037 break-glass reset), suspected credential leak, tenant-isolation incident, elevated 5xx, disk, certificate expiry, custom-domain failure. Every alert links to its runbook section.

### 37. Security scans
**PASS**, no high severity waived silently ([11-security-scans.md](11-security-scans.md)).
- bandit, pip-audit and npm audit: clean.
- semgrep (final tree): 0 findings; the 2 triaged false positives are annotated. Three files were only partly parsed (a parser limit).
- gitleaks: clean with the triaged config (history, and the final tree again).
- trivy (release candidates 3, 4 and 5): 0 CRITICAL, 0 fixable HIGH, 0 secrets. 47 and 45 unfixable Debian OS HIGHs are triaged, and CI fails on any fixable one.
- **Not executed:** CodeQL is configured but needs GitHub (the repository was not pushed).

### 38. Fresh DB
**PASS.**
- **Rehearsal** (`scripts/rehearse_phase12_upgrade.py`, 5/5): migrate from zero; no model change without a migration; deploy checks (RLS + FORCE, role, ownership, timeouts); seed (Super Admin, four products) and a service smoke.
- **Staging:** the stack was itself created from an empty volume and seeded. Its baseline smoke passed 9/9: sign-up with emailed verification, trial, hosted checkout, order fulfilled, sign-in, session, My Products, catalog, latency under 2 s.

### 39. Upgrade rehearsal
**PASS (12/12), twice.** The final run used the final backup (item 21), stepped back to the previous release's schema.
- **Data:** 625 users, 603 organizations, 35 subscriptions (77 items), 533 product trials, 610 entitlements, 534 seats, 20 partners, 39 product grants, 71 partner–customer links, 42 orders, 53 invoices, 37 commission accruals and a paid payout. The payout went through draft, ready, approval by a second staff member and paid.
- **Upgrade:** migrated to Phase 12 with no pre-existing value of any of 20 authoritative tables changed (users, organizations, memberships, subscriptions and items, trials, entitlements and snapshots, seats, products, plan versions, orders, invoices, partners, product grants, partner links, commissions, payouts, product integrations, authorization codes); the deploy checks pass.
- **Rollback and re-forward:** rolled back over the populated data with nothing changed, re-applied, still unchanged.
- **Pending auth codes:** an authorization code minted by the previous release is judged least-trusted.

The earlier run on a smaller dataset gave the same result.

### 40. P0/P1/P2/P3
See §2.

### 41. Go-live blockers
**GO-LIVE BLOCKER: YES.** See §2 and §4.

### 42. Final four-product matrix
See §3.

---

## 2. Remaining issues, classified

Everything found **inside the application code** in Phase 12 was fixed and re-verified, including the one open platform P1 (D-037, staff MFA) and the findings of the independent final audit. The full list is in [01-audit-gap-list.md](01-audit-gap-list.md): G-01…G-63 and B-01…B-08. What remains:

| ID | Sev | Issue | Owner | Go-live blocker |
|---|---|---|---|---|
| X-02 | **P0** | Easy Project trusts client-supplied `X-User-Id` / `X-Org-Id` headers and has no password; anyone who knows two UUIDs can act as that user. The platform's launch URL carries the organization UUID. | Easy Project team | **YES** (do not offer Easy Project) |
| X-01 | **P1** | No product has an OIDC client or verifies entitlement with the platform. Customers would sign in to each product separately, and a product cannot tell a paid organization from an unpaid, suspended or cancelled one. The platform's commercial state stops at the launch button. | Each product team | **YES** |
| X-03 | **P1** | Easy CRM's host fails the TLS handshake, so launch lands on an error. | Easy CRM team | **YES** (Easy CRM) |
| G-19 | **P1** | Easy CRM, EasyBook and Easy Help Desk are registered on `<AWS IP>.sslip.io` names, which follow whoever holds the IP next (phishing through the launcher). `check --deploy` warns W360.W010. | Product teams + owner | **YES** (those three) |
| X-04 | **P1** | The platform has never been deployed. `workeasy360.com` 301s to itself (Cloudflare in front of Hostinger, consistent with an SSL-mode loop), and no platform host resolves. | Owner / ops | **YES** |
| X-05a | **P1** | No real email provider. SPF, DKIM and DMARC for the sending domain are also missing. Sign-up verification, password reset and owner activation depend on email. | Owner | **YES** |
| X-05b | **P1** | No real payment provider; TEST/MANUAL only. | Owner | **YES**, unless the owner decides the controlled go-live collects payment manually (the platform supports manual collection) |
| D-039 | P2 | No OIDC back-channel logout or per-user revocation to products. | Platform + products | No today; **YES before the first product turns SSO on** |
| OPS-1 | P2 | Zero-downtime deploys need two or more api/web instances; the compose topology has a gap of about 10 s per deploy (item 33). | Ops | No (announce deploy windows) |
| OPS-2 | **P1** | **Backups are not scheduled and nothing leaves the host** (G-59). `backup.sh`, `wal_retention.sh`, restore and PITR exist and were proven on staging, but nothing runs them. Unpruned, the WAL archive grew 1.2 GB in a day, which fills the disk and stops PostgreSQL. Until scheduled, the RPO is unbounded. | Ops | **YES** |
| OPS-3 | **P1** | **No monitoring in the production topology:** no Prometheus, Alertmanager, node_exporter or pager route. The 20 rules and the metrics exist and were proven on staging, but in production nothing evaluates them and nobody is paged. | Ops | **YES** |
| D-036 | P2 | No organization purge or export (privacy); the data inventory exists. | Platform | No; before the first deletion request |
| D-038 | P2 | The MFA encryption key cannot be rotated. | Platform | No (target 2027-03-31) |
| D-037r | P3 | The bootstrap Super Admin is created with a password only. Its authenticator is enrolled at its first sign-in, which the operator does at deploy time (runbook "staff member locked out"). Every other staff identity needs a confirmed factor before designation (G-58). | Owner | No |
| D-035p | P3 | Next.js skips its proxy for prefetch requests, so on a partner's custom domain a prefetch gets neither the D-035 redirect nor a CSP header. There is still no credential form there. Enforcing the partner-host path allow-list in the edge would close it. | Platform | No |
| A11Y-1 | P2 | A manual screen-reader pass has not been done; automated axe and keyboard checks pass. | Owner | No |
| LOAD-1 | P2 | One api instance with 3 workers is the throughput ceiling (~110 requests/s on this host); PgBouncer's single process is the next (07-load-test.md). Proportionate for a controlled launch; raise workers and instances, and address the pooler, before general availability. | Platform + ops | No |
| LOAD-2 | P3 | A POST is refused (502) when a recycled gunicorn worker closes the pooled connection it was sent on: 6 in ~43,000 requests. | Platform | No |
| LOAD-3 | P3 | The staff directory aggregates the whole customer summary per request: fine at 600 organizations, cache before ~100,000. | Platform | No |
| SCAN-1 | P3 | CodeQL is configured but not run (runs on GitHub after a push). | Owner | No |
| IMG-1 | P3 | 47 / 45 unfixable Debian OS HIGHs in the images. They are not reachable by the application, and a fix, once published, fails CI. Distroless later. | Platform | No |
| RET-1 | P3 | Several small tables have no prune yet (retention defined, [09](09-capacity-and-retention.md)). The audit table should be partitioned before about 50 M rows. | Platform | No |
| G-20r | P3 | Cookies lack the `__Host-` prefix; `/userinfo` is unthrottled; the client-assertion replay cache fails open while Redis is down (documented). | Platform | No |
| UX-1 | P3 | An owner who starts a trial or buys holds no seat until they assign one; the card now says how. Auto-assigning the buyer is a product decision. | Owner | No |

**GO-LIVE BLOCKER: YES.** One P0 and eight P1s. None is a defect in the application code: each is a product, provider or operations matter. Calling any of them debt would hide it.

## 3. Four-product matrix and pass conditions

Step 63's matrix. "Platform" rows are what Workeasy360 itself does for that product, verified in real Chrome on release candidates 3 and 5 and in the backend suite. The last row is added so the product side is not hidden behind the platform's PASS.

| | Easy CRM | EasyBook | Easy Project | Easy Help Desk |
|---|---|---|---|---|
| Catalog | PASS | PASS | PASS | PASS |
| Trial | PASS | PASS | PASS | PASS |
| Purchase | PASS | PASS | PASS | PASS |
| Entitlement | PASS | PASS | PASS | PASS |
| Dashboard | PASS | PASS | PASS | PASS |
| Launch | BLOCKED¹ | PASS | PASS | PASS |
| SSO | BLOCKED² | BLOCKED² | BLOCKED² | BLOCKED² |
| Suspend | PASS | PASS | PASS | PASS |
| Resume | PASS | PASS | PASS | PASS |
| Cancel | PASS | PASS | PASS | PASS |
| Reseller | PASS | PASS | PASS | PASS |
| Commission | PASS | PASS | PASS | PASS |
| White-label | PASS | PASS | PASS | PASS |
| External outage | PASS | PASS | PASS | PASS |
| Cross-org isolation | PASS | PASS | PASS | PASS |
| Browser E2E | PASS | PASS | PASS | PASS |
| *Product side (added)* | *BLOCKED: TLS, sslip.io* | *BLOCKED: sslip.io* | ***FAIL: header auth (P0)*** | *BLOCKED: sslip.io* |

1. The platform hands off correctly (the registered launch URL with `organization=<uuid>`, no token), but Easy CRM's host fails TLS, so a customer cannot actually open it.
2. No product has an OIDC client. The platform side is PASS per product (32 tests); nothing insecure was added to work around it.

**Reseller / Commission / White-label.** CRM and EasyBook were sold by the Step 10 reseller at 15 %. That reseller was correctly refused Easy Project. Easy Project and Easy Help Desk (and all four) were sold by the Step 11 white-label partner at 10 %. **External outage:** 4 faults × 4 products (item 20). **Cross-org isolation:** item 11.

### The brief's pass conditions

| Condition | Met? |
|---|---|
| All 4 products independently verified | Yes: each product through the platform, and each product's own endpoint from outside |
| SSO working per product, or explicitly blocked with no insecure bypass | Yes: blocked, no bypass |
| D-035 has a secure final architecture | Yes (ADR-039) |
| Real browser journeys executed | Yes (83/83, on release candidates 3 and 5) |
| Production accessibility check at automated-browser level | Yes |
| Tenant isolation passes | Yes |
| RLS passes | Yes (live, app role) |
| Entitlement writer remains unique | Yes (architecture test) |
| Commercial writes remain authoritative | Yes on the platform; products do not consume them yet (X-01) |
| Backups proven restorable | Yes (item 22, PITR too); **not scheduled** (OPS-2, P1) |
| Redis/RabbitMQ failure behaviour proven | Yes |
| External product outage cannot cascade | Yes |
| Secrets are clean | Yes |
| Production settings are hardened | Yes |
| Deployment is repeatable | Yes |
| Rollback/forward-fix documented | Yes |
| Observability/alerts exist | In code and proven on staging; **not deployed in production** (OPS-3, P1) |
| **No unresolved P0** | **No: X-02 (Easy Project)** |
| **No go-live P1** | **No: X-01, X-03, G-19, X-04, X-05a, X-05b, OPS-2, OPS-3** |
| Final backend suite green | Yes (3,306 passed, 0 failed, 0 skipped) |
| Final frontend suite green | Yes (388/388) |
| Fresh DB passes | Yes |
| Upgrade rehearsal passes | Yes (12/12) |

## 4. What would change the answer to YES, in order

1. **Easy Project** replaces header authentication with real authentication, ideally as a Workeasy360 OIDC client. Until then, withdraw Easy Project from sale (catalog status) rather than launch customers into it.
2. **Each product** implements its OIDC client: authorization code + PKCE, `private_key_jwt` (the manifest already declares the redirect URI) and server-side entitlement verification (`/integrations/entitlements/verify`, `revalidate_after` 900 s). Register its credentials. Before the first product goes live with SSO, the platform adds back-channel logout (D-039).
3. **Product hosts:** move all four to names the owner controls (for example `crm.workeasy360.com`) with valid TLS, and fix Easy CRM's certificate. Update `infra/ecosystem/products.json`; `check --deploy` must then show no W360.W010.
4. **Deploy the platform:**
   - DNS for the canonical host;
   - fix the Cloudflare/Hostinger redirect loop (SSL mode "Full (strict)" with a valid origin certificate). If Cloudflare stays in front, set `W360_EDGE_TRUSTED_PROXIES` to its ranges and `W360_TRUSTED_PROXY_COUNT=2` (G-55);
   - the host(s) and the secret store;
   - **schedule** the backups and WAL retention and ship them off the host (OPS-2);
   - Prometheus, Alertmanager, node_exporter and a pager route (OPS-3);
   - enrol the bootstrap Super Admin's authenticator at once.

   Follow [04-deployment.md](04-deployment.md), including §7.
5. **Email:** a real provider, with SPF, DKIM and DMARC; prove delivery of a reset email to an ops mailbox (checklist in 04 §5).
6. **Payments:** a real provider, or a recorded owner decision to collect manually during the controlled go-live.
7. **Re-verify on the real environment:** the browser check (`scripts/phase12_browser_check/`), the resilience suite, the load test, and backup, restore and PITR. Then re-evaluate this report.

## 5. Changes outside the repository, and what was not done

- **The local development database** (`workeasy360-postgres-1`): `ALTER ROLE w360_app SET statement_timeout = '10s'`, `lock_timeout = '5s'`, `idle_in_transaction_session_timeout = '15s'`. These are the same defaults `infra/postgres/init.sql` now sets for a new database. Reverse with `ALTER ROLE w360_app RESET ALL`.
- **Staging** (Docker project `w360stg`, release candidate 5) is **stopped, not removed**: its volumes keep the test data (customers and partners from the browser and load runs). To restart it, use `docker compose -p w360stg -f deploy/compose/production.yml -f deploy/staging/compose.staging.yml --env-file <staging.env> start`. To delete it, use `docker compose -p w360stg down -v`. Scratch databases created by the rehearsal were dropped.
- **Not done:** nothing was committed, pushed or deployed. No real DNS, email, payment or product configuration was touched. CodeQL, a manual screen-reader pass and product-side SSO were not executed.

# Observability, health endpoints and alerts (Phase 12, Steps 31–34)

## 1. Structured logs (Step 31)

- **One stream, JSON.** Every log record renders through one `ProcessorFormatter`: structlog events, Django, Celery, psycopg, and this codebase's own `logging.getLogger(...)` records with `extra=`.
  - Phase 12 fix G-14: before, stdlib records were printed as bare `%(message)s`, with no JSON, no fields and no redaction.
- **Always present:** `event` (stable dotted name), `level`, `logger`, `timestamp` (UTC ISO), `environment`, `release`, `correlation_id` / `request_id`.
  - The correlation ID is accepted from `X-Correlation-ID`, returned on every response, and carried into Celery tasks through task headers.
- **When a tenant or actor is known:** `organization_id`, `membership_id`, `user_id`. These are identifiers, never names or emails; PII keys are blanked to `[pii]`.
- **Request line:** `http.request` with method, path (no query string), status and `duration_ms`. Gunicorn's own access log is off, so there is no second, unredacted request log.
- **Redaction (Step 34)** is by key name, recursive, applied last. It covers:
  - `password`, `secret`, `token`, `api_key`, `authorization`, `auth`, `credential`, `private_key`
  - `session_id`, `session_key` (Phase 12 addition), `csrf`, `cookie`, `signature`, `otp`, `mfa_code`
  - `card_number`, `cvv`, `iban`, `assertion`, `verifier`, `authorization_code`
- **Pinned by tests:** `tests/phase12/test_12_logging.py`, including the stdlib path for Authorization, session key, CSRF, MFA, SMTP/DB passwords, OIDC authorization code, client assertion, API key, card number and private key. Values never logged in the first place (database URLs, OIDC codes, SSM secrets) are covered by the same key rules as a backstop.

## 2. Health endpoints (Step 33)

| Endpoint | Checks | Used by | External products? |
|---|---|---|---|
| `/healthz/live` | the process answers | container restart | no |
| `/healthz/ready` | PostgreSQL + migrations (blocking); cache (reported, non-blocking) | load balancer / compose healthcheck | **no** |
| `/healthz/startup` | DB, migrations, registries, **RLS role, table ownership, DB timeouts** (Phase 12) | orchestrator start gate | no |
| `/healthz/deep` | everything above plus broker, outbox age, due-work lag, AI providers, signing keys; staff only (404 otherwise) | operators | **yes, here only** (product health, AI provider state) |
| `/healthz/metrics` | Prometheus gauges; bearer token (404 without) | the scraper | health gauges only |

- The three probes and metrics are answered **before** host validation and the HTTPS redirect, because orchestrators probe by IP over plain HTTP (G-05).
- Unauthenticated probes return only the exception *type*, never connection details.
- The edge refuses `/healthz/*` from outside (404).
- `tests/architecture/test_phase6_architecture.py` and `test_product_health_never_affects_platform_readiness` pin that no product or integration state can fail readiness.
- Proven on staging: with each of the four products failing four ways (5xx, 20 s timeout, NXDOMAIN, expired TLS certificate), readiness stayed 200 and core requests stayed under 2 s (`resilience.py`, 64 product checks).

## 3. Metrics (Step 31)

| Source | What | Scraped from |
|---|---|---|
| **Edge (Caddy)** | request rate, latency histogram and status by host: **HTTP latency and errors** | `edge:9180/metrics` |
| **API gauges** (`/healthz/metrics`) | `w360_database_up`, `w360_database_connections{state}`, `w360_cache_up`, `w360_broker_up`, **`w360_queue_messages{queue}`** and `w360_queue_consumers{queue}` (queue lag), `w360_outbox_pending`, `w360_outbox_oldest_pending_seconds`, `w360_outbox_dead_letters`, `w360_due_work_overdue`, `w360_due_work_oldest_overdue_seconds` (**integration backlog**), `w360_product_health{product_code,status}` and `w360_product_health_age_seconds` (**external product health**), `w360_partner_domains{status}`, `w360_partner_domains_failing`, `w360_gauge_source_up{source}` | `api:8000/healthz/metrics` |
| **Log-based metrics** | counts of events by `event` name. Used for SSO, entitlement, launch, trial and checkout **rates**, which are per-process counters inside the app and cannot be summed across gunicorn workers honestly. | the log pipeline (CloudWatch metric filters, Loki, Datadog) |

Every gauge reads either a control-plane table or an aggregate that needs no tenant. A count over an RLS table would silently read zero, which is worse than no metric. The in-process counters also no longer leak memory (G-34: a bounded window).

## 4. Alerts (Step 32)

Rules: `deploy/observability/alerts.yml` (20 rules, validated by `promtool` and loaded by the staging Prometheus). Each has a `for:` window and a runbook link.

**Where they run.** `deploy/compose/production.yml` does **not** run Prometheus, Alertmanager or node_exporter; only the staging overlay runs Prometheus. Production needs all three and a pager route before go-live (phase-12-status §2, OPS-3; 04-deployment.md §7). Until then no rule is evaluated and nobody is paged.

**The edge's request metrics** (`caddy_http_request_*`, read by `W360High5xxRate`) are opt-in in Caddy and were off until the final audit (G-51): the page alert could never fire. They are enabled in the Caddyfile's global options, and on staging the rule now evaluates as healthy against real traffic.

| Alert | Signal | Severity |
|---|---|---|
| W360ApiDown | scrape target down 2 m | page |
| **W360DatabaseUnavailable** | `w360_database_up == 0` for 1 m | page |
| **W360BrokerUnavailable** | `w360_broker_up == 0` for 2 m | page |
| W360CacheUnavailable | `w360_cache_up == 0` for 5 m | ticket (Tier 1, degraded) |
| **W360High5xxRate** | edge 5xx > 2 % for 10 m | page |
| W360WorkerPoolDown | a queue with 0 consumers for 5 m | page |
| **W360CriticalQueueLag / W360QueueLag** | depth thresholds per queue | page / ticket |
| W360OutboxStuck, W360DueWorkLag, W360DeadLetters | integration backlog | page / ticket |
| W360DatabaseConnectionsHigh | > 160 of 200 | ticket |
| **W360ProductUnreachable**, W360ProductHealthStale | external product health | ticket (the product team's page) |
| **W360CustomDomainFailing** | ownership re-check failing 1 h | ticket |
| **W360DiskFillingUp** | < 15 % free (node_exporter) | page |
| **W360CertificateExpiring** | < 14 days (blackbox_exporter; Caddy renews at 30) | page |
| W360MetricsSourceFailing | a gauge source unreadable | ticket |

**Log-based alerts** (configure in the log pipeline; the event names are stable):

| Alert | Query | Threshold |
|---|---|---|
| **OIDC failures** | `event IN ("ecosystem.token.subject_no_longer_valid", "ecosystem.authorize.membership_denied", "ecosystem.clientauth.failed")` or `http.request` with `http_path` `/api/v1/integrations/oauth/token` and 5xx | > 20 in 10 m, or any 5xx on /token for 5 m (page) |
| **Entitlement failures** | `event = "ecosystem.entitlement.unavailable"` (the platform could not decide: never a denial) | any, 5 m (page) |
| Denial spike | `ecosystem_authorization_denied_total` reasons in logs | 5× baseline (ticket) |
| **Backup failure** | the backup job's exit status (cron wrapper posts to a dead-man's switch such as healthchecks.io) | no success in 26 h (page) |
| Checkout failures | `event` `payments.checkout.*failed` | > 5 in 15 m (ticket) |
| Isolation alarms | `tenant.context.missing`, `tenancy.db_scope.missing` | any (page: a correctness alarm, never noise) |
| **Staff authenticator enrolled** (D-037) | `event = "identity.mfa.staff_factor_enrolled"` | any (page the security owner: whoever holds the factor holds the control plane) |
| Startup gate refused | `event = "healthz.check_failed"` with `check` in (`rls_role`, `db_timeouts`) | any (page: a container refused to start on an unsafe database role) |
| Metric series dropped | `event = "metrics.series_dropped"` | any (ticket: a metric outgrew its label ceiling; G-47) |

**Noise control:** Tier 3 dependencies (a product, the AI provider, the email provider) never page the platform on-call directly; they raise tickets for their owners. Paging is reserved for customer-facing or data-integrity failure.

**Proven on staging:**
- Prometheus scraped both targets with all 20 rules loaded.
- With RabbitMQ stopped, `W360BrokerUnavailable` went pending at +29 s and firing at +149 s, and resolved 30 s after the broker returned.
- `W360ProductUnreachable` fired for the real Easy CRM (broken TLS).

See phase-12-status items 27–28.

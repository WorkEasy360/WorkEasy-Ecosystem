# 11 — Observability

**Status:** Accepted (Phase 0)
**Owner:** Architecture
**Last reviewed:** 2026-09-21

---

## 1. Purpose

Make production incidents diagnosable without guessing. The standard this document holds itself to:

> Given an alert at 03:00, an engineer who did not write the code must be able to determine **which tenant, which product, which request, and which dependency** is involved, using queries — not intuition.

Observability is designed in Phase 0 because instrumentation retrofitted after an incident is always the wrong instrumentation, and because the cross-cutting fields (`organization_id`, `correlation_id`, `product`) must be present from the first line of code or they are never consistently present.

## 2. The four signals, and what each answers

| Signal | Answers | Cardinality | Retention |
|---|---|---|---|
| **Metrics** | Is something wrong? How bad? | Low (bounded labels) | 15 months downsampled |
| **Traces** | Where is the time going? What called what? | High, sampled | 7 days (30 d for errors) |
| **Logs** | What exactly happened in this case? | Highest | 30 days hot, 1 year cold |
| **Audit** | Who did what, when, to whose data? | Moderate | 7 years, immutable |

**Audit is not logging.** Logs are operational, sampled, and may be dropped under load. Audit is a business and legal record: complete, immutable, tenant-visible, and never sampled. Conflating them means either dropping audit records under load or paying audit-grade storage costs for debug lines.

## 3. Correlation — the spine

One identifier ties everything together, and it must survive every hop.

```
Browser  →  X-Request-ID (generated at edge, validated if client-supplied)
              │
              ├─► correlation_id in TenantContext (contextvar)
              ├─► every log line
              ├─► every span (as an attribute)
              ├─► Celery task header  w360_correlation_id
              ├─► DomainEvent.correlation_id
              ├─► outbound webhook header  X-W360-Correlation-Id
              └─► returned to the client in the response header
```

- **Generated at the edge**, not in the app, so failures before the app still correlate.
- **Client-supplied values are validated** (UUID format, length-capped) and never trusted for anything but correlation — a request ID is not a credential and must not appear in an authorization decision.
- **Returned to the client** so a customer reporting "this failed" can quote an ID that locates the exact request. This alone removes most of the guesswork from support escalations.
- `causation_id` chains events to their cause, making a fan-out reconstructible.

## 4. Structured logging

JSON only. Human-readable formatting exists in local development; production emits machine-parseable lines.

```json
{
  "timestamp": "2026-09-21T14:32:11.482Z",
  "level": "error",
  "event": "deal.win.failed",
  "logger": "products.crm.services.deal_services",
  "correlation_id": "0192f3a1-...",
  "request_id": "0192f3a1-...",
  "organization_id": "018f2c5e-...",
  "user_id": "018f2c60-...",
  "membership_id": "018f2c61-...",
  "product": "crm",
  "module": "crm.deals",
  "http_method": "POST",
  "http_path": "/api/v1/orgs/{slug}/crm/deals/{id}/win",
  "http_status": 500,
  "duration_ms": 412,
  "db_query_count": 14,
  "db_time_ms": 88,
  "error_type": "IntegrityError",
  "error_fingerprint": "crm.deal.win.integrity",
  "deal_id": "018f2c62-...",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "environment": "production",
  "release": "2026.09.21-a1b2c3d"
}
```

### Mandatory fields

Injected by middleware and the logging processor chain, never by hand: `timestamp`, `level`, `event`, `logger`, `correlation_id`, `organization_id` (when in tenant context), `trace_id`, `environment`, `release`.

### Rules

1. **`event` is a stable, dotted, machine-readable name** — `deal.win.failed`, not "Failed to win deal for user". Free-text messages cannot be aggregated or alerted on.
2. **Context goes in fields, never interpolated into strings.** `log.info("deal.won", deal_id=x)` — not `f"Deal {x} won"`. String interpolation makes grouping impossible.
3. **No PII in logs.** Never log email addresses, names, phone numbers, addresses, financial details or file contents. Log IDs; join to the data when authorised. Enforced by a CI scanner on known-sensitive field names plus a redaction processor as a backstop.
4. **No secrets, tokens, keys, passwords or session IDs**, including inside exception payloads and request bodies. The redaction processor strips known key patterns before emission.
5. **`error_fingerprint`** groups alike errors regardless of message details, so alerting is stable.
6. **Log levels mean specific things:**

| Level | Meaning | Alerting |
|---|---|---|
| `debug` | Development only | Never |
| `info` | Notable business/system event | No |
| `warning` | Degradation, retry, fallback taken | Dashboard; alert on rate |
| `error` | An operation failed; a user or job is affected | Alert on rate |
| `critical` | Systemic failure; data at risk | Page immediately |

Using `error` for expected conditions (a 404, a validation failure) destroys the value of error rate as a signal. Those are `info`.

7. **Sampling:** `info` sampled at 100 % initially, reduced by volume later; `warning` and above never sampled; **all logs for a request that produced an error are retained in full**, including its `debug` lines (tail-based retention). The failing request is exactly the one you need in full.

## 5. Distributed tracing

OpenTelemetry, exported via OTLP. Even in a monolith, tracing earns its place: it shows the request → task → event → handler chain that logs alone make tedious to reconstruct.

### Instrumented automatically

Django request/response, DRF view dispatch, psycopg (with statement, parameters stripped), Redis, Celery publish and consume, `requests`/`httpx` outbound, S3, template rendering.

### Instrumented manually

Application service boundaries, domain event publish and handle, entitlement and authorization checks (as events on the span, not child spans — they are too frequent), external provider calls with provider and operation attributes, cache hit/miss.

### Span attributes — the ones that matter

Every span carries: `w360.organization_id`, `w360.product`, `w360.module`, `w360.correlation_id`. Never as *metric* labels (cardinality explosion) — but on spans this is exactly right, because it enables "show me all slow traces for tenant X".

### Trace context across async boundaries

The chain must not break at the queue:

```python
# publish
carrier = {}
TraceContextTextMapPropagator().inject(carrier)
task.apply_async(headers={"traceparent": carrier.get("traceparent"), ...})

# consume
ctx = TraceContextTextMapPropagator().extract(self.request.headers or {})
with tracer.start_as_current_span("task.execute", context=ctx, links=[...]):
    ...
```

A **span link** rather than a pure parent-child relationship is used for event handlers, because one event may be handled by several consumers long after the producing trace ended. Links express "caused by" without distorting latency attribution.

### Sampling

Parent-based, with a tail sampler: 1 % of successful requests, **100 % of errors**, 100 % of requests above the p99 budget, 100 % of a configurable debug-tenant list. Head-only sampling at 1 % means the incident you care about is almost certainly not sampled.

## 6. Metrics

Low cardinality is the discipline that keeps a metrics system affordable. **`organization_id` is never a metric label** — with 10,000 tenants and 20 metrics, that is 200,000 series. Per-tenant analysis belongs in traces, logs and the analytics store.

### RED — every request-handling surface

| Metric | Type | Labels |
|---|---|---|
| `http_requests_total` | counter | `method`, `route` (templated), `status_class`, `product` |
| `http_request_duration_seconds` | histogram | `method`, `route`, `product` |
| `http_requests_in_flight` | gauge | `product` |

`route` is the URL *pattern*, never the resolved path — `/api/v1/orgs/{slug}/crm/deals/{id}` — otherwise every object ID becomes a series.

### USE — resources

`db_pool_connections{tier,state}`, `db_query_duration_seconds{operation}`, `db_deadlocks_total`, `redis_command_duration_seconds{command}`, `process_cpu/memory`, `worker_concurrency_used{queue}`.

### Queue health — the most important operational signals

| Metric | Why it matters |
|---|---|
| `queue_oldest_message_age_seconds{queue}` | **The primary SLI.** Depth is misleading; a large draining queue is fine, a small stuck one is not |
| `queue_depth{queue}` | Capacity planning |
| `task_duration_seconds{task,queue}` | Regression detection |
| `task_failures_total{task,reason}` | Reliability |
| `task_retries_total{task}` | Rising retries precede outages |
| `outbox_pending_age_seconds` | Event pipeline health |
| `outbox_pending_count` | Backlog size |
| `dlq_depth{consumer}` | Anything above zero needs a human |

### Platform-specific metrics

```
entitlement_checks_total{result}              # allowed | denied | unknown
entitlement_cache_hit_ratio
entitlement_drift_detected_total              # reconciliation disagreements — should be ~0
authz_checks_total{result}
authz_denials_total{permission}               # spikes indicate a broken role or an attack
tenant_context_missing_total{module}          # MUST be zero; any value is a defect
rls_violation_total                           # MUST be zero; any value is an incident
unscoped_query_total{module}                  # audited escape-hatch usage
```

The last three are **correctness alarms, not performance metrics.** `tenant_context_missing_total > 0` means a code path can run unscoped; it pages regardless of volume.

### Business metrics

Emitted as domain events into the analytics store, not scraped from the transactional database: trials started/converted, subscriptions created/cancelled, MRR movement, seats assigned, product activations, partner-attributed signups, feature adoption.

Keeping these out of the operational metrics system is deliberate — they have different cardinality, retention and correctness requirements, and a dashboard query for revenue should never touch the production primary.

## 7. Health checks

Three endpoints with genuinely different meanings. Conflating them is a classic self-inflicted outage.

| Endpoint | Checks | Failure means | Timeout |
|---|---|---|---|
| `/healthz/live` | Process is responsive. **No dependency checks.** | Restart the container | 1 s |
| `/healthz/ready` | DB reachable, broker reachable, migrations applied | Remove from load balancer | 2 s |
| `/healthz/startup` | Migrations applied, registries loaded, config valid | Container failed to start | 30 s |

**Liveness must never check dependencies.** If it does, a brief database blip restarts every container simultaneously, turning a 30-second degradation into a full outage with cold caches. This mistake is common enough to be worth stating as a rule.

Readiness checks are cached for 5 seconds so a health-check storm cannot itself become load.

A separate authenticated `/healthz/deep` reports per-dependency status for dashboards and never influences orchestration.

## 8. Alerting

### Alert on symptoms, not causes

Alerting on causes ("CPU > 80 %") produces noise and misses novel failures. Alerting on symptoms ("users are getting errors") catches everything that matters. Causes belong on dashboards, to be consulted after a symptom alert fires.

### SLOs

| SLO | Target | Window |
|---|---|---|
| API availability (non-5xx) | 99.9 % | 30 d rolling |
| API latency p95 < 500 ms | 99 % | 30 d rolling |
| Event delivery < 60 s | 99 % | 7 d rolling |
| Billing job success | 99.9 % | 30 d rolling |
| Auth availability | 99.95 % | 30 d rolling |

Alerts fire on **error-budget burn rate**, not on raw thresholds: a fast burn (14.4× for 1 h — 2 % of the monthly budget) pages; a slow burn (6× for 6 h) raises a ticket. This is what keeps 3 a.m. pages meaningful.

### Always pages, regardless of budget

| Condition | Why |
|---|---|
| `rls_violation_total > 0` | Possible cross-tenant exposure |
| `tenant_context_missing_total > 0` | A path can run unscoped |
| Canary tenant sees foreign data | Confirmed isolation breach |
| `entitlement_drift_detected_total` above threshold | Commercial/access divergence |
| Payment reconciliation mismatch | Money is wrong |
| `dlq_depth` rising | Business events are being lost |
| Audit write failure | Compliance record broken |
| Backup failure or restore test failure | Recovery capability lost |

These are not latency problems; they are correctness problems, and they are worth waking someone for at any hour.

### Alert hygiene

Every alert must state **what is wrong, who is affected, what to check first**, and link a runbook. An alert without a runbook is deleted at the next review — it will not help whoever receives it.

```yaml
- alert: OutboxRelayStalled
  expr: outbox_pending_age_seconds > 1800
  for: 5m
  labels: {severity: page, team: platform}
  annotations:
    summary: "Outbox relay stalled — events >30m old"
    impact: "Cross-product automation delayed for all tenants; no data lost."
    first_check: "Celery 'events' queue workers alive? Broker reachable? Poison event at the head?"
    runbook: "https://runbooks.internal/outbox-stalled"
```

## 9. Audit log

```python
class AuditEvent(models.Model):             # platform/audit — append-only
    id              = UUIDField(primary_key=True, default=uuid7)
    organization_id = UUIDField(db_index=True)
    occurred_at     = DateTimeField(db_index=True)
    actor_kind      = CharField()     # user | service | system | staff_break_glass | partner
    actor_user_id   = UUIDField(null=True)
    actor_membership_id = UUIDField(null=True)
    on_behalf_of    = UUIDField(null=True)      # impersonation target
    action          = CharField(db_index=True)  # "crm.deal.updated"
    resource_type   = CharField()
    resource_id     = CharField(null=True)
    changes         = JSONField(null=True)      # field-level before/after, redacted
    outcome         = CharField()               # success | denied | error
    reason          = TextField(null=True)      # required for break-glass and overrides
    ip_hash         = CharField(null=True)
    correlation_id  = UUIDField(db_index=True)
    prev_hash       = CharField(null=True)      # tamper-evident chain
    entry_hash      = CharField()
```

### What is always audited

Authentication (success, failure, MFA, SSO), authorization denials for sensitive permissions, all membership and role changes, **all impersonation and break-glass activity**, entitlement and subscription changes, partner data-access grants and uses, security setting changes, API key lifecycle, data export and bulk delete, and every write performed by staff or a partner.

Ordinary business writes are audited per-module by policy — auditing every field change in every product produces volume nobody reads. Financial and access-control changes are always audited.

### Properties

- **Append-only.** No `UPDATE`, no `DELETE`. Enforced by database grants on the application role, not by application discipline.
- **Hash-chained** per organization (`prev_hash` → `entry_hash`), making silent tampering detectable. Verified by a scheduled job.
- **Customer-visible.** Organizations read their own audit log, including every staff access. This is a trust feature, and it makes internal misuse self-policing.
- **Never sampled, never dropped.** An audit write failure fails the operation it describes — the one place where a logging failure is allowed to fail the request, because an unaudited privileged action is worse than a failed one.
- Written in the same transaction as the change it records, so they cannot diverge.

## 10. Security events

A distinct stream from audit, feeding detection rather than compliance: repeated authentication failures, credential-stuffing patterns, impossible-travel logins, cross-tenant ID probing (repeated 404s on well-formed UUIDs), privilege-escalation attempts, unusual export volume, API keys used from new networks, rate-limit exhaustion, SSRF/webhook validation rejections, and break-glass outside business hours.

These route to the security monitoring pipeline with their own alerting, because their signal shape is different: individually unremarkable events that matter in aggregate and in sequence.

## 11. Dashboards

| Dashboard | Audience | Contents |
|---|---|---|
| **Platform health** | On-call | RED by product, queue ages, DB pools, error budget burn |
| **Tenant health** | Support | Per-org: error rate, latency, entitlement status, recent audit, quota usage |
| **Event pipeline** | Platform | Outbox lag, delivery latency, DLQ, per-consumer breakers |
| **Commercial** | Product/Finance | Trials, conversions, MRR movement, dunning, failed payments |
| **Partner** | Partnerships | Attribution, commissions accrued, payout status |
| **Security** | Security | Auth failures, break-glass sessions, anomalies, isolation alarms |
| **Per-product** | Product teams | That product's RED, adoption, top errors |

The **tenant health** dashboard is the one that most changes day-to-day operations: it turns "customer X says it's slow" from an investigation into a lookup.

## 12. Incident diagnosability checklist

A feature is not production-ready until all are true:

- [ ] Every error path logs with a stable `event` name and `error_fingerprint`
- [ ] Every external call is traced with provider and operation attributes
- [ ] Every background task emits start/finish/failure with duration
- [ ] Failure modes have metrics, not only logs
- [ ] `organization_id` is present on every log line and span in tenant context
- [ ] The runbook exists and has been followed once, in staging
- [ ] Degraded states are visible in the UI, not silent
- [ ] Alerts reference the runbook and state user impact

## 13. Cost control

Observability data can cost more than the system it observes. Controls: metric cardinality budget per service with CI enforcement on new labels; tail-based trace sampling; log level by environment; 30-day hot / 1-year cold log tiering; aggressive downsampling of old metrics; and a quarterly review that deletes unused dashboards and alerts.

Unused telemetry is worse than none — it costs money and dilutes attention during incidents.

## 14. Related documents

- [09-failure-resilience.md](09-failure-resilience.md) — what the alerts are detecting
- [12-security.md](12-security.md) — audit requirements, break-glass visibility
- [03-multi-tenancy.md](03-multi-tenancy.md) — isolation alarms and canary tenants
- [10-performance-concurrency.md](10-performance-concurrency.md) — latency budgets behind the SLOs

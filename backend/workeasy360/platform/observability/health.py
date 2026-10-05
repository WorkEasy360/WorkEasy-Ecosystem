"""Health endpoints - three, with genuinely different meanings.

Conflating them is a classic self-inflicted outage
(11-observability.md section 7):

============ ================================== ==========================
Endpoint     Checks                             Failure means
============ ================================== ==========================
/healthz/live  Process responds. No dependencies. Restart the container
/healthz/ready DB reachable, migrations applied.  Remove from the LB
/healthz/startup Migrations, registries, config.  Container failed to start
/healthz/deep  Per-dependency detail.             Nothing - dashboards only
============ ================================== ==========================

**Liveness must never check dependencies.** If it does, a brief database blip
restarts every container simultaneously, turning a 30-second degradation into
a full outage with cold caches.

Readiness deliberately checks *only* Tier 0/1 dependencies. Email, AI and
payment providers are Tier 3: an outage there must never remove an instance
from the load balancer, because the core platform is still perfectly able to
serve requests.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

#: Readiness results are cached briefly so a health-check storm cannot itself
#: become load on the database.
_readiness_cache: dict[str, Any] = {"at": 0.0, "payload": None, "ok": False}


@dataclass(frozen=True, slots=True)
class DependencyStatus:
    name: str
    ok: bool
    tier: int
    detail: str = ""
    latency_ms: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "tier": self.tier,
            "detail": self.detail,
            "latency_ms": self.latency_ms,
        }


def _timed_check(
    name: str, tier: int, probe: Callable[[], str], *, verbose: bool = False
) -> DependencyStatus:
    """Run one probe. ``verbose`` is for the staff-only deep endpoint.

    The unauthenticated probes (ready, startup) answer with the exception
    *type* only: a driver's message names internal hosts, ports and roles
    (``connection to server at "pgbouncer" (10.0.3.7), port 6432 failed``),
    which is topology an anonymous caller has no business reading (Phase 12,
    G-05). The full message goes to the log, where operators look anyway.
    """
    started = time.perf_counter()
    try:
        detail = probe()
        return DependencyStatus(
            name=name,
            ok=True,
            tier=tier,
            detail=detail,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"[:200]
        logger.error("healthz.check_failed", check=name, detail=message)
        return DependencyStatus(
            name=name,
            ok=False,
            tier=tier,
            detail=message if verbose else type(exc).__name__,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )


# --- probes --------------------------------------------------------------


def _probe_database() -> str:
    with connection.cursor() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()
    return "reachable"


def _probe_migrations() -> str:
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
    if pending:
        raise RuntimeError(f"{len(pending)} migration(s) not applied")
    return "up to date"


def _probe_cache() -> str:
    from django.core.cache import cache

    probe_key = "w360:healthz:probe"
    cache.set(probe_key, "1", 5)
    # A miss is tolerated: Redis is Tier 1 and every correctness path falls
    # back to PostgreSQL when it is down (config/settings/base.py, CACHES), so
    # an outage degrades latency, not correctness. Readiness reports it without
    # failing on it. (Django's RedisCache has no ``ignore_exceptions``: a down
    # Redis raises here and is reported as a failed, non-blocking check.)
    return "reachable" if cache.get(probe_key) == "1" else "degraded (miss)"


def _probe_registries() -> str:
    """Registries must be populated before an instance serves traffic.

    ``contracts`` is L0 so importing it is always legal. The permission count
    comes from a registered probe rather than a direct import of
    ``platform/authz``, which observability may not import (it is P2, above).
    """
    from workeasy360.contracts.events.registry import registry

    events = len(registry.all_refs())
    extra = _run_registered("registries")
    return f"{events} event schemas" + (f", {extra}" if extra else "")


#: Probes contributed by other modules. A module owns its own health
#: definition: observability knows how to *run* and *report* a probe, not what
#: "healthy" means for tenancy, authz or entitlements. This also keeps
#: observability from importing modules above it.
_registered_probes: dict[str, list[tuple[str, Callable[[], str]]]] = {}


def register_probe(group: str, name: str, probe: Callable[[], str]) -> None:
    """Register a named probe into a group ("startup", "registries", "deep")."""
    probes = _registered_probes.setdefault(group, [])
    probes[:] = [(n, p) for n, p in probes if n != name]
    probes.append((name, probe))


def registered_probe_names(group: str) -> list[str]:
    return [name for name, _ in _registered_probes.get(group, [])]


def _run_registered(group: str) -> str:
    """Run a group's probes, joining their summaries. Raises on first failure."""
    parts = [probe() for _name, probe in _registered_probes.get(group, [])]
    return ", ".join(p for p in parts if p)


def _startup_checks() -> list[Any]:
    """The startup contract, shared by ``/healthz/startup`` and the
    ``w360_startup_gate`` command every api and worker container runs before
    it serves (Phase 12)."""
    return [
        _timed_check("postgresql", 0, _probe_database),
        _timed_check("migrations", 0, _probe_migrations),
        _timed_check("registries", 0, _probe_registries),
        # Contributed by platform/tenancy at app-ready: the RLS role
        # assertion (no BYPASSRLS, no table ownership) and the database
        # timeouts. A container that boots with a role that can bypass RLS has
        # no tenant isolation and must never serve traffic (R-02).
        *[
            _timed_check(name, 0, probe)
            for name, probe in _registered_probes.get("startup", [])
        ],
    ]


def startup_failures() -> list[str]:
    """Names of the startup checks that fail now (empty when all pass)."""
    return [check.name for check in _startup_checks() if not check.ok]


# --- views ---------------------------------------------------------------


@require_GET
@never_cache
def liveness(request: HttpRequest) -> JsonResponse:
    """Is this process responsive? Nothing else.

    No database, no cache, no broker. Deliberately trivial.
    """
    return JsonResponse({"status": "alive"}, status=200)


@require_GET
@never_cache
def readiness(request: HttpRequest) -> JsonResponse:
    """Should this instance receive traffic?

    Checks Tier 0 (PostgreSQL) and schema currency. The cache is probed and
    reported but is *not* allowed to fail readiness: Redis is Tier 1 with a
    documented degradation path, and pulling every instance out of the load
    balancer over a cache outage would convert a slowdown into an outage.
    """
    now = time.monotonic()
    ttl = getattr(settings, "READINESS_CACHE_SECONDS", 5)
    if _readiness_cache["payload"] is not None and now - _readiness_cache["at"] < ttl:
        return JsonResponse(
            _readiness_cache["payload"],
            status=200 if _readiness_cache["ok"] else 503,
        )

    checks = [
        _timed_check("postgresql", 0, _probe_database),
        _timed_check("migrations", 0, _probe_migrations),
        _timed_check("cache", 1, _probe_cache),
    ]

    blocking = [c for c in checks if c.tier == 0]
    ok = all(c.ok for c in blocking)

    payload = {
        "status": "ready" if ok else "not_ready",
        "checks": [c.as_dict() for c in checks],
    }
    _readiness_cache.update({"at": now, "payload": payload, "ok": ok})

    if not ok:
        logger.error(
            "healthz.not_ready",
            failed=[c.name for c in blocking if not c.ok],
        )
    return JsonResponse(payload, status=200 if ok else 503)


@require_GET
@never_cache
def startup(request: HttpRequest) -> JsonResponse:
    """Did this container start correctly?

    Stricter than readiness and run once: registries must be loaded and the
    database role must be unable to bypass RLS. A container that boots with a
    BYPASSRLS role has no tenant isolation, and it must never serve traffic.
    """
    checks = _startup_checks()
    ok = all(c.ok for c in checks)
    return JsonResponse(
        {
            "status": "started" if ok else "failed",
            "checks": [c.as_dict() for c in checks],
        },
        status=200 if ok else 503,
    )


@require_GET
@never_cache
def deep(request: HttpRequest) -> JsonResponse:
    """Per-dependency detail for dashboards.

    Never influences orchestration: this endpoint reports Tier 3 providers,
    and a Tier 3 outage must not restart or de-pool anything.
    """
    if not request.user.is_authenticated or not request.user.is_staff:
        return JsonResponse({"code": "not_found", "detail": "Not found."}, status=404)

    checks = [
        _timed_check("postgresql", 0, _probe_database, verbose=True),
        _timed_check("migrations", 0, _probe_migrations, verbose=True),
        _timed_check("cache", 1, _probe_cache, verbose=True),
        _timed_check("registries", 0, _probe_registries, verbose=True),
        _timed_check("broker", 2, _probe_broker, verbose=True),
        # Contributed by integration/events. observability is L1 and must not
        # import L2, so the outbox probe is registered rather than imported.
        *[
            _timed_check(name, 2, probe, verbose=True)
            for name, probe in _registered_probes.get("deep", [])
        ],
    ]
    from workeasy360.platform.observability.metrics import metrics

    return JsonResponse(
        {
            "status": "ok",
            "environment": getattr(settings, "ENVIRONMENT", "unknown"),
            "release": getattr(settings, "RELEASE", "unknown"),
            "checks": [c.as_dict() for c in checks],
            "metrics": metrics.snapshot(),
        },
        status=200,
    )


def _probe_broker() -> str:
    from config.celery import app as celery_app

    conn = celery_app.connection()
    try:
        # Bounded: an unreachable broker must fail fast, not hang the probe.
        conn.ensure_connection(max_retries=0, timeout=1.0)
        return "reachable"
    finally:
        conn.release()


# --- metrics (Phase 12, G-35) ---------------------------------------------------
#
# A Prometheus text endpoint for the scraper, ``/healthz/metrics``. It exports
# **gauges computed at scrape time** from authoritative state - broker queue
# depth, outbox and due-work lag, product integration health, partner domain
# state, database connections - never this process's in-memory counters,
# which with several gunicorn workers would be one worker's partial view.
# Event *rates* (HTTP 5xx, OIDC denials, entitlement denials, checkout
# failures) come from the edge's own metrics and from the structured log
# stream (docs/phase-12/08-observability-and-alerts.md).
#
# Every gauge read is either a control-plane table or an aggregate that needs
# no tenant: a count across an RLS-protected table would silently read zero,
# which is worse than no metric.

Sample = tuple[str, dict[str, str], float]
_registered_gauges: dict[str, Callable[[], list[Sample]]] = {}


def register_gauges(name: str, collect: Callable[[], list[Sample]]) -> None:
    """A module contributes its own gauges, exactly as it contributes probes."""
    _registered_gauges[name] = collect


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _render(samples: list[Sample]) -> str:
    lines: list[str] = []
    for name, labels, value in samples:
        rendered = ",".join(f'{k}="{_escape(str(v))}"' for k, v in sorted(labels.items()))
        lines.append(
            f"{name}{{{rendered}}} {float(value)}"
            if rendered
            else f"{name} {float(value)}"
        )
    return "\n".join(lines) + "\n"


def _core_samples() -> list[Sample]:
    samples: list[Sample] = [
        (
            "w360_info",
            {
                "release": str(getattr(settings, "RELEASE", "unknown")),
                "environment": str(getattr(settings, "ENVIRONMENT", "unknown")),
            },
            1.0,
        )
    ]
    try:
        _probe_database()
        samples.append(("w360_database_up", {}, 1.0))
        with connection.cursor() as cur:
            cur.execute(
                "SELECT coalesce(state, 'unknown'), count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() GROUP BY 1"
            )
            for state, count in cur.fetchall():
                samples.append(
                    ("w360_database_connections", {"state": str(state)}, count)
                )
            # WAL archiving is what point-in-time recovery - and so the RPO -
            # rests on. The Phase 12 rehearsal found it failing silently on
            # every segment; these make that an alert (W360WalArchivingFailing).
            cur.execute(
                "SELECT (SELECT setting FROM pg_settings WHERE name = 'archive_mode'), "
                "archived_count, failed_count, "
                "coalesce(extract(epoch FROM now() - last_archived_time), -1) "
                "FROM pg_stat_archiver"
            )
            mode, archived, failed, age = cur.fetchone()
            samples.append(
                ("w360_wal_archive_enabled", {}, 0.0 if mode == "off" else 1.0)
            )
            samples.append(("w360_wal_archived_total", {}, float(archived)))
            samples.append(("w360_wal_archive_failed_total", {}, float(failed)))
            samples.append(("w360_wal_last_archived_age_seconds", {}, float(age)))
    except Exception:
        samples.append(("w360_database_up", {}, 0.0))
    try:
        samples.append(
            ("w360_cache_up", {}, 1.0 if _probe_cache() == "reachable" else 0.0)
        )
    except Exception:
        samples.append(("w360_cache_up", {}, 0.0))
    samples.extend(_broker_samples())
    return samples


def _broker_samples() -> list[Sample]:
    """Depth and consumers per queue group, by passive declaration."""
    from config.celery import app as celery_app

    samples: list[Sample] = []
    conn = celery_app.connection_for_read()
    try:
        conn.ensure_connection(max_retries=0, timeout=1.0)
        samples.append(("w360_broker_up", {}, 1.0))
        for queue in getattr(settings, "CELERY_QUEUE_GROUPS", ()):
            try:
                channel = conn.channel()
                try:
                    _name, depth, consumers = channel.queue_declare(
                        queue=queue, passive=True
                    )
                finally:
                    channel.close()
                samples.append(("w360_queue_messages", {"queue": queue}, depth))
                samples.append(("w360_queue_consumers", {"queue": queue}, consumers))
            except Exception:
                # Not declared yet (no worker has started for it): no consumer.
                samples.append(("w360_queue_consumers", {"queue": queue}, 0.0))
    except Exception:
        samples.append(("w360_broker_up", {}, 0.0))
    finally:
        conn.release()
    return samples


@require_GET
@never_cache
def metrics_view(request: HttpRequest) -> HttpResponse:
    """Prometheus exposition for the scraper. 404 unless the bearer token
    matches ``W360_METRICS_TOKEN`` (unset: the endpoint does not exist)."""
    import hmac

    expected = str(getattr(settings, "METRICS_TOKEN", "") or "")
    supplied = request.headers.get("Authorization", "")
    if not expected or not hmac.compare_digest(
        supplied.encode("utf-8"), f"Bearer {expected}".encode()
    ):
        return JsonResponse({"code": "not_found", "detail": "Not found."}, status=404)

    samples = _core_samples()
    for source, collect in sorted(_registered_gauges.items()):
        try:
            samples.extend(collect())
            samples.append(("w360_gauge_source_up", {"source": source}, 1.0))
        except Exception as exc:
            logger.error(
                "metrics.gauge_failed", source=source, error_type=type(exc).__name__
            )
            samples.append(("w360_gauge_source_up", {"source": source}, 0.0))
    return HttpResponse(
        _render(samples), content_type="text/plain; version=0.0.4; charset=utf-8"
    )

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
from django.http import HttpRequest, JsonResponse
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


def _timed_check(name: str, tier: int, probe: Callable[[], str]) -> DependencyStatus:
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
        return DependencyStatus(
            name=name,
            ok=False,
            tier=tier,
            # Type and message only - never the connection string, which
            # would put credentials in an unauthenticated response.
            detail=f"{type(exc).__name__}: {exc}"[:200],
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
    # A miss is tolerated: the cache backend is configured with
    # ignore_exceptions, so a Redis outage degrades latency rather than
    # correctness. Readiness reports it without failing on it.
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
    checks = [
        _timed_check("postgresql", 0, _probe_database),
        _timed_check("migrations", 0, _probe_migrations),
        _timed_check("registries", 0, _probe_registries),
        # Contributed by platform/tenancy at app-ready: the RLS role
        # assertion. A container that boots with a BYPASSRLS role has no
        # tenant isolation and must never serve traffic (R-02).
        *[
            _timed_check(name, 0, probe)
            for name, probe in _registered_probes.get("startup", [])
        ],
    ]
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
        _timed_check("postgresql", 0, _probe_database),
        _timed_check("migrations", 0, _probe_migrations),
        _timed_check("cache", 1, _probe_cache),
        _timed_check("registries", 0, _probe_registries),
        _timed_check("broker", 2, _probe_broker),
        # Contributed by integration/events. observability is L1 and must not
        # import L2, so the outbox probe is registered rather than imported.
        *[
            _timed_check(name, 2, probe)
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


def _raise_if(problems: list[str], ok_message: str) -> str:
    if problems:
        raise RuntimeError("; ".join(problems))
    return ok_message


def _probe_broker() -> str:
    from config.celery import app as celery_app

    conn = celery_app.connection()
    try:
        # Bounded: an unreachable broker must fail fast, not hang the probe.
        conn.ensure_connection(max_retries=0, timeout=1.0)
        return "reachable"
    finally:
        conn.release()

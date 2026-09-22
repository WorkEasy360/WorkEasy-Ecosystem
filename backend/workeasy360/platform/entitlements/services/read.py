"""The entitlement read path - what products call.

Three cache layers, in order (06-entitlements.md section 4.4):

    request-local  ->  Django cache (version-keyed)  ->  EntitlementSnapshot

R-14 is about whether this meets a 1ms p50 / 5ms p95 budget. The design
answer is that the *first* check in a request may touch the database, and
every subsequent check in the same request is a dict lookup - so the cost
that matters is per-request, not per-check. The benchmark in
docs/phase-1/entitlement-benchmark.md measures both.

Failure behaviour is stated precisely, because "fail closed" and "graceful
degradation" pull in opposite directions here:

- Cache down            -> read the snapshot from PostgreSQL. Slower, correct.
- Snapshot row missing  -> compute from Entitlement rows, then self-heal.
- Provisioning lagging  -> entitlements are *stale*, not wrong. The customer
                           keeps what they had.
- Anything unknown      -> deny.

The governing rule: **absence of information never grants, and never
revokes.** An org with no entitlement row is denied because the default is
``not_entitled``; an org whose provisioning worker is behind keeps its
existing access because revocation requires a positive durable decision.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from django.utils import timezone

from workeasy360.contracts.enums import EntitlementReason, EntitlementStatus
from workeasy360.contracts.errors import EntitlementDenied
from workeasy360.contracts.ports.entitlements import (
    UNLIMITED,
    EntitlementDecision,
    FeatureValue,
)
from workeasy360.contracts.values.identifiers import FeatureKey, ProductCode
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import (
    ENTITLEMENT_CACHE_HITS,
    ENTITLEMENT_CACHE_MISSES,
    ENTITLEMENT_CHECK_DURATION,
    ENTITLEMENT_CHECKS,
    metrics,
    timed,
)

logger = get_logger(__name__)


@dataclass
class _RequestCache:
    """Per-request memo of resolved snapshots.

    Keyed by organization because a single request may legitimately touch
    more than one (an internal cross-org report), and keying by "the current
    org" would then return the wrong answer.
    """

    snapshots: dict[UUID, ResolvedEntitlements] = field(default_factory=dict)
    hits: int = 0
    misses: int = 0


_request_cache: ContextVar[_RequestCache | None] = ContextVar(
    "w360_entitlement_cache", default=None
)


@contextmanager
def entitlement_request_scope() -> Iterator[_RequestCache]:
    """Memoise entitlement reads for the duration of a request or task.

    Installed for requests by ``EntitlementRequestScopeMiddleware``, which
    sits inside the tenancy middleware. Without it every check re-reads the
    cache backend; with it, checks after the first are dictionary lookups, and
    that is the difference between the R-14 budget being per-request and being
    per-check.

    A task that performs many checks should enter it explicitly. It is not
    installed by ``TenantTask``, because ``platform/tenancy`` sits below this
    module in the platform layering contract and may not import it.
    """
    cache = _RequestCache()
    token = _request_cache.set(cache)
    try:
        yield cache
    finally:
        _request_cache.reset(token)


def request_cache_stats() -> tuple[int, int]:
    """(hits, misses) for the active scope. Used by the R-14 benchmark."""
    cache = _request_cache.get()
    return (cache.hits, cache.misses) if cache else (0, 0)


@dataclass(frozen=True, slots=True)
class ResolvedEntitlements:
    """One organization's fully resolved entitlement state."""

    organization_id: UUID
    version: int
    products: dict[str, dict[str, Any]]
    features: dict[str, Any]

    def product_entry(self, product: str) -> dict[str, Any] | None:
        return self.products.get(str(product))


# --- public read API ------------------------------------------------------


def check(
    organization_id: UUID, product: ProductCode | str, *, write: bool = False
) -> EntitlementDecision:
    """Whether the organization may use ``product``.

    ``write=True`` additionally requires a status that permits writes, so a
    suspended entitlement reads but does not mutate.
    """
    resolved = _resolve(organization_id)
    entry = resolved.product_entry(str(product))

    if entry is None:
        metrics.increment(ENTITLEMENT_CHECKS, result="denied")
        return EntitlementDecision.denied(EntitlementReason.NOT_ENTITLED)

    try:
        status = EntitlementStatus(entry.get("status", ""))
    except ValueError:
        # An unrecognised status is treated as unknown and denied. Guessing
        # would be the one failure mode that grants access by accident.
        logger.error(
            "entitlement.status.unknown",
            product=str(product),
            status=entry.get("status"),
        )
        metrics.increment(ENTITLEMENT_CHECKS, result="unknown")
        return EntitlementDecision.denied(EntitlementReason.UNKNOWN)

    if not status.allows_read:
        metrics.increment(ENTITLEMENT_CHECKS, result="denied")
        return EntitlementDecision(
            allowed=False,
            status=status,
            write_allowed=False,
            reason_code=EntitlementReason.EXPIRED,
            upgrade_hint=f"resubscribe:{product}",
        )

    write_allowed = status.allows_write
    if write and not write_allowed:
        metrics.increment(ENTITLEMENT_CHECKS, result="denied")
        return EntitlementDecision(
            allowed=False,
            status=status,
            write_allowed=False,
            reason_code=EntitlementReason.READ_ONLY
            if status is EntitlementStatus.SUSPENDED
            else EntitlementReason.SUSPENDED,
            grace_ends_at=_parse_dt(entry.get("grace_ends_at")),
            upgrade_hint=f"reactivate:{product}",
        )

    metrics.increment(ENTITLEMENT_CHECKS, result="allowed")
    return EntitlementDecision(
        allowed=True,
        status=status,
        write_allowed=write_allowed,
        reason_code=EntitlementReason.OK,
        grace_ends_at=_parse_dt(entry.get("grace_ends_at")),
    )


def require(
    organization_id: UUID, product: ProductCode | str, *, write: bool = False
) -> EntitlementDecision:
    """Raise ``EntitlementDenied`` unless allowed."""
    decision = check(organization_id, product, write=write)
    if not decision.allowed:
        raise EntitlementDenied(
            product=str(product),
            reason_code=decision.reason_code.value,
            upgrade_hint=decision.upgrade_hint,
        )
    return decision


def feature(
    organization_id: UUID, key: FeatureKey | str, *, default: FeatureValue = None
) -> FeatureValue:
    """Resolved value of a feature, most-generous-wins across sources."""
    resolved = _resolve(organization_id)
    return resolved.features.get(str(key), default)


def require_feature(organization_id: UUID, key: FeatureKey | str) -> FeatureValue:
    value = feature(organization_id, key)
    if not value:
        raise EntitlementDenied(
            feature=str(key),
            reason_code=EntitlementReason.NOT_ENTITLED.value,
            upgrade_hint=f"upgrade:{key}",
        )
    return value


def quota(organization_id: UUID, key: FeatureKey | str, *, default: int = 0) -> int:
    """Numeric limit for a quota feature.

    Returns ``UNLIMITED`` (-1) when unlimited, so callers write one guard
    (``if limit != UNLIMITED and used >= limit``) rather than branching on
    None as well.
    """
    value = feature(organization_id, key, default=default)
    if value is None:
        return default
    if isinstance(value, bool):
        # A boolean feature read as a quota is a caller bug, but returning
        # True as 1 would silently impose a limit of one.
        return UNLIMITED if value else 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def within_quota(organization_id: UUID, key: FeatureKey | str, used: int) -> bool:
    limit = quota(organization_id, key)
    return limit == UNLIMITED or used < limit


def has_seat(
    organization_id: UUID, membership_id: UUID, product: ProductCode | str
) -> bool:
    """Whether this member holds a seat in the product's pool.

    Not cached in the snapshot: seat assignments change far more often than
    entitlements, and a stale "yes" would let an unlicensed user work.
    """
    from workeasy360.platform.entitlements.models import SeatAssignment

    return SeatAssignment.objects.filter(
        organization_id=organization_id,
        membership_id=membership_id,
        seat_grant__pool_key=str(product),
        released_at__isnull=True,
    ).exists()


def products(organization_id: UUID) -> Sequence[ProductCode]:
    """Every product the organization currently holds."""
    resolved = _resolve(organization_id)
    return [
        ProductCode(code)
        for code, entry in resolved.products.items()
        if EntitlementStatus(entry.get("status", "expired")).allows_read
    ]


# --- resolution -----------------------------------------------------------


def _resolve(organization_id: UUID) -> ResolvedEntitlements:
    """Fetch the resolved state, through the three cache layers.

    Timed, because ENTITLEMENT_P50_BUDGET_MS and ENTITLEMENT_P95_BUDGET_MS are
    settings the system is supposed to be held to, and until this was observed
    the only thing that ever measured them was the benchmark.
    """
    with timed(ENTITLEMENT_CHECK_DURATION):
        return _resolve_timed(organization_id)


def _resolve_timed(organization_id: UUID) -> ResolvedEntitlements:
    local = _request_cache.get()
    if local is not None:
        cached = local.snapshots.get(organization_id)
        if cached is not None:
            local.hits += 1
            metrics.increment(ENTITLEMENT_CACHE_HITS, layer="request")
            return cached
        local.misses += 1

    resolved = _resolve_from_shared_cache(organization_id)

    if local is not None:
        local.snapshots[organization_id] = resolved
    return resolved


def _resolve_from_shared_cache(organization_id: UUID) -> ResolvedEntitlements:
    from django.conf import settings
    from django.core.cache import cache

    version = _snapshot_version(organization_id)
    cache_key = f"w360:{organization_id}:ent:v{version}"

    try:
        payload = cache.get(cache_key)
    except Exception:  # pragma: no cover - cache backend down
        payload = None

    if payload is not None:
        metrics.increment(ENTITLEMENT_CACHE_HITS, layer="shared")
        return ResolvedEntitlements(
            organization_id=organization_id,
            version=version,
            products=payload.get("products", {}),
            features=payload.get("features", {}),
        )

    metrics.increment(ENTITLEMENT_CACHE_MISSES, layer="shared")
    resolved = _resolve_from_database(organization_id)

    try:
        cache.set(
            cache_key,
            {"products": resolved.products, "features": resolved.features},
            getattr(settings, "ENTITLEMENT_CACHE_TTL_S", 300),
        )
    except Exception:  # noqa: S110 - pragma: no cover
        # Redis down means the snapshot is read from PostgreSQL next time.
        # Slower, still correct (09-failure-resilience.md section 3.1).
        pass
    return resolved


def _snapshot_version(organization_id: UUID) -> int:
    from workeasy360.platform.entitlements.models import EntitlementSnapshot

    version = (
        EntitlementSnapshot.objects.filter(organization_id=organization_id)
        .values_list("version", flat=True)
        .first()
    )
    return version or 0


def _resolve_from_database(organization_id: UUID) -> ResolvedEntitlements:
    """Read the snapshot; if it is missing, compute and self-heal."""
    from workeasy360.platform.entitlements.models import EntitlementSnapshot

    snapshot = EntitlementSnapshot.objects.filter(organization_id=organization_id).first()

    if snapshot is not None:
        return ResolvedEntitlements(
            organization_id=organization_id,
            version=snapshot.version,
            products=snapshot.products or {},
            features=snapshot.features or {},
        )

    logger.warning(
        "entitlement.snapshot.missing",
        organization_id=str(organization_id),
    )
    return _compute_live(organization_id)


def _compute_live(organization_id: UUID) -> ResolvedEntitlements:
    """Resolve directly from Entitlement rows.

    The fallback when no snapshot exists. Deliberately the same resolution
    logic the writer uses - "most generous wins" - so the fallback cannot
    disagree with the materialised answer.
    """
    from workeasy360.platform.entitlements.models import (
        Entitlement,
        FeatureEntitlement,
    )

    now = timezone.now()
    products_out: dict[str, dict[str, Any]] = {}

    for row in Entitlement.objects.filter(organization_id=organization_id):
        if not row.is_effective_at(now):
            continue
        existing = products_out.get(row.product_code)
        candidate = {
            "status": row.status,
            "seats": row.seats_total,
            "write": row.status_enum.allows_write,
            "grace_ends_at": row.grace_ends_at.isoformat() if row.grace_ends_at else None,
        }
        products_out[row.product_code] = _more_generous_product(existing, candidate)

    features_out: dict[str, Any] = {}
    for row in FeatureEntitlement.objects.filter(organization_id=organization_id):
        if row.effective_from > now:
            continue
        if row.effective_to is not None and row.effective_to <= now:
            continue
        features_out[row.feature_key] = _more_generous_feature(
            features_out.get(row.feature_key), row.value
        )

    return ResolvedEntitlements(
        organization_id=organization_id,
        version=0,
        products=products_out,
        features=features_out,
    )


#: Access-granting statuses, most generous first. Used to rank overlapping
#: sources so that adding a grant can never *reduce* access - the property
#: that prevents the worst support incidents.
_STATUS_RANK = {
    EntitlementStatus.ACTIVE.value: 6,
    EntitlementStatus.TRIAL.value: 5,
    EntitlementStatus.GRACE.value: 4,
    EntitlementStatus.PAST_DUE.value: 3,
    EntitlementStatus.CANCELLED.value: 2,
    EntitlementStatus.SUSPENDED.value: 1,
    EntitlementStatus.EXPIRED.value: 0,
}


def _more_generous_product(
    existing: dict[str, Any] | None, candidate: dict[str, Any]
) -> dict[str, Any]:
    if existing is None:
        return candidate
    winner = (
        candidate
        if _STATUS_RANK.get(candidate["status"], 0)
        > _STATUS_RANK.get(existing["status"], 0)
        else existing
    )
    # Seats sum across active sources rather than taking the max: two
    # subscriptions of 5 seats each genuinely provide 10.
    seats = [s for s in (existing.get("seats"), candidate.get("seats")) if s is not None]
    return {**winner, "seats": sum(seats) if seats else None}


def _more_generous_feature(existing: Any, candidate: Any) -> Any:
    """boolean: any True wins. quota: max, with -1 beating any number."""
    if existing is None:
        return candidate
    if isinstance(existing, bool) or isinstance(candidate, bool):
        return bool(existing) or bool(candidate)
    if isinstance(existing, int) and isinstance(candidate, int):
        if UNLIMITED in (existing, candidate):
            return UNLIMITED
        return max(existing, candidate)
    return candidate


def _parse_dt(value: Any):
    if not value:
        return None
    from datetime import datetime

    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError:
        return None


# --- the port implementation ---------------------------------------------


class DjangoEntitlementService:
    """Concrete ``EntitlementService`` / ``EntitlementGate``.

    Products depend on the protocol in ``contracts/ports/entitlements.py``,
    not on this class, so the implementation can change without touching a
    single caller.
    """

    check = staticmethod(check)
    require = staticmethod(require)
    feature = staticmethod(feature)
    require_feature = staticmethod(require_feature)
    quota = staticmethod(quota)
    has_seat = staticmethod(has_seat)
    products = staticmethod(products)


entitlements = DjangoEntitlementService()

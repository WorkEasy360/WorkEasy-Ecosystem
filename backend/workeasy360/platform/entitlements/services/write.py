"""Entitlement writes - importable ONLY by ``commercial/provisioning``.

Enforced by an import-linter ``forbidden`` contract naming this exact module
as the target. Products, other commercial modules and interfaces all fail the
build if they import it.

One writer means one place to audit, one place to test, and one place to fix.
Drift then has exactly one possible source (ADR-005).

Nothing here decides *what* an organization should have - that is
``provisioning.compute``, a pure function of durable state. This module only
applies a computed result idempotently.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from django.db.models import F, Q, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from workeasy360.contracts.enums import EntitlementSourceKind, EntitlementStatus
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

#: A seat pool whose capacity dropped below its occupancy. Never zero for
#: long in a healthy system: it means a customer has members awaiting a
#: remediation decision.
SEAT_OVER_LIMIT = "entitlement_seat_over_limit_total"


@dataclass(frozen=True, slots=True)
class ComputedProduct:
    product_code: str
    status: EntitlementStatus
    source_kind: EntitlementSourceKind
    source_ref: str
    seats_total: int | None = None
    effective_from: Any = None
    effective_to: Any = None
    grace_ends_at: Any = None


@dataclass(frozen=True, slots=True)
class ComputedFeature:
    feature_key: str
    value: Any
    kind: str
    source_ref: str
    effective_from: Any = None
    effective_to: Any = None


@dataclass(frozen=True, slots=True)
class ComputedSeatPool:
    """Seat capacity from one source, for one pool.

    ``pool_key`` rather than ``product_code`` because a bundle may grant 10
    seats usable across CRM and Projects. Per-product pools are the common
    case (``pool_key == product_code``); shared pools are the enterprise
    case, and one model covers both.
    """

    pool_key: str
    seats_total: int
    source_ref: str


@dataclass(frozen=True, slots=True)
class ComputedEntitlements:
    """The output of ``compute_entitlements`` - a complete desired state.

    Complete rather than a delta, deliberately. A diff-from-truth model is
    immune to duplicated and out-of-order provider events, which are normal
    with payment providers; an incremental "apply this delta" model is not
    (06-entitlements.md section 5.2).
    """

    organization_id: UUID
    products: tuple[ComputedProduct, ...] = ()
    features: tuple[ComputedFeature, ...] = ()
    seats: tuple[ComputedSeatPool, ...] = ()
    computation_id: UUID = field(default_factory=uuid.uuid4)


def apply_entitlements(computed: ComputedEntitlements) -> int:
    """Write a computed state idempotently. Returns the new snapshot version.

    Idempotent and diff-based: re-running with the same input produces the
    same rows and the same snapshot content. That property is what makes the
    system recoverable from any inconsistency by a single reconciliation pass.

    Runs in one transaction with the snapshot rewrite, so a reader never sees
    entitlement rows and a snapshot that disagree.
    """
    from workeasy360.platform.entitlements.models import (
        Entitlement,
        FeatureEntitlement,
    )
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    now = timezone.now()
    org_id = computed.organization_id

    # Two independent layers must both be satisfied:
    #   unscoped_access()       relaxes the ORM manager (Layer 2)
    #   organization_db_scope() satisfies the RLS policy (Layer 3)
    # Relaxing only the first leaves every write refused by WITH CHECK, which
    # is the defence-in-depth property working rather than a bug to route
    # around. Provisioning acts on exactly one organization per call, so
    # entering that organization's database scope is correct and narrow.
    #
    # The scope opens the transaction as well, and puts back whatever tenant
    # variable it found. Setting it bare - as this did - leaves provisioning's
    # organization bound to the *caller's* transaction, because set_config is
    # reverted by transaction end and not by savepoint release. A request that
    # provisions one organization and then reads another would have read the
    # wrong one.
    with (
        organization_db_scope(org_id),
        unscoped_access(
            reason="Provisioning writes entitlements outside a request tenant context",
            module="platform.entitlements.services.write",
        ),
    ):
        # Serialise concurrent recomputes for one organization. Last writer
        # wins safely because compute is deterministic: two racing runs from
        # the same durable state produce the same answer
        # (10-performance-concurrency.md section 12, contention point 7).
        _advisory_lock(org_id)

        # One statement per table rather than one per row.
        #
        # ``update_or_create`` is a SELECT followed by an INSERT or UPDATE,
        # so the previous shape cost two queries per entitlement, per feature
        # and per seat pool - an N+1 on the one path that runs for every
        # organization on a schedule, where nobody is watching the query
        # count. ``INSERT ... ON CONFLICT DO UPDATE`` is the same operation
        # with the same idempotence, in one round trip.
        if computed.products:
            Entitlement.unscoped.bulk_create(
                [
                    Entitlement(
                        organization_id=org_id,
                        product_code=product.product_code,
                        source_ref=product.source_ref,
                        status=product.status.value,
                        source_kind=product.source_kind.value,
                        seats_total=product.seats_total,
                        effective_from=product.effective_from or now,
                        effective_to=product.effective_to,
                        grace_ends_at=product.grace_ends_at,
                        computed_at=now,
                        computation_id=computed.computation_id,
                    )
                    for product in computed.products
                ],
                update_conflicts=True,
                unique_fields=["organization", "product_code", "source_ref"],
                update_fields=[
                    "status",
                    "source_kind",
                    "seats_total",
                    "effective_from",
                    "effective_to",
                    "grace_ends_at",
                    "computed_at",
                    "computation_id",
                    "updated_at",
                ],
            )

        # Rows absent from the computed state are expired rather than
        # deleted: deleting would lose the history a support engineer needs
        # to answer "what did they have last month?".
        #
        # Identified by ``computation_id``. Every row this pass wrote carries
        # the current one, so anything else is by definition stale - which is
        # both cheaper than comparing tuples in Python and impossible to get
        # subtly wrong as the key grows.
        Entitlement.unscoped.filter(organization_id=org_id).exclude(
            computation_id=computed.computation_id
        ).exclude(status=EntitlementStatus.EXPIRED.value).update(
            status=EntitlementStatus.EXPIRED.value,
            effective_to=Coalesce(F("effective_to"), Value(now)),
            computed_at=now,
            computation_id=computed.computation_id,
            updated_at=now,
        )

        if computed.features:
            FeatureEntitlement.unscoped.bulk_create(
                [
                    FeatureEntitlement(
                        organization_id=org_id,
                        feature_key=feature.feature_key,
                        source_ref=feature.source_ref,
                        value=feature.value,
                        kind=feature.kind,
                        effective_from=feature.effective_from or now,
                        effective_to=feature.effective_to,
                    )
                    for feature in computed.features
                ],
                update_conflicts=True,
                unique_fields=["organization", "feature_key", "source_ref"],
                update_fields=[
                    "value",
                    "kind",
                    "effective_from",
                    "effective_to",
                    "updated_at",
                ],
            )

        # Features carry no expiry of their own - the resolver reads them as
        # a set - so a feature no longer granted is removed rather than
        # marked. One statement, whatever the count.
        stale_features = FeatureEntitlement.unscoped.filter(organization_id=org_id)
        keep = _pair_filter(
            "feature_key",
            "source_ref",
            {(f.feature_key, f.source_ref) for f in computed.features},
        )
        if keep is not None:
            stale_features = stale_features.exclude(keep)
        stale_features.delete()

        _apply_seat_grants(org_id, computed)

        version = _rewrite_snapshot(org_id, computed, now)

    _invalidate_cache(org_id, version)

    logger.info(
        "entitlement.applied",
        organization_id=str(org_id),
        version=version,
        products=len(computed.products),
        features=len(computed.features),
        computation_id=str(computed.computation_id),
    )
    return version


def _apply_seat_grants(org_id: UUID, computed: ComputedEntitlements) -> None:
    """Reconcile seat **capacity** to the computed state.

    Two rules, both learned from what goes wrong otherwise:

    **A stale grant is zeroed, never deleted.** ``SeatAssignment`` cascades
    from ``SeatGrant``, so deleting a grant would silently delete every
    assignment under it - revoking members' access as a side effect of a
    commercial recomputation, with no record that it happened. Zeroing
    removes the capacity and leaves the assignments to be marked over-limit,
    which is the documented behaviour (06-entitlements.md section 6).

    **Over-limit marking runs for every touched pool**, not only shrinking
    ones, so that capacity *returning* clears the flag automatically. A flag
    that only ever gets set becomes permanent noise nobody trusts.
    """
    from workeasy360.platform.entitlements.models import SeatAssignment, SeatGrant

    if computed.seats:
        SeatGrant.unscoped.bulk_create(
            [
                SeatGrant(
                    organization_id=org_id,
                    pool_key=pool.pool_key,
                    source_ref=pool.source_ref,
                    seats_total=pool.seats_total,
                )
                for pool in computed.seats
            ],
            update_conflicts=True,
            unique_fields=["organization", "pool_key", "source_ref"],
            update_fields=["seats_total", "updated_at"],
        )

    stale = SeatGrant.unscoped.filter(organization_id=org_id).exclude(seats_total=0)
    keep = _pair_filter(
        "pool_key", "source_ref", {(p.pool_key, p.source_ref) for p in computed.seats}
    )
    if keep is not None:
        stale = stale.exclude(keep)
    stale.update(seats_total=0, updated_at=timezone.now())

    capacity: dict[str, int] = {}
    for pool_key, total in SeatGrant.unscoped.filter(organization_id=org_id).values_list(
        "pool_key", "seats_total"
    ):
        capacity[pool_key] = capacity.get(pool_key, 0) + total

    _reconcile_over_limit(org_id, capacity, SeatAssignment)


def _reconcile_over_limit(
    org_id: UUID, capacity: dict[str, int], assignment_model: Any
) -> None:
    """Flag - never revoke - assignments beyond their pool's capacity.

    One read for the whole organization and at most two writes, rather than
    three queries per pool: a customer on a five-product suite would
    otherwise pay fifteen queries here on every recomputation, almost always
    to discover that nothing changed.

    Deterministic: the **most recently assigned** lose their seat first, so a
    retry flags the same members rather than shuffling who is affected. The
    flag clears itself when capacity returns, because a flag that only ever
    gets set becomes noise nobody trusts.
    """
    active = list(
        assignment_model.unscoped.filter(organization_id=org_id, released_at__isnull=True)
        .values("id", "over_limit_since", "seat_grant__pool_key")
        .order_by("assigned_at", "id")
    )
    if not active:
        return

    used: dict[str, int] = {}
    keep: list[UUID] = []
    strand: list[UUID] = []
    stranded_pools: set[str] = set()

    for row in active:
        pool_key = row["seat_grant__pool_key"]
        used[pool_key] = used.get(pool_key, 0) + 1
        if used[pool_key] <= capacity.get(pool_key, 0):
            if row["over_limit_since"] is not None:
                keep.append(row["id"])
        elif row["over_limit_since"] is None:
            strand.append(row["id"])
            stranded_pools.add(pool_key)

    if keep:
        restored = assignment_model.unscoped.filter(id__in=keep).update(
            over_limit_since=None
        )
        logger.info("entitlement.seat.over_limit_cleared", restored=restored)
    if strand:
        flagged = assignment_model.unscoped.filter(id__in=strand).update(
            over_limit_since=timezone.now()
        )
        for pool_key in sorted(stranded_pools):
            metrics.increment(SEAT_OVER_LIMIT, pool_key=pool_key)
        logger.warning(
            "entitlement.seat.over_limit",
            flagged=flagged,
            pools=sorted(stranded_pools),
        )


def _pair_filter(first: str, second: str, pairs: set[tuple[str, str]]) -> Any:
    """A ``Q`` matching any of ``pairs``, or None when there are none.

    Lets a "delete everything except these" become one statement instead of
    a Python loop issuing one per row. Returning None rather than an empty
    ``Q`` is deliberate: an empty ``Q`` matches *everything*, so excluding it
    would delete nothing at exactly the moment everything should go.
    """
    if not pairs:
        return None
    clauses = [Q(**{first: a, second: b}) for a, b in sorted(pairs)]
    combined = clauses[0]
    for clause in clauses[1:]:
        combined |= clause
    return combined


def _rewrite_snapshot(org_id: UUID, computed: ComputedEntitlements, now: Any) -> int:
    """Materialise the resolved answer into one row.

    Resolution is "most generous wins": a boolean is True if any source grants
    it, a quota is the max with -1 beating any number, and seats sum across
    active sources. The property that matters is that adding a grant can never
    *reduce* access (06-entitlements.md section 4.1).
    """
    from workeasy360.platform.entitlements.models import EntitlementSnapshot
    from workeasy360.platform.entitlements.services.read import (
        _more_generous_feature,
        more_generous_product,
    )

    products: dict[str, dict[str, Any]] = {}
    for product in computed.products:
        candidate = {
            "status": product.status.value,
            "seats": product.seats_total,
            "write": product.status.allows_write,
            "grace_ends_at": (
                product.grace_ends_at.isoformat() if product.grace_ends_at else None
            ),
        }
        products[product.product_code] = more_generous_product(
            products.get(product.product_code), candidate
        )

    features: dict[str, Any] = {}
    for feature in computed.features:
        features[feature.feature_key] = _more_generous_feature(
            features.get(feature.feature_key), feature.value
        )

    snapshot = EntitlementSnapshot.unscoped.filter(organization_id=org_id).first()
    if snapshot is None:
        snapshot = EntitlementSnapshot(organization_id=org_id, version=0)

    snapshot.version += 1
    snapshot.products = products
    snapshot.features = features
    snapshot.computed_at = now
    snapshot.save()
    return snapshot.version


def _advisory_lock(org_id: UUID) -> None:
    """Transaction-scoped advisory lock keyed on the organization.

    ``pg_advisory_xact_lock`` rather than the session-scoped variant: session
    locks are broken by transaction pooling
    (10-performance-concurrency.md section 4).
    """
    from django.db import connection

    with connection.cursor() as cur:
        # hashtext gives a stable bigint from the UUID; collisions merely
        # serialise two unrelated organizations occasionally, which is
        # harmless.
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [str(org_id)])


def _invalidate_cache(org_id: UUID, version: int) -> None:
    """Version-keyed invalidation: the old key is simply never read again.

    No key enumeration, no SCAN, no cross-tenant flush. Stale entries expire
    on their own (10-performance-concurrency.md section 7 rule 1).
    """
    from django.core.cache import cache

    try:
        cache.delete(f"w360:{org_id}:ent:v{max(version - 1, 0)}")
    except Exception:  # pragma: no cover - cache backend down
        # A failed invalidation is survivable: keys carry a TTL, and the
        # version bump means the new key is a miss anyway.
        logger.warning(
            "entitlement.cache.invalidation_failed",
            organization_id=str(org_id),
        )

"""Translation of commercial state into entitlement state.

``commercial/provisioning`` is the **only** module permitted to write the
entitlement read model (ADR-005). Everything a product may do is decided
here, from durable commercial facts, and nowhere else.

The whole design rests on two properties:

- ``compute_entitlements`` is a **pure, deterministic function of durable
  database state** - never of a provider response, never of an event payload.
  Re-run at any time with the same ``at`` it produces the same answer, which
  is what makes the system recoverable: if the entitlement tables were lost
  entirely, one reconciliation pass rebuilds them from subscriptions.
- ``apply_entitlements`` is idempotent and diff-based.

Together they make duplicated, out-of-order and lost events non-corrupting.
An event is a *hint that something changed*, never the change itself; nothing
here reads a payload to decide what to write.

**Desired state, not deltas.** Reconciliation computes what the organization
*should* have and diffs. An incremental "apply this change" model is broken
by a duplicated event, by an out-of-order one, and by a worker that died
halfway; a diff-from-truth model converges from all three.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from django.utils import timezone

from workeasy360.contracts.enums import (
    EntitlementSourceKind,
    EntitlementStatus,
    FeatureKind,
)
from workeasy360.contracts.values.identifiers import FeatureKey, ProductCode
from workeasy360.platform.entitlements.services.write import (
    ComputedEntitlements,
    ComputedFeature,
    ComputedProduct,
    ComputedSeatPool,
    apply_entitlements,
)
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

#: Drift is a bug, never a data-quality note. A rising rate means provisioning
#: logic or event delivery is broken, and it must surface before customers
#: notice (06-entitlements.md section 5.3).
ENTITLEMENT_DRIFT = "entitlement_drift_total"
RECONCILE_FAILURES = "entitlement_reconcile_failures_total"


def compute_entitlements(
    organization_id: UUID, at: datetime | None = None
) -> ComputedEntitlements:
    """Derive the desired entitlement state for one organization.

    Inputs, both read through the owning module's **selectors** rather than
    its models: subscriptions (``commercial/subscriptions``) and support
    grants (``platform/entitlements``). Even though provisioning is the sole
    entitlement *writer*, reading another module's tables directly is still
    forbidden (F-6) - a queryset over another module's model welds this code
    to a schema it does not own and is not tested against.
    """
    from workeasy360.commercial.catalog import selectors as catalog
    from workeasy360.commercial.subscriptions import selectors as subscriptions
    from workeasy360.platform.entitlements.selectors import active_grants

    moment = at or timezone.now()
    products: list[ComputedProduct] = []
    features: list[ComputedFeature] = []
    seats: list[ComputedSeatPool] = []

    state = subscriptions.commercial_state(organization_id)

    # One batched catalog read for every plan version in play. Per-item
    # lookups here would be an N+1 inside the reconciliation loop, which is
    # the least visible place in the system for one to hide.
    wanted_plan_versions = [
        item.plan_version_id
        for subscription in state
        for item in subscription.effective_items(moment)
        if item.plan_version_id is not None
    ]
    plans = catalog.plan_versions(wanted_plan_versions)

    for subscription in state:
        if not subscription.grants_access_at(moment):
            continue

        status = subscription.status.entitlement_status
        access_ends = subscription.access_ends_at()

        for item in subscription.effective_items(moment):
            plan = plans.get(item.plan_version_id)
            if plan is None:
                # PROTECT on the foreign key makes this impossible through
                # the ORM, so reaching it means the catalog row was removed
                # by something that bypassed it. Skipping rather than
                # guessing: granting a product whose terms we cannot read
                # would be granting an unknown amount of access.
                logger.error(
                    "provisioning.plan_version.missing",
                    organization_id=str(organization_id),
                    plan_version_id=str(item.plan_version_id),
                )
                continue

            source_ref = item.source_ref
            products.append(
                ComputedProduct(
                    product_code=str(ProductCode(plan.product_code)),
                    status=status,
                    source_kind=(
                        EntitlementSourceKind.BUNDLE
                        if item.is_derived
                        else EntitlementSourceKind.SUBSCRIPTION
                    ),
                    source_ref=source_ref,
                    seats_total=plan.included_seats * item.quantity,
                    effective_from=item.added_at,
                    effective_to=_earliest(item.removed_at, access_ends),
                )
            )
            for grant in plan.features:
                features.append(
                    ComputedFeature(
                        feature_key=str(FeatureKey(grant.key)),
                        value=grant.value,
                        kind=grant.kind.value,
                        source_ref=source_ref,
                        effective_from=item.added_at,
                        effective_to=_earliest(item.removed_at, access_ends),
                    )
                )
            seats.append(
                ComputedSeatPool(
                    pool_key=item.seat_pool or plan.product_code,
                    seats_total=plan.included_seats * item.quantity,
                    source_ref=source_ref,
                )
            )

    # Support grants are an **input** to the computation, not an override
    # applied afterwards. That is what makes "support fixes it manually, the
    # nightly job undoes it" structurally impossible.
    for grant in active_grants(organization_id):
        if not grant.is_effective_at(moment):
            continue
        if grant.product_code:
            products.append(
                ComputedProduct(
                    product_code=grant.product_code,
                    status=EntitlementStatus.ACTIVE,
                    source_kind=EntitlementSourceKind.GRANT,
                    source_ref=grant.source_ref,
                    effective_from=grant.created_at,
                    effective_to=grant.expires_at,
                )
            )
        if grant.feature_key:
            features.append(
                ComputedFeature(
                    feature_key=grant.feature_key,
                    value=grant.value if grant.value is not None else True,
                    kind=_infer_kind(grant.value),
                    source_ref=grant.source_ref,
                    effective_from=grant.created_at,
                    effective_to=grant.expires_at,
                )
            )

    return ComputedEntitlements(
        organization_id=organization_id,
        products=tuple(products),
        features=tuple(features),
        seats=tuple(seats),
        computation_id=uuid.uuid4(),
    )


def _earliest(*moments: datetime | None) -> datetime | None:
    present = [m for m in moments if m is not None]
    return min(present) if present else None


# --- reconciliation -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DriftReport:
    """What a recomputation changed, if anything."""

    organization_id: UUID
    version: int
    products_changed: tuple[str, ...] = ()
    features_changed: tuple[str, ...] = ()

    @property
    def has_drift(self) -> bool:
        return bool(self.products_changed or self.features_changed)


def reconcile(organization_id: UUID, *, at: datetime | None = None) -> int:
    """Recompute and apply. Returns the new snapshot version.

    The single provisioning entry point. Every commercial change reaches
    entitlements through this function - there is no "apply this upgrade"
    path, because a second path is a second place for the two to disagree.

    Idempotent and retry-safe: calling it twice for unchanged state produces
    identical rows. The snapshot version still advances, which is deliberate -
    it invalidates caches without requiring a diff to decide whether to.
    """
    computed = compute_entitlements(organization_id, at)
    version = apply_entitlements(computed)
    logger.info(
        "provisioning.reconciled",
        organization_id=str(organization_id),
        version=version,
        products=len(computed.products),
        seats=len(computed.seats),
    )
    return version


def reconcile_and_report_drift(
    organization_id: UUID, *, at: datetime | None = None
) -> DriftReport:
    """Reconcile, and report what was wrong before doing so.

    Used by the scheduled pass. Drift is **repaired and signalled**, never
    silently fixed: a rising drift rate means provisioning logic or event
    delivery is broken, and the signal is what surfaces that before a
    customer does.
    """
    before = _stored_snapshot(organization_id)
    computed = compute_entitlements(organization_id, at)
    expected = _expected_snapshot(computed)

    products_changed = tuple(
        sorted(_differing_keys(before.get("products", {}), expected["products"]))
    )
    features_changed = tuple(
        sorted(_differing_keys(before.get("features", {}), expected["features"]))
    )

    version = apply_entitlements(computed)
    report = DriftReport(
        organization_id=organization_id,
        version=version,
        products_changed=products_changed,
        features_changed=features_changed,
    )

    if report.has_drift:
        metrics.increment(ENTITLEMENT_DRIFT)
        logger.warning(
            "provisioning.drift_repaired",
            organization_id=str(organization_id),
            products_changed=list(products_changed),
            features_changed=list(features_changed),
        )
        _publish_drift(report)
    return report


def _stored_snapshot(organization_id: UUID) -> dict[str, Any]:
    """Read through the owning module's selector, never its models (F-6)."""
    from workeasy360.platform.entitlements.selectors import stored_snapshot

    snapshot = stored_snapshot(organization_id)
    if snapshot is None:
        return {}
    return {"products": snapshot.products, "features": snapshot.features}


def _expected_snapshot(computed: ComputedEntitlements) -> dict[str, Any]:
    """What the snapshot will contain, using the writer's own resolver.

    Deliberately the same ``_more_generous_*`` functions the writer uses. A
    second implementation of "most generous wins" here would eventually
    disagree with the first, and the drift detector would then report drift
    that does not exist - which is worse than no detector, because people
    stop believing it.
    """
    from workeasy360.platform.entitlements.services.read import (
        _more_generous_feature,
        more_generous_product,
    )

    products: dict[str, Any] = {}
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
    return {"products": products, "features": features}


def _differing_keys(before: dict[str, Any], after: dict[str, Any]) -> set[str]:
    return {key for key in set(before) | set(after) if before.get(key) != after.get(key)}


def _publish_drift(report: DriftReport) -> None:
    """Emit ``entitlements.drift.repaired.v1``.

    Best-effort by design. The repair is already durable; failing the repair
    because its announcement could not be written would trade a real fix for
    a notification.
    """
    from django.db import transaction

    from workeasy360.contracts.enums import ActorKind
    from workeasy360.contracts.events.envelope import Actor, DomainEvent
    from workeasy360.contracts.events.platform_events import (
        ENTITLEMENT_DRIFT_REPAIRED_V1,
    )
    from workeasy360.integration.events import services as events

    try:
        with transaction.atomic():
            events.publish(
                DomainEvent(
                    event_type=ENTITLEMENT_DRIFT_REPAIRED_V1.event_type,
                    event_version=1,
                    organization_id=report.organization_id,
                    aggregate_type=ENTITLEMENT_DRIFT_REPAIRED_V1.aggregate_type,
                    aggregate_id=str(report.organization_id),
                    payload={
                        "organization_id": str(report.organization_id),
                        "products_changed": list(report.products_changed),
                        "features_changed": list(report.features_changed),
                    },
                    actor=Actor(kind=ActorKind.SYSTEM, service_name="provisioning"),
                )
            )
    except Exception as exc:  # pragma: no cover - outbox unavailable
        logger.error(
            "provisioning.drift_event.failed",
            organization_id=str(report.organization_id),
            error_type=type(exc).__name__,
        )


# --- direct grants --------------------------------------------------------


def grant_product(
    organization_id: UUID,
    product: ProductCode | str,
    *,
    status: EntitlementStatus = EntitlementStatus.ACTIVE,
    seats: int | None = None,
    source_kind: EntitlementSourceKind = EntitlementSourceKind.SUBSCRIPTION,
    source_ref: str = "",
    features: dict[str, Any] | None = None,
) -> int:
    """Grant a product directly. Returns the new snapshot version.

    For tests and bootstrap tooling. Real provisioning goes through
    ``reconcile``, which derives everything from durable state rather than
    being told what to write - and is therefore the only path that survives a
    recomputation.
    """
    ref = source_ref or f"bootstrap:{product}"
    computed = ComputedEntitlements(
        organization_id=organization_id,
        products=(
            ComputedProduct(
                product_code=str(product),
                status=status,
                source_kind=source_kind,
                source_ref=ref,
                seats_total=seats,
                effective_from=timezone.now(),
            ),
        ),
        features=tuple(
            ComputedFeature(
                feature_key=str(FeatureKey(key)),
                value=value,
                kind=_infer_kind(value),
                source_ref=ref,
                effective_from=timezone.now(),
            )
            for key, value in (features or {}).items()
        ),
        seats=(
            (ComputedSeatPool(pool_key=str(product), seats_total=seats, source_ref=ref),)
            if seats is not None
            else ()
        ),
    )
    return apply_entitlements(computed)


def _infer_kind(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return FeatureKind.BOOLEAN.value
    if isinstance(value, int):
        return FeatureKind.QUOTA.value
    return FeatureKind.ENUM.value

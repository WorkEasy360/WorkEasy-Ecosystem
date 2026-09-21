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

from django.db import transaction
from django.utils import timezone

from workeasy360.contracts.enums import EntitlementSourceKind, EntitlementStatus
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)


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
    computation_id: UUID = field(default_factory=uuid.uuid4)


@transaction.atomic
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
    from workeasy360.platform.tenancy.db import set_tenant_variable
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    now = timezone.now()
    org_id = computed.organization_id

    # Two independent layers must both be satisfied:
    #   unscoped_access()     relaxes the ORM manager (Layer 2)
    #   set_tenant_variable() satisfies the RLS policy (Layer 3)
    # Relaxing only the first leaves every write refused by WITH CHECK, which
    # is the defence-in-depth property working rather than a bug to route
    # around. Provisioning acts on exactly one organization per call, so
    # entering that organization's database scope is correct and narrow.
    set_tenant_variable(org_id)

    with unscoped_access(
        reason="Provisioning writes entitlements outside a request tenant context",
        module="platform.entitlements.services.write",
    ):
        # Serialise concurrent recomputes for one organization. Last writer
        # wins safely because compute is deterministic: two racing runs from
        # the same durable state produce the same answer
        # (10-performance-concurrency.md section 12, contention point 7).
        _advisory_lock(org_id)

        seen_products: set[tuple[str, str]] = set()
        for product in computed.products:
            Entitlement.unscoped.update_or_create(
                organization_id=org_id,
                product_code=product.product_code,
                source_ref=product.source_ref,
                defaults={
                    "status": product.status.value,
                    "source_kind": product.source_kind.value,
                    "seats_total": product.seats_total,
                    "effective_from": product.effective_from or now,
                    "effective_to": product.effective_to,
                    "grace_ends_at": product.grace_ends_at,
                    "computed_at": now,
                    "computation_id": computed.computation_id,
                },
            )
            seen_products.add((product.product_code, product.source_ref))

        # Rows absent from the computed state are expired rather than deleted.
        # Deleting would lose the history a support engineer needs to answer
        # "what did they have last month?".
        stale = Entitlement.unscoped.filter(organization_id=org_id).exclude(
            status=EntitlementStatus.EXPIRED.value
        )
        for row in stale:
            if (row.product_code, row.source_ref) not in seen_products:
                row.status = EntitlementStatus.EXPIRED.value
                row.effective_to = row.effective_to or now
                row.computed_at = now
                row.computation_id = computed.computation_id
                row.save(
                    update_fields=[
                        "status",
                        "effective_to",
                        "computed_at",
                        "computation_id",
                        "updated_at",
                    ]
                )

        seen_features: set[tuple[str, str]] = set()
        for feature in computed.features:
            FeatureEntitlement.unscoped.update_or_create(
                organization_id=org_id,
                feature_key=feature.feature_key,
                source_ref=feature.source_ref,
                defaults={
                    "value": feature.value,
                    "kind": feature.kind,
                    "effective_from": feature.effective_from or now,
                    "effective_to": feature.effective_to,
                },
            )
            seen_features.add((feature.feature_key, feature.source_ref))

        FeatureEntitlement.unscoped.filter(organization_id=org_id).exclude(
            feature_key__in=[f.feature_key for f in computed.features]
        ).delete()

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
        _more_generous_product,
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
        products[product.product_code] = _more_generous_product(
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

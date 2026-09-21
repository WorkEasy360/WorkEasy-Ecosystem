"""Translation of commercial state into entitlement state.

``commercial/provisioning`` is the **only** module permitted to write the
entitlement read model (ADR-005). Phase 1 implements the translator and its
write path, but no billing or subscription flows: those arrive with
``commercial/subscriptions``.

The shape that matters is already here, and it is what makes billing failures
non-corrupting:

- ``compute_entitlements`` is a **pure, deterministic function of durable
  database state** - never of a provider response. It can be re-run at any
  time and produces the same answer.
- ``apply_entitlements`` is idempotent and diff-based.

Because of those two properties, a duplicated webhook, an out-of-order event
or a stuck worker converges to the same result rather than corrupting access.
"""

from __future__ import annotations

import uuid
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
    apply_entitlements,
)
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)


def compute_entitlements(
    organization_id: UUID, at: datetime | None = None
) -> ComputedEntitlements:
    """Derive the desired entitlement state for one organization.

    Pure: reads durable rows only, performs no I/O to providers, and returns
    the same result for the same state. That is the single property that makes
    the system recoverable - if the entitlement tables were lost entirely, one
    reconciliation pass rebuilds them.

    Phase 1 has no Subscription model, so the only input is
    ``EntitlementGrant``. Subscription-derived entitlements are added here,
    not anywhere else, when ``commercial/subscriptions`` ships.
    """
    # Read through the owning module's selector, never its models. Even
    # though provisioning is the sole entitlement *writer*, reading another
    # module's tables directly is still forbidden (F-6) - it welds this code
    # to a schema it does not own and is not tested against.
    from workeasy360.platform.entitlements.selectors import active_grants

    moment = at or timezone.now()
    products: list[ComputedProduct] = []
    features: list[ComputedFeature] = []

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
        computation_id=uuid.uuid4(),
    )


def reconcile(organization_id: UUID) -> int:
    """Recompute and apply. The disaster-recovery and drift-repair tool.

    Drift is a bug and is never silently fixed: the caller logs the diff and
    alerts. A rising drift rate means provisioning logic or event delivery is
    broken, and it must surface before customers notice
    (06-entitlements.md section 5.3).
    """
    computed = compute_entitlements(organization_id)
    version = apply_entitlements(computed)
    logger.info(
        "provisioning.reconciled",
        organization_id=str(organization_id),
        version=version,
    )
    return version


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

    Used by tests and by bootstrap tooling. Real subscription-driven
    provisioning goes through ``reconcile``, which derives everything from
    durable state rather than being told what to write.
    """
    computed = ComputedEntitlements(
        organization_id=organization_id,
        products=(
            ComputedProduct(
                product_code=str(product),
                status=status,
                source_kind=source_kind,
                source_ref=source_ref or f"bootstrap:{product}",
                seats_total=seats,
                effective_from=timezone.now(),
            ),
        ),
        features=tuple(
            ComputedFeature(
                feature_key=str(FeatureKey(key)),
                value=value,
                kind=_infer_kind(value),
                source_ref=source_ref or f"bootstrap:{product}",
                effective_from=timezone.now(),
            )
            for key, value in (features or {}).items()
        ),
    )
    return apply_entitlements(computed)


def _infer_kind(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return FeatureKind.BOOLEAN.value
    if isinstance(value, int):
        return FeatureKind.QUOTA.value
    return FeatureKind.ENUM.value

"""Support entitlement grants: issuing and revoking a time-boxed override.

A grant is an **input** to ``provisioning.compute_entitlements``, never an edit
of an entitlement row. That is the property that makes "support fixed it by
hand and the nightly job undid it" impossible, and it is why the control plane
overrides an entitlement by writing one of *these* and asking provisioning to
reconcile - never by touching ``Entitlement``.

This module writes grants only. It does not authorize (the caller requires a
dangerous staff permission and AAL 2), does not audit and does not reconcile;
those are the composing layer's, because ``entitlements`` may import neither
``audit`` nor ``commercial`` (F-5, F-4).

Bounds that make an override *explainable later*: an expiry is mandatory and
capped at 90 days, a reason is mandatory, and exactly one of product or feature
is targeted. A permanent override is a commercial change and belongs in a
subscription, not here.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from workeasy360.contracts.errors import CatalogValidationError
from workeasy360.contracts.values.identifiers import FeatureKey, ProductCode
from workeasy360.contracts.values.tenant import TenantContext

MAX_GRANT_DAYS = 90
MIN_REASON = 10


def issue_grant(
    ctx: TenantContext,
    *,
    product_code: str = "",
    feature_key: str = "",
    value: Any = None,
    reason: str,
    expires_at: datetime,
) -> Any:
    """Record a grant for the organization ``ctx`` is bound to."""
    from workeasy360.platform.entitlements.models import EntitlementGrant

    if bool(product_code) == bool(feature_key):
        raise CatalogValidationError(
            "Grant exactly one of a product or a feature.", field="target"
        )
    try:
        if product_code:
            ProductCode(product_code)
        else:
            FeatureKey(feature_key)
    except ValueError as exc:
        raise CatalogValidationError(str(exc), field="target") from exc
    if len((reason or "").strip()) < MIN_REASON:
        raise CatalogValidationError(
            f"A reason of at least {MIN_REASON} characters is required.", field="reason"
        )
    now = timezone.now()
    if expires_at <= now or expires_at > now + timedelta(days=MAX_GRANT_DAYS):
        raise CatalogValidationError(
            f"An override must expire within {MAX_GRANT_DAYS} days.", field="expires_at"
        )
    with transaction.atomic():
        return EntitlementGrant.objects.create(
            organization_id=ctx.organization_id,
            product_code=product_code,
            feature_key=feature_key,
            value=value,
            reason=reason.strip(),
            granted_by_id=ctx.user_id,
            expires_at=expires_at,
        )


def revoke_grant(ctx: TenantContext, grant_id: UUID) -> Any:
    """Revoke a live grant of the bound organization. ``None`` if there is none."""
    from workeasy360.platform.entitlements.models import EntitlementGrant

    with transaction.atomic():
        grant = (
            EntitlementGrant.objects.select_for_update()
            .filter(id=grant_id, revoked_at__isnull=True)
            .first()
        )
        if grant is None:
            return None
        grant.revoked_at = timezone.now()
        grant.save(update_fields=["revoked_at", "updated_at"])
        return grant


def live_grants(ctx: TenantContext) -> list[dict[str, Any]]:
    """The bound organization's unrevoked grants, for display."""
    from workeasy360.platform.entitlements.models import EntitlementGrant

    return [
        {
            "id": str(g.id),
            "product_code": g.product_code,
            "feature_key": g.feature_key,
            "reason": g.reason,
            "expires_at": g.expires_at.isoformat() if g.expires_at else None,
            "created_at": g.created_at.isoformat(),
        }
        for g in EntitlementGrant.objects.filter(revoked_at__isnull=True).order_by(
            "-created_at"
        )[:50]
    ]

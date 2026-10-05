"""Read queries over entitlement tables, returning DTOs.

Exists so that ``commercial/provisioning`` can compute entitlements without
querying ``platform/entitlements`` tables directly. Even where the import is
legal by layer, reading another module's tables is not (F-6): a queryset over
another module's model welds the caller to its schema, and when that schema
changes the caller breaks without being in the owning module's test suite.

Selectors are a contract; models are an implementation detail.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class GrantDTO:
    """A support-issued entitlement grant, as provisioning sees it.

    A DTO rather than the model, so provisioning cannot accidentally write
    through it - the single-writer rule is about writes going through one
    service, and handing out a live model instance would undermine that.
    """

    id: UUID
    product_code: str
    feature_key: str
    value: Any
    created_at: datetime
    expires_at: datetime | None

    @property
    def source_ref(self) -> str:
        return f"entitlements.grant:{self.id}"

    def is_effective_at(self, moment: datetime) -> bool:
        if self.expires_at is not None and self.expires_at <= moment:
            return False
        return True


def active_grants(organization_id: UUID) -> list[GrantDTO]:
    """Unrevoked grants for an organization.

    Grants are an **input** to ``compute_entitlements``, not an override
    applied afterwards. That is what makes "support fixes it manually, the
    nightly job undoes it" structurally impossible
    (06-entitlements.md section 5.4).
    """
    from workeasy360.platform.entitlements.models import EntitlementGrant
    from workeasy360.platform.tenancy.db import assert_tenant_scope, organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    # Both layers, always. ``unscoped_access`` relaxes the ORM manager
    # (Layer 2); the RLS policy (Layer 3) is untouched by it, and with no
    # tenant variable set the policy matches **zero rows**. Relaxing only the
    # first therefore does not fail - it silently returns nothing, and a
    # computation that reads no grants produces an entitlement state missing
    # them. Fail-closed is the right default; silently-empty is not an
    # acceptable way to reach it.
    with (
        unscoped_access(
            reason="Entitlement computation runs outside a request tenant context",
            module="platform.entitlements.selectors",
        ),
        organization_db_scope(organization_id),
    ):
        # Expected scope verified, not assumed - the same guard
        # ``commercial_state`` carries, for the same reason: a grant this read
        # misses is access the recomputation silently withdraws.
        assert_tenant_scope(organization_id, what="active_grants")
        rows = EntitlementGrant.unscoped.filter(
            organization_id=organization_id, revoked_at__isnull=True
        ).values("id", "product_code", "feature_key", "value", "created_at", "expires_at")
        return [
            GrantDTO(
                id=row["id"],
                product_code=row["product_code"],
                feature_key=row["feature_key"],
                value=row["value"],
                created_at=row["created_at"],
                expires_at=row["expires_at"],
            )
            for row in rows
        ]


def decision_for_organization(
    organization_id: UUID, product_code: str, *, write: bool = False
) -> Any:
    """The entitlement decision for an organization, with **no tenant context**.

    The read path for a caller that is not a signed-in user: the remote
    entitlement API answering a product's "may this organization use me?" for
    an organization nobody is currently signed in to, and which the calling
    product may not even have a user for.

    ``services.read.check`` cannot serve that caller. It reads a
    ``TenantScopedModel`` through the scoped manager, so it needs Layer 2
    bound - and binding Layer 2 means a ``TenantContext``, which means a
    ``user_id`` and a ``membership_id``. There is no user here. Fabricating
    one would put an invented person's identifier into the logs and traces of
    every service-to-service entitlement check, which is the same objection
    ``audit.record_system`` makes against a synthetic actor.

    So this takes the route the rest of this module already takes for
    legitimately cross-tenant reads: relax Layer 2 explicitly and audibly,
    enter the organization's **database** scope so RLS still confines the read
    to exactly one tenant, and assert that the scope is what was asked for.
    Layer 3 is never relaxed - a bug here returns nothing rather than another
    tenant's row.

    The decision itself is delegated to ``services.read.decide``, the same
    function the request path uses, so the platform and a product can never be
    told different things about the same entitlement.
    """
    from workeasy360.platform.entitlements.models import Entitlement, EntitlementSnapshot
    from workeasy360.platform.entitlements.services.read import (
        decide,
        more_generous_product,
    )
    from workeasy360.platform.tenancy.db import assert_tenant_scope, organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(
            reason="Remote entitlement verification answers for an organization "
            "with no signed-in user",
            module="platform.entitlements.selectors",
        ),
        organization_db_scope(organization_id),
    ):
        assert_tenant_scope(organization_id, what="decision_for_organization")
        row = (
            EntitlementSnapshot.unscoped.filter(organization_id=organization_id)
            .values("products")
            .first()
        )
        products: dict[str, Any] = (row["products"] or {}) if row is not None else {}

        if row is None:
            # No snapshot. Almost always an organization that has never
            # bought anything, which resolves to a denial either way - but it
            # is also what a lost snapshot looks like, and that case is a
            # paying customer being locked out of a product they hold. So the
            # answer is computed from the durable Entitlement rows instead of
            # assumed, exactly as ``services.read`` does on the request path.
            #
            # Unlike that path, nothing is written back. Self-healing here
            # would let an unauthenticated organization identifier drive a
            # database write from outside the platform; repair belongs to the
            # reconciliation job, which already owns it.
            products = _live_products(Entitlement, organization_id, more_generous_product)

    return decide(products.get(str(product_code)), product_code, write=write)


def feature_for_organization(organization_id: UUID, key: str) -> Any:
    """A resolved feature value for an organization, with **no tenant context**.

    The feature counterpart of :func:`decision_for_organization`, for the same
    caller: a product calling the platform server to server (Phase 8's AI
    gateway asks for ``<product>.ai.monthly_budget_micros``), with no user and
    no membership to build a context from. Same route, for the same reasons:
    Layer 2 relaxed audibly, the organization's database scope entered so RLS
    confines the read to one tenant, the stored snapshot read first and the
    durable ``FeatureEntitlement`` rows used - never written back - when it is
    missing. Most-generous-wins is the writer's own merge.
    """
    from django.utils import timezone

    from workeasy360.platform.entitlements.models import (
        EntitlementSnapshot,
        FeatureEntitlement,
    )
    from workeasy360.platform.entitlements.services.read import _more_generous_feature
    from workeasy360.platform.tenancy.db import assert_tenant_scope, organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(
            reason="Remote AI entitlement check answers for an organization "
            "with no signed-in user",
            module="platform.entitlements.selectors",
        ),
        organization_db_scope(organization_id),
    ):
        assert_tenant_scope(organization_id, what="feature_for_organization")
        row = (
            EntitlementSnapshot.unscoped.filter(organization_id=organization_id)
            .values("features")
            .first()
        )
        if row is not None:
            return (row["features"] or {}).get(str(key))

        now = timezone.now()
        value: Any = None
        for feature in FeatureEntitlement.unscoped.filter(
            organization_id=organization_id, feature_key=str(key)
        ):
            if feature.effective_from > now:
                continue
            if feature.effective_to is not None and feature.effective_to <= now:
                continue
            value = _more_generous_feature(value, feature.value)
        return value


def _live_products(model: Any, organization_id: UUID, merge: Any) -> dict[str, Any]:
    """Resolve products straight from ``Entitlement`` rows, unscoped.

    Called inside the organization's database scope, so RLS confines it to one
    tenant regardless of the relaxed ORM manager. The merge is the same
    function the request path uses.
    """
    from django.utils import timezone

    now = timezone.now()
    out: dict[str, Any] = {}
    for row in model.unscoped.filter(organization_id=organization_id):
        if not row.is_effective_at(now):
            continue
        candidate = {
            "status": row.status,
            "seats": row.seats_total,
            "write": row.status_enum.allows_write,
            "grace_ends_at": row.grace_ends_at.isoformat() if row.grace_ends_at else None,
        }
        out[row.product_code] = merge(out.get(row.product_code), candidate)
    return out


@dataclass(frozen=True, slots=True)
class SnapshotDTO:
    """The materialised entitlement answer for one organization."""

    version: int
    products: dict[str, Any]
    features: dict[str, Any]


def stored_snapshot(organization_id: UUID) -> SnapshotDTO | None:
    """What is currently stored, for drift comparison.

    Exists so that provisioning can compare its recomputation against the
    stored answer without querying this module's tables. Reading another
    module's models is forbidden even where the import is legal by layer
    (F-6), and drift detection is exactly the kind of code that would
    otherwise grow a queryset over a schema it does not own.
    """
    from workeasy360.platform.entitlements.models import EntitlementSnapshot
    from workeasy360.platform.tenancy.db import assert_tenant_scope, organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(
            reason="Drift detection reads the snapshot outside a request context",
            module="platform.entitlements.selectors",
        ),
        organization_db_scope(organization_id),
    ):
        assert_tenant_scope(organization_id, what="stored_snapshot")
        row = (
            EntitlementSnapshot.unscoped.filter(organization_id=organization_id)
            .values("version", "products", "features")
            .first()
        )
    if row is None:
        return None
    return SnapshotDTO(
        version=row["version"],
        products=row["products"] or {},
        features=row["features"] or {},
    )

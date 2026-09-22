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

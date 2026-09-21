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
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with unscoped_access(
        reason="Entitlement computation runs outside a request tenant context",
        module="platform.entitlements.selectors",
    ):
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


def organizations_with_entitlements() -> list[UUID]:
    """Every organization holding at least one entitlement.

    Used by the reconciliation job, which must iterate organizations and
    enter tenant context per organization rather than running one unscoped
    query across all tenants.
    """
    from workeasy360.platform.entitlements.models import Entitlement
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with unscoped_access(
        reason="Reconciliation enumerates organizations to visit",
        module="platform.entitlements.selectors",
    ):
        return list(
            Entitlement.unscoped.values_list("organization_id", flat=True).distinct()
        )

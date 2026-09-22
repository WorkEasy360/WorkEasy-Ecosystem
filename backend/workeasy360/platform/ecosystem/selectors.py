"""Read queries over the integration registry, returning DTOs.

Selectors are a contract; models are an implementation detail. Nothing outside
this module receives a ``ProductIntegration`` instance, so a schema change
here cannot break a caller that is not in this module's test suite (F-6).

**What these never return.** No secret hash, no public key material beyond
what is already public, no private key reference. The registry listing is
read by operators through an administration API, and a field that is not in
the DTO cannot be serialised into a response by accident.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz

#: Bounded, with no unbounded variant. The registry is small by construction -
#: one row per product in the ecosystem - but "small by construction" is what
#: every table that later became an outage was described as.
MAX_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class IntegrationDTO:
    """A registration as an operator sees it."""

    client_id: str
    product_code: str
    status: str
    manifest: dict[str, Any]
    activated_at: datetime | None
    suspended_at: datetime | None
    suspension_reason: str
    created_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "product_code": self.product_code,
            "status": self.status,
            "manifest": self.manifest,
            "activated_at": _iso(self.activated_at),
            "suspended_at": _iso(self.suspended_at),
            "suspension_reason": self.suspension_reason,
            "created_at": _iso(self.created_at),
        }


@dataclass(frozen=True, slots=True)
class CredentialDTO:
    """A credential's *metadata*. Never its secret, which is not stored."""

    credential_id: str
    kind: str
    status: str
    algorithm: str
    label: str
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_on: Any
    created_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "credential_id": self.credential_id,
            "kind": self.kind,
            "status": self.status,
            "algorithm": self.algorithm,
            "label": self.label,
            "expires_at": _iso(self.expires_at),
            "revoked_at": _iso(self.revoked_at),
            "last_used_on": self.last_used_on.isoformat() if self.last_used_on else None,
            "created_at": _iso(self.created_at),
        }


def integrations(ctx: TenantContext, *, limit: int = 50) -> list[IntegrationDTO]:
    """Every registered integration. Authorized, staff-only.

    Authorization is enforced here rather than in the view because a view-only
    check is bypassed by any internal caller and by the task path (F-7, C7).
    """
    from workeasy360.platform.ecosystem.models import ProductIntegration

    authz.require(ctx, "platform.integration.view")
    authz.require_platform_staff(ctx, operation="ecosystem.integration.list")

    rows = ProductIntegration.objects.all()[: max(1, min(limit, MAX_PAGE_SIZE))]
    return [_integration_dto(row) for row in rows]


def integration(ctx: TenantContext, *, client_id: str) -> IntegrationDTO | None:
    from workeasy360.platform.ecosystem.models import ProductIntegration

    authz.require(ctx, "platform.integration.view")
    authz.require_platform_staff(ctx, operation="ecosystem.integration.get")

    row = ProductIntegration.objects.filter(client_id=client_id).first()
    return _integration_dto(row) if row is not None else None


def credentials(ctx: TenantContext, *, client_id: str) -> list[CredentialDTO]:
    """Credential metadata for one integration.

    ``select_related`` is not needed and not used: the filter goes through the
    join without fetching the parent, and nothing in the DTO reads it. Pinned
    by the query-count assertion in tests/performance/.
    """
    from workeasy360.platform.ecosystem.models import IntegrationCredential

    authz.require(ctx, "platform.integration.view")
    authz.require_platform_staff(ctx, operation="ecosystem.credential.list")

    rows = IntegrationCredential.objects.filter(
        integration__client_id=client_id
    ).order_by("-created_at")[:MAX_PAGE_SIZE]
    return [
        CredentialDTO(
            credential_id=row.credential_id,
            kind=row.kind,
            status=row.status,
            algorithm=row.algorithm,
            label=row.label,
            expires_at=row.expires_at,
            revoked_at=row.revoked_at,
            last_used_on=row.last_used_on,
            created_at=row.created_at,
        )
        for row in rows
    ]


def _integration_dto(row: Any) -> IntegrationDTO:
    return IntegrationDTO(
        client_id=row.client_id,
        product_code=row.product_code,
        status=row.status,
        manifest=row.manifest,
        activated_at=row.activated_at,
        suspended_at=row.suspended_at,
        suspension_reason=row.suspension_reason,
        created_at=row.created_at,
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None

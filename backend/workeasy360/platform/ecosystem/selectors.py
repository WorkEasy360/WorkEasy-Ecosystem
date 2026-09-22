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

from workeasy360.contracts.values.manifest import from_stored_manifest
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


@dataclass(frozen=True, slots=True)
class LaunchTargetDTO:
    """The one, narrow projection ``platform/launcher`` may read (ADR-021).

    Deliberately smaller than :class:`IntegrationDTO`: no manifest, no
    redirect URI set, no scopes, no client id. A member browsing the app
    directory is not staff, and the launcher's job needs exactly one fact
    ``platform/ecosystem`` alone can answer - "is there a product to send
    this browser to, and where" - not the registration record.

    Semantic booleans rather than the raw status string or
    ``IntegrationStatus`` itself (F-6): a selector's whole purpose is that a
    caller is welded to *this* schema, not to ``platform.ecosystem.models``,
    and handing out the model's own enum would weld it to that module's
    internal vocabulary instead - asserted structurally by
    ``test_no_cross_module_orm_access``.
    """

    product_code: str
    display_name: str
    launch_url: str
    #: Only ``active`` integrations may authenticate a user.
    can_authenticate: bool
    #: A reversible operational stop - the integration will very likely
    #: resume. Distinct from "not built" so the launcher can render
    #: "temporarily unavailable" rather than "coming soon" (Task 24/48).
    is_suspended: bool
    #: ``retired``: terminal, will not return. Grouped with ``draft`` by the
    #: launcher (neither has anywhere to send a browser today), but kept as
    #: its own field rather than folded into a single "unavailable" boolean,
    #: so a future caller that *does* need to distinguish them still can.
    is_terminal: bool


def launch_target(product_code: str) -> LaunchTargetDTO | None:
    """The launchable integration for a product code, or ``None`` if the
    platform has never registered one.

    No authorization check: unlike every other function in this module, this
    one is not staff-gated, because any signed-in member of any organization
    is entitled to know whether a product they might use exists at all - the
    same information the app directory renders as "coming soon" (Task 21).
    What it must never do is what it does not do here: return a credential,
    a redirect URI, or anything ``IntegrationDTO`` carries that this member
    has no business seeing.
    """
    from workeasy360.platform.ecosystem.models import (
        IntegrationStatus,
        ProductIntegration,
    )

    row = (
        ProductIntegration.objects.filter(product_code=str(product_code))
        .values("product_code", "status", "manifest")
        .first()
    )
    if row is None:
        return None
    manifest = from_stored_manifest(row["manifest"])
    status = IntegrationStatus(row["status"])
    return LaunchTargetDTO(
        product_code=row["product_code"],
        display_name=manifest.display_name,
        launch_url=manifest.launch_url,
        can_authenticate=status.can_authenticate,
        is_suspended=status is IntegrationStatus.SUSPENDED,
        is_terminal=status.is_terminal,
    )


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

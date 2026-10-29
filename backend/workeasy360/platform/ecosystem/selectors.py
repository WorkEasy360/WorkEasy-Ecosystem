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

from collections.abc import Iterable
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
    #: Phase 11 - what the bounded probe last saw, and whether the product can
    #: complete single sign-on at all (an unrevoked, unexpired credential
    #: exists). Both are read-only facts about the registration.
    health_status: str = "unknown"
    health_checked_at: datetime | None = None
    health_detail: str = ""
    sso: str = "integration_required"

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
            "health": {
                "status": self.health_status,
                "checked_at": _iso(self.health_checked_at),
                "detail": self.health_detail,
            },
            "sso": self.sso,
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
    authz.require_platform_staff(
        ctx,
        operation="ecosystem.integration.list",
        permission="platform.integration.view",
    )

    rows = _with_sso(ProductIntegration.objects.all())[
        : max(1, min(limit, MAX_PAGE_SIZE))
    ]
    return [_integration_dto(row) for row in rows]


def integration(ctx: TenantContext, *, client_id: str) -> IntegrationDTO | None:
    from workeasy360.platform.ecosystem.models import ProductIntegration

    authz.require(ctx, "platform.integration.view")
    authz.require_platform_staff(
        ctx,
        operation="ecosystem.integration.get",
        permission="platform.integration.view",
    )

    row = _with_sso(ProductIntegration.objects.filter(client_id=client_id)).first()
    return _integration_dto(row) if row is not None else None


def integration_for_product(
    ctx: TenantContext, *, product_code: str
) -> IntegrationDTO | None:
    """One registration by the product it belongs to (Phase 11: the Super
    Admin console works in product codes, not client ids). Authorized like
    every read here."""
    from workeasy360.platform.ecosystem.models import ProductIntegration

    authz.require(ctx, "platform.integration.view")
    authz.require_platform_staff(
        ctx,
        operation="ecosystem.integration.get",
        permission="platform.integration.view",
    )
    row = _with_sso(
        ProductIntegration.objects.filter(product_code=str(product_code))
    ).first()
    return _integration_dto(row) if row is not None else None


def credentials(ctx: TenantContext, *, client_id: str) -> list[CredentialDTO]:
    """Credential metadata for one integration.

    ``select_related`` is not needed and not used: the filter goes through the
    join without fetching the parent, and nothing in the DTO reads it. Pinned
    by the query-count assertion in tests/performance/.
    """
    from workeasy360.platform.ecosystem.models import IntegrationCredential

    authz.require(ctx, "platform.integration.view")
    authz.require_platform_staff(
        ctx,
        operation="ecosystem.credential.list",
        permission="platform.integration.view",
    )

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
    #: ADR-044: the product's own sign-in page, for a person whose effective
    #: sign-in is the product's (``ProductManifest.product_login_url``), or
    #: ``""``. A place a browser is sent, like ``launch_url`` - never a
    #: redirect URI or a credential.
    product_login_url: str = ""


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
        product_login_url=manifest.product_login_url,
    )


def launch_urls(product_codes: Iterable[str]) -> dict[str, str]:
    """``{product_code: launch_url}`` for the registered integrations of these
    products, in one query (the Product Manager's list, ADR-051). Ungated for
    the reason ``launch_target`` is: a launch URL is where any member's browser
    is sent, not a secret. Nothing else of the registration is returned."""
    from workeasy360.platform.ecosystem.models import ProductIntegration

    codes = sorted({str(c) for c in product_codes})
    if not codes:
        return {}
    return {
        row["product_code"]: str(from_stored_manifest(row["manifest"]).launch_url)
        for row in ProductIntegration.objects.filter(product_code__in=codes).values(
            "product_code", "manifest"
        )
    }


@dataclass(frozen=True, slots=True)
class IntegrationPresenceDTO:
    """What a member may be shown about a product's connection - nothing that
    identifies the registration: no client id, no URL, no credential detail."""

    product_code: str
    sso: str
    health_status: str
    health_checked_at: datetime | None


def presence_for(product_codes: Iterable[str]) -> dict[str, IntegrationPresenceDTO]:
    """SSO and health for many products in **one** query (the app directory
    renders a card per product and must not cost a query each). Not
    staff-gated, for the reason ``launch_target`` is not: it is catalogue
    metadata about a product, not about any organization."""
    from workeasy360.platform.ecosystem.models import ProductIntegration

    codes = sorted({str(c) for c in product_codes})
    if not codes:
        return {}
    rows = _with_sso(ProductIntegration.objects.filter(product_code__in=codes)).values(
        "product_code", "has_usable_credential", "health_status", "health_checked_at"
    )
    return {
        row["product_code"]: IntegrationPresenceDTO(
            product_code=row["product_code"],
            sso=sso_label(bool(row["has_usable_credential"])),
            health_status=row["health_status"],
            health_checked_at=row["health_checked_at"],
        )
        for row in rows
    }


@dataclass(frozen=True, slots=True)
class PublishedEventDTO:
    """One event type a product declared in its manifest (Phase 6).

    Name, version and description only - the declaration is all the
    platform holds about a product's event, by design (A14).
    """

    product_code: str
    event_type: str
    version: int
    description: str

    @property
    def ref(self) -> str:
        return f"{self.event_type}.v{self.version}"


def published_events(product_codes: Iterable[str]) -> list[PublishedEventDTO]:
    """Event types declared by the **active** integrations of these products.

    Not authorization-gated, for the same reason as :func:`launch_target`:
    the answer is catalogue metadata about products, not about any
    organization. Callers pass the product codes an organization is
    *entitled* to, which is where the tenant-specific decision is made
    (``integration/events/catalog.py``). A suspended or retired
    integration publishes nothing and so offers nothing to subscribe to.
    """
    from workeasy360.platform.ecosystem.models import (
        IntegrationStatus,
        ProductIntegration,
    )

    codes = sorted({str(c) for c in product_codes})
    if not codes:
        return []
    rows = ProductIntegration.objects.filter(
        product_code__in=codes, status=IntegrationStatus.ACTIVE.value
    ).values_list("product_code", "manifest")
    found: list[PublishedEventDTO] = []
    for product_code, manifest in rows:
        for declaration in from_stored_manifest(manifest).events:
            found.append(
                PublishedEventDTO(
                    product_code=product_code,
                    event_type=declaration.type,
                    version=declaration.version,
                    description=declaration.description,
                )
            )
    return sorted(found, key=lambda e: (e.event_type, e.version))


def _with_sso(queryset: Any) -> Any:
    """Annotate each integration with whether it holds a *usable* credential:
    active, not revoked, not expired - the same test as
    ``IntegrationCredential.is_usable``, expressed once as a subquery so a
    list of integrations costs one query, not one per row."""
    from django.db.models import Exists, OuterRef, Q
    from django.utils import timezone

    from workeasy360.platform.ecosystem.models import (
        CredentialStatus,
        IntegrationCredential,
    )

    usable = IntegrationCredential.objects.filter(
        integration=OuterRef("pk"),
        status=CredentialStatus.ACTIVE.value,
        revoked_at__isnull=True,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
    return queryset.annotate(has_usable_credential=Exists(usable))


def sso_label(has_usable_credential: bool) -> str:
    """The two honest answers. ``connected`` means a credential exists that
    the token endpoint would accept - not that the product's own client code
    works (that is proven by a sign-in, not by a row)."""
    return "connected" if has_usable_credential else "integration_required"


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
        health_status=row.health_status,
        health_checked_at=row.health_checked_at,
        health_detail=row.health_detail,
        sso=sso_label(bool(getattr(row, "has_usable_credential", False))),
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None

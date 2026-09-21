"""Tenant resolution: the shape of the answer, and who supplies it.

``platform/tenancy`` is P0 and ``platform/organizations`` is P1, so tenancy
must not import organizations - upward edges inside platform are forbidden
(F-4, and the layer table in 01-domain-boundaries.md section 5).

But resolution genuinely needs the Organization and Membership tables. The
sanctioned replacement for a forbidden edge is runtime resolution through a
registry (07-cross-product-integration.md section 4): tenancy declares the
protocol and owns the registry; organizations implements it and registers
itself at app-ready. There is no import edge, so the linter stays satisfied
and the layering is real rather than nominal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Protocol
from uuid import UUID

from django.http import HttpRequest

from workeasy360.contracts.enums import MembershipStatus, OrganizationStatus
from workeasy360.contracts.values.tenant import TenantContext

#: Organization is taken from the URL path and nowhere else (TB-1/TB-2).
#: A header or body value is attacker-controlled; a path segment is at least
#: matched against a membership the server verified.
_ORG_PATH_RE: Final = re.compile(
    r"^/api/v\d+/orgs/(?P<slug>[a-z0-9][a-z0-9-]{0,62})(/|$)"
)


@dataclass(frozen=True, slots=True)
class TenantResolution:
    """Everything the middleware needs to admit or deny a request."""

    organization_id: UUID
    organization_slug: str
    organization_status: OrganizationStatus
    membership_id: UUID
    membership_status: MembershipStatus
    user_id: UUID
    aal: int = 1
    is_break_glass: bool = False
    reason: str | None = None

    def to_context(self) -> TenantContext:
        return TenantContext(
            organization_id=self.organization_id,
            membership_id=self.membership_id,
            user_id=self.user_id,
            organization_status=self.organization_status,
            aal=self.aal,
            is_break_glass=self.is_break_glass,
            reason=self.reason,
        )


class TenantResolver(Protocol):
    """Implemented by ``platform/organizations``, registered at app-ready."""

    def resolve(
        self, *, user_id: UUID, organization_slug: str
    ) -> TenantResolution | None:
        """Return the resolution, or None if there is no such pairing.

        None must be returned for *both* "no such organization" and "user is
        not a member". Distinguishing them here would let the caller build an
        organization-enumeration oracle out of the difference.
        """
        ...


_resolver: TenantResolver | None = None


def register_resolver(resolver: TenantResolver) -> None:
    """Called by ``organizations.apps.OrganizationsConfig.ready()``."""
    global _resolver
    _resolver = resolver


def get_resolver() -> TenantResolver | None:
    return _resolver


def organization_slug_from_path(path: str) -> str | None:
    """Extract the organization slug, or None if this is not a tenant URL."""
    match = _ORG_PATH_RE.match(path)
    return match.group("slug") if match else None


def resolve_tenant_from_path(request: HttpRequest) -> TenantResolution | None:
    """Resolve (principal, organization) for this request.

    Returns None when the URL is not tenant-scoped. Raises nothing for an
    unauthenticated user on a tenant URL - that is handled as a denial by the
    caller, which keeps the response uniform.
    """
    slug = organization_slug_from_path(request.path)
    if slug is None:
        return None

    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return _anonymous_denial(slug)

    resolver = get_resolver()
    if resolver is None:  # pragma: no cover - startup check prevents this
        raise RuntimeError(
            "No TenantResolver is registered. platform/organizations should "
            "register one in AppConfig.ready()."
        )

    resolution = resolver.resolve(user_id=user.pk, organization_slug=slug)
    if resolution is None:
        return _anonymous_denial(slug)
    return resolution


def _anonymous_denial(slug: str) -> TenantResolution:
    """A resolution that always fails the access check.

    Returning a denying resolution rather than None keeps one code path in the
    middleware: every denial goes through the same uniform response, so an
    unauthenticated request, a non-member and a non-existent organization are
    indistinguishable from the outside.
    """
    nil = UUID(int=0)
    return TenantResolution(
        organization_id=nil,
        organization_slug=slug,
        organization_status=OrganizationStatus.LOCKED,
        membership_id=nil,
        membership_status=MembershipStatus.REMOVED,
        user_id=nil,
    )

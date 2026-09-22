"""The TenantResolver implementation, registered into tenancy at app-ready.

This is the concrete half of the runtime-registry pattern described in
``tenancy/resolution.py``: organizations (P1) may import tenancy (P0), but not
the reverse, so organizations supplies the implementation and tenancy holds
only the protocol.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from workeasy360.contracts.enums import MembershipStatus, OrganizationStatus
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.tenancy.resolution import TenantResolution

logger = get_logger(__name__)


def _resolve(user_id: UUID, **organization_lookup: Any) -> TenantResolution | None:
    """The tenant bootstrap query, keyed however the caller needs.

    Uses ``unscoped`` on Membership deliberately and unavoidably: this runs
    *before* tenant context exists, so the scoped manager would raise. It is
    the bootstrap query that establishes the context everything else depends
    on, and it is safe because it filters on both the verified user id and a
    single candidate organization, returning at most one row that the user
    provably owns.

    Parameterised by the organization lookup rather than duplicated, because
    there are two ways in - by slug for a tenant-scoped HTTP path, by id for
    an OIDC authorization request - and they must apply *identical* rules. A
    second copy of this function is a second place for "and check the
    organization is not soft-deleted" to be forgotten.
    """
    from workeasy360.platform.organizations.models import Membership, Organization
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with unscoped_access(
        reason="Tenant bootstrap: resolve membership before context exists",
        module="platform.organizations.resolver",
    ):
        # Step 1: the organization. A control-plane table with no RLS, so
        # it is readable before any tenant variable is set. This is the
        # only way out of the chicken-and-egg: the membership lookup needs
        # a tenant scope, and the tenant is what we are resolving.
        organization = (
            Organization.objects.filter(deleted_at__isnull=True, **organization_lookup)
            .only("id", "slug", "status")
            .first()
        )
        if organization is None:
            # Indistinguishable from "not a member" to the caller, which
            # is what stops this becoming an enumeration oracle.
            return None

        # Step 2: the membership, inside that organization's scope.
        # Membership is tenant-scoped and RLS-protected, so without this
        # the query returns zero rows and every request would be denied.
        #
        # Safe because the candidate organization came from the caller and
        # the query is additionally filtered by the *verified* user id: a
        # non-member simply finds nothing.
        with organization_db_scope(organization.id):
            membership = (
                Membership.unscoped.filter(
                    organization_id=organization.id, user_id=user_id
                )
                .only("id", "status", "user_id", "organization_id")
                .first()
            )
        if membership is None:
            return None

    return TenantResolution(
        organization_id=organization.id,
        organization_slug=organization.slug,
        organization_status=OrganizationStatus(organization.status),
        membership_id=membership.id,
        membership_status=MembershipStatus(membership.status),
        user_id=user_id,
    )


def resolve_by_organization_id(
    *, user_id: UUID, organization_id: UUID
) -> TenantResolution | None:
    """Resolve (user, organization id) - the OIDC authorization entry point.

    Exists because an OIDC authorization request names the organization by
    its stable opaque identifier, not by slug. A slug is a *display* handle:
    it appears in URLs, it is human-chosen, and using it as security identity
    would make the tenant a product could name depend on a string a customer
    picked (16-ecosystem-product-boundary.md section 9, P-6).

    Returns None for "no such organization" and for "not a member", exactly
    as :class:`TenantResolver` requires and for the same reason: the
    difference between them is an organization-enumeration oracle, and the
    authorization endpoint is reachable by any authenticated user.
    """
    return _resolve(user_id, id=organization_id)


class DatabaseTenantResolver:
    """Resolves (user, org slug) to a full tenant resolution."""

    def resolve(
        self, *, user_id: UUID, organization_slug: str
    ) -> TenantResolution | None:
        return _resolve(user_id, slug=organization_slug)


def register() -> None:
    """Called from ``OrganizationsConfig.ready()``."""
    from workeasy360.platform.tenancy.resolution import register_resolver

    register_resolver(DatabaseTenantResolver())

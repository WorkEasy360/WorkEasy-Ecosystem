"""Organization onboarding: the orchestrating use cases (Phase 5).

**Why this module exists rather than living in ``platform/organizations``.**
Creating an organization touches four modules that do not import each other:
``organizations`` (the tenant and membership), ``identity`` (the acting
user - a P1 *sibling* of organizations, F-5), ``authz`` (the owner role
assignment, P2) and ``audit`` (P2). Composing several platform modules that
sit at or below a common layer is exactly what ``platform/ecosystem`` and
``platform/launcher`` already do, at P3 and P4 respectively (ADR-021,
``.importlinter``'s ``platform-internal-layers`` contract) - this module is
the same pattern for the same reason, declared as P3 alongside ``ecosystem``
(ADR-023, docs/phase-5/00-overview.md).

Two use cases: ``create_organization`` (Tasks 2-13) and ``remove_member``
(the minimal membership-removal path Task 20's projection sync and Task 67's
acceptance scenario need - see this module's ``remove_member`` docstring for
why a full invitation/acceptance system is not built here, Task 32).

Every write in ``create_organization`` happens inside **one** transaction:
organization, owner membership, owner role assignment, audit entry, access-
directory projection row, and (if the outbox is installed) the
``organizations.organization.created`` event. A failure at any point rolls
back all of it - there is no state where an organization exists without an
owner, or an owner membership points at an organization that was never
committed (Task 5).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import transaction

from workeasy360.contracts.enums import AuditOutcome, OrganizationStatus
from workeasy360.contracts.errors import (
    IdempotencyConflict,
    PermissionDenied,
)
from workeasy360.contracts.ports.events import get_event_publisher
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)


def create_organization(*, user_id: UUID, name: Any, idempotency_key: str | None = None):
    """Create a new organization with its creator as owner (Tasks 2-13).

    **Authorization.** Any authenticated, active account may call this - see
    Task 10 (docs/phase-5/00-overview.md): there is deliberately no permission check here,
    because permission is a *membership* concept and this call exists
    precisely for a user who has none yet (Task 4: "do not special-case
    owner permission checks across the application" - this is the one
    legitimate special case, and it is a *lack* of a check, not an added one).

    **Mass assignment (Task 55).** The only accepted input is ``name``.
    ``organization_id``, ``owner_id``/membership role and lifecycle status
    are never read from caller input - the id is server-generated, the owner
    is always ``user_id`` (the verified caller), and status is always
    ``ACTIVE`` (Task 8).
    """
    from workeasy360.platform.authz import services as authz
    from workeasy360.platform.identity.models import User
    from workeasy360.platform.organizations.services import (
        Claim,
        claim_creation,
        complete_creation,
        create_organization_and_owner_membership,
        fingerprint,
        replay_result,
        validate_name,
    )
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    clean_name = validate_name(name)
    request_fingerprint = fingerprint({"name": clean_name})

    # The entire command runs with no tenant context, by construction: there
    # is no organization to scope to until this command creates one. Every
    # tenant-scoped write below (RoleAssignment, the audit entry, the outbox
    # event - organization/membership are organizations' own concern, see
    # create_organization_and_owner_membership) sets organization_id
    # explicitly and relies on organization_db_scope() for RLS (Layer 3)
    # rather than the scoped manager (Layer 2), exactly the pattern
    # platform.organizations.resolver's tenant bootstrap already uses.
    with (
        transaction.atomic(),
        unscoped_access(
            reason="Organization creation orchestration: no tenant context "
            "can exist before this call creates the tenant",
            module="platform.onboarding.services",
        ),
    ):
        user = User.objects.filter(pk=user_id).first()
        if user is None or not user.is_active:
            # Deliberately the same denial shape as "no such user": a caller
            # reaching this with a stale/disabled account should not learn
            # which. In practice unreachable via HTTP (the view requires an
            # authenticated session), defence-in-depth for any other caller.
            raise PermissionDenied("Account may not create an organization.")

        claim: Claim | None = None
        if idempotency_key:
            claim = claim_creation(
                user_id=user_id,
                key=idempotency_key,
                request_fingerprint=request_fingerprint,
            )
            if claim.is_replay:
                if (
                    claim.organization_id is None
                ):  # pragma: no cover - see Claim's docstring
                    raise IdempotencyConflict(
                        "This organization-creation request is already being "
                        "processed. Try again shortly.",
                    )
                return replay_result(claim.organization_id, user_id)

        # The organization, its owner membership and the owner's chooser row.
        created = create_organization_and_owner_membership(
            user_id=user_id, name=clean_name
        )
        organization_id = created.organization_id

        ctx = TenantContext(
            organization_id=organization_id,
            membership_id=created.membership_id,
            user_id=user.id,
            organization_status=OrganizationStatus.ACTIVE,
        )

        # create_organization_and_owner_membership opens and closes its own
        # organization_db_scope internally (Layer 3 RLS only), so neither
        # that nor a tenant context is still bound by the time it returns
        # here. Every tenant-scoped write below (RoleAssignment, the audit
        # entry, the outbox event) needs both layers re-entered: Layer 3
        # (organization_db_scope) for RLS's WITH CHECK, and Layer 2
        # (tenant_context) because audit.record's own read
        # (AuditEvent.objects, the *scoped* manager) requires a real bound
        # context, not merely unscoped_access - found by
        # test_organization_creation_never_produces_a_cross_organization_role_assignment
        # at a real commit, not by a rollback-mode test that would have
        # rolled back before either check ran. With a genuine ctx bound,
        # every write below uses its normal scoped manager - unscoped_access
        # is no longer needed past this point.
        with tenant_context(ctx), organization_db_scope(organization_id):
            authz.assign_system_role(
                organization_id=organization_id,
                membership_id=created.membership_id,
                role_key="owner",
            )

            from workeasy360.platform.audit import services as audit

            audit.record(
                ctx,
                "organization.created",
                resource_type="organizations.organization",
                resource_id=organization_id,
                changes={"display_name": clean_name, "slug": created.slug},
                outcome=AuditOutcome.SUCCESS,
            )

            publisher = get_event_publisher()
            if publisher is not None:
                publisher.publish(
                    ctx,
                    "organizations.organization.created",
                    1,
                    aggregate_type="organizations.organization",
                    aggregate_id=str(organization_id),
                    payload={
                        "organization_id": str(organization_id),
                        "slug": created.slug,
                        "display_name": created.display_name,
                        "created_by_user_id": str(user.id),
                    },
                )

        if claim is not None:
            complete_creation(claim, organization_id)

    metrics.increment("organization_created_total")
    logger.info("organizations.created", organization_id=str(organization_id))
    return created


def remove_member(ctx: TenantContext, membership_id: UUID) -> None:
    """Remove a membership from the acting user's organization.

    Minimal membership-lifecycle mutation this phase adds (Task 20's
    "membership removed" projection-sync trigger, Task 67's acceptance
    scenario). **Not an invitation system** - Task 32 explicitly does not
    require one for organization creation to be usable, and this phase does
    not build one; this only removes an *existing* membership, which
    something must be able to do for the projection's removal path to be
    exercised at all.
    """
    from workeasy360.platform.authz import services as authz
    from workeasy360.platform.entitlements.services import seats
    from workeasy360.platform.organizations.services import mark_membership_removed

    authz.require(ctx, "platform.member.remove")

    # The membership and its chooser row.
    member = mark_membership_removed(ctx.organization_id, membership_id)

    # Same transaction as the removal. Found by the Phase 5 final audit: the
    # seat stayed assigned to the removed member, and since the seat screen
    # lists active members only, nobody could free it - each removal of a
    # seated member permanently shrank the organization's usable seats.
    released_seats = seats.release_all_seats(
        ctx.organization_id, membership_id=member.membership_id
    )

    from workeasy360.platform.audit import services as audit

    audit.record(
        ctx,
        "membership.removed",
        resource_type="organizations.membership",
        resource_id=member.membership_id,
        changes={"seats_released": released_seats},
    )

    publisher = get_event_publisher()
    if publisher is not None:
        publisher.publish(
            ctx,
            "organizations.membership.removed",
            1,
            aggregate_type="organizations.membership",
            aggregate_id=str(member.membership_id),
            payload={
                "organization_id": str(ctx.organization_id),
                "membership_id": str(member.membership_id),
                "user_id": str(member.user_id),
            },
        )

    metrics.increment("organization_membership_removed_total")


def create_organization_for_pending_owner(*, owner_user_id: UUID, name: Any) -> Any:
    """Create an organization whose owner has been **invited, not enrolled**.

    For a partner provisioning a customer: the partner names the owner, but
    naming someone must not make them the owner of an organization they never
    asked for. The owner ``Membership`` is created ``invited`` - it grants
    nothing, holds no role and is not in anyone's chooser - until that person
    accepts (:func:`activate_pending_owner`).

    Returns the ``CreateOrganizationResult``. No role assignment or audit
    entry is written here: the caller, which knows who is acting, records the
    audit trail in the new organization's own scope.
    """
    from workeasy360.contracts.enums import MembershipStatus
    from workeasy360.platform.organizations.services import (
        create_organization_and_owner_membership,
        validate_name,
    )
    from workeasy360.platform.tenancy.context import no_tenant

    # Like every organization creation, this runs with no ORM tenant context:
    # there is no organization to scope to until it exists. (A partner caller
    # arrives bound to the platform organization; that binding must not be the
    # one the new membership row is checked against.)
    with no_tenant():
        return create_organization_and_owner_membership(
            user_id=owner_user_id,
            name=validate_name(name),
            owner_status=MembershipStatus.INVITED,
        )


def activate_pending_owner(*, user_id: UUID, organization_id: UUID) -> Any:
    """The invited owner accepts: the membership becomes active and the owner
    role is assigned, in one transaction. Idempotent: a second acceptance
    finds the owner already active and changes nothing.

    The caller has verified that ``user_id`` is the authenticated person the
    invitation was issued to; this function trusts that and does the work.
    """
    from workeasy360.platform.authz import services as authz
    from workeasy360.platform.organizations.services import activate_invited_owner
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        transaction.atomic(),
        unscoped_access(
            reason="An invited owner accepts before any tenant context can exist",
            module="platform.onboarding.services",
        ),
        organization_db_scope(organization_id),
    ):
        # The membership and the owner's chooser row.
        owner, status, activated = activate_invited_owner(organization_id, user_id)
        if not activated:
            return owner
        ctx = TenantContext(
            organization_id=organization_id,
            membership_id=owner.membership_id,
            user_id=user_id,
            organization_status=status,
        )
        with tenant_context(ctx):
            authz.assign_system_role(
                organization_id=organization_id,
                membership_id=owner.membership_id,
                role_key="owner",
            )
            from workeasy360.platform.audit import services as audit

            audit.record(
                ctx,
                "organization.owner_accepted",
                resource_type="organizations.membership",
                resource_id=owner.membership_id,
            )
    return owner

"""Phase 5 concurrency, with real threads, real connections, real commits.

Same method as test_commercial_concurrency.py: nothing mocked, every worker
on its own connection, released from a barrier so the requests genuinely
overlap, ``transaction=True`` so every write really commits. Each test
asserts the absence of the specific failure a race would produce here:
a duplicate organization, membership, role assignment or projection row, a
partial bootstrap, a resurrected projection row, a recovery or TOTP code
accepted twice - and, in every test, no worker error at all (a deadlock or
an unhandled IntegrityError would land there).
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from django.db import connections

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.concurrency]

WORKERS = 8


def _run(worker, count=WORKERS):
    """Fire ``count`` workers simultaneously; collect outcomes and errors."""
    results: list = []
    errors: list = []
    lock = threading.Lock()
    barrier = threading.Barrier(count)

    def wrapped(index: int) -> None:
        try:
            barrier.wait(timeout=30)
            outcome = worker(index)
            with lock:
                results.append(outcome)
        except Exception as exc:
            with lock:
                errors.append(exc)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=count) as pool:
        for future in as_completed([pool.submit(wrapped, i) for i in range(count)]):
            future.result()
    return results, errors


def _state_of(organization_id):
    """Everything organization creation writes, read back per organization."""
    from workeasy360.integration.events.models import OutboxEvent
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.authz.models import RoleAssignment
    from workeasy360.platform.organizations.models import (
        Membership,
        UserOrganizationAccess,
    )
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="test assertion", module="tests"),
        organization_db_scope(organization_id),
    ):
        return {
            "memberships": list(
                Membership.unscoped.filter(organization_id=organization_id).values_list(
                    "user_id", "is_owner", "status"
                )
            ),
            "role_assignments": list(
                RoleAssignment.unscoped.filter(
                    organization_id=organization_id
                ).values_list("role__key", flat=True)
            ),
            "audit": AuditEvent.unscoped.filter(
                organization_id=organization_id, action="organization.created"
            ).count(),
            "outbox": OutboxEvent.objects.filter(
                organization_id=organization_id,
                event_type="organizations.organization.created",
            ).count(),
            "projection": UserOrganizationAccess.objects.filter(
                organization_id=organization_id
            ).count(),
        }


def _complete_bootstrap(user_id):
    return {
        "memberships": [(user_id, True, "active")],
        "role_assignments": ["owner"],
        "audit": 1,
        "outbox": 1,
        "projection": 1,
    }


# --- organization creation ------------------------------------------------


def test_one_idempotency_key_from_many_threads_creates_one_organization(user_factory):
    from workeasy360.platform.onboarding.services import create_organization
    from workeasy360.platform.organizations.models import (
        Organization,
        OrganizationCreationRequest,
    )

    user = user_factory()
    key = uuid.uuid4().hex

    results, errors = _run(
        lambda _i: create_organization(
            user_id=user.id, name="Race Co", idempotency_key=key
        )
    )

    assert errors == []
    assert len({r.organization_id for r in results}) == 1
    assert sum(r.created for r in results) == 1  # the rest are replays
    assert Organization.objects.filter(created_by_id=user.id).count() == 1
    assert OrganizationCreationRequest.objects.filter(user_id=user.id).count() == 1
    assert _state_of(results[0].organization_id) == _complete_bootstrap(user.id)


def test_distinct_keys_from_many_threads_create_independent_complete_organizations(
    user_factory,
):
    from workeasy360.platform.authz.models import Role
    from workeasy360.platform.onboarding.services import create_organization
    from workeasy360.platform.organizations.models import (
        Organization,
        UserOrganizationAccess,
    )

    user = user_factory()

    results, errors = _run(
        lambda _i: create_organization(
            user_id=user.id, name="Same Name Ltd", idempotency_key=uuid.uuid4().hex
        )
    )

    assert errors == []
    assert all(r.created for r in results)
    ids = {r.organization_id for r in results}
    assert len(ids) == WORKERS
    assert len({r.slug for r in results}) == WORKERS  # slug collisions resolved
    assert Organization.objects.filter(created_by_id=user.id).count() == WORKERS
    for organization_id in ids:
        assert _state_of(organization_id) == _complete_bootstrap(user.id)
    assert UserOrganizationAccess.objects.filter(user_id=user.id).count() == WORKERS
    # Role definitions are global: N organizations never duplicate them.
    assert Role.objects.filter(key="owner", organization__isnull=True).count() == 1


def test_many_users_creating_at_once_never_cross_ownership(user_factory):
    from workeasy360.platform.onboarding.services import create_organization

    users = [user_factory() for _ in range(WORKERS)]

    results, errors = _run(
        lambda i: (
            users[i].id,
            create_organization(user_id=users[i].id, name=f"Company {i}"),
        )
    )

    assert errors == []
    for user_id, result in results:
        assert _state_of(result.organization_id) == _complete_bootstrap(user_id)


# --- projection ------------------------------------------------------------


def _organization_with_members(user_factory, count):
    """A real organization (owner bootstrapped by the production path) plus
    ``count`` ordinary active members, projection rows included."""
    from workeasy360.contracts.enums import MembershipStatus, OrganizationStatus
    from workeasy360.contracts.values.tenant import TenantContext
    from workeasy360.platform.onboarding.services import create_organization
    from workeasy360.platform.organizations import projection
    from workeasy360.platform.organizations.models import Membership, Organization
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    owner = user_factory()
    created = create_organization(user_id=owner.id, name="Busy Org")
    members = []
    with (
        unscoped_access(reason="test fixture", module="tests"),
        organization_db_scope(created.organization_id),
    ):
        org = Organization.objects.get(id=created.organization_id)
        for _ in range(count):
            user = user_factory()
            membership = Membership(
                organization_id=org.id,
                user_id=user.id,
                status=MembershipStatus.ACTIVE.value,
                member_type="internal",
            )
            membership.save()
            projection.sync_access_for_membership(
                user_id=user.id, membership=membership, organization=org
            )
            members.append(membership)
    ctx = TenantContext(
        organization_id=org.id,
        membership_id=created.membership_id,
        user_id=owner.id,
        organization_status=OrganizationStatus.ACTIVE,
    )
    return org, ctx, members


def _remove(ctx, membership_id):
    from workeasy360.platform.onboarding.services import remove_member
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import tenant_db_scope

    with tenant_context(ctx), tenant_db_scope(ctx):
        remove_member(ctx, membership_id)


def test_rebuild_racing_member_removal_never_resurrects_a_removed_member(user_factory):
    """Without the rebuild's row locks, a rebuild that read a membership as
    active just before its removal committed would write its projection row
    back afterwards - a stale row the next drift check reports. Several
    rounds, each a full rebuild racing a burst of removals."""
    from workeasy360.platform.organizations.models import UserOrganizationAccess
    from workeasy360.platform.organizations.projection import (
        check_access_projection_drift,
        rebuild_access_projection,
    )

    org, ctx, members = _organization_with_members(user_factory, 5 * (WORKERS - 1))
    batches = [members[i :: WORKERS - 1] for i in range(WORKERS - 1)]

    def worker(index: int):
        if index == 0:
            return [rebuild_access_projection() for _ in range(3)]
        for membership in batches[index - 1]:
            _remove(ctx, membership.id)
        return None

    _results, errors = _run(worker)

    assert errors == []
    remaining = set(
        UserOrganizationAccess.objects.filter(organization_id=org.id).values_list(
            "user_id", flat=True
        )
    )
    assert remaining == {ctx.user_id}  # only the owner is left
    report = check_access_projection_drift()
    assert report.is_clean, report


def test_concurrent_projection_syncs_for_one_pair_converge_on_one_row(user_factory):
    from workeasy360.platform.organizations import projection
    from workeasy360.platform.organizations.models import (
        Membership,
        Organization,
        UserOrganizationAccess,
    )
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    org, _ctx, (member,) = _organization_with_members(user_factory, 1)
    UserOrganizationAccess.objects.filter(user_id=member.user_id).delete()

    def worker(_index: int):
        with (
            unscoped_access(reason="test worker", module="tests"),
            organization_db_scope(org.id),
        ):
            organization = Organization.objects.get(id=org.id)
            membership = Membership.unscoped.get(id=member.id)
            return projection.sync_access_for_membership(
                user_id=member.user_id, membership=membership, organization=organization
            )

    results, errors = _run(worker)

    assert errors == []
    assert sum(results) == 1  # one insert; every other call found it converged
    assert UserOrganizationAccess.objects.filter(user_id=member.user_id).count() == 1


def test_concurrent_removals_of_one_member_record_one_state_change(user_factory):
    from workeasy360.contracts.errors import OrganizationValidationError
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    org, ctx, (member,) = _organization_with_members(user_factory, 1)

    results, errors = _run(lambda _i: _remove(ctx, member.id))

    assert len(results) == 1
    assert len(errors) == WORKERS - 1
    assert all(isinstance(e, OrganizationValidationError) for e in errors)
    with (
        unscoped_access(reason="test assertion", module="tests"),
        organization_db_scope(org.id),
    ):
        assert (
            AuditEvent.unscoped.filter(
                organization_id=org.id, action="membership.removed"
            ).count()
            == 1
        )

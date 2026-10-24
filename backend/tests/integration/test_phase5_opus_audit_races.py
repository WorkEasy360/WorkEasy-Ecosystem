"""Phase 5 final (Opus) audit: failure injection and races with real commits.

``transaction=True`` throughout: a rolled-back test transaction would hide
exactly what these check - what is left behind once a transaction really
commits or really rolls back, and what concurrent real transactions do to
each other. Threads use the same barrier harness as
test_phase5_concurrency.py.
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from django.db import connections

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.concurrency]

WORKERS = 8


def _run(worker, count=WORKERS):
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


def _everything_written_for(organization_id):
    """Every row organization creation writes, for one organization id."""
    from workeasy360.integration.events.models import OutboxEvent
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.authz.models import RoleAssignment
    from workeasy360.platform.organizations.models import (
        Membership,
        Organization,
        UserOrganizationAccess,
    )
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="test assertion over one organization", module="tests"),
        organization_db_scope(organization_id),
    ):
        return {
            "organization": Organization.objects.filter(id=organization_id).count(),
            "memberships": Membership.unscoped.filter(
                organization_id=organization_id
            ).count(),
            "role_assignments": RoleAssignment.unscoped.filter(
                organization_id=organization_id
            ).count(),
            "audit": AuditEvent.unscoped.filter(organization_id=organization_id).count(),
            "projection": UserOrganizationAccess.objects.filter(
                organization_id=organization_id
            ).count(),
            "outbox": OutboxEvent.objects.filter(organization_id=organization_id).count(),
        }


NOTHING = {
    "organization": 0,
    "memberships": 0,
    "role_assignments": 0,
    "audit": 0,
    "projection": 0,
    "outbox": 0,
}
COMPLETE = {
    "organization": 1,
    "memberships": 1,
    "role_assignments": 1,
    "audit": 1,
    "projection": 1,
    "outbox": 1,
}


def _fail(*_args, **_kwargs):
    raise RuntimeError("injected failure")


class _FailingPublisher:
    def publish(self, *_args, **_kwargs):
        raise RuntimeError("injected failure")


# --- Audit 1/3: creation atomicity, failure after each stage -----------------


@pytest.mark.parametrize(
    "stage",
    [
        "membership",
        "role_assignment",
        "audit",
        "projection",
        "outbox",
        "idempotency_completion",
    ],
)
def test_a_failure_at_any_creation_stage_leaves_nothing_and_a_retry_completes(
    user_factory, monkeypatch, stage
):
    """Organization, owner membership, owner role, audit, projection, outbox
    and the idempotency claim are one transaction: a failure after any of
    them commits none of them, leaves no tenant variable bound, and the same
    idempotency key then creates exactly one complete organization."""
    from workeasy360.platform.audit import services as audit_services
    from workeasy360.platform.authz import services as authz_services
    from workeasy360.platform.onboarding import services as onboarding
    from workeasy360.platform.organizations import projection
    from workeasy360.platform.organizations import services as org_services
    from workeasy360.platform.organizations.models import (
        Membership,
        Organization,
        OrganizationCreationRequest,
    )
    from workeasy360.platform.tenancy.db import current_tenant_variable

    user = user_factory()
    key = uuid.uuid4().hex
    created_ids = []
    real_create = org_services.create_organization_and_owner_membership

    def spy(**kwargs):
        result = real_create(**kwargs)
        created_ids.append(result.organization_id)
        return result

    monkeypatch.setattr(org_services, "create_organization_and_owner_membership", spy)
    with monkeypatch.context() as injected:
        if stage == "membership":
            injected.setattr(Membership, "save", _fail)
        elif stage == "role_assignment":
            injected.setattr(authz_services, "assign_system_role", _fail)
        elif stage == "audit":
            injected.setattr(audit_services, "record", _fail)
        elif stage == "projection":
            injected.setattr(projection, "sync_access_for_membership", _fail)
        elif stage == "outbox":
            injected.setattr(onboarding, "get_event_publisher", _FailingPublisher)
        elif stage == "idempotency_completion":
            injected.setattr(org_services, "complete_creation", _fail)

        with pytest.raises(RuntimeError, match="injected failure"):
            onboarding.create_organization(
                user_id=user.id, name="Atomic Co", idempotency_key=key
            )
        assert current_tenant_variable() is None

    assert Organization.objects.filter(created_by_id=user.id).count() == 0
    assert OrganizationCreationRequest.objects.filter(user_id=user.id).count() == 0
    for organization_id in created_ids:
        assert _everything_written_for(organization_id) == NOTHING

    retried = onboarding.create_organization(
        user_id=user.id, name="Atomic Co", idempotency_key=key
    )
    assert retried.created
    assert Organization.objects.filter(created_by_id=user.id).count() == 1
    assert _everything_written_for(retried.organization_id) == COMPLETE


def test_creation_inside_another_tenants_scope_neither_leaks_nor_widens_it(
    organization_factory, user_factory
):
    """Audit 3: a caller already bound to organization A that creates
    organization B gets A back, bound, afterwards - B's scope never leaks
    into the rest of A's transaction."""
    from django.db import transaction

    from workeasy360.platform.onboarding.services import create_organization
    from workeasy360.platform.tenancy.db import (
        current_tenant_variable,
        organization_db_scope,
    )

    org_a, _membership, _user = organization_factory()
    creator = user_factory()
    with transaction.atomic(), organization_db_scope(org_a.id):
        created = create_organization(user_id=creator.id, name="Nested Co")
        assert current_tenant_variable() == str(org_a.id)
    assert created.organization_id != org_a.id
    assert _everything_written_for(created.organization_id) == COMPLETE


# --- Audit 9/11: projection rebuild races and failure recovery --------------


def _org_with_members(user_factory, count):
    from workeasy360.contracts.enums import MembershipStatus, OrganizationStatus
    from workeasy360.contracts.values.tenant import TenantContext
    from workeasy360.platform.onboarding.services import create_organization
    from workeasy360.platform.organizations import projection
    from workeasy360.platform.organizations.models import Membership, Organization
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    owner = user_factory()
    created = create_organization(user_id=owner.id, name=f"Org {uuid.uuid4().hex[:6]}")
    members = []
    with (
        unscoped_access(reason="test fixture", module="tests"),
        organization_db_scope(created.organization_id),
    ):
        org = Organization.objects.get(id=created.organization_id)
        for _ in range(count):
            membership = Membership(
                organization_id=org.id,
                user_id=user_factory().id,
                status=MembershipStatus.ACTIVE.value,
            )
            membership.save()
            projection.sync_access_for_membership(
                user_id=membership.user_id, membership=membership, organization=org
            )
            members.append(membership)
    ctx = TenantContext(
        organization_id=org.id,
        membership_id=created.membership_id,
        user_id=owner.id,
        organization_status=OrganizationStatus.ACTIVE,
    )
    return org, ctx, members


def _projected(organization_id):
    from workeasy360.platform.organizations.models import UserOrganizationAccess

    return {
        row.user_id: row
        for row in UserOrganizationAccess.objects.filter(organization_id=organization_id)
    }


def _suspend(org_id, reactivate=False):
    from workeasy360.contracts.enums import OrganizationStatus
    from workeasy360.platform.organizations.services import set_lifecycle_status

    set_lifecycle_status(
        org_id,
        target=OrganizationStatus.ACTIVE if reactivate else OrganizationStatus.SUSPENDED,
        reason="audit race",
    )


def test_rebuild_racing_suspension_converges_on_the_real_status(user_factory):
    """A rebuild that read the organization before a suspension committed
    must not write the old status back over the suspension's own sync."""
    from workeasy360.platform.organizations.models import Organization
    from workeasy360.platform.organizations.projection import (
        check_access_projection_drift,
        rebuild_access_projection,
    )

    org, _ctx, _members = _org_with_members(user_factory, 20)

    def worker(index):
        if index == 0:
            return [rebuild_access_projection() for _ in range(3)]
        for flip in range(4):
            _suspend(org.id, reactivate=bool((index + flip) % 2))
        return None

    _results, errors = _run(worker, count=4)

    assert errors == []
    status = Organization.objects.get(id=org.id).status
    assert {row.organization_status for row in _projected(org.id).values()} == {status}
    assert check_access_projection_drift().is_clean


def test_two_rebuilds_and_a_prune_at_once_converge_without_duplicates(user_factory):
    from workeasy360.platform.organizations.models import UserOrganizationAccess
    from workeasy360.platform.organizations.projection import (
        check_access_projection_drift,
        rebuild_access_projection,
    )

    orgs = [_org_with_members(user_factory, 6) for _ in range(3)]
    for org, _ctx, members in orgs:
        # Missing rows for the rebuilds to restore.
        UserOrganizationAccess.objects.filter(
            organization_id=org.id, user_id__in=[m.user_id for m in members[:3]]
        ).delete()

    results, errors = _run(
        lambda i: rebuild_access_projection(prune_stale=(i % 2 == 0)), count=4
    )

    assert errors == []
    assert sum(r.organizations_failed for r in results) == 0
    for org, ctx, members in orgs:
        rows = _projected(org.id)
        assert set(rows) == {ctx.user_id, *(m.user_id for m in members)}
    assert check_access_projection_drift().is_clean


def test_rebuild_racing_organization_creation_misses_nothing(user_factory):
    """Organizations created while a rebuild runs are either visited by it or
    already complete from their own transaction - drift is clean either way."""
    from workeasy360.platform.onboarding.services import create_organization
    from workeasy360.platform.organizations.projection import (
        check_access_projection_drift,
        rebuild_access_projection,
    )

    for _ in range(3):
        _org_with_members(user_factory, 3)
    creators = [user_factory() for _ in range(WORKERS)]

    def worker(index):
        if index == 0:
            return rebuild_access_projection(prune_stale=True)
        return create_organization(user_id=creators[index].id, name=f"New {index}")

    results, errors = _run(worker)

    assert errors == []
    for result in results:
        if hasattr(result, "organization_id"):
            assert _everything_written_for(result.organization_id) == COMPLETE
    assert check_access_projection_drift().is_clean


def test_a_rebuild_that_fails_midway_loses_nothing_and_a_retry_completes(
    user_factory, monkeypatch
):
    """Audit 11: a failure part-way through leaves every organization either
    fully rebuilt or untouched - no partial organization, no duplicate row,
    no legitimate row removed - and a re-run converges."""
    from workeasy360.platform.organizations import projection
    from workeasy360.platform.organizations.models import UserOrganizationAccess

    orgs = [_org_with_members(user_factory, 4) for _ in range(3)]
    before = {org.id: set(_projected(org.id)) for org, _c, _m in orgs}
    victim = orgs[1][0]
    UserOrganizationAccess.objects.filter(
        organization_id__in=[o.id for o, _c, _m in orgs]
    ).exclude(is_owner=True).delete()

    real_sync = projection.sync_access_for_membership
    calls = {"n": 0}

    def flaky(*, user_id, membership, organization):
        if organization.id == victim.id:
            calls["n"] += 1
            if calls["n"] == 2:  # after one row of this organization is written
                raise RuntimeError("injected failure")
        return real_sync(
            user_id=user_id, membership=membership, organization=organization
        )

    monkeypatch.setattr(projection, "sync_access_for_membership", flaky)
    first = projection.rebuild_access_projection(prune_stale=True)
    monkeypatch.setattr(projection, "sync_access_for_membership", real_sync)

    assert first.organizations_failed == 1
    # The failed organization rolled back as a unit: only its owner row
    # (never deleted above) is there, not a half-rebuilt set.
    assert set(_projected(victim.id)) == {orgs[1][1].user_id}
    for org, _c, _m in orgs:
        if org.id != victim.id:
            assert set(_projected(org.id)) == before[org.id]

    second = projection.rebuild_access_projection(prune_stale=True)
    assert second.organizations_failed == 0
    for org, _c, _m in orgs:
        assert set(_projected(org.id)) == before[org.id]
        assert UserOrganizationAccess.objects.filter(
            organization_id=org.id
        ).count() == len(before[org.id])


def test_a_converged_rebuild_writes_nothing_and_keeps_the_chooser_order(user_factory):
    """Audit 10: byte-identical, updated_at included."""
    from workeasy360.platform.organizations.models import UserOrganizationAccess
    from workeasy360.platform.organizations.projection import rebuild_access_projection

    for _ in range(2):
        _org_with_members(user_factory, 5)
    snapshot = list(UserOrganizationAccess.objects.order_by("id").values())

    stats = [rebuild_access_projection() for _ in range(3)]

    assert [s.rows_written for s in stats] == [0, 0, 0]
    assert list(UserOrganizationAccess.objects.order_by("id").values()) == snapshot


# --- Audit 13: chooser pagination while the directory changes ---------------


def _page_all(client, *, mutate_after_first=None, limit=5):
    seen = []
    response = client.get("/api/v1/organizations", {"limit": limit}).json()
    seen.extend(o["organization_id"] for o in response["organizations"])
    if mutate_after_first:
        mutate_after_first()
    while response["has_more"]:
        response = client.get(
            "/api/v1/organizations", {"limit": limit, "cursor": response["next_cursor"]}
        ).json()
        seen.extend(o["organization_id"] for o in response["organizations"])
    return seen


@pytest.mark.parametrize("mutation", ["remove", "add", "suspend"])
def test_chooser_paging_through_a_mutation_never_repeats_and_only_drops_the_mutated(
    client, user_factory, mutation
):
    """Keyset semantics, stated precisely: a traversal in progress never sees
    an organization twice; a membership removed mid-traversal may vanish
    (correctly); one added mid-traversal appears on the next traversal (it
    sorts first); a suspension changes nothing about order."""
    from workeasy360.contracts.enums import MembershipStatus
    from workeasy360.platform.onboarding.services import (
        create_organization,
        remove_member,
    )
    from workeasy360.platform.organizations import projection
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import tenant_db_scope

    member = user_factory(password="a long enough passphrase for tests")
    orgs = [_org_with_members(user_factory, 0) for _ in range(12)]
    memberships = {}
    for org, ctx, _m in orgs:
        with tenant_context(ctx), tenant_db_scope(ctx):
            membership = Membership(
                organization_id=org.id, user=member, status=MembershipStatus.ACTIVE.value
            )
            membership.save()
            projection.sync_access_for_membership(
                user_id=member.id, membership=membership, organization=org
            )
            memberships[org.id] = (ctx, membership)
        time.sleep(0.002)  # distinct updated_at values, so order is meaningful

    client.force_login(member)
    everything = {str(org.id) for org, _c, _m in orgs}
    first_page = client.get("/api/v1/organizations", {"limit": 5}).json()["organizations"]
    target = next(
        org
        for org, _c, _m in orgs
        if str(org.id) not in {o["organization_id"] for o in first_page}
    )
    added = {}

    def mutate():
        if mutation == "remove":
            ctx, membership = memberships[target.id]
            with tenant_context(ctx), tenant_db_scope(ctx):
                remove_member(ctx, membership.id)
        elif mutation == "suspend":
            _suspend(target.id)
        else:
            added["id"] = str(
                create_organization(user_id=member.id, name="Late Co").organization_id
            )

    seen = _page_all(client, mutate_after_first=mutate)

    assert len(seen) == len(set(seen)), "an organization was listed twice"
    if mutation == "remove":
        assert set(seen) == everything - {str(target.id)}
    elif mutation == "suspend":
        assert set(seen) == everything
    else:
        assert set(seen) == everything  # the new one sorts ahead of the cursor
        assert added["id"] in set(_page_all(client))  # and the next traversal has it


# --- Audit 60: the one-owner invariant under concurrency --------------------


def test_concurrent_removals_never_leave_an_organization_without_its_owner(user_factory):
    """Every member (the owner included) removed at once by the owner: the
    owner's removal is refused every time, and the owner keeps the owner
    role. There is exactly one owner per organization (uq_one_owner_per_org)
    and no transfer path, so 'last owner' is 'the owner'."""
    from workeasy360.contracts.errors import OrganizationValidationError
    from workeasy360.platform.authz.models import RoleAssignment
    from workeasy360.platform.onboarding.services import remove_member
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope, tenant_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    org, ctx, members = _org_with_members(user_factory, WORKERS - 1)
    targets = [ctx.membership_id, *(m.id for m in members)]

    def worker(index):
        with tenant_context(ctx), tenant_db_scope(ctx):
            remove_member(ctx, targets[index])

    results, errors = _run(worker)

    assert len(results) == WORKERS - 1
    assert len(errors) == 1 and isinstance(errors[0], OrganizationValidationError)
    with (
        unscoped_access(reason="test assertion", module="tests"),
        organization_db_scope(org.id),
    ):
        owner = Membership.unscoped.get(organization_id=org.id, is_owner=True)
        assert owner.id == ctx.membership_id and owner.status == "active"
        assert RoleAssignment.unscoped.filter(
            membership_id=owner.id, role__key="owner"
        ).exists()


# --- Audit 55/59: member removal racing a seat assignment -------------------


def test_a_seat_assigned_while_the_member_is_removed_does_not_survive(user_factory):
    """The seat assignment checks the member is active, then inserts. A
    removal committing in between used to release nothing (the seat did not
    exist yet), after which the insert committed a seat for a removed member -
    the leak P1-2 closes, reopened by a race. The check now locks the
    membership row, so the removal waits for the assignment and then releases
    it (or, the other way round, the assignment sees the removal)."""
    from workeasy360.commercial.provisioning.services import grant_product
    from workeasy360.platform.entitlements.services import seats
    from workeasy360.platform.launcher import services as launcher
    from workeasy360.platform.organizations import selectors
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import tenant_db_scope

    org, ctx, (member,) = _org_with_members(user_factory, 1)
    grant_product(org.id, "crm", seats=2)
    checked = threading.Event()
    removed = threading.Event()
    real_check = selectors.active_membership_exists

    def check_then_let_the_removal_run(*args, **kwargs):
        result = real_check(*args, **kwargs)
        checked.set()
        # Unfixed, the removal commits here; fixed, it is blocked by our row
        # lock, so give up waiting and carry on.
        removed.wait(timeout=2)
        return result

    def worker(index):
        with tenant_context(ctx), tenant_db_scope(ctx):
            if index == 0:
                launcher.assign_seat(ctx, product_code="crm", membership_id=member.id)
                return "assigned"
            checked.wait(timeout=10)
            from workeasy360.platform.onboarding.services import remove_member

            remove_member(ctx, member.id)
        removed.set()
        return "removed"

    import unittest.mock as mock

    with mock.patch.object(
        selectors, "active_membership_exists", check_then_let_the_removal_run
    ):
        results, errors = _run(worker, count=2)

    assert errors == [], errors
    assert sorted(results) == ["assigned", "removed"]
    with tenant_context(ctx), tenant_db_scope(ctx):
        assert member.id not in seats.held_by(org.id, pool_key="crm")
        assert seats.assigned_count(org.id, pool_key="crm") == 0

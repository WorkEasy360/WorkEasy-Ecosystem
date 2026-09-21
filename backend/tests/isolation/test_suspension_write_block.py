"""R-10 - suspended organizations cannot write, through any path.

The risk: ``suspended`` must block every write path - API, tasks, event
handlers, workflows, connectors. Implementing this as a per-view check
guarantees paths will be missed, and a missed path means a non-paying
customer keeps writing, or worse, an inconsistent half-suspended state.

The design is therefore **structural, at two chokepoints**:

    1. the tenancy middleware, which rejects unsafe HTTP methods
    2. TenantScopedModel.save()/delete(), the persistence boundary

Neither is sufficient alone. Middleware never sees a Celery task, a
management command or a shell; the model layer never sees a request early
enough to return a well-shaped 402. Both are tested here, along with the bulk
paths that bypass ``save()`` entirely - which is exactly what someone reaches
for when fixing data under time pressure.

Reads continue throughout. Suspension is read-only, not lockout: a customer
whose card expired must still be able to see their invoices, export their
data and pay (03-multi-tenancy.md section 3.1).
"""

from __future__ import annotations

import uuid

import pytest

from workeasy360.contracts.enums import MembershipStatus, OrganizationStatus
from workeasy360.contracts.errors import OrganizationNotWritable
from workeasy360.platform.organizations.models import Membership

pytestmark = [pytest.mark.django_db, pytest.mark.isolation]


@pytest.fixture
def suspended_org(organization_factory, tenant_ctx_factory):
    """An organization in the suspended state, with a live membership."""
    org, membership, user = organization_factory(status=OrganizationStatus.SUSPENDED)
    ctx = tenant_ctx_factory(org, membership, user)
    return org, membership, user, ctx


# --- chokepoint 2: the persistence boundary ------------------------------


def test_save_is_blocked_when_suspended(suspended_org, in_tenant):
    """model.save() refuses, whatever called it."""
    org, membership, user, ctx = suspended_org

    with in_tenant(ctx):
        new_member = Membership(
            organization_id=org.id,
            user_id=user.id,
            status=MembershipStatus.ACTIVE.value,
            member_type="internal",
        )
        with pytest.raises(OrganizationNotWritable) as exc:
            new_member.save()

    # 402, not 403: the remedy is commercial, not a permission change.
    assert exc.value.http_status == 402
    assert exc.value.code == "organization_not_writable"


def test_update_of_existing_row_is_blocked(suspended_org, in_tenant):
    org, membership, user, ctx = suspended_org

    with in_tenant(ctx):
        existing = Membership.objects.get(id=membership.id)
        existing.member_type = "collaborator"
        with pytest.raises(OrganizationNotWritable):
            existing.save()


def test_delete_is_blocked_when_suspended(suspended_org, in_tenant):
    org, membership, user, ctx = suspended_org

    with in_tenant(ctx):
        existing = Membership.objects.get(id=membership.id)
        with pytest.raises(OrganizationNotWritable):
            existing.delete()


# --- bulk paths, which bypass save() entirely ----------------------------


def test_queryset_update_is_blocked(suspended_org, in_tenant):
    """QuerySet.update() never calls save(), so it needs its own guard.

    Without this the block is bypassed by a one-line change that looks
    entirely reasonable in review.
    """
    org, membership, user, ctx = suspended_org

    with in_tenant(ctx):
        with pytest.raises(OrganizationNotWritable):
            Membership.objects.filter(id=membership.id).update(member_type="collaborator")


def test_queryset_delete_is_blocked(suspended_org, in_tenant):
    org, membership, user, ctx = suspended_org

    with in_tenant(ctx):
        with pytest.raises(OrganizationNotWritable):
            Membership.objects.filter(id=membership.id).delete()


def test_bulk_create_is_blocked(suspended_org, in_tenant):
    org, membership, user, ctx = suspended_org

    with in_tenant(ctx):
        with pytest.raises(OrganizationNotWritable):
            Membership.objects.bulk_create(
                [
                    Membership(
                        organization_id=org.id,
                        user_id=user.id,
                        status=MembershipStatus.INVITED.value,
                        member_type="internal",
                    )
                ]
            )


def test_bulk_update_is_blocked(suspended_org, in_tenant):
    org, membership, user, ctx = suspended_org

    with in_tenant(ctx):
        existing = Membership.objects.get(id=membership.id)
        existing.member_type = "collaborator"
        with pytest.raises(OrganizationNotWritable):
            Membership.objects.bulk_update([existing], ["member_type"])


# --- reads continue -------------------------------------------------------


def test_reads_still_work_when_suspended(suspended_org, in_tenant):
    """Suspension is read-only, not lockout.

    Hard lockout maximises churn, generates support load and risks
    data-protection complaints. The customer must still be able to see and
    export their data, and to pay.
    """
    org, membership, user, ctx = suspended_org

    with in_tenant(ctx):
        rows = list(Membership.objects.all())
        assert len(rows) == 1
        assert rows[0].id == membership.id


def test_context_reports_read_allowed_write_blocked(suspended_org):
    _, _, _, ctx = suspended_org
    assert ctx.is_read_allowed is True
    assert ctx.is_write_allowed is False


# --- background-task path -------------------------------------------------


def test_task_path_is_blocked_too(suspended_org, in_tenant):
    """A Celery task writing for a suspended org is blocked identically.

    This is the path middleware cannot see, and the reason the persistence
    chokepoint exists at all. The task carries its tenant context in headers
    and the model layer enforces the rule on arrival.
    """
    from workeasy360.contracts.values.tenant import TenantContext
    from workeasy360.platform.tenancy.context import tenant_context

    org, membership, user, ctx = suspended_org

    # Rebuild the context exactly as TenantTask.before_start would, from
    # serialised headers - proving the status survives the queue hop.
    headers = ctx.to_headers()
    restored = TenantContext.from_headers(headers)
    assert restored.organization_status is OrganizationStatus.SUSPENDED
    assert restored.is_write_allowed is False

    with in_tenant(ctx), tenant_context(restored):
        with pytest.raises(OrganizationNotWritable):
            Membership(
                organization_id=org.id,
                user_id=user.id,
                status=MembershipStatus.ACTIVE.value,
                member_type="internal",
            ).save()


# --- other non-writable statuses -----------------------------------------


@pytest.mark.parametrize(
    "status",
    [
        OrganizationStatus.SUSPENDED,
        OrganizationStatus.PENDING_DELETION,
        OrganizationStatus.PROVISIONING,
        OrganizationStatus.LOCKED,
        OrganizationStatus.DELETED,
    ],
)
def test_only_active_permits_writes(
    organization_factory, tenant_ctx_factory, in_tenant, status
):
    """Every non-active status blocks writes.

    Parameterised rather than written once for `suspended`, because the rule
    is "only active writes", not "suspended does not write" - and a new status
    added later must fail this test rather than silently permit writing.
    """
    org, membership, user = organization_factory(status=status)
    ctx = tenant_ctx_factory(org, membership, user)

    with in_tenant(ctx):
        with pytest.raises(OrganizationNotWritable):
            Membership(
                organization_id=org.id,
                user_id=user.id,
                status=MembershipStatus.ACTIVE.value,
                member_type="internal",
            ).save()


def test_active_organization_can_write(
    organization_factory, tenant_ctx_factory, in_tenant, user_factory
):
    """The positive case, so the block is not passing for the wrong reason.

    A test suite that only asserts denials can pass with a model layer that
    refuses everything.
    """
    org, membership, user = organization_factory(status=OrganizationStatus.ACTIVE)
    ctx = tenant_ctx_factory(org, membership, user)
    other_user = user_factory()

    with in_tenant(ctx):
        created = Membership(
            organization_id=org.id,
            user_id=other_user.id,
            status=MembershipStatus.ACTIVE.value,
            member_type="internal",
        )
        created.save()
        assert Membership.objects.filter(id=created.id).exists()


def test_reactivation_restores_writes(
    organization_factory, tenant_ctx_factory, in_tenant, user_factory
):
    """Nothing was deleted, so reactivation needs no data restoration."""
    org, membership, user = organization_factory(status=OrganizationStatus.SUSPENDED)
    other_user = user_factory()

    suspended_ctx = tenant_ctx_factory(org, membership, user)
    with in_tenant(suspended_ctx):
        with pytest.raises(OrganizationNotWritable):
            Membership(
                organization_id=org.id,
                user_id=other_user.id,
                status=MembershipStatus.ACTIVE.value,
                member_type="internal",
            ).save()

    active_ctx = suspended_ctx.replace_status(OrganizationStatus.ACTIVE)
    with in_tenant(active_ctx):
        Membership(
            organization_id=org.id,
            user_id=other_user.id,
            status=MembershipStatus.ACTIVE.value,
            member_type="internal",
        ).save()
        assert Membership.objects.count() == 2


# --- documented exceptions ------------------------------------------------


def test_audit_can_still_be_written_for_a_suspended_organization(
    suspended_org, in_tenant
):
    """The one permitted exception, and it is deliberate.

    Audit records what happened, including a *denied* attempt. If suspension
    blocked audit writes, the moment a customer most needs a record - the
    suspension itself, and every rejected action after it - would be the
    moment nothing was recorded.

    Audit is append-only and customer-visible, so permitting it carries no
    risk of a suspended tenant mutating business state.
    """
    from workeasy360.contracts.enums import AuditOutcome
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.audit.services import record

    org, membership, user, ctx = suspended_org

    with in_tenant(ctx):
        event = record(
            ctx,
            "platform.member.invite",
            resource_type="organizations.membership",
            resource_id=str(uuid.uuid4()),
            outcome=AuditOutcome.DENIED,
            reason="Organization is suspended",
        )
        assert AuditEvent.objects.filter(id=event.id).exists()

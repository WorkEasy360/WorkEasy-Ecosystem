"""The partner's people: invited by name, invited now or at activation, moved
between roles, removed - and what each does to their access and their account."""

from __future__ import annotations

import json
import uuid

import pytest
from django.core import mail

from tests.partner_setup.conftest import VIEW, post
from tests.phase9.conftest import deliver_account_mail
from workeasy360.commercial.partners import services
from workeasy360.commercial.partners.models import PartnerMember
from workeasy360.contracts.enums import PartnerStatus
from workeasy360.contracts.errors import (
    PartnerValidationError,
    PermissionDenied,
)

pytestmark = pytest.mark.django_db


def invite_url(partner_id) -> str:
    return f"/api/v1/admin/partners/{partner_id}/administrators"


def role_url(partner_id, member_id) -> str:
    return f"/api/v1/admin/partners/{partner_id}/members/{member_id}/role"


def invite(client, partner_id, **body):
    return client.post(
        invite_url(partner_id),
        json.dumps({"role": "admin", **body}),
        content_type="application/json",
    )


@pytest.fixture
def draft(console):
    return console["draft"], console["staff_ctx"]


def sent_to(address):
    deliver_account_mail()
    return [m for m in mail.outbox if address in m.to]


# --- naming the person ----------------------------------------------------------------------------------------


def test_a_new_administrator_is_created_with_the_name_given(manager, draft):
    partner, _ctx = draft
    email = f"new-{uuid.uuid4().hex[:6]}@example.com"
    response = invite(
        manager, partner.id, email=email, first_name="Ravi", last_name="Rao"
    )
    assert response.status_code == 201, response.content
    from workeasy360.platform.identity.models import User

    assert User.objects.get(email=email).full_name == "Ravi Rao"


def test_an_existing_accounts_name_is_never_overwritten_by_whoever_names_them(
    manager, draft, user_factory
):
    partner, _ctx = draft
    known = user_factory(full_name="Real Name")
    invite(manager, partner.id, email=known.email, first_name="Someone", last_name="Else")
    known.refresh_from_db()
    assert known.full_name == "Real Name"


def test_names_are_bounded_single_line_text(manager, draft):
    partner, _ctx = draft
    for bad in ({"first_name": "x" * 200}, {"first_name": "a\nb"}, {"last_name": ["x"]}):
        response = invite(manager, partner.id, email="n@example.com", **bad)
        assert response.status_code == 400, bad


# --- now or at activation -------------------------------------------------------------------------------------


def test_inviting_now_sends_the_link_now_and_records_when(manager, draft):
    partner, _ctx = draft
    email = f"now-{uuid.uuid4().hex[:6]}@example.com"
    invite(manager, partner.id, email=email, send_invitation="now")
    member = PartnerMember.objects.get(partner_id=partner.id, user__email=email)
    assert member.invitation_sent_at is not None
    assert len(sent_to(email)) == 1


def test_a_deferred_invitation_sends_nothing_until_the_partner_is_activated(
    manager, draft
):
    partner, ctx = draft
    email = f"later-{uuid.uuid4().hex[:6]}@example.com"
    response = invite(manager, partner.id, email=email, send_invitation="on_activation")
    assert response.status_code == 201
    member = PartnerMember.objects.get(partner_id=partner.id, user__email=email)
    assert member.invitation_sent_at is None and member.invitation_deferred is True
    assert sent_to(email) == []
    services.set_partner_status(
        ctx, partner.id, target=PartnerStatus.ACTIVE, reason="Ready to go live"
    )
    member.refresh_from_db()
    assert member.invitation_sent_at is not None and member.invitation_deferred is False
    assert len(sent_to(email)) == 1


def test_a_deferred_invitation_goes_out_exactly_once(manager, draft):
    partner, ctx = draft
    email = f"once-{uuid.uuid4().hex[:6]}@example.com"
    invite(manager, partner.id, email=email, send_invitation="on_activation")
    services.set_partner_status(
        ctx, partner.id, target=PartnerStatus.ACTIVE, reason="Ready to go live"
    )
    services.set_partner_status(
        ctx, partner.id, target=PartnerStatus.SUSPENDED, reason="Paused for the test"
    )
    services.set_partner_status(
        ctx, partner.id, target=PartnerStatus.ACTIVE, reason="Resumed after the pause"
    )
    assert len(sent_to(email)) == 1


def test_an_already_invited_member_is_not_invited_again_at_activation(manager, draft):
    partner, ctx = draft
    email = f"dup-{uuid.uuid4().hex[:6]}@example.com"
    invite(manager, partner.id, email=email, send_invitation="now")
    services.set_partner_status(
        ctx, partner.id, target=PartnerStatus.ACTIVE, reason="Ready to go live"
    )
    assert len(sent_to(email)) == 1


def test_a_person_who_can_already_sign_in_is_never_emailed_a_setup_link(
    manager, draft, user_factory
):
    partner, ctx = draft
    known = user_factory(password="correct horse battery staple 42")
    invite(manager, partner.id, email=known.email, send_invitation="on_activation")
    services.set_partner_status(
        ctx, partner.id, target=PartnerStatus.ACTIVE, reason="Ready to go live"
    )
    assert sent_to(known.email) == []


def test_resending_a_link_marks_the_invitation_sent(manager, draft):
    partner, _ctx = draft
    email = f"resend-{uuid.uuid4().hex[:6]}@example.com"
    invite(manager, partner.id, email=email, send_invitation="on_activation")
    member = PartnerMember.objects.get(partner_id=partner.id, user__email=email)
    response = post(
        manager,
        f"/api/v1/admin/partners/{partner.id}/members/{member.id}/resend-setup",
        key=False,
    )
    assert response.json() == {"sent": True}
    member.refresh_from_db()
    assert member.invitation_sent_at is not None


def test_the_timing_must_be_one_of_the_two(manager, draft):
    partner, _ctx = draft
    response = invite(
        manager, partner.id, email="t@example.com", send_invitation="whenever"
    )
    assert response.status_code == 400


def test_the_invitation_email_never_contains_a_password(manager, draft):
    partner, _ctx = draft
    email = f"nopw-{uuid.uuid4().hex[:6]}@example.com"
    invite(manager, partner.id, email=email, first_name="No", last_name="Password")
    (message,) = sent_to(email)
    assert "password:" not in message.body.lower() and "#token=" in message.body


# --- the people list ------------------------------------------------------------------------------------------


def members(client, partner_id):
    return client.get(f"/api/v1/admin/partners/{partner_id}").json()["members"]


def test_the_member_rows_carry_name_last_activity_and_invitation_state(manager, draft):
    from django.utils import timezone

    from workeasy360.platform.identity.models import User

    partner, _ctx = draft
    email = f"rows-{uuid.uuid4().hex[:6]}@example.com"
    invite(
        manager,
        partner.id,
        email=email,
        first_name="Row",
        last_name="Person",
        send_invitation="on_activation",
    )
    (row,) = [m for m in members(manager, partner.id) if m["email"] == email]
    assert row["full_name"] == "Row Person"
    assert "mfa_enabled" not in row
    assert row["last_login_at"] is None
    assert row["invitation_sent_at"] is None and row["password_set"] is False
    assert row["invitation_deferred"] is True
    user = User.objects.get(email=email)
    user.last_login_at = timezone.now()
    user.save(update_fields=["last_login_at"])
    (row,) = [m for m in members(manager, partner.id) if m["email"] == email]
    assert row["last_login_at"]


# --- changing a role ------------------------------------------------------------------------------------------


def test_a_manager_moves_a_person_between_the_two_tiers(manager, channel):
    pid = channel["partner"].id
    second = invite(
        manager, pid, email=f"second-{uuid.uuid4().hex[:6]}@example.com"
    ).json()["id"]
    response = post(manager, role_url(pid, second), {"role": "viewer"}, key=False)
    assert response.status_code == 200 and response.json() == {
        "id": second,
        "role": "viewer",
    }
    assert PartnerMember.objects.get(id=second).role == "viewer"
    assert (
        post(manager, role_url(pid, second), {"role": "admin"}, key=False).json()["role"]
        == "admin"
    )


def test_the_same_role_again_is_a_quiet_no_op(manager, channel):
    from tests.phase11.conftest import audit_of

    pid = channel["partner"].id
    member = PartnerMember.objects.filter(partner_id=pid, status="active").first()
    post(manager, role_url(pid, member.id), {"role": member.role}, key=False)
    assert not [
        r
        for r in audit_of(member.id, "action")
        if r["action"] == "partner.member_role_changed"
    ]


def test_an_active_partner_keeps_its_last_administrator(manager, channel):
    pid = channel["partner"].id
    admins = PartnerMember.objects.filter(partner_id=pid, status="active", role="admin")
    assert admins.count() == 1
    response = post(
        manager, role_url(pid, admins.first().id), {"role": "viewer"}, key=False
    )
    assert response.status_code == 400
    assert admins.count() == 1


def test_a_draft_partner_may_demote_its_only_administrator(manager, draft):
    partner, _ctx = draft
    member_id = invite(
        manager, partner.id, email=f"solo-{uuid.uuid4().hex[:6]}@example.com"
    ).json()["id"]
    assert (
        post(
            manager, role_url(partner.id, member_id), {"role": "viewer"}, key=False
        ).status_code
        == 200
    )


def test_the_role_must_be_a_known_preset(manager, channel):
    pid = channel["partner"].id
    member = PartnerMember.objects.filter(partner_id=pid, status="active").first()
    for role in ("root", "superuser", "", None, 1):
        assert (
            post(manager, role_url(pid, member.id), {"role": role}, key=False).status_code
            == 400
        )


def test_a_member_of_another_partner_cannot_be_reached_through_this_one(
    manager, channel, partner_factory
):
    other = partner_factory()
    theirs = PartnerMember.objects.filter(
        partner_id=other["partner"].id, status="active"
    ).first()
    response = post(
        manager, role_url(channel["partner"].id, theirs.id), {"role": "viewer"}, key=False
    )
    assert response.status_code == 404


def test_a_role_change_takes_effect_on_the_persons_next_request(channel, partner_client):
    client, fx = partner_client("customer.provision", "subscription.sell")
    services.grant_product(fx["staff_ctx"], fx["partner"].id, product_code="crm")
    extra = (
        PartnerMember.objects.filter(partner_id=fx["partner"].id, status="active")
        .exclude(user=fx["user"])
        .first()
    )
    assert extra is None or extra.role
    own = f"/api/v1/partner/{fx['partner'].id}/catalog"
    assert client.get(own).status_code == 200
    # Demote the signed-in administrator (another administrator exists, so the rule allows it).
    other_admin = services.invite_member(
        fx["staff_ctx"],
        fx["partner"].id,
        email=f"co-{uuid.uuid4().hex[:6]}@example.com",
        role="admin",
    )
    assert other_admin.member.role == "admin"
    mine = PartnerMember.objects.get(partner_id=fx["partner"].id, user=fx["user"])
    services.change_member_role(
        fx["staff_ctx"], fx["partner"].id, member_id=mine.id, role="viewer"
    )
    response = client.post(
        f"/api/v1/partner/{fx['partner'].id}/customers",
        json.dumps({"organization_name": "X Ltd", "owner_email": "x@example.com"}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert response.status_code == 403


def test_changing_a_role_needs_manage_and_a_second_factor(
    staff_client, channel, staff_factory
):
    pid = channel["partner"].id
    member = PartnerMember.objects.filter(partner_id=pid, status="active").first()
    viewer, _u = staff_client(VIEW)
    assert (
        post(viewer, role_url(pid, member.id), {"role": "viewer"}, key=False).status_code
        == 403
    )
    _p, plain = staff_factory(VIEW)
    with pytest.raises(PermissionDenied):
        services.change_member_role(plain, pid, member_id=member.id, role="viewer")


def test_a_partner_cannot_change_its_own_roles(partner_client):
    client, fx = partner_client("customer.provision")
    mine = PartnerMember.objects.get(partner_id=fx["partner"].id, user=fx["user"])
    assert (
        post(
            client, role_url(fx["partner"].id, mine.id), {"role": "viewer"}, key=False
        ).status_code
        == 403
    )


def test_role_changes_are_audited(manager, channel):
    from tests.phase11.conftest import audit_of

    pid = channel["partner"].id
    second = invite(manager, pid, email=f"aud-{uuid.uuid4().hex[:6]}@example.com").json()[
        "id"
    ]
    post(manager, role_url(pid, second), {"role": "viewer"}, key=False)
    entry = next(
        r
        for r in audit_of(second, "action", "changes")
        if r["action"] == "partner.member_role_changed"
    )
    assert entry["changes"]["from"] == "admin" and entry["changes"]["to"] == "viewer"


# --- removing a person ------------------------------------------------------------------------------------------


def test_removing_a_person_ends_partner_access_at_once_and_keeps_their_account(
    manager, partner_client
):
    from workeasy360.platform.identity.models import User

    client, fx = partner_client("customer.provision", "subscription.sell")
    portal = f"/api/v1/partner/{fx['partner'].id}/customers"
    assert client.get(portal).status_code == 200
    services.invite_member(
        fx["staff_ctx"],
        fx["partner"].id,
        email=f"keep-{uuid.uuid4().hex[:6]}@example.com",
        role="admin",
    )
    mine = PartnerMember.objects.get(partner_id=fx["partner"].id, user=fx["user"])
    response = post(
        manager,
        f"/api/v1/admin/partners/{fx['partner'].id}/members/{mine.id}/remove",
        key=False,
    )
    assert response.status_code == 200 and response.json()["status"] == "removed"
    assert client.get(portal).status_code in (403, 404)  # the very next request
    account = User.objects.get(id=fx["user"].id)
    assert account.status == "active"  # the Workeasy360 account is untouched


def test_a_removed_persons_customer_memberships_are_untouched(
    manager, partner_client, console_org
):
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    client, fx = partner_client("customer.provision")
    org = console_org()
    mine = PartnerMember.objects.get(partner_id=fx["partner"].id, user=fx["user"])
    services.invite_member(
        fx["staff_ctx"],
        fx["partner"].id,
        email=f"x-{uuid.uuid4().hex[:6]}@example.com",
        role="admin",
    )
    with (
        unscoped_access(reason="Test adds a membership", module="tests"),
        organization_db_scope(org["org"].id),
    ):
        before = Membership.unscoped.filter(organization_id=org["org"].id).count()
    post(
        manager,
        f"/api/v1/admin/partners/{fx['partner'].id}/members/{mine.id}/remove",
        key=False,
    )
    with (
        unscoped_access(reason="Test reads memberships", module="tests"),
        organization_db_scope(org["org"].id),
    ):
        assert Membership.unscoped.filter(organization_id=org["org"].id).count() == before
    assert client is not None


def test_a_terminated_partner_takes_no_new_people_with_names_either(draft):
    partner, ctx = draft
    services.set_partner_status(
        ctx, partner.id, target=PartnerStatus.TERMINATED, reason="Ended for the test"
    )
    with pytest.raises(PartnerValidationError):
        services.invite_member(
            ctx,
            partner.id,
            email="late@example.com",
            role="admin",
            full_name="Late Comer",
        )


def test_a_person_attached_the_old_way_is_never_emailed_by_activation(
    draft, user_factory
):
    """Activation invites only people whose invitation was *deferred*. Someone
    attached with an existing command (no tracked invitation) is left alone."""
    partner, ctx = draft
    plain = user_factory()  # an account with no password, attached without an invitation
    services.add_member(ctx, partner.id, email=plain.email, role="admin")
    member = PartnerMember.objects.get(partner_id=partner.id, user=plain)
    assert member.invitation_deferred is False and member.invitation_sent_at is None
    mail.outbox.clear()
    services.set_partner_status(
        ctx, partner.id, target=PartnerStatus.ACTIVE, reason="Ready to go live"
    )
    assert sent_to(plain.email) == []

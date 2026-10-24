"""Invitations: issue, seal, deliver, accept - and every refusal (Phase 8
extension, section 2)."""

from __future__ import annotations

import re
import threading
from datetime import timedelta

import pytest
from django.core import mail
from django.db import connection
from django.utils import timezone

from tests.extension.conftest import PASSWORD, url

pytestmark = pytest.mark.django_db

PREVIEW = "/api/v1/public/invitations/preview"
ACCEPT = "/api/v1/public/invitations/accept"
NEW_PASSWORD = "welcome aboard passphrase 2026"


def _deliver_all(organization_id):
    """Run every pending delivery now (the worker's job) and return the mail."""
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.notifications.models import NotificationDelivery
    from workeasy360.platform.notifications.services import attempt_delivery
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    # Exactly what the delivery task does: its tenant context (TenantTask)
    # and that organization's database scope.
    with (
        tenant_context(routing_context(organization_id)),
        organization_db_scope(organization_id),
    ):
        pending = list(
            NotificationDelivery.objects.filter(status="pending").values_list(
                "id", flat=True
            )
        )
        for delivery_id in pending:
            attempt_delivery(delivery_id)
    return mail.outbox


def _token_from(message) -> str:
    match = re.search(r"#token=([A-Za-z0-9._-]+)", message.body)
    assert match, message.body
    return match.group(1)


def _invite(client, slug, email, role="member"):
    return client.post(
        url(slug, "invitations"),
        {"email": email, "role": role},
        content_type="application/json",
    )


def _notifications(organization_id):
    from workeasy360.platform.notifications.models import Notification
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="Test reads tenant rows directly", module="tests"),
        organization_db_scope(organization_id),
    ):
        return list(Notification.unscoped.filter(organization_id=organization_id))


def test_an_invitation_link_is_never_stored_in_the_clear(console_org, login):
    org = console_org()
    owner = login(org["owner"])
    response = _invite(owner, org["slug"], "newcomer@example.com")
    assert response.status_code == 201, response.content
    assert response.json()["state"] == "pending"

    (notification,) = _notifications(org["org"].id)
    assert "invitation_url" not in notification.context
    assert notification.sealed_context  # sealed until delivered

    (message,) = _deliver_all(org["org"].id)
    token = _token_from(message)
    assert message.to == ["newcomer@example.com"]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM organizations_invitation WHERE token_hash = %s", [token]
        )
        assert cursor.fetchone()[0] == 0  # only the hash is stored
    (delivered,) = _notifications(org["org"].id)
    assert delivered.sealed_context == ""  # wiped once delivery is final


def test_a_new_person_accepts_by_creating_their_account(console_org, login, client):
    org = console_org()
    _invite(login(org["owner"]), org["slug"], "Newcomer@Example.com", role="readonly")
    token = _token_from(_deliver_all(org["org"].id)[0])

    preview = client.post(
        PREVIEW, {"token": token}, content_type="application/json"
    ).json()
    assert preview["email"] == "newcomer@example.com"
    assert preview["account_exists"] is False and preview["role"] == "readonly"

    response = client.post(
        ACCEPT,
        {"token": token, "full_name": "Nia Newcomer", "password": NEW_PASSWORD},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    assert response.json()["account_created"] is True
    # Signed in by the acceptance, and a read-only member of the organization.
    access = client.get(url(org["slug"], "access")).json()
    assert access["membership"]["roles"] == ["readonly"]
    # A token works once.
    again = client.post(
        ACCEPT,
        {"token": token, "password": NEW_PASSWORD},
        content_type="application/json",
    )
    assert (again.status_code, again.json()["code"]) == (404, "invitation_invalid")


def test_an_existing_account_must_sign_in_first_and_as_the_invitee(
    console_org, login, user_factory, client
):
    org = console_org()
    invitee = user_factory(email="existing@example.com", password=PASSWORD)
    stranger = user_factory(password=PASSWORD)
    _invite(login(org["owner"]), org["slug"], invitee.email)
    token = _token_from(_deliver_all(org["org"].id)[0])

    anonymous = client.post(
        ACCEPT,
        {"token": token, "full_name": "X", "password": NEW_PASSWORD},
        content_type="application/json",
    )
    assert (anonymous.status_code, anonymous.json()["code"]) == (409, "sign_in_required")
    wrong = login(stranger).post(
        ACCEPT, {"token": token}, content_type="application/json"
    )
    assert (wrong.status_code, wrong.json()["code"]) == (403, "invitation_email_mismatch")

    right = login(invitee)
    assert (
        right.post(ACCEPT, {"token": token}, content_type="application/json").status_code
        == 200
    )
    assert right.get(url(org["slug"], "access")).status_code == 200


def test_bad_expired_and_revoked_tokens_are_the_same_answer(console_org, login, client):
    from workeasy360.platform.organizations.models import Invitation
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    org = console_org()
    owner = login(org["owner"])
    _invite(owner, org["slug"], "late@example.com")
    _invite(owner, org["slug"], "revoked@example.com")
    late, revoked = (_token_from(m) for m in _deliver_all(org["org"].id))
    with (
        unscoped_access(reason="Test arranges tenant rows directly", module="tests"),
        organization_db_scope(org["org"].id),
    ):
        Invitation.unscoped.filter(email="late@example.com").update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        invitation_id = Invitation.unscoped.get(email="revoked@example.com").id
    assert (
        owner.post(url(org["slug"], f"invitations/{invitation_id}/revoke")).status_code
        == 200
    )

    for token in (late, revoked, "not-a-token", f"{org['org'].id.hex}.guessed", ""):
        response = client.post(PREVIEW, {"token": token}, content_type="application/json")
        assert (response.status_code, response.json()["code"]) == (
            404,
            "invitation_invalid",
        ), token


def test_resending_rotates_the_link(console_org, login, client):
    org = console_org()
    owner = login(org["owner"])
    invitation = _invite(owner, org["slug"], "twice@example.com").json()
    first = _token_from(_deliver_all(org["org"].id)[0])
    mail.outbox.clear()
    assert (
        owner.post(
            url(org["slug"], f"invitations/{invitation['invitation_id']}/resend")
        ).status_code
        == 200
    )
    second = _token_from(_deliver_all(org["org"].id)[0])
    assert first != second
    assert (
        client.post(
            PREVIEW, {"token": first}, content_type="application/json"
        ).status_code
        == 404
    )
    assert (
        client.post(
            PREVIEW, {"token": second}, content_type="application/json"
        ).status_code
        == 200
    )


def test_inviting_as_administrator_needs_role_assign(console_org, login):
    org = console_org()
    owner = login(org["owner"])
    assert (
        _invite(owner, org["slug"], "boss@example.com", role="admin").status_code == 201
    )
    assert (
        _invite(owner, org["slug"], "worker@example.com", role="member").status_code
        == 201
    )
    assert (
        _invite(owner, org["slug"], "owner2@example.com", role="owner").status_code == 400
    )

    billing = org["add"]("billing_manager")
    assert (
        _invite(login(billing["user"]), org["slug"], "x@example.com").status_code == 403
    )


def test_duplicates_and_existing_members_are_refused(console_org, login):
    org = console_org()
    member = org["add"]("member")
    owner = login(org["owner"])
    assert _invite(owner, org["slug"], "dup@example.com").status_code == 201
    again = _invite(owner, org["slug"], "DUP@example.com")
    assert (again.status_code, again.json()["code"]) == (409, "membership_conflict")
    existing = _invite(owner, org["slug"], member["user"].email)
    assert existing.status_code == 409
    assert _invite(owner, org["slug"], "not an email").status_code == 400


def test_an_expired_invitation_can_be_replaced(console_org, login):
    from workeasy360.platform.organizations.models import Invitation
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    org = console_org()
    owner = login(org["owner"])
    _invite(owner, org["slug"], "again@example.com")
    with (
        unscoped_access(reason="Test arranges tenant rows directly", module="tests"),
        organization_db_scope(org["org"].id),
    ):
        Invitation.unscoped.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert _invite(owner, org["slug"], "again@example.com").status_code == 201


def test_a_suspended_organization_admits_nobody(console_org, login, client):
    from workeasy360.platform.organizations.models import Organization

    org = console_org()
    _invite(login(org["owner"]), org["slug"], "wait@example.com")
    token = _token_from(_deliver_all(org["org"].id)[0])
    Organization.objects.filter(id=org["org"].id).update(status="suspended")
    response = client.post(
        ACCEPT,
        {"token": token, "full_name": "W", "password": NEW_PASSWORD},
        content_type="application/json",
    )
    assert response.status_code == 402


def test_the_invitee_pages_are_rate_limited(settings, client):
    settings.CONTROL_PLANE_RATE_LIMITS = {
        **settings.CONTROL_PLANE_RATE_LIMITS,
        "invitation_public_ip": (3, 60),
    }
    codes = [
        client.post(PREVIEW, {"token": "x"}, content_type="application/json").status_code
        for _ in range(5)
    ]
    assert codes[:3] == [404, 404, 404] and codes[3:] == [429, 429]


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_acceptances_admit_the_person_once(console_org, login):
    from django.test import Client

    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    org = console_org()
    _invite(login(org["owner"]), org["slug"], "racer@example.com")
    token = _token_from(_deliver_all(org["org"].id)[0])
    barrier = threading.Barrier(2)
    results: list[int] = []

    def accept():
        client = Client()
        barrier.wait()
        response = client.post(
            ACCEPT,
            {"token": token, "full_name": "Rae Racer", "password": NEW_PASSWORD},
            content_type="application/json",
        )
        results.append(response.status_code)
        connection.close()

    threads = [threading.Thread(target=accept) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results)[0] == 200
    assert sorted(results)[1] in (404, 409)
    with (
        unscoped_access(reason="Test arranges tenant rows directly", module="tests"),
        organization_db_scope(org["org"].id),
    ):
        assert (
            Membership.unscoped.filter(
                organization_id=org["org"].id, user__email="racer@example.com"
            ).count()
            == 1
        )

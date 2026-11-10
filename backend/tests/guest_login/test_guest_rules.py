"""Login as Guest: what a guest is shown, Manage Guest Access, and the record
(ADR-062). Single-host mode, where one session serves every page."""

from __future__ import annotations

import importlib
from datetime import timedelta

import pytest
from django.apps import apps
from django.utils import timezone

from tests.guest_login.conftest import END, GUEST, REASON, START, VIEW, admin_path, send
from workeasy360.commercial.partners import guest
from workeasy360.commercial.partners.models import PartnerGuestSession
from workeasy360.platform.audit.models import AuditEvent

pytestmark = pytest.mark.django_db


def start(client, partner_id, **body):
    return send(client, admin_path(partner_id, START), {"reason": REASON, **body})


def audit(action):
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="Test reads guest audit entries", module="tests"),
        organization_db_scope(PLATFORM_ORGANIZATION_ID),
    ):
        return list(
            AuditEvent.unscoped.filter(
                organization_id=PLATFORM_ORGANIZATION_ID, action=action
            ).order_by("occurred_at")
        )


# --- what a guest is shown ----------------------------------------------------------


def test_the_kyc_application_shows_its_state_and_nothing_it_contains(
    catalog, admin, reseller
):
    owner, partner_id = reseller
    as_owner = owner.get(f"/api/v1/partner/{partner_id}/kyc")
    assert as_owner.status_code == 200, as_owner.content
    assert start(admin, partner_id).status_code == 201
    as_guest = admin.get(f"/api/v1/partner/{partner_id}/kyc")
    assert as_guest.status_code == 200, as_guest.content
    body = as_guest.json()
    assert body["redacted"] is True
    assert body["status"] == as_owner.json()["status"]
    assert body["details"] == {} and body["documents"] == []
    assert body["may_edit"] is False


def test_payout_details_are_on_file_but_not_shown(catalog, admin, reseller):
    owner, partner_id = reseller
    import json

    saved = owner.put(
        f"/api/v1/partner/{partner_id}/withdrawals/method",
        json.dumps(
            {
                "method": "bank",
                "account_holder": "Pat Owner",
                "bank_name": "Example Bank",
                "ifsc": "EXMP0001234",
                "account_number": "111122223333",
                "account_number_confirm": "111122223333",
                "pan": "ABCDE1234F",  # pragma: allowlist secret
            }
        ),
        content_type="application/json",
    )
    assert saved.status_code in (200, 201), saved.content
    assert start(admin, partner_id).status_code == 201
    wallet = admin.get(f"/api/v1/partner/{partner_id}/withdrawals")
    assert wallet.status_code == 200, wallet.content
    method = wallet.json()["method"]
    assert method["redacted"] is True
    assert method["method"] == "bank"
    for hidden in ("account_holder", "bank_name", "ifsc", "account_hint", "pan_hint"):
        assert method[hidden] == "", hidden
    assert "EXMP" not in str(method) and "Pat Owner" not in str(method)


def test_the_profile_hides_registration_and_tax_numbers(catalog, admin, reseller):
    import json

    owner, partner_id = reseller
    current = owner.get(f"/api/v1/partner/{partner_id}/profile").json()
    admin.put(
        admin_path(partner_id, "/profile"),
        json.dumps(
            {
                "expected_version": current["version"],
                "business": {
                    "tax_number": "27AAAAA0000A1Z5",
                    "registration_number": "U12345",
                },
            }
        ),
        content_type="application/json",
    )
    assert (
        owner.get(f"/api/v1/partner/{partner_id}/profile").json()["business"][
            "tax_number"
        ]
        == "27AAAAA0000A1Z5"
    )
    start(admin, partner_id)
    seen = admin.get(f"/api/v1/partner/{partner_id}/profile")
    assert seen.status_code == 200, seen.content
    business = seen.json()["business"]
    assert business["tax_number"] == "" and business["registration_number"] == ""
    assert business["display_name"]


def test_kyc_documents_cannot_be_opened(catalog, admin, reseller):
    import uuid

    _owner, partner_id = reseller
    start(admin, partner_id)
    response = admin.get(
        f"/api/v1/partner/{partner_id}/kyc/documents/{uuid.uuid4()}/content"
    )
    assert response.status_code == 403


# --- Manage Guest Access ----------------------------------------------------------------


def test_manage_guest_access_lists_who_looked_and_how_it_ended(catalog, admin, reseller):
    _owner, partner_id = reseller
    first = start(admin, partner_id).json()
    send(admin, END)
    second = start(admin, partner_id).json()
    listed = admin.get(admin_path(partner_id, "/guest-sessions"))
    assert listed.status_code == 200, listed.content
    body = listed.json()
    assert body["minutes"] == guest.GUEST_MINUTES and body["may_start"] is True
    rows = body["sessions"]
    assert [r["id"] for r in rows] == [second["id"], first["id"]]
    assert [r["status"] for r in rows] == ["active", "exited"]
    assert rows[0]["staff"]["email"] == admin.staff_user.email
    assert rows[0]["reason"] == REASON and rows[0]["mine"] is True
    # Never a ticket or a sign-in.
    assert "sign_in_id" not in str(body) and "digest" not in str(body)


def test_view_only_staff_see_the_list_but_cannot_revoke(
    catalog, admin, reseller, staff_client
):
    _owner, partner_id = reseller
    started = start(admin, partner_id).json()
    viewer, _user = staff_client(VIEW)
    listed = viewer.get(admin_path(partner_id, "/guest-sessions"))
    assert listed.status_code == 200 and listed.json()["may_start"] is False
    revoke = send(
        viewer,
        admin_path(partner_id, f"/guest-sessions/{started['id']}/revoke"),
        {"reason": "Closing it"},
    )
    assert revoke.status_code == 403
    assert PartnerGuestSession.objects.get(id=started["id"]).ended_at is None


def test_revoking_is_audited_with_who_and_why(catalog, admin, reseller, staff_client):
    _owner, partner_id = reseller
    started = start(admin, partner_id).json()
    other, other_user = staff_client(GUEST, VIEW)
    revoked = send(
        other,
        admin_path(partner_id, f"/guest-sessions/{started['id']}/revoke"),
        {"reason": "Support case closed"},
    )
    assert revoked.status_code == 200, revoked.content
    (event,) = audit("partner.guest_view_ended")
    assert event.actor_user_id == other_user.id
    assert event.reason == "Support case closed"
    assert event.changes["end_reason"] == "revoked"
    # The guest's next request is refused.
    assert admin.get(f"/api/v1/partner/{partner_id}/customers").status_code == 404


def test_a_session_of_another_partner_cannot_be_revoked_through_this_one(
    catalog, admin, reseller, affiliate
):
    _owner, partner_id = reseller
    _other, other_id = affiliate
    started = start(admin, partner_id).json()
    response = send(
        admin,
        admin_path(other_id, f"/guest-sessions/{started['id']}/revoke"),
        {"reason": "Wrong partner"},
    )
    assert response.status_code == 404
    assert PartnerGuestSession.objects.get(id=started["id"]).ended_at is None


# --- the record ---------------------------------------------------------------------------


def test_start_and_end_are_both_audited_against_the_partner(catalog, admin, reseller):
    _owner, partner_id = reseller
    started = start(admin, partner_id).json()
    send(admin, END)
    (opened,) = audit("partner.guest_view_started")
    (closed,) = audit("partner.guest_view_ended")
    for event in (opened, closed):
        assert event.resource_id == str(partner_id)
        assert event.actor_user_id == admin.staff_user.id
        assert event.changes["guest_view_id"] == started["id"]
    assert closed.changes["end_reason"] == "exited"


def test_the_sweep_records_expired_views_once(catalog, admin, reseller, monkeypatch):
    _owner, partner_id = reseller
    started = start(admin, partner_id).json()
    later = timezone.now() + timedelta(minutes=guest.GUEST_MINUTES, seconds=5)
    monkeypatch.setattr(guest.timezone, "now", lambda: later)
    assert guest.close_expired() == 1
    assert guest.close_expired() == 0
    row = PartnerGuestSession.objects.get(id=started["id"])
    assert row.end_reason == "expired" and row.ended_by_id is None
    assert row.ended_at == row.expires_at
    (event,) = audit("partner.guest_view_ended")
    assert event.changes["end_reason"] == "expired"


def test_one_open_view_per_sign_in_is_held_by_the_database(catalog, admin, reseller):
    from django.db import IntegrityError, transaction

    _owner, partner_id = reseller
    started = start(admin, partner_id).json()
    row = PartnerGuestSession.objects.get(id=started["id"])
    with pytest.raises(IntegrityError), transaction.atomic():
        PartnerGuestSession.objects.create(
            partner_id=row.partner_id,
            staff_user_id=row.staff_user_id,
            sign_in_id=row.sign_in_id,
            reason="A second one",
            started_at=row.started_at,
            expires_at=row.expires_at,
        )


# --- the upgrade gives the live Admin the new permission ------------------------------------


migration = importlib.import_module(
    "workeasy360.platform.authz.migrations.0011_partner_guest_permission"
)


def test_the_upgrade_gives_security_administrators_login_as_guest(user_factory):
    from workeasy360.platform.authz import staff
    from workeasy360.platform.authz.models import StaffGrant

    root = user_factory(is_staff=True)
    security = user_factory(is_staff=True)
    billing = user_factory(is_staff=True)
    staff.create_grant(
        user_id=security.id,
        permission="platform.staff_security.manage",
        granted_by_id=root.id,
        reason="Test makes a security administrator",
    )
    staff.create_grant(
        user_id=billing.id,
        permission="platform.staff_payment.view",
        granted_by_id=root.id,
        reason="Test makes a billing administrator",
    )
    migration.give(apps, None)
    migration.give(apps, None)  # twice changes nothing
    live = StaffGrant.objects.filter(permission=GUEST, revoked_at__isnull=True)
    assert list(live.values_list("user_id", flat=True)) == [security.id]
    migration.take_back(apps, None)
    assert not StaffGrant.objects.filter(
        permission=GUEST, revoked_at__isnull=True
    ).exists()

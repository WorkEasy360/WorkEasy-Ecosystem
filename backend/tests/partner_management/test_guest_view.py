"""All Partners → Guest login: a staff member's read-only view of a partner's
portal (``commercial.partners.guest``).

The rules held here: it needs the staff manage grant and a reason; it is the
staff member acting (audited under their name, with the reason), never the
partner's identity; it reads and never writes; it opens exactly one partner,
for one sign-in, for a limited time; and the partner can see that it happened.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock as unittest_mock

import pytest
from django.utils import timezone

from tests.partner_experience.conftest import admin_path, send
from workeasy360.commercial.partners import guest
from workeasy360.platform.audit.models import AuditEvent

pytestmark = pytest.mark.django_db


def audit_rows(action):
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="Test reads the guest view audit entries", module="tests"),
        organization_db_scope(PLATFORM_ORGANIZATION_ID),
    ):
        return list(
            AuditEvent.unscoped.filter(
                organization_id=PLATFORM_ORGANIZATION_ID, action=action
            )
        )


PORTAL = "/api/v1/partner/portal"
END = "/api/v1/partner/guest-view/end"


def start(client, partner_id, reason="Checking the reseller's catalogue for support"):
    return send(client, admin_path(partner_id, "/guest-view"), {"reason": reason})


def portal_entry(client, partner_id):
    response = client.get(PORTAL)
    assert response.status_code == 200, response.content
    return next(
        (p for p in response.json()["partners"] if p["partner_id"] == str(partner_id)),
        None,
    )


def test_guest_login_opens_the_partner_portal_read_only(catalog, admin, reseller):
    _owner, partner_id = reseller
    response = start(admin, partner_id)
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["portal_path"] == "/partner/reseller/dashboard"
    assert body["reason"] == "Checking the reseller's catalogue for support"

    entry = portal_entry(admin, partner_id)
    assert entry is not None
    assert entry["guest"]["reason"] == body["reason"]
    assert entry["role"] == "viewer" and entry["is_admin"] is False

    assert admin.get(f"/api/v1/partner/{partner_id}/customers").status_code == 200
    write = send(admin, f"/api/v1/partner/{partner_id}/customers", {"name": "Acme"})
    assert write.status_code == 403
    assert write.json()["reason"] == "guest_read_only"


@pytest.mark.parametrize(
    ("kind", "home"),
    [
        ("reseller", "/partner/reseller/dashboard"),
        ("white_label", "/partner/white-label/dashboard"),
        ("affiliate", "/partner/affiliate/dashboard"),
    ],
)
def test_guest_login_opens_the_workspace_the_partner_lands_in(
    catalog, admin, request, kind, home
):
    """The guest sees the portal the partner sees: the workspace its own
    sign-in opens, resolved by the server from its live grants."""
    owner, partner_id = request.getfixturevalue(kind)
    body = start(admin, partner_id).json()
    assert body["portal_path"] == home
    entry = portal_entry(admin, partner_id)
    assert entry["landing"] == portal_entry(owner, partner_id)["landing"] == kind


def test_a_partner_with_no_workspace_opens_the_generic_panel(catalog, admin, reseller):
    from workeasy360.commercial.partners import selectors

    _owner, partner_id = reseller
    with unittest_mock.patch.object(
        selectors, "active_capabilities", return_value=frozenset({"support.access"})
    ):
        assert guest.portal_path(partner_id) == f"/partner/{partner_id}"


def test_the_guest_holds_no_more_than_a_viewer_of_the_partner(catalog, admin, reseller):
    owner, partner_id = reseller
    start(admin, partner_id)
    as_guest = set(portal_entry(admin, partner_id)["permissions"])
    as_owner = set(portal_entry(owner, partner_id)["permissions"])
    assert as_guest
    assert as_guest < as_owner


def test_it_is_audited_under_the_staff_member_with_the_reason(catalog, admin, reseller):
    owner, partner_id = reseller
    start(admin, partner_id, reason="Customer asked us to look")
    (event,) = audit_rows("partner.guest_view_started")
    assert event.actor_user_id == admin.staff_user.id
    assert event.resource_id == str(partner_id)
    assert event.reason == "Customer asked us to look"
    # The partner sees it in its own Activity.
    activity = owner.get(f"/api/v1/partner/{partner_id}/activity")
    assert activity.status_code == 200, activity.content
    labels = [item["label"] for item in activity.json()["results"]]
    assert "Staff opened a guest view" in labels


def test_a_reason_is_required(catalog, admin, reseller):
    _owner, partner_id = reseller
    for reason in ("", "  ", "ok", "x" * 201):
        response = start(admin, partner_id, reason=reason)
        assert response.status_code == 400, reason
    assert portal_entry(admin, partner_id) is None


def test_view_only_staff_and_partners_cannot_start_one(
    catalog, admin, reseller, staff_client
):
    owner, partner_id = reseller
    viewer, _user = staff_client("platform.staff_partner.view")
    assert start(viewer, partner_id).status_code == 403
    assert start(owner, partner_id).status_code in (403, 404)


def test_it_opens_exactly_one_partner_for_one_sign_in(
    catalog, admin, reseller, affiliate
):
    _owner, partner_id = reseller
    _other_owner, other_id = affiliate
    start(admin, partner_id)
    assert admin.get(f"/api/v1/partner/{other_id}/customers").status_code == 404

    # The same staff member signed in elsewhere has no guest view.
    from django.test import Client

    from tests.phase7.conftest import PASSWORD

    elsewhere = Client()
    elsewhere.get("/api/v1/auth/session")
    login = elsewhere.post(
        "/api/v1/auth/login",
        {"email": admin.staff_user.email, "password": PASSWORD},
        content_type="application/json",
    )
    assert login.status_code == 200
    assert elsewhere.get(f"/api/v1/partner/{partner_id}/customers").status_code in (
        403,
        404,
    )


def test_opening_another_partner_ends_the_first(catalog, admin, reseller, affiliate):
    _owner, partner_id = reseller
    _other_owner, other_id = affiliate
    start(admin, partner_id)
    start(admin, other_id)
    assert admin.get(f"/api/v1/partner/{partner_id}/customers").status_code == 404
    assert portal_entry(admin, other_id) is not None
    assert len(audit_rows("partner.guest_view_ended")) == 1


def test_exit_ends_it_and_is_audited(catalog, admin, reseller):
    _owner, partner_id = reseller
    start(admin, partner_id)
    response = send(admin, END)
    assert response.status_code == 200 and response.json() == {"ended": True}
    assert portal_entry(admin, partner_id) is None
    assert admin.get(f"/api/v1/partner/{partner_id}/customers").status_code == 404
    assert len(audit_rows("partner.guest_view_ended")) == 1
    assert send(admin, END).json() == {"ended": False}


def test_it_ends_by_itself(catalog, admin, reseller, monkeypatch):
    _owner, partner_id = reseller
    start(admin, partner_id)
    later = timezone.now() + timedelta(minutes=guest.GUEST_MINUTES, seconds=1)
    monkeypatch.setattr(guest.timezone, "now", lambda: later)
    assert portal_entry(admin, partner_id) is None
    assert admin.get(f"/api/v1/partner/{partner_id}/customers").status_code == 404


def test_losing_the_staff_grant_ends_access(catalog, admin, reseller):
    from workeasy360.platform.authz import staff
    from workeasy360.platform.authz.models import StaffGrant

    _owner, partner_id = reseller
    start(admin, partner_id)
    # Manage includes view, so both go.
    for grant in StaffGrant.objects.filter(
        user_id=admin.staff_user.id,
        permission__in=["platform.staff_partner.view", "platform.staff_partner.manage"],
        revoked_at__isnull=True,
    ):
        staff.end_grant(
            grant_id=grant.id, revoked_by_id=admin.staff_user.id, reason="Test revokes it"
        )
    assert portal_entry(admin, partner_id) is None
    assert admin.get(f"/api/v1/partner/{partner_id}/customers").status_code == 403


def test_the_customer_export_is_refused(catalog, admin, reseller):
    _owner, partner_id = reseller
    start(admin, partner_id)
    response = admin.get(f"/api/v1/partner/{partner_id}/customers/export")
    assert response.status_code == 403


def test_the_list_rows_carry_phone_and_country(catalog, admin):
    from tests.partner_experience.conftest import CREATE, make_body

    response = send(admin, CREATE, make_body("reseller", name="Phone Co", country="IN"))
    assert response.status_code == 201, response.content
    rows = admin.get(
        "/api/v1/admin/partners/resellers", {"scope": "all", "limit": 100}
    ).json()["results"]
    row = next(r for r in rows if r["id"] == response.json()["id"])
    assert row["country"] == "IN"
    assert row["phone"] == ""


def test_exit_on_the_partner_host_ends_that_session_only(
    catalog, admin, reseller, settings
):
    """Multi-portal mode: "Exit guest session" on partner.<domain> also ends the
    staff member's partner-portal session, which existed only for the guest
    view - no session is left behind on the partner origin."""
    from tests.portals.conftest import HOST, ORIGINS, WEB

    _owner, partner_id = reseller
    start(admin, partner_id)
    settings.PORTALS_ENABLED = True
    settings.W360_PORTAL_ORIGINS = dict(ORIGINS)
    settings.ALLOWED_HOSTS = list(HOST.values())
    settings.CSRF_TRUSTED_ORIGINS = [ORIGINS[n] for n in WEB]
    on_partner = {"HTTP_HOST": HOST["partner"], "secure": True}

    response = admin.post(END, "{}", content_type="application/json", **on_partner)
    assert response.status_code == 200 and response.json() == {"ended": True}
    session = admin.get("/api/v1/auth/session", **on_partner)
    assert session.json()["authenticated"] is False
    assert len(audit_rows("partner.guest_view_ended")) == 1

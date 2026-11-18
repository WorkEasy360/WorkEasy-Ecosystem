"""Login as Guest across hosts, as production runs it (ADR-062, ADR-059).

The Super Admin signs in on admin. itself. "Login as Guest" answers a one-time
ticket in the response body; the Admin page posts it, in a form, to the partner
host, which spends it and opens that host's own session - read-only, for exactly
that partner, ending with the guest view. Before this change the partner host
had no way in at all: per-portal sign-in removed the hand-off the guest view
relied on, so the button led to a sign-in page.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from tests.guest_login.conftest import (
    END,
    ENTER,
    ORIGINS,
    HostClient,
    admin_host_sign_in,
    enable_portals,
    enter_partner_host,
    form_post,
    put_json,
    start_on_admin,
)
from workeasy360.commercial.partners import guest
from workeasy360.commercial.partners.models import PartnerGuestSession

pytestmark = pytest.mark.django_db


@pytest.fixture
def on_admin(admin, settings):
    """Portals on; the same Super Admin signed in on admin.<domain>."""
    enable_portals(settings)
    return admin_host_sign_in(admin.staff_user)


def entered(on_admin, partner_id, **body):
    started = start_on_admin(on_admin, partner_id, **body)
    assert started.status_code == 201, started.content
    partner, response = enter_partner_host(started.json()["entry"]["ticket"])
    return started, partner, response


# --- the way in ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "home"),
    [
        ("affiliate", "/partner/affiliate/dashboard"),
        ("reseller", "/partner/reseller/dashboard"),
        ("white_label", "/partner/white-label/dashboard"),
    ],
)
def test_login_as_guest_opens_that_partners_dashboard_on_the_partner_host(
    catalog, admin, settings, request, kind, home
):
    _owner, partner_id = request.getfixturevalue(kind)
    enable_portals(settings)
    on_admin = admin_host_sign_in(admin.staff_user)

    started = start_on_admin(on_admin, partner_id)
    assert started.status_code == 201, started.content
    body = started.json()
    assert started["Cache-Control"] == "no-store"
    assert body["portal_path"] == home
    entry = body["entry"]
    assert entry["action"] == f"{ORIGINS['partner']}{ENTER}"
    # The ticket is in the body only: never in a URL.
    assert entry["ticket"] not in entry["action"]

    partner, response = enter_partner_host(entry["ticket"])
    assert response.status_code == 303, response.content
    assert response["Location"] == home
    assert response["Cache-Control"] == "no-store"

    portal = partner.get("/api/v1/partner/portal")
    assert portal.status_code == 200, portal.content
    (only,) = portal.json()["partners"]
    assert only["partner_id"] == str(partner_id)
    assert only["landing"] == kind
    assert only["role"] == "viewer" and only["is_admin"] is False
    assert only["guest"]["id"] == body["id"]
    assert only["guest"]["viewer"]["email"] == admin.staff_user.email
    assert partner.get(f"/api/v1/partner/{partner_id}/dashboard").status_code == 200


def test_the_guest_session_is_the_staff_member_never_the_partner(
    catalog, admin, reseller, on_admin
):
    _owner, partner_id = reseller
    _started, partner, _response = entered(on_admin, partner_id)
    session = partner.get("/api/v1/auth/session").json()
    assert session["authenticated"] is True
    assert session["email"] == admin.staff_user.email
    # The partner's own administrator (tests/partner_experience) is not who
    # this session is: no partner credential was used.
    assert session["email"] != "owner@rs-one.example.com"


def test_the_ticket_is_single_use(catalog, reseller, on_admin):
    _owner, partner_id = reseller
    started = start_on_admin(on_admin, partner_id)
    ticket = started.json()["entry"]["ticket"]
    _first, ok = enter_partner_host(ticket)
    assert ok.status_code == 303 and ok["Location"].startswith("/partner/")
    _second, again = enter_partner_host(ticket)
    assert again.status_code == 303
    assert again["Location"] == (f"{ORIGINS['admin']}/admin/partners/all?guest=expired")
    # Nothing is stored that could replay it.
    row = PartnerGuestSession.objects.get(id=started.json()["id"])
    assert row.entry_digest is None and row.entered_at is not None


def test_the_ticket_lives_sixty_seconds(catalog, reseller, on_admin, monkeypatch):
    _owner, partner_id = reseller
    ticket = start_on_admin(on_admin, partner_id).json()["entry"]["ticket"]
    later = timezone.now() + timedelta(seconds=guest.ENTRY_SECONDS + 1)
    monkeypatch.setattr(guest.timezone, "now", lambda: later)
    partner, response = enter_partner_host(ticket)
    assert response["Location"].endswith("?guest=expired")
    assert partner.get("/api/v1/auth/session").json()["authenticated"] is False


@pytest.mark.parametrize(
    "origin",
    [
        "",  # no Origin header
        "https://partner.workeasy360.com",
        "https://app.workeasy360.com",
        "https://evil.example.com",
    ],
)
def test_only_the_admin_origin_may_post_the_ticket(catalog, reseller, on_admin, origin):
    _owner, partner_id = reseller
    ticket = start_on_admin(on_admin, partner_id).json()["entry"]["ticket"]
    partner = HostClient("partner")
    response = form_post(partner, ENTER, {"ticket": ticket}, origin=origin or None)
    assert response.status_code == 403
    assert partner.get("/api/v1/auth/session").json()["authenticated"] is False
    # Refused before it was spent: the Admin's own browser can still use it.
    _ok_client, ok = enter_partner_host(ticket)
    assert ok.status_code == 303 and ok["Location"].startswith("/partner/")


def test_the_entry_is_authenticated_by_its_ticket_never_the_session_cookie(
    catalog, reseller, on_admin
):
    """CSRF-exempt, so a cookie must get nothing: the Admin's own session
    carried to the partner host, with the right Origin but no good ticket, opens
    no guest view and spends nothing."""
    _owner, partner_id = reseller
    started = start_on_admin(on_admin, partner_id).json()
    carried = HostClient("partner")
    carried.client.cookies = on_admin.client.cookies
    real = started["entry"]["ticket"]
    altered = real[:-1] + ("B" if real.endswith("A") else "A")
    for ticket in ("", "x" * 43, altered):
        response = form_post(carried, ENTER, {"ticket": ticket}, origin=ORIGINS["admin"])
        assert response.status_code == 303
        assert response["Location"].endswith("?guest=expired")
    row = PartnerGuestSession.objects.get(id=started["id"])
    assert row.entered_at is None and row.entry_digest is not None


def test_the_entry_is_only_on_the_partner_host(catalog, reseller, on_admin):
    _owner, partner_id = reseller
    ticket = start_on_admin(on_admin, partner_id).json()["entry"]["ticket"]
    for portal in ("admin", "app", "auth"):
        client = HostClient(portal)
        response = form_post(client, ENTER, {"ticket": ticket}, origin=ORIGINS["admin"])
        assert response.status_code in (403, 404), portal


def test_single_host_mode_has_no_entry(client):
    assert client.post(ENTER, {"ticket": "x" * 43}).status_code == 404


def test_a_draft_partner_has_no_portal_to_view(catalog, admin, settings):
    from tests.guest_login.conftest import CREATE, make_body, send

    created = send(
        admin,
        CREATE,
        make_body("affiliate", name="Draft Affiliate", email="d@x-draft.example.com"),
    )
    assert created.status_code == 201, created.content
    enable_portals(settings)
    on_admin = admin_host_sign_in(admin.staff_user)
    response = start_on_admin(on_admin, created.json()["id"])
    assert response.status_code == 400
    assert "draft" in response.json()["detail"]


# --- read-only, one partner, no Admin powers ----------------------------------------


def test_every_write_is_refused_on_every_endpoint(catalog, reseller, on_admin):
    _owner, partner_id = reseller
    _started, partner, _response = entered(on_admin, partner_id)
    base = f"/api/v1/partner/{partner_id}"
    writes = [
        partner.post(f"{base}/customers", {"name": "Acme"}),
        partner.post(f"{base}/withdrawals/method", {"method": "upi", "upi_vpa": "x@y"}),
        partner.post(f"{base}/withdrawals/request", {}),
        partner.post(f"{base}/members", {"email": "new@x.example.com", "role": "admin"}),
        partner.post(f"{base}/api-keys", {"name": "k"}),
        partner.post(f"{base}/kyc/submit", {}),
        put_json(partner, f"{base}/profile", {"expected_version": 0, "business": {}}),
        put_json(partner, f"{base}/kyc", {"details": {}}),
        # Not a partner endpoint: the staff member's own password.
        put_json(
            partner,
            "/api/v1/auth/password",
            {"current_password": "x", "new_password": "y" * 16},
        ),
        partner.post("/api/v1/auth/password", {"current_password": "x"}),
    ]
    for response in writes:
        assert response.status_code == 403, (
            response.request["PATH_INFO"],
            response.content,
        )
        assert response.json()["reason"] == "guest_read_only"


def test_another_partner_is_not_reachable(catalog, reseller, affiliate, on_admin):
    _owner, partner_id = reseller
    _other_owner, other_id = affiliate
    _started, partner, _response = entered(on_admin, partner_id)
    assert partner.get(f"/api/v1/partner/{other_id}/dashboard").status_code == 404
    assert partner.get(f"/api/v1/partner/{other_id}/customers").status_code == 404


def test_the_guest_session_holds_no_admin_power(catalog, reseller, on_admin):
    _owner, partner_id = reseller
    _started, partner, _response = entered(on_admin, partner_id)
    # The admin API is not served on the partner host at all...
    assert partner.get("/api/v1/admin/session").status_code == 404
    assert partner.get("/api/v1/admin/partners/resellers").status_code == 404
    # ...and the staff diagnostics answer it as they answer anyone.
    assert partner.get("/healthz/deep").status_code == 404


def test_a_staff_endpoint_refuses_a_guest_session_on_any_host(
    catalog, reseller, on_admin
):
    """Even carried to the Admin host, the guest session is not a staff session."""
    _owner, partner_id = reseller
    _started, partner, _response = entered(on_admin, partner_id)
    carried = HostClient("admin")
    carried.client.cookies = partner.client.cookies
    response = carried.get("/api/v1/admin/partners/resellers")
    assert response.status_code == 403
    assert response.json()["reason"] == "guest_session"


# --- the way out --------------------------------------------------------------------


def test_exit_ends_the_view_and_returns_to_the_admin_session(catalog, reseller, on_admin):
    _owner, partner_id = reseller
    _started, partner, _response = entered(
        on_admin, partner_id, return_path="/admin/partners/reseller"
    )
    exited = partner.post(END)
    assert exited.status_code == 200
    assert exited.json() == {
        "ended": True,
        "return_url": f"{ORIGINS['admin']}/admin/partners/reseller",
    }
    assert partner.get("/api/v1/auth/session").json()["authenticated"] is False
    # The Admin's own session is where it was.
    assert on_admin.get("/api/v1/admin/session").status_code == 200
    (row,) = PartnerGuestSession.objects.filter(partner_id=partner_id)
    assert row.end_reason == "exited"


@pytest.mark.parametrize(
    "asked",
    ["https://evil.example.com/x", "//evil.example.com", "/admin/users", "/partner/x"],
)
def test_the_way_back_is_only_an_admin_partner_page(catalog, reseller, on_admin, asked):
    _owner, partner_id = reseller
    _started, partner, _response = entered(on_admin, partner_id, return_path=asked)
    assert partner.post(END).json()["return_url"] == (
        f"{ORIGINS['admin']}/admin/partners/all"
    )


def test_revoking_ends_the_guest_session_on_its_next_request(
    catalog, reseller, on_admin, staff_user
):
    _owner, partner_id = reseller
    started, partner, _response = entered(on_admin, partner_id)
    assert partner.get(f"/api/v1/partner/{partner_id}/dashboard").status_code == 200

    from tests.guest_login.conftest import GUEST, VIEW

    other_admin = staff_user(GUEST, VIEW)
    second = admin_host_sign_in(other_admin)
    revoked = second.post(
        f"/api/v1/admin/partners/{partner_id}/guest-sessions/{started.json()['id']}/revoke",
        {"reason": "Support case closed"},
    )
    assert revoked.status_code == 200, revoked.content
    statuses = [s["status"] for s in revoked.json()["sessions"]]
    assert statuses == ["revoked"]

    assert partner.get(f"/api/v1/partner/{partner_id}/dashboard").status_code in (
        401,
        403,
    )
    assert partner.get("/api/v1/auth/session").json()["authenticated"] is False
    row = PartnerGuestSession.objects.get(id=started.json()["id"])
    assert row.end_reason == "revoked" and row.ended_by_id == other_admin.id


def test_it_ends_by_itself(catalog, reseller, on_admin, monkeypatch):
    _owner, partner_id = reseller
    _started, partner, _response = entered(on_admin, partner_id)
    later = timezone.now() + timedelta(minutes=guest.GUEST_MINUTES, seconds=1)
    monkeypatch.setattr(guest.timezone, "now", lambda: later)
    assert partner.get(f"/api/v1/partner/{partner_id}/dashboard").status_code in (
        401,
        403,
    )
    assert partner.get("/api/v1/auth/session").json()["authenticated"] is False


def test_signing_out_of_the_admin_ends_the_guest_session(catalog, reseller, on_admin):
    _owner, partner_id = reseller
    started, partner, _response = entered(on_admin, partner_id)
    assert on_admin.post("/api/v1/auth/logout").status_code == 200
    assert partner.get("/api/v1/auth/session").json()["authenticated"] is False
    row = PartnerGuestSession.objects.get(id=started.json()["id"])
    assert row.end_reason == "signed_out"


def test_signing_out_in_guest_mode_ends_only_the_guest_session(
    catalog, reseller, on_admin
):
    """ADR-059: a portal's sign-out is that portal's. Signing out on the partner
    host ends the guest view and that session; the Admin stays signed in."""
    _owner, partner_id = reseller
    started, partner, _response = entered(on_admin, partner_id)
    assert partner.post("/api/v1/auth/logout").status_code == 200
    assert partner.get("/api/v1/auth/session").json()["authenticated"] is False
    assert on_admin.get("/api/v1/admin/session").status_code == 200
    row = PartnerGuestSession.objects.get(id=started.json()["id"])
    assert row.end_reason == "signed_out"


def test_losing_the_guest_permission_refuses_the_entry(
    catalog, admin, reseller, on_admin
):
    from workeasy360.platform.authz import staff
    from workeasy360.platform.authz.models import StaffGrant

    _owner, partner_id = reseller
    ticket = start_on_admin(on_admin, partner_id).json()["entry"]["ticket"]
    for grant in StaffGrant.objects.filter(
        user_id=admin.staff_user.id,
        permission="platform.staff_partner.guest",
        revoked_at__isnull=True,
    ):
        staff.end_grant(
            grant_id=grant.id, revoked_by_id=admin.staff_user.id, reason="Test revokes it"
        )
    partner, response = enter_partner_host(ticket)
    assert response["Location"].endswith("?guest=denied")
    assert partner.get("/api/v1/auth/session").json()["authenticated"] is False


def test_starting_another_ends_the_first_session(catalog, reseller, affiliate, on_admin):
    _owner, partner_id = reseller
    _other_owner, other_id = affiliate
    _started, first, _response = entered(on_admin, partner_id)
    _again, second, _ok = entered(on_admin, other_id)
    assert first.get("/api/v1/auth/session").json()["authenticated"] is False
    assert second.get(f"/api/v1/partner/{other_id}/dashboard").status_code == 200
    ends = sorted(PartnerGuestSession.objects.values_list("end_reason", flat=True))
    assert ends == ["", "replaced"]


def test_starting_is_rate_limited(catalog, reseller, on_admin, settings):
    _owner, partner_id = reseller
    settings.CONTROL_PLANE_RATE_LIMITS = {
        **settings.CONTROL_PLANE_RATE_LIMITS,
        "partner_guest_start_user": (2, 60),
    }
    codes = [start_on_admin(on_admin, partner_id).status_code for _ in range(3)]
    assert codes == [201, 201, 429]

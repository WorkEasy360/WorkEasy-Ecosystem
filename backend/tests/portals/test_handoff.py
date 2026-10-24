"""Central sign-in and the portal hand-off (ADR-043, design section 3).

Every portal session is born from one sign-in on the central host, through a
single-use code bound to the portal's host and to the browser the portal sent
to sign in. Whether an account may enter a portal is decided from identity -
live StaffGrants, partner membership - never from the host or the sign-in
page's mode, and the portal re-checks on every request.
"""

from __future__ import annotations

import time
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.portals.conftest import HostClient, code_of, new_state

pytestmark = pytest.mark.django_db


# --- single-host mode --------------------------------------------------------


def test_single_host_mode_has_no_hand_off(client):
    """Unconfigured (development, every earlier suite): one host serves
    everything and the hand-off does not exist."""
    assert client.post("/api/v1/auth/handoff", {}).status_code == 404
    assert client.post("/api/v1/auth/handoff/redeem", {}).status_code == 404


# --- the customer portal -------------------------------------------------------


def test_a_customer_is_handed_to_the_customer_portal(sign_in, hand_off, customer):
    auth = sign_in(customer["user"])
    app, issued, redeemed = hand_off(auth, "user", next_path=f"/o/{customer['org'].slug}")
    assert issued.status_code == 200
    url = issued.json()["redirect_url"]
    # The code travels in the fragment: never in a query string, so never in a
    # server log, a proxy log or a Referer.
    assert url.startswith("https://app.workeasy360.com/auth/continue#code=")
    assert "?" not in url
    assert redeemed.status_code == 200, redeemed.content
    assert redeemed.json() == {"next": f"/o/{customer['org'].slug}"}
    session = app.get("/api/v1/auth/session").json()
    assert session["authenticated"] is True
    assert session["email"] == customer["user"].email
    # The app host's session is its own row, not the auth host's.
    assert app.session.session_key != auth.session.session_key


def test_the_hand_off_carries_no_credential_into_the_page(sign_in, customer):
    """The response is the URL only; nothing cacheable."""
    auth = sign_in(customer["user"])
    issued = auth.post(
        "/api/v1/auth/handoff",
        {"mode": "user", "next": "/dashboard", "state": new_state()},
    )
    assert set(issued.json()) == {"redirect_url"}
    assert issued["Cache-Control"] == "no-store"


# --- the Super Admin portal ------------------------------------------------------


def test_a_customer_is_refused_the_admin_portal_after_signing_in(sign_in, customer):
    """Authentication succeeds; authorization for the admin portal does not."""
    auth = sign_in(customer["user"])
    refused = auth.post(
        "/api/v1/auth/handoff", {"mode": "admin", "next": "/admin", "state": new_state()}
    )
    assert refused.status_code == 403
    assert refused.json() == {"code": "portal_access_denied"}


def test_the_admin_host_grants_a_customer_nothing(sign_in, hand_off, customer):
    """Even carried straight to the admin host, a customer's session is
    refused by the admin API: the host never decides who may do what."""
    auth = sign_in(customer["user"])
    app, _, _ = hand_off(auth, "user")
    admin = HostClient("admin")
    admin.client.cookies = app.client.cookies  # the same session, another host
    response = admin.get("/api/v1/admin/session")
    assert response.status_code in (401, 403)
    assert "permissions" not in response.json()


def test_staff_without_a_grant_in_force_are_refused_the_admin_portal(
    sign_in, user_factory
):
    from tests.portals.conftest import PASSWORD

    user = user_factory(is_staff=True, password=PASSWORD)
    auth = sign_in(user, aal2=True)
    refused = auth.post(
        "/api/v1/auth/handoff", {"mode": "admin", "next": "/admin", "state": new_state()}
    )
    assert refused.json() == {"code": "portal_access_denied"}


def test_an_enrolled_administrator_is_handed_off_with_a_password_sign_in(
    sign_in, hand_off, staff_user
):
    """Owner decision 2026-10-24: no sign-in code before the admin portal. The
    portal session starts at AAL 1; dangerous actions there still step up."""
    admin, issued, redeemed = hand_off(
        sign_in(staff_user()), "admin", next_path="/admin/customers"
    )
    assert issued.json()["redirect_url"].startswith("https://admin.workeasy360.com/")
    assert redeemed.json() == {"next": "/admin/customers"}
    response = admin.get("/api/v1/admin/session")
    assert response.status_code == 200, response.content
    assert response.json()["aal"] == 1


def test_a_step_up_on_the_sign_in_host_carries_into_the_admin_portal(
    sign_in, hand_off, staff_user
):
    from workeasy360.platform.tenancy.resolution import record_step_up

    auth = sign_in(staff_user())
    session = auth.session
    record_step_up(session)
    session.save()
    admin, _, redeemed = hand_off(auth, "admin")
    assert redeemed.status_code == 200
    assert admin.get("/api/v1/admin/session").json()["aal"] == 2


def test_the_hand_off_never_extends_elevation(sign_in, hand_off, staff_user):
    """The portal session's AAL 2 keeps the step-up time of the sign-in it
    came from: it lapses at the same moment, never later."""
    from workeasy360.platform.tenancy.resolution import (
        SESSION_STEP_UP_AT_KEY,
        record_step_up,
    )

    auth = sign_in(staff_user())
    session = auth.session
    record_step_up(session)
    session[SESSION_STEP_UP_AT_KEY] = time.time() - 600  # ten minutes ago
    session.save()
    stepped_up_at = auth.session[SESSION_STEP_UP_AT_KEY]
    admin, _, redeemed = hand_off(auth, "admin")
    assert redeemed.status_code == 200
    assert admin.session[SESSION_STEP_UP_AT_KEY] == stepped_up_at


def test_an_administrator_without_a_factor_reaches_the_console_at_aal1(
    sign_in, hand_off, staff_user
):
    admin, issued, redeemed = hand_off(sign_in(staff_user(mfa=False)), "admin")
    assert issued.status_code == 200 and redeemed.status_code == 200
    response = admin.get("/api/v1/admin/session")
    assert response.status_code == 200, response.content
    assert response.json()["aal"] == 1


# --- the Partner portal --------------------------------------------------------------


def test_a_partner_member_is_handed_to_the_partner_portal(
    sign_in, hand_off, partner_member
):
    partner, issued, redeemed = hand_off(sign_in(partner_member["user"]), "partner")
    assert issued.json()["redirect_url"].startswith("https://partner.workeasy360.com/")
    assert redeemed.json() == {"next": "/partner"}
    portal = partner.get("/api/v1/partner/portal").json()
    assert [p["partner_id"] for p in portal["partners"]] == [
        str(partner_member["partner"].id)
    ]


def test_a_customer_is_refused_the_partner_portal(sign_in, customer):
    refused = sign_in(customer["user"]).post(
        "/api/v1/auth/handoff",
        {"mode": "partner", "next": "/partner", "state": new_state()},
    )
    assert refused.json() == {"code": "portal_access_denied"}


def test_a_partner_member_reaches_the_customer_portal_only_through_membership(
    sign_in, hand_off, partner_member
):
    """The customer portal admits any signed-in person, and shows nothing of
    any organization without a membership in it."""
    app, _, redeemed = hand_off(sign_in(partner_member["user"]), "user")
    assert redeemed.status_code == 200
    assert app.get("/api/v1/organizations").json()["organizations"] == []


def test_an_administrator_without_a_membership_sees_no_organization(
    sign_in, hand_off, staff_user, customer
):
    from workeasy360.platform.tenancy.resolution import record_step_up

    auth = sign_in(staff_user())
    session = auth.session
    record_step_up(session)
    session.save()
    app, _, _ = hand_off(auth, "user")
    assert app.get("/api/v1/organizations").json()["organizations"] == []
    other = app.get(f"/api/v1/orgs/{customer['org'].slug}/apps")
    assert other.status_code in (403, 404)


# --- what a code is bound to ------------------------------------------------------------


def _issue(auth, mode="user", next_path="/dashboard"):
    state = new_state()
    issued = auth.post(
        "/api/v1/auth/handoff", {"mode": mode, "next": next_path, "state": state}
    )
    assert issued.status_code == 200, issued.content
    return code_of(issued), state


def _redeem(portal, code, state):
    client = HostClient(portal)
    if state is not None:
        client.set_cookie("w360_portal_state", state)
    return client, client.post("/api/v1/auth/handoff/redeem", {"code": code})


def test_a_code_is_single_use(sign_in, customer):
    code, state = _issue(sign_in(customer["user"]))
    assert _redeem("app", code, state)[1].status_code == 200
    replay = _redeem("app", code, state)[1]
    assert replay.status_code == 400
    assert replay.json() == {"code": "handoff_invalid"}


def test_a_code_needs_the_browser_the_portal_sent_to_sign_in(sign_in, customer):
    """Login CSRF: an attacker's own code, delivered to a victim's browser,
    finds no matching binding cookie there - and is burnt by the attempt."""
    code, state = _issue(sign_in(customer["user"]))
    assert _redeem("app", code, None)[1].status_code == 400
    assert _redeem("app", code, state)[1].status_code == 400

    code, state = _issue(sign_in(customer["user"]))
    assert _redeem("app", code, new_state())[1].status_code == 400


def test_a_code_works_only_on_the_portal_it_names(sign_in, staff_user):
    from workeasy360.platform.tenancy.resolution import record_step_up

    auth = sign_in(staff_user())
    session = auth.session
    record_step_up(session)
    session.save()
    code, state = _issue(auth, mode="admin", next_path="/admin")
    assert _redeem("app", code, state)[1].status_code == 400
    # Burnt by the wrong-host attempt.
    assert _redeem("admin", code, state)[1].status_code == 400


def test_a_code_is_not_redeemed_on_the_sign_in_or_api_host(sign_in, customer):
    code, state = _issue(sign_in(customer["user"]))
    assert _redeem("auth", code, state)[1].status_code == 404
    assert _redeem("api", code, state)[1].status_code == 404


def test_a_code_expires(sign_in, customer):
    from workeasy360.platform.identity.models import SessionHandoff

    code, state = _issue(sign_in(customer["user"]))
    SessionHandoff.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert _redeem("app", code, state)[1].status_code == 400


def test_signing_out_before_the_code_is_used_voids_it(sign_in, customer):
    auth = sign_in(customer["user"])
    code, state = _issue(auth)
    assert auth.post("/api/v1/auth/logout").status_code == 200
    assert _redeem("app", code, state)[1].status_code == 400


def test_signing_out_everywhere_voids_an_unused_code(sign_in, customer):
    from workeasy360.platform.identity.models import User

    auth = sign_in(customer["user"])
    code, state = _issue(auth)
    User.objects.filter(pk=customer["user"].pk).update(session_epoch=99)
    assert _redeem("app", code, state)[1].status_code == 400


def test_a_disabled_account_cannot_redeem(sign_in, customer):
    from workeasy360.platform.identity.models import User

    code, state = _issue(sign_in(customer["user"]))
    User.objects.filter(pk=customer["user"].pk).update(status="disabled")
    assert _redeem("app", code, state)[1].status_code == 400


def test_a_malformed_or_unknown_code_is_refused_alike(portals_on):
    for code in ("", "x" * 43, "not a code", None, 7, "A" * 500):
        response = _redeem("app", code, new_state())[1]
        assert response.status_code == 400
        assert response.json() == {"code": "handoff_invalid"}


def test_no_bearer_secret_is_stored(sign_in, customer):
    from workeasy360.platform.identity.models import SessionHandoff

    auth = sign_in(customer["user"])
    code, state = _issue(auth)
    row = SessionHandoff.objects.get()
    assert code not in (row.code_hash, row.state_hash)
    assert state not in (row.code_hash, row.state_hash)
    assert len(row.code_hash) == len(row.state_hash) == 64


def test_issuing_needs_a_signed_in_session_and_a_good_state(
    portals_on, sign_in, customer
):
    anonymous = HostClient("auth")
    response = anonymous.post(
        "/api/v1/auth/handoff", {"mode": "user", "state": new_state()}
    )
    assert response.status_code == 401
    auth = sign_in(customer["user"])
    for body in (
        {"mode": "user", "state": "short"},
        {"mode": "user"},
        {"mode": "root", "state": new_state()},
        {"mode": None, "state": new_state()},
    ):
        assert auth.post("/api/v1/auth/handoff", body).json() == {
            "code": "handoff_invalid"
        }


# --- session fixation ---------------------------------------------------------------------


def test_redeeming_replaces_whatever_session_the_portal_host_had(
    sign_in, customer, user_factory
):
    """A session planted on the portal host (another account's) is flushed:
    the redeemed session is a new key for the right person."""
    from tests.portals.conftest import PASSWORD

    other = user_factory(password=PASSWORD)
    planted_app, _ = _redeem("app", *_issue(sign_in(other)))
    planted_key = planted_app.session.session_key

    code, state = _issue(sign_in(customer["user"]))
    planted_app.set_cookie("w360_portal_state", state)
    response = planted_app.post("/api/v1/auth/handoff/redeem", {"code": code})
    assert response.status_code == 200
    assert planted_app.session.session_key != planted_key
    me = planted_app.get("/api/v1/auth/session").json()
    assert me["email"] == customer["user"].email


# --- sign-out --------------------------------------------------------------------------------


def test_signing_out_of_one_portal_ends_the_whole_sign_in(sign_in, hand_off, staff_user):
    """No stale elevated admin session: signing out of the customer portal
    ends the Super Admin and central sessions of the same sign-in too."""
    from workeasy360.platform.tenancy.resolution import record_step_up

    auth = sign_in(staff_user())
    session = auth.session
    record_step_up(session)
    session.save()
    admin, _, _ = hand_off(auth, "admin")
    app, _, _ = hand_off(auth, "user")
    assert admin.get("/api/v1/admin/session").status_code == 200

    assert app.post("/api/v1/auth/logout").status_code == 200
    assert app.get("/api/v1/auth/session").json() == {"authenticated": False}
    assert admin.get("/api/v1/auth/session").json() == {"authenticated": False}
    assert admin.get("/api/v1/admin/session").status_code in (401, 403)
    assert auth.get("/api/v1/auth/session").json() == {"authenticated": False}


def test_signing_out_ends_only_that_sign_in(sign_in, hand_off, customer):
    """A second sign-in (another browser) is untouched."""
    first = sign_in(customer["user"])
    second = sign_in(customer["user"])
    app_one, _, _ = hand_off(first, "user")
    app_two, _, _ = hand_off(second, "user")
    app_one.post("/api/v1/auth/logout")
    assert app_two.get("/api/v1/auth/session").json()["authenticated"] is True
    assert second.get("/api/v1/auth/session").json()["authenticated"] is True


# --- Guest login across hosts (Super Admin -> a partner's portal) ---------------------


def _guest_staff(staff_user):
    return staff_user("platform.staff_partner.manage", "platform.staff_partner.view")


def test_guest_login_reaches_the_partner_portal_without_a_partner_membership(
    sign_in, hand_off, staff_user, partner_member
):
    """The Super Admin starts a guest view on admin., and the same sign-in is
    then handed to partner. - although the staff member belongs to no partner.
    Before the guest view (and after it ends) the hand-off is refused."""
    partner_id = str(partner_member["partner"].id)
    auth = sign_in(_guest_staff(staff_user), aal2=True)
    _, refused, _ = hand_off(auth, "partner")
    assert refused.json() == {"code": "portal_access_denied"}

    admin, _, _ = hand_off(auth, "admin")
    started = admin.post(
        f"/api/v1/admin/partners/{partner_id}/guest-view",
        {"reason": "Checking the partner's setup for support"},
    )
    assert started.status_code == 201, started.content
    assert started.json()["portal_path"].startswith("/partner/")

    partner, issued, redeemed = hand_off(
        auth, "partner", next_path=started.json()["portal_path"]
    )
    assert issued.status_code == 200, issued.content
    assert redeemed.json() == {"next": started.json()["portal_path"]}
    entries = partner.get("/api/v1/partner/portal").json()["partners"]
    assert [(e["partner_id"], e["role"]) for e in entries] == [(partner_id, "viewer")]
    assert entries[0]["guest"]["reason"] == "Checking the partner's setup for support"
    write = partner.post(f"/api/v1/partner/{partner_id}/customers", {"name": "Acme"})
    assert write.status_code == 403
    assert write.json()["reason"] == "guest_read_only"

    # Exit Guest Session on partner.: the guest view and that session end;
    # the Super Admin's own sessions stay.
    exited = partner.post("/api/v1/partner/guest-view/end")
    assert exited.json() == {"ended": True}
    assert partner.get("/api/v1/auth/session").json()["authenticated"] is False
    assert admin.get("/api/v1/admin/session").status_code == 200
    _, again, _ = hand_off(auth, "partner")
    assert again.json() == {"code": "portal_access_denied"}


def test_a_guest_view_of_another_sign_in_opens_nothing(
    sign_in, hand_off, staff_user, partner_member
):
    """The grant belongs to one sign-in: the same staff member signed in a
    second time (another browser) is still refused the partner portal."""
    user = _guest_staff(staff_user)
    first = sign_in(user, aal2=True)
    admin, _, _ = hand_off(first, "admin")
    started = admin.post(
        f"/api/v1/admin/partners/{partner_member['partner'].id}/guest-view",
        {"reason": "Checking the partner's setup for support"},
    )
    assert started.status_code == 201, started.content
    second = sign_in(user, aal2=True)
    _, refused, _ = hand_off(second, "partner")
    assert refused.json() == {"code": "portal_access_denied"}


def _guest_session(sign_in, hand_off, staff_user, partner_member):
    """A staff member's guest view started on admin., then the hand-off to partner."""
    user = _guest_staff(staff_user)
    auth = sign_in(user, aal2=True)
    admin, _, _ = hand_off(auth, "admin")
    started = admin.post(
        f"/api/v1/admin/partners/{partner_member['partner'].id}/guest-view",
        {"reason": "Checking the partner's setup for support"},
    )
    assert started.status_code == 201, started.content
    return user, auth, admin


def test_a_guest_only_partner_session_is_never_elevated(
    sign_in, hand_off, staff_user, partner_member
):
    """AAL 1 and no step-up time on partner.: a dangerous self-service action
    there asks for the second factor again (audit P2, 2026-10-23)."""
    from workeasy360.platform.identity.sessions import SESSION_GUEST_UNTIL_KEY
    from workeasy360.platform.tenancy.resolution import (
        SESSION_AAL_KEY,
        SESSION_STEP_UP_AT_KEY,
    )

    _user, auth, _admin = _guest_session(sign_in, hand_off, staff_user, partner_member)
    assert auth.session[SESSION_AAL_KEY] == 2
    partner, _, redeemed = hand_off(auth, "partner")
    assert redeemed.status_code == 200, redeemed.content
    session = partner.session
    assert session[SESSION_AAL_KEY] == 1
    assert SESSION_STEP_UP_AT_KEY not in session
    assert session[SESSION_GUEST_UNTIL_KEY] > time.time()


def test_a_guest_only_partner_session_ends_with_the_guest_view(
    sign_in, hand_off, staff_user, partner_member
):
    from workeasy360.platform.identity.sessions import SESSION_GUEST_UNTIL_KEY

    _user, auth, admin = _guest_session(sign_in, hand_off, staff_user, partner_member)
    partner, _, _ = hand_off(auth, "partner")
    assert partner.get("/api/v1/auth/session").json()["authenticated"] is True
    session = partner.session
    session[SESSION_GUEST_UNTIL_KEY] = int(time.time()) - 1
    session.save()
    assert partner.get("/api/v1/auth/session").json()["authenticated"] is False
    # The session row is gone, not merely refused; the other hosts stay.
    assert partner.get("/api/v1/auth/session").json()["authenticated"] is False
    assert admin.get("/api/v1/admin/session").status_code == 200


def test_losing_the_staff_view_grant_refuses_the_guest_hand_off(
    sign_in, hand_off, staff_user, partner_member
):
    from workeasy360.platform.authz import staff
    from workeasy360.platform.authz.models import StaffGrant

    user, auth, _admin = _guest_session(sign_in, hand_off, staff_user, partner_member)
    for grant in StaffGrant.objects.filter(user_id=user.id, revoked_at__isnull=True):
        staff.end_grant(
            grant_id=grant.id, revoked_by_id=user.id, reason="Test revokes the grant"
        )
    _, refused, _ = hand_off(auth, "partner")
    assert refused.json() == {"code": "portal_access_denied"}


def test_signing_out_ends_the_guest_view_and_records_it(
    sign_in, hand_off, staff_user, partner_member
):
    from workeasy360.commercial.partners import guest
    from workeasy360.platform.identity.sessions import SESSION_SIGN_IN_ID_KEY

    user, auth, admin = _guest_session(sign_in, hand_off, staff_user, partner_member)
    sign_in_id = auth.session[SESSION_SIGN_IN_ID_KEY]
    assert guest.current(user.id, sign_in_id) is not None
    assert admin.post("/api/v1/auth/logout").status_code == 200
    assert guest.current(user.id, sign_in_id) is None
    from tests.partner_management.test_guest_view import audit_rows

    assert len(audit_rows("partner.guest_view_ended")) == 1

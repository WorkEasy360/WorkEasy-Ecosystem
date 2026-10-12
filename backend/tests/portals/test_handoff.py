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


def test_an_enrolled_administrator_verifies_the_factor_before_the_hand_off(
    sign_in, hand_off, staff_user
):
    user = staff_user()
    auth = sign_in(user)
    _, needs_mfa, _ = hand_off(auth, "admin")
    assert needs_mfa.status_code == 403
    assert needs_mfa.json() == {"code": "mfa_required"}

    from workeasy360.platform.tenancy.resolution import record_step_up

    session = auth.session
    record_step_up(session)
    session.save()
    admin, issued, redeemed = hand_off(auth, "admin", next_path="/admin/customers")
    assert issued.json()["redirect_url"].startswith("https://admin.workeasy360.com/")
    assert redeemed.json() == {"next": "/admin/customers"}
    response = admin.get("/api/v1/admin/session")
    assert response.status_code == 200, response.content
    assert response.json()["aal"] == 2


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


def test_an_administrator_without_a_factor_reaches_the_console_gate_at_aal1(
    sign_in, hand_off, staff_user
):
    """No factor enrolled: the hand-off happens at AAL 1 and the console's own
    gate (D-037) refuses everything until a factor is enrolled and verified."""
    admin, issued, redeemed = hand_off(sign_in(staff_user(mfa=False)), "admin")
    assert issued.status_code == 200 and redeemed.status_code == 200
    response = admin.get("/api/v1/admin/session")
    assert response.status_code == 403
    assert response.json()["code"] == "mfa_required"


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

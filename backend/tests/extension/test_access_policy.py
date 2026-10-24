"""An organization's access policy: auth configuration and network access, at
both doors (console and product sign-in) - Phase 8 extension, section 3."""

from __future__ import annotations

import time

import pytest

from tests.extension.conftest import PASSWORD, url

pytestmark = pytest.mark.django_db

POLICY = "security/policy"
NETWORK = "security/network"


def _policy(client, slug):
    return client.get(url(slug, POLICY)).json()


def _put(client, slug, path, body):
    return client.put(url(slug, path), body, content_type="application/json")


# --- the pure evaluator ------------------------------------------------------------


def test_an_empty_policy_denies_nothing():
    from workeasy360.contracts.values.access_policy import (
        AccessPolicy,
        SessionFacts,
        evaluate,
    )

    facts = SessionFacts(client_ip="unknown", authenticated_at=None)
    assert evaluate(AccessPolicy(), facts, now=time.time()) is None


def test_denials_are_checked_network_first():
    from workeasy360.contracts.values.access_policy import (
        AccessPolicy,
        SessionFacts,
        evaluate,
    )

    strict = AccessPolicy(
        session_max_age_s=60,
        ip_allowlist_enabled=True,
        ip_allowlist=("10.0.0.0/8",),
    )
    outside = SessionFacts(client_ip="203.0.113.5", authenticated_at=None)
    assert evaluate(strict, outside, now=time.time()) == "network_not_allowed"
    inside_old = SessionFacts(client_ip="10.1.2.3", authenticated_at=time.time() - 120)
    assert evaluate(strict, inside_old, now=time.time()) == "reauthentication_required"
    ok = SessionFacts(client_ip="10.1.2.3", authenticated_at=time.time())
    assert evaluate(strict, ok, now=time.time()) is None


def test_allowlists_are_normalised_and_bounded():
    from workeasy360.contracts.values.access_policy import (
        address_allowed,
        normalise_allowlist,
    )

    assert normalise_allowlist(["203.0.113.9", "10.0.0.0/8", "10.0.0.0/8", " "]) == (
        "203.0.113.9/32",
        "10.0.0.0/8",
    )
    for bad in (["0.0.0.0/0"], ["::/0"], ["not-an-ip"], ["10.0.0.0/4"]):
        with pytest.raises(ValueError):
            normalise_allowlist(bad)
    assert address_allowed("::ffff:10.0.0.1", ["10.0.0.0/8"])
    assert not address_allowed("unknown", ["10.0.0.0/8"])


# --- auth configuration ------------------------------------------------------------


def test_policy_changes_need_the_permission_and_a_current_version(console_org, login):
    org = console_org()
    admin = org["add"]("admin")
    member = org["add"]("member")
    body = {
        "session_max_age_minutes": 60,
        "password_min_length": None,
        "version": 1,
    }

    assert _put(login(member["user"]), org["slug"], POLICY, body).status_code == 403

    admin_client = login(admin["user"])
    saved = _put(admin_client, org["slug"], POLICY, body)
    assert saved.status_code == 200 and saved.json()["version"] == 2
    stale = _put(
        admin_client, org["slug"], POLICY, {**body, "session_max_age_minutes": 120}
    )
    assert (stale.status_code, stale.json()["code"]) == (409, "concurrent_modification")

    entries = admin_client.get(url(org["slug"], "audit"), {"action": "security."}).json()[
        "results"
    ]
    assert entries[0]["action"] == "security.policy_updated"
    assert entries[0]["changes"]["after"]["sign_in_max_age_minutes"] == 60


def test_policy_values_are_bounded(console_org, login):
    org = console_org()
    owner = login(org["owner"])
    for field, value in (
        ("session_max_age_minutes", 5),
        ("session_max_age_minutes", 10**7),
        ("password_min_length", 6),
        ("password_min_length", 500),
    ):
        body = {
            "session_max_age_minutes": None,
            "password_min_length": None,
            "version": 1,
        }
        body[field] = value
        response = _put(owner, org["slug"], POLICY, body)
        assert response.status_code == 400, (field, value)


def test_the_session_age_limit_applies_per_organization(console_org, login):
    from workeasy360.platform.tenancy.resolution import SESSION_AUTHENTICATED_AT_KEY

    strict, relaxed = console_org(), console_org()
    member = strict["add"]("member")
    from workeasy360.platform.organizations.models import Organization

    Organization.objects.filter(id=strict["org"].id).update(session_max_age_minutes=60)
    client = login(member["user"])
    assert client.get(url(strict["slug"], "access")).status_code == 200

    session = client.session
    session[SESSION_AUTHENTICATED_AT_KEY] = time.time() - 2 * 3600
    session.save()
    denied = client.get(url(strict["slug"], "access"))
    assert (denied.status_code, denied.json()["code"]) == (
        401,
        "reauthentication_required",
    )
    relaxed_owner = login(relaxed["owner"])
    assert relaxed_owner.get(url(relaxed["slug"], "access")).status_code == 200


def test_a_short_password_must_be_changed_before_entering(console_org, login):
    from workeasy360.platform.organizations.models import Organization

    org = console_org()
    member = org["add"]("member")
    Organization.objects.filter(id=org["org"].id).update(password_min_length=40)
    client = login(member["user"])
    denied = client.get(url(org["slug"], "access"))
    assert (denied.status_code, denied.json()["code"]) == (
        403,
        "password_change_required",
    )

    long_enough = "this passphrase is certainly forty characters long"
    changed = client.post(
        "/api/v1/auth/password",
        {"current_password": PASSWORD, "new_password": long_enough},
        content_type="application/json",
    )
    assert changed.status_code == 200, changed.content
    assert client.get(url(org["slug"], "access")).status_code == 200


# --- network access -------------------------------------------------------------------


def test_the_allow_list_refuses_to_lock_out_its_editor(console_org, login):
    org = console_org()
    owner = login(org["owner"])
    view = owner.get(url(org["slug"], NETWORK)).json()
    assert view["your_address"] == "127.0.0.1" and view["your_network"] == "127.0.0.0/24"

    locked = _put(
        owner,
        org["slug"],
        NETWORK,
        {"ip_allowlist_enabled": True, "ip_allowlist": ["10.0.0.0/8"], "version": 1},
    )
    assert (locked.status_code, locked.json()["code"]) == (409, "network_lockout")
    empty = _put(
        owner,
        org["slug"],
        NETWORK,
        {"ip_allowlist_enabled": True, "ip_allowlist": [], "version": 1},
    )
    assert empty.status_code == 400
    ok = _put(
        owner,
        org["slug"],
        NETWORK,
        {"ip_allowlist_enabled": True, "ip_allowlist": ["127.0.0.0/24"], "version": 1},
    )
    assert ok.status_code == 200 and ok.json()["ip_allowlist"] == ["127.0.0.0/24"]


def test_outside_addresses_are_refused_everywhere_in_the_organization(console_org, login):
    org = console_org()
    member = org["add"]("member")
    _put(
        login(org["owner"]),
        org["slug"],
        NETWORK,
        {
            "ip_allowlist_enabled": True,
            "ip_allowlist": ["127.0.0.0/24"],
            "version": 1,
        },
    )
    outside = login(member["user"], ip="203.0.113.50")
    for path in ("access", "members", "dashboard"):
        response = outside.get(url(org["slug"], path))
        assert (response.status_code, response.json()["code"]) == (
            403,
            "network_not_allowed",
        ), path
    inside = login(member["user"], ip="127.0.0.9")
    assert inside.get(url(org["slug"], "access")).status_code == 200
    # The organization chooser (not an organization path) still lists it.
    assert org["slug"] in [
        o["slug"] for o in outside.get("/api/v1/organizations").json()["organizations"]
    ]


def test_staff_can_break_glass_a_locked_out_organization(console_org, login, staff_login):
    org = console_org()
    _put(
        login(org["owner"]),
        org["slug"],
        NETWORK,
        {
            "ip_allowlist_enabled": True,
            "ip_allowlist": ["127.0.0.0/24"],
            "version": 1,
        },
    )
    staff, _user = staff_login("platform.staff_customer.manage")
    target = f"/api/v1/admin/customers/{org['org'].id}/network-policy/reset"
    assert (
        staff.post(
            target, {"reason": "short"}, content_type="application/json"
        ).status_code
        == 400
    )
    assert (
        staff.post(
            target,
            {"reason": "Customer locked out after an ISP change"},
            content_type="application/json",
        ).status_code
        == 200
    )

    outside = login(org["owner"], ip="198.51.100.4")
    assert outside.get(url(org["slug"], "access")).status_code == 200
    audit = (
        login(org["owner"])
        .get(url(org["slug"], "audit"), {"action": "security.network"})
        .json()
    )
    assert audit["results"][0]["action"] == "security.network_policy_reset"
    assert audit["results"][0]["reason"] == "Customer locked out after an ISP change"

    viewer, _ = staff_login("platform.staff_customer.view")
    assert (
        viewer.post(
            target,
            {"reason": "Customer locked out again"},
            content_type="application/json",
        ).status_code
        == 403
    )


# --- the second door: product sign-in ----------------------------------------------------


def test_resolve_subject_refuses_what_the_policy_refuses(organization_factory):
    from workeasy360.contracts.values.access_policy import SessionFacts
    from workeasy360.platform.ecosystem import authorization, protocol
    from workeasy360.platform.organizations.models import Organization

    org, _membership, user = organization_factory()
    Organization.objects.filter(id=org.id).update(
        ip_allowlist_enabled=True, ip_allowlist=["10.0.0.0/8"]
    )
    outside = SessionFacts(client_ip="203.0.113.1", authenticated_at=time.time())
    with pytest.raises(protocol.OAuthError) as denied:
        authorization.resolve_subject(
            user_id=user.id, organization_id=org.id, facts=outside
        )
    assert denied.value.error == protocol.ACCESS_POLICY_DENIED
    inside = SessionFacts(client_ip="10.9.9.9", authenticated_at=time.time())
    subject = authorization.resolve_subject(
        user_id=user.id, organization_id=org.id, facts=inside
    )
    assert subject.organization_id == org.id

"""The organization console: access, dashboard, members, audit, sign-ins,
rename (Phase 8 extension, section 2)."""

from __future__ import annotations

import pytest

from tests.extension.conftest import step_up, url

pytestmark = pytest.mark.django_db


def test_access_names_the_console_and_records_one_sign_in_per_session(console_org, login):
    from workeasy360.platform.organizations.models import MemberSignIn
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    org = console_org()
    member = org["add"]("member")
    owner_client = login(org["owner"])
    member_client = login(member["user"])

    owner_view = owner_client.get(url(org["slug"], "access")).json()
    assert owner_view["console"] == "admin"
    assert owner_view["membership"]["is_owner"] is True
    assert "platform.member.invite" in owner_view["permissions"]
    member_view = member_client.get(url(org["slug"], "access")).json()
    assert member_view["console"] == "member"
    assert member_view["membership"]["roles"] == ["member"]
    assert not any(p.startswith("platform.staff") for p in owner_view["permissions"])

    for _ in range(3):
        owner_client.get(url(org["slug"], "access"))
    with (
        unscoped_access(reason="test reads sign-ins", module="tests"),
        organization_db_scope(org["org"].id),
    ):
        rows = MemberSignIn.unscoped.filter(organization_id=org["org"].id)
        assert rows.count() == 2  # one per (member, session), however many loads
        assert set(rows.values_list("device_label", flat=True)) == {"Chrome on Windows"}


def test_dashboard_sections_follow_permissions(console_org, login):
    org = console_org()
    member = org["add"]("member")
    org["add"]("readonly", status="suspended")
    owner = login(org["owner"])
    owner.get(url(org["slug"], "access"))

    full = owner.get(url(org["slug"], "dashboard"), {"window": 7}).json()
    assert full["members"]["active_members"] == 2
    assert full["members"]["suspended_members"] == 1
    assert full["members"]["members_added"] == 3
    assert len(full["activity"]["series"]) == 7
    assert full["activity"]["most_active"][0]["sign_ins"] == 1
    assert "apps" not in full or full["apps"]["entitled"] == 0

    limited = login(member["user"]).get(url(org["slug"], "dashboard")).json()
    assert "members" in limited  # members may see who is in their organization
    assert "activity" not in limited  # but not how often colleagues sign in
    assert "subscription" not in limited


def test_the_dashboard_never_counts_another_tenants_activity(console_org, login):
    a, b = console_org(), console_org()
    login(b["owner"]).get(url(b["slug"], "access"))
    data = login(a["owner"]).get(url(a["slug"], "dashboard")).json()
    assert data["activity"]["most_active"] == []
    assert data["members"]["active_members"] == 1


def test_member_directory_carries_roles_and_last_sign_in(console_org, login):
    org = console_org()
    admin = org["add"]("admin", name="Ada Admin")
    login(admin["user"]).get(url(org["slug"], "access"))
    rows = login(org["owner"]).get(url(org["slug"], "members")).json()["results"]
    by_email = {r["email"]: r for r in rows}
    assert by_email[org["owner"].email]["roles"][0] == "owner"
    assert by_email[admin["user"].email]["roles"] == ["admin"]
    assert by_email[admin["user"].email]["last_sign_in_at"] is not None
    # Not just "ada": fixture emails are random hex, which can contain it.
    searched = (
        login(org["owner"]).get(url(org["slug"], "members"), {"q": "ada admin"}).json()
    )
    assert [r["email"] for r in searched["results"]] == [admin["user"].email]


def test_changing_a_role_needs_step_up_and_takes_effect_immediately(console_org, login):
    org = console_org()
    member = org["add"]("member")
    owner = login(org["owner"])
    target = url(org["slug"], f"members/{member['membership'].id}/role")

    refused = owner.post(target, {"role": "admin"}, content_type="application/json")
    assert (refused.status_code, refused.json()["code"]) == (401, "step_up_required")

    step_up(owner)
    assert (
        owner.post(target, {"role": "admin"}, content_type="application/json").status_code
        == 200
    )
    promoted = login(member["user"]).get(url(org["slug"], "access")).json()
    assert promoted["console"] == "admin"


def test_the_owner_and_oneself_are_protected(console_org, login):
    org = console_org()
    admin = org["add"]("admin")
    client = login(admin["user"], aal2=True)
    owner_id = org["membership"].id
    for action in ("suspend",):
        response = client.post(url(org["slug"], f"members/{owner_id}/{action}"))
        assert (response.status_code, response.json()["code"]) == (
            409,
            "membership_conflict",
        )
    response = client.post(
        url(org["slug"], f"members/{owner_id}/role"),
        {"role": "member"},
        content_type="application/json",
    )
    assert response.status_code == 409
    response = client.post(url(org["slug"], f"members/{admin['membership'].id}/suspend"))
    assert response.status_code == 409
    response = client.post(url(org["slug"], f"members/{owner_id}/sign-out"))
    assert response.status_code == 409  # only the owner signs the owner out


def test_a_member_cannot_manage_members(console_org, login):
    org = console_org()
    member = org["add"]("member")
    other = org["add"]("member")
    client = login(member["user"], aal2=True)
    for path, body in (
        (f"members/{other['membership'].id}/role", {"role": "admin"}),
        (f"members/{other['membership'].id}/suspend", {}),
        (f"members/{other['membership'].id}/sign-out", {}),
    ):
        response = client.post(
            url(org["slug"], path), body, content_type="application/json"
        )
        assert response.status_code == 403, path


def test_suspension_locks_the_member_out_and_releases_their_seats(console_org, login):
    org = console_org()
    member = org["add"]("member")
    member_client = login(member["user"])
    assert member_client.get(url(org["slug"], "access")).status_code == 200

    owner = login(org["owner"])
    assert (
        owner.post(
            url(org["slug"], f"members/{member['membership'].id}/suspend")
        ).status_code
        == 200
    )
    assert member_client.get(url(org["slug"], "access")).status_code == 404
    chooser = member_client.get("/api/v1/organizations").json()["organizations"]
    assert org["slug"] not in [o["slug"] for o in chooser]

    assert (
        owner.post(
            url(org["slug"], f"members/{member['membership'].id}/reactivate")
        ).status_code
        == 200
    )
    assert member_client.get(url(org["slug"], "access")).status_code == 200


def test_signing_a_member_out_ends_their_sessions_here_only(console_org, login):
    here, elsewhere = console_org(), console_org()
    member = here["add"]("member")
    # The same person also belongs to a second organization.
    from workeasy360.contracts.enums import MembershipStatus
    from workeasy360.platform.organizations import projection
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="Test arranges tenant rows directly", module="tests"),
        organization_db_scope(elsewhere["org"].id),
    ):
        second = Membership(
            organization_id=elsewhere["org"].id,
            user_id=member["user"].id,
            status=MembershipStatus.ACTIVE.value,
        )
        second.save()
        projection.sync_access_for_membership(
            user_id=member["user"].id, membership=second, organization=elsewhere["org"]
        )
    member_client = login(member["user"])
    assert member_client.get(url(here["slug"], "access")).status_code == 200

    owner = login(here["owner"], aal2=True)
    assert (
        owner.post(
            url(here["slug"], f"members/{member['membership'].id}/sign-out")
        ).status_code
        == 200
    )

    denied = member_client.get(url(here["slug"], "access"))
    assert (denied.status_code, denied.json()["code"]) == (
        401,
        "reauthentication_required",
    )
    assert member_client.get(url(elsewhere["slug"], "access")).status_code == 200
    # Signing in again restores access.
    fresh = login(member["user"])
    assert fresh.get(url(here["slug"], "access")).status_code == 200


def test_the_audit_log_is_the_organizations_own_and_permissioned(console_org, login):
    a, b = console_org(), console_org()
    member = a["add"]("member")
    owner_a = login(a["owner"], aal2=True)
    owner_a.post(
        url(a["slug"], f"members/{member['membership'].id}/role"),
        {"role": "readonly"},
        content_type="application/json",
    )
    login(b["owner"], aal2=True).patch(
        url(b["slug"], "organization"),
        {"display_name": "Bee Industries"},
        content_type="application/json",
    )

    entries = owner_a.get(url(a["slug"], "audit")).json()["results"]
    actions = {e["action"] for e in entries}
    assert "membership.role_changed" in actions
    assert "organization.renamed" not in actions
    filtered = owner_a.get(url(a["slug"], "audit"), {"action": "membership."}).json()[
        "results"
    ]
    assert all(e["action"].startswith("membership.") for e in filtered)
    assert login(member["user"]).get(url(a["slug"], "audit")).status_code == 403


def test_sign_in_activity_lists_members_entering_the_organization(console_org, login):
    org = console_org()
    member = org["add"]("member", name="Mira Member")
    login(member["user"]).get(url(org["slug"], "access"))
    rows = login(org["owner"]).get(url(org["slug"], "sign-ins")).json()["results"]
    assert [r["email"] for r in rows] == [member["user"].email]
    assert rows[0]["ip_prefix"] == "127.0.0.0/24"


def test_renaming_updates_the_chooser_and_is_audited(console_org, login):
    org = console_org()
    owner = login(org["owner"])
    response = owner.patch(
        url(org["slug"], "organization"),
        {"display_name": "Renamed Ltd"},
        content_type="application/json",
    )
    assert (
        response.status_code == 200 and response.json()["display_name"] == "Renamed Ltd"
    )
    chooser = owner.get("/api/v1/organizations").json()["organizations"]
    assert chooser[0]["display_name"] == "Renamed Ltd"
    member = org["add"]("member")
    refused = login(member["user"]).patch(
        url(org["slug"], "organization"),
        {"display_name": "Nope"},
        content_type="application/json",
    )
    assert refused.status_code == 403

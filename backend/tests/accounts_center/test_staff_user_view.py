"""The Super Admin's view of a person (ADR-072): central user id ->
organizations -> purchased products and seats -> product connections ->
sign-in history -> known sessions, behind the existing
``platform.staff_user.view``, with every view audited once per window."""

from __future__ import annotations

import pytest
from django.test import Client

from tests.accounts_center.conftest import BROWSER_AGENT, sign_in_to_product

pytestmark = pytest.mark.django_db

USERS = "/api/v1/admin/users"


@pytest.fixture
def staff_with(user_factory):
    """A staff member holding exactly ``permissions``, granted for real."""
    from workeasy360.platform.authz import staff

    root = user_factory(is_staff=True)

    def make(*permissions: str):
        member = user_factory(is_staff=True)
        for permission in permissions:
            staff.create_grant(
                user_id=member.id,
                permission=permission,
                granted_by_id=root.id,
                reason="Test fixture grants a control-plane permission",
            )
        return member

    return make


@pytest.fixture
def connected_person(
    signing_key, person, register_product, bcl_manifest, password_login, product_client
):
    crm = register_product("crm", manifest=bcl_manifest("crm"))
    browser = password_login(
        Client(HTTP_USER_AGENT=BROWSER_AGENT, REMOTE_ADDR="203.0.113.77"), person["user"]
    )
    sign_in_to_product(browser, crm, person["organization_id"], product_client)
    return {**person, "browser": browser, "crm": crm}


def _views(admin) -> list[dict]:
    return admin.get("/api/v1/admin/audit", {"action": "staff.user_viewed"}).json()[
        "results"
    ]


def test_the_person_mapping_without_secrets(
    connected_person, staff_with, accounts_on, on_admin
):
    accounts_on()
    viewer = staff_with("platform.staff_user.view")
    response = on_admin(viewer).get(f"{USERS}/{connected_person['user'].pk}")
    assert response.status_code == 200, response.content
    detail = response.json()
    user_id = str(connected_person["user"].pk)
    assert detail["central_user_id"] == user_id
    (membership,) = detail["memberships"]
    assert membership["organization_id"] == connected_person["organization_id"]
    # The organization's products come from its subscription summary (none
    # here: the test grants directly); the seat is the person's own.
    assert membership["seats"] == ["crm"]
    (connection,) = detail["connections"]
    assert connection["product_code"] == "crm" and connection["name"] == "CRM"
    assert connection["platform_sign_in"] == "open"
    (org,) = connection["organizations"]
    assert org["organization_id"] == connected_person["organization_id"]
    assert org["sign_in_count"] == 1
    kinds = [e["kind"] for e in detail["sign_in_history"]]
    assert "product_sign_in" in kinds and "sign_in" in kinds
    assert (
        next(e for e in detail["sign_in_history"] if e["kind"] == "product_sign_in")[
            "product_name"
        ]
        == "CRM"
    )
    (sign_in,) = detail["sign_ins"]
    assert sign_in["ip_prefix"] == "203.0.113.0/24"

    text = response.content.decode()
    assert "203.0.113.77" not in text  # never a full address
    assert "session_key" not in text
    assert connected_person["browser"].session.session_key not in text
    assert "argon2" not in text and "pbkdf2" not in text
    assert connected_person["crm"]["client_id"] not in text
    assert connected_person["crm"]["private_key_pem"].split("\n")[1] not in text


def test_viewing_a_person_is_audited_once_per_window(
    connected_person, staff_with, accounts_on, on_admin, monkeypatch
):
    from workeasy360.platform.administration import staff_users

    accounts_on()
    viewer = staff_with("platform.staff_user.view", "platform.staff_audit.view")
    admin = on_admin(viewer)
    target = f"{USERS}/{connected_person['user'].pk}"
    for _ in range(3):
        assert admin.get(target).status_code == 200
    (entry,) = _views(admin)
    assert entry["resource_id"] == str(connected_person["user"].pk)
    assert entry["actor_user_id"] == str(viewer.pk)
    assert "connections" in entry["changes"]["sections"]

    # Another staff member's look is their own entry.
    colleague = staff_with("platform.staff_user.view")
    on_admin(colleague).get(target)
    assert len(_views(admin)) == 2

    # Once the window has passed, the same staff member is recorded again.
    monkeypatch.setattr(staff_users, "VIEW_AUDIT_WINDOW_S", 0)
    admin.get(target)
    assert len(_views(admin)) == 3


@pytest.mark.parametrize("role", ["PRODUCT_ADMIN", "BILLING_ADMIN"])
def test_roles_without_the_permission_are_refused(
    connected_person, staff_with, accounts_on, on_admin, role
):
    from workeasy360.platform.authz.staff_roles import role as staff_role

    held = staff_role(role).permissions
    assert "platform.staff_user.view" not in held
    accounts_on()
    outsider = staff_with(*sorted(held))
    response = on_admin(outsider).get(f"{USERS}/{connected_person['user'].pk}")
    assert response.status_code == 403
    assert str(connected_person["user"].pk) not in response.content.decode()
    auditor = staff_with("platform.staff_audit.view")
    assert _views(on_admin(auditor)) == []


def test_a_customer_cannot_reach_the_staff_view(connected_person, accounts_on, on_admin):
    accounts_on()
    response = on_admin(connected_person["user"]).get(
        f"{USERS}/{connected_person['user'].pk}"
    )
    assert response.status_code in (401, 403)


def test_with_identity_off_the_view_is_unchanged_and_unaudited(
    connected_person, staff_with, accounts_on, on_admin
):
    accounts_on(False)
    viewer = staff_with("platform.staff_user.view", "platform.staff_audit.view")
    admin = on_admin(viewer)
    detail = admin.get(f"{USERS}/{connected_person['user'].pk}").json()
    for added in ("central_user_id", "connections", "sign_in_history", "sign_ins"):
        assert added not in detail
    assert "seats" not in detail["memberships"][0]
    assert _views(admin) == []

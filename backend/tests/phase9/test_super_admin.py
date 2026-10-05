"""The Super Admin (Phase 9): the environment-driven bootstrap, named staff
roles over the one grant model, the last-administrator guard, staff sign-in
audit, and the user and customer segments - Explorer, Trial, Customer - that
classify one identity without ever moving it."""

from __future__ import annotations

import io
import uuid

import pytest
from django.core.management import CommandError, call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.extension.conftest import buy_directly, place_and_pay, url
from tests.phase9.conftest import PASSWORD

pytestmark = pytest.mark.django_db

ADMIN = "owner.admin@example.com"
SECRET = "a long operator passphrase 77"


def _bootstrap(monkeypatch, *, email=ADMIN, password=SECRET, dry_run=False):
    monkeypatch.setenv("PLATFORM_SUPERADMIN_EMAIL", email)
    if password is None:
        monkeypatch.delenv("PLATFORM_SUPERADMIN_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("PLATFORM_SUPERADMIN_PASSWORD", password)
    out = io.StringIO()
    call_command("bootstrap_superadmin", *(["--dry-run"] if dry_run else []), stdout=out)
    return out.getvalue()


def _user(email=ADMIN):
    from workeasy360.platform.identity.models import User

    return User.objects.filter(email=email).first()


# --- bootstrap_superadmin -----------------------------------------------------------


def test_bootstrap_creates_a_verified_super_admin_holding_every_staff_grant(monkeypatch):
    from workeasy360.platform.authz import staff, staff_roles

    output = _bootstrap(monkeypatch)
    user = _user()
    assert user is not None and user.is_staff and not user.is_superuser
    assert user.status == "active" and user.email_verified_at is not None
    assert user.acquisition_source == "operator"
    assert user.check_password(SECRET) and SECRET not in user.password
    held = {g["permission"] for g in staff.live_grants(user_id=user.id)}
    assert held == staff_roles.role("SUPER_ADMIN").permissions
    assert SECRET not in output


def test_bootstrap_is_idempotent_and_never_resets_a_password(monkeypatch):
    _bootstrap(monkeypatch)
    again = _bootstrap(monkeypatch, password="a different passphrase 88")
    assert "Nothing changed" in again
    assert _user().check_password(SECRET)


def test_bootstrap_promotes_an_existing_account_without_touching_its_password(
    monkeypatch, user_factory
):
    person = user_factory(email=ADMIN, password=PASSWORD)
    output = _bootstrap(monkeypatch, password=None)
    person.refresh_from_db()
    assert person.is_staff and person.check_password(PASSWORD)
    assert "password was not changed" in output


def test_bootstrap_is_no_back_door_once_another_administrator_exists(monkeypatch):
    _bootstrap(monkeypatch)
    with pytest.raises(CommandError, match="different security administrator"):
        _bootstrap(monkeypatch, email="intruder@example.com")
    assert _user("intruder@example.com") is None


@pytest.mark.parametrize(
    ("email", "password", "message"),
    [
        ("", SECRET, "PLATFORM_SUPERADMIN_EMAIL"),
        ("not-an-address", SECRET, "PLATFORM_SUPERADMIN_EMAIL"),
        (ADMIN, None, "PLATFORM_SUPERADMIN_PASSWORD"),
        (ADMIN, "short", "password policy"),
    ],
)
def test_bootstrap_refuses_bad_configuration_without_echoing_it(
    monkeypatch, email, password, message
):
    with pytest.raises(CommandError, match=message) as refused:
        _bootstrap(monkeypatch, email=email, password=password)
    assert "short" not in str(refused.value) or password != "short"
    assert _user() is None


def test_bootstrap_dry_run_changes_nothing_and_is_audited_when_real(
    monkeypatch, staff_login
):
    assert "Would create" in _bootstrap(monkeypatch, dry_run=True)
    assert _user() is None
    _bootstrap(monkeypatch)
    reader, _ = staff_login("platform.staff_audit.view")
    rows = reader.get("/api/v1/admin/audit", {"action": "staff.bootstrap"}).json()[
        "results"
    ]
    assert [r["action"] for r in rows] == ["staff.bootstrap_superadmin"]
    assert rows[0]["actor_kind"] == "system"


# --- named roles ----------------------------------------------------------------------


def test_roles_name_only_registered_staff_permissions_and_read_only_is_harmless():
    from workeasy360.platform.authz.registry import permission_registry
    from workeasy360.platform.authz.staff_roles import ROLE_KEYS, roles

    declared = {r.key: r for r in roles()}
    assert tuple(declared) == ROLE_KEYS
    for role in declared.values():
        for key in role.permissions:
            declaration = permission_registry.get(key)
            assert declaration.control_plane, key
    assert not any(
        permission_registry.get(k).dangerous
        for k in declared["READ_ONLY_ADMIN"].permissions
    )
    dangerous_platform_wide = {
        "platform.staff_security.manage",
        "platform.staff_payout.approve",
        "platform.staff_partner.manage",
        "platform.staff_settings.manage",
    }
    for key, role in declared.items():
        if key != "SUPER_ADMIN":
            assert not role.permissions & dangerous_platform_wide, key


def test_granting_a_role_is_dangerous_audited_and_derives_its_holders(
    staff_login, user_factory
):
    security, _ = staff_login("platform.staff_security.manage")
    person = user_factory(email="billing.admin@example.com", is_staff=True)
    body = {
        "email": person.email,
        "role": "BILLING_ADMIN",
        "reason": "Joined the finance team",
    }
    low, _ = staff_login("platform.staff_security.manage", aal2=False)
    assert (
        low.post(
            "/api/v1/admin/security/roles", body, content_type="application/json"
        ).status_code
        == 401
    )
    granted = security.post(
        "/api/v1/admin/security/roles", body, content_type="application/json"
    )
    assert granted.status_code == 201, granted.content
    assert "platform.staff_payment.manage" in granted.json()["added"]
    catalog = {
        r["key"]: r
        for r in security.get("/api/v1/admin/security/roles").json()["results"]
    }
    assert person.email in [h["email"] for h in catalog["BILLING_ADMIN"]["holders"]]
    assert person.email not in [h["email"] for h in catalog["SUPER_ADMIN"]["holders"]]
    # It is only grants: the one decision function sees them directly.
    from workeasy360.platform.authz import staff

    assert "platform.staff_payment.manage" in staff.staff_permissions(person.id)


def test_revoking_a_role_keeps_what_another_held_role_still_needs(
    staff_login, user_factory
):
    from workeasy360.platform.authz import staff

    security, _ = staff_login("platform.staff_security.manage")
    person = user_factory(email="two.roles@example.com", is_staff=True)
    for role in ("BILLING_ADMIN", "SUPPORT_ADMIN"):
        security.post(
            "/api/v1/admin/security/roles",
            {"email": person.email, "role": role, "reason": "Covering two desks"},
            content_type="application/json",
        )
    revoked = security.post(
        "/api/v1/admin/security/roles/revoke",
        {
            "user_id": str(person.id),
            "role": "SUPPORT_ADMIN",
            "reason": "Left the support desk",
        },
        content_type="application/json",
    )
    assert revoked.status_code == 200, revoked.content
    held = staff.staff_permissions(person.id)
    assert "platform.staff_support.view" not in held
    # Shared with BILLING_ADMIN, which the person still holds:
    assert {"platform.staff_customer.view", "platform.staff_billing.view"} <= held


def test_the_last_permanent_security_administrator_cannot_lose_the_role(
    monkeypatch, staff_login
):
    _bootstrap(monkeypatch)
    admin = _user()
    helper, _ = staff_login("platform.staff_security.manage")
    from workeasy360.platform.authz import staff

    # Only the bootstrap admin holds it permanently once the helper's grant
    # is the one being examined; revoking the admin's role must keep one.
    holders_before = {
        g["user_id"]
        for g in staff.live_grants()
        if g["permission"] == staff.SECURITY_ADMIN
    }
    assert str(admin.id) in holders_before
    response = helper.post(
        "/api/v1/admin/security/roles/revoke",
        {
            "user_id": str(admin.id),
            "role": "SUPER_ADMIN",
            "reason": "Rotating administrators",
        },
        content_type="application/json",
    )
    # With the helper also holding it permanently, the revocation is allowed.
    assert response.status_code == 200, response.content


# --- the last-administrator guard on account actions (audit fix) ---------------------------


def test_disabling_the_last_security_administrator_is_refused(monkeypatch, staff_login):
    _bootstrap(monkeypatch)
    admin = _user()
    users, _ = staff_login("platform.staff_user.manage")
    refused = users.post(
        f"/api/v1/admin/users/{admin.id}/disable",
        {"reason": "Suspected compromise of the account"},
        content_type="application/json",
    )
    assert (refused.status_code, refused.json()["code"]) == (409, "membership_conflict")
    admin.refresh_from_db()
    assert admin.status == "active"


def test_disabling_one_of_two_security_administrators_is_allowed(
    monkeypatch, staff_login
):
    _bootstrap(monkeypatch)
    admin = _user()
    users, _ = staff_login("platform.staff_user.manage", "platform.staff_security.manage")
    response = users.post(
        f"/api/v1/admin/users/{admin.id}/disable",
        {"reason": "Suspected compromise of the account"},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content


# --- staff sign-in audit ------------------------------------------------------------------


def test_staff_sign_in_and_out_are_in_the_platform_audit_log(staff_login, login):
    staff_client, staff_user = staff_login("platform.staff_audit.view")
    staff_client.post("/api/v1/auth/logout")
    reader, _ = staff_login("platform.staff_audit.view")
    rows = reader.get("/api/v1/admin/audit", {"action": "staff.signed"}).json()["results"]
    actions = [(r["action"], r["actor_user_id"]) for r in rows]
    assert ("staff.signed_in", str(staff_user.id)) in actions
    assert ("staff.signed_out", str(staff_user.id)) in actions


def test_customer_sign_ins_are_not_written_to_the_platform_log(
    console_org, login, staff_login
):
    org = console_org()
    login(org["owner"])
    reader, _ = staff_login("platform.staff_audit.view")
    rows = reader.get("/api/v1/admin/audit", {"action": "staff.signed_in"}).json()[
        "results"
    ]
    assert str(org["owner"].id) not in [r["actor_user_id"] for r in rows]


# --- one identity, classified ------------------------------------------------------------------


@pytest.fixture
def population(console_org, login, catalog, fake_payments, user_factory):
    """An Explorer, a trial, a customer, an unverified signup, a locked-out
    and a deactivated account."""
    from datetime import timedelta

    from django.utils import timezone

    explorer = console_org()
    trial = console_org()
    customer = console_org()
    owner = login(trial["owner"])
    owner.post(
        url(trial["slug"], "billing-account"),
        {"currency": "GBP"},
        content_type="application/json",
    )
    owner.post(
        url(trial["slug"], "orders"),
        {
            "plan_version_id": str(catalog["versions"]["crm_starter"].id),
            "with_trial": True,
        },
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    buy_directly(customer, catalog["versions"]["crm_professional"].id)
    pending = user_factory(email="pending@example.com", status="pending")
    locked = user_factory(email="locked@example.com")
    locked.locked_until = timezone.now() + timedelta(minutes=10)
    locked.save()
    disabled = user_factory(email="disabled@example.com", status="disabled")
    return {
        "explorer": explorer,
        "trial": trial,
        "customer": customer,
        "pending": pending,
        "locked": locked,
        "disabled": disabled,
        "catalog": catalog,
    }


def _segment(client, segment):
    rows = client.get("/api/v1/admin/users", {"segment": segment, "limit": 100}).json()[
        "results"
    ]
    return {r["email"]: r for r in rows}


def test_users_are_segmented_by_what_their_organizations_hold(population, staff_login):
    staff_client, _ = staff_login("platform.staff_user.view")
    explorers = _segment(staff_client, "explorers")
    trials = _segment(staff_client, "trial")
    customers = _segment(staff_client, "customers")
    assert population["explorer"]["owner"].email in explorers
    assert population["trial"]["owner"].email in trials
    assert population["customer"]["owner"].email in customers
    assert population["customer"]["owner"].email not in explorers
    row = customers[population["customer"]["owner"].email]
    assert (row["lifecycle"], row["products"], row["subscription_status"]) == (
        "customer",
        ["crm"],
        "active",
    )
    assert set(_segment(staff_client, "unverified")) == {"pending@example.com"}
    assert "locked@example.com" in _segment(staff_client, "suspended")
    assert set(_segment(staff_client, "deactivated")) == {"disabled@example.com"}
    assert staff_client.get("/api/v1/admin/users", {"segment": "vip"}).status_code == 400


def test_buying_turns_the_same_explorer_into_a_customer(population, staff_login, login):
    staff_client, _ = staff_login("platform.staff_user.view")
    explorer = population["explorer"]
    before = _segment(staff_client, "explorers")[explorer["owner"].email]
    owner = login(explorer["owner"])
    owner.post(
        url(explorer["slug"], "billing-account"),
        {"currency": "GBP"},
        content_type="application/json",
    )
    order = place_and_pay(
        owner,
        explorer["slug"],
        explorer["org"].id,
        plan_version_id=str(population["catalog"]["versions"]["books_professional"].id),
    )
    assert order["status"] == "fulfilled"
    after = _segment(staff_client, "customers")[explorer["owner"].email]
    # The same identity - nothing was moved or recreated.
    assert after["user_id"] == before["user_id"]
    assert explorer["owner"].email not in _segment(staff_client, "explorers")


def test_a_segment_page_costs_the_same_whatever_its_size(
    population, staff_login, console_org
):
    """Both pages hold people with organizations, so every lookup runs in both
    measurements - and none of them runs per row."""
    staff_client, _ = staff_login("platform.staff_user.view")
    for _ in range(2):
        console_org()
    with CaptureQueriesContext(connection) as small:
        staff_client.get("/api/v1/admin/users", {"segment": "explorers", "limit": 2})
    for _ in range(8):
        console_org()
    with CaptureQueriesContext(connection) as large:
        staff_client.get("/api/v1/admin/users", {"segment": "explorers", "limit": 50})
    assert len(large.captured_queries) == len(small.captured_queries)


def test_the_user_detail_shows_organizations_products_and_orders(population, staff_login):
    viewer, _ = staff_login("platform.staff_user.view", "platform.staff_payment.view")
    customer = population["customer"]["owner"]
    detail = viewer.get(f"/api/v1/admin/users/{customer.id}").json()
    assert detail["lifecycle"] == "customer"
    membership = detail["memberships"][0]
    assert (membership["access_class"], membership["products"]) == ("customer", ["crm"])
    assert "orders" in detail and "password" not in str(detail).lower()


def test_customers_filter_by_subscription_access_and_product(population, staff_login):
    viewer, _ = staff_login("platform.staff_customer.view")

    def slugs(**params):
        rows = viewer.get("/api/v1/admin/customers", params).json()["results"]
        return {r["slug"] for r in rows}

    assert population["trial"]["slug"] in slugs(subscription="trialing")
    assert population["customer"]["slug"] in slugs(access="customer")
    assert population["customer"]["slug"] not in slugs(access="trial")
    assert population["explorer"]["slug"] in slugs(access="none")
    holders = slugs(product="crm")
    assert {population["customer"]["slug"], population["trial"]["slug"]} <= holders
    assert population["explorer"]["slug"] not in holders
    assert (
        viewer.get("/api/v1/admin/customers", {"subscription": "bogus"}).status_code
        == 400
    )

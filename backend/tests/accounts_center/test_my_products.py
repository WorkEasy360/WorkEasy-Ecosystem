"""My Products and Organizations on WorkEasy360 Accounts (ADR-072): the
person's products across all their organizations, assembled from the
launcher's own directory per organization - never a second entitlement
computation - and "Open" decided by the launcher's own re-check and route."""

from __future__ import annotations

import pytest

from tests.accounts_center.conftest import ACCOUNT_API

pytestmark = pytest.mark.django_db

PRODUCTS = f"{ACCOUNT_API}/products"


def _launch(org_id: str, code: str) -> str:
    return f"{PRODUCTS}/{org_id}/{code}/launch"


@pytest.fixture
def two_orgs(person, organization_factory, entitle_products, register_product):
    """The person owns Acme (CRM, with a seat) and Beta (CRM without a seat
    for them, and Books, which every member of Beta can use)."""
    register_product("crm")
    register_product("books")
    beta, beta_membership, _ = organization_factory(owner=person["user"])
    # One complete desired state (the sole entitlement writer replaces it per
    # call); no seat pool, so nobody in Beta holds a CRM seat.
    entitle_products(beta.id, {"crm": {}, "books": {}})
    return {**person, "beta": beta, "beta_id": str(beta.id)}


def _org(body: dict, organization_id: str) -> dict:
    (found,) = [
        o for o in body["organizations"] if o["organization_id"] == organization_id
    ]
    return found


def _app(org: dict, code: str) -> dict:
    (found,) = [a for a in org["apps"] if a["product_code"] == code]
    return found


def test_every_organization_with_the_launchers_own_answer(
    two_orgs, accounts_on, on_accounts
):
    accounts_on()
    body = on_accounts(two_orgs["user"]).get(PRODUCTS).json()
    acme = _org(body, two_orgs["organization_id"])
    beta = _org(body, two_orgs["beta_id"])
    assert acme["restricted"] is None and acme["is_owner"] is True
    assert _app(acme, "crm")["state"] == "active"  # held, seat assigned
    assert _app(acme, "crm")["entitlement_status"] == "active"
    assert _app(acme, "books")["state"] == "no_entitlement"
    assert _app(beta, "crm")["state"] == "no_seat"  # purchased, not assigned to them
    assert _app(beta, "books")["state"] == "active"  # organization-wide
    # The directory's own wire shape: never a launch URL.
    assert "launch_url" not in str(body) and "localhost:9100" not in str(body)
    assert body["app_origin"].startswith("https://app.")


def test_open_is_the_server_built_launch_url_with_the_organization(
    two_orgs, accounts_on, on_accounts
):
    accounts_on()
    me = on_accounts(two_orgs["user"])
    opened = me.post(_launch(two_orgs["organization_id"], "crm"))
    assert opened.status_code == 200, opened.content
    assert opened.json()["redirect_url"] == (
        f"http://localhost:9100/launch?organization={two_orgs['organization_id']}"
    )
    assert opened.json()["sign_in"] == "workeasy360"
    # Only when access is granted: no seat in Beta, so no launch.
    refused = me.post(_launch(two_orgs["beta_id"], "crm"))
    assert (refused.status_code, refused.json()["state"]) == (409, "no_seat")
    unknown = me.post(_launch(two_orgs["organization_id"], "not-a-product"))
    assert unknown.status_code == 409 and "redirect_url" not in unknown.json()


def test_another_persons_organization_cannot_be_listed_or_opened(
    two_orgs, user_factory, accounts_on, on_accounts
):
    stranger = user_factory()
    accounts_on()
    theirs = on_accounts(stranger)
    assert theirs.get(PRODUCTS).json()["organizations"] == []
    refused = theirs.post(_launch(two_orgs["organization_id"], "crm"))
    assert refused.status_code == 404 and "redirect_url" not in refused.json()


def test_an_organization_that_refuses_this_session_is_shown_restricted(
    two_orgs, accounts_on, on_accounts
):
    from workeasy360.platform.organizations.models import Organization

    Organization.objects.filter(id=two_orgs["beta"].id).update(
        ip_allowlist_enabled=True, ip_allowlist=["10.0.0.0/8"]
    )
    accounts_on()
    me = on_accounts(two_orgs["user"], REMOTE_ADDR="203.0.113.50")
    beta = _org(me.get(PRODUCTS).json(), two_orgs["beta_id"])
    assert beta["restricted"] == "network_not_allowed" and beta["apps"] == []
    refused = me.post(_launch(two_orgs["beta_id"], "books"))
    assert (refused.status_code, refused.json()["code"]) == (403, "network_not_allowed")
    # Acme has no such policy and is unaffected.
    assert (
        _org(me.get(PRODUCTS).json(), two_orgs["organization_id"])["restricted"] is None
    )


def test_the_roles_come_from_the_organization(console_org, accounts_on, on_accounts):
    org = console_org()
    member = org["add"]("member")
    accounts_on()
    body = on_accounts(member["user"]).get(PRODUCTS).json()
    (only,) = body["organizations"]
    assert only["roles"] == ["member"] and only["is_owner"] is False
    assert only["may_assign_seats"] is False


@pytest.mark.parametrize(
    "path, method",
    [
        (PRODUCTS, "get"),
        (f"{ACCOUNT_API}/connections", "get"),
        (f"{ACCOUNT_API}/sessions", "get"),
        (f"{ACCOUNT_API}/history", "get"),
        (f"{ACCOUNT_API}/profile", "get"),
        (f"{ACCOUNT_API}/connections/crm/sign-out", "post"),
        (_launch("11111111-1111-4111-8111-111111111111", "crm"), "post"),
    ],
)
def test_with_identity_off_the_control_center_does_not_exist(
    person, accounts_on, on_accounts, path, method
):
    accounts_on(False)
    response = getattr(on_accounts(person["user"]), method)(path)
    assert response.status_code == 404


def test_it_is_served_on_workeasy360_accounts_only(person, accounts_on, on_app):
    accounts_on()
    assert on_app(person["user"]).get(PRODUCTS).status_code == 404


def test_signed_out_is_refused(accounts_on):
    from tests.accounts_center.conftest import OnHost
    from tests.accounts_portal.conftest import ACCOUNTS

    accounts_on()
    assert OnHost(ACCOUNTS).get(PRODUCTS).status_code == 401

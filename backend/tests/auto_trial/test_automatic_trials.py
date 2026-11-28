"""Automatic free trials (2026-11-28 brief): Signup -> Select Product -> Start Free
Trial -> Open Product, with no Super Admin step, no manual seat and no manual
organization.

Driven through the real HTTP surface (sessions, idempotency keys) with the four
real product codes, each with a priced plan, a configured trial and an active
integration - the same fixture the Phase 11 and portal suites use.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.extension.conftest import PASSWORD, place_and_pay, staff_scope, url
from tests.phase11 import conftest as _phase11
from tests.phase11 import test_11i_all_four_products as _four
from tests.phase11.conftest import audit_of, tenant_rows
from workeasy360.platform.entitlements.selectors import decision_for_organization

pytestmark = pytest.mark.django_db

console_org = _phase11.console_org
fake_payments = _phase11.fake_payments
login = _phase11.login
four_products = _four.four_products
shop_for = _four.shop_for

FOUR_PRODUCTS = _four.FOUR_PRODUCTS


# --- helpers ----------------------------------------------------------------------------


def _access(client, slug):
    answer = client.get(url(slug, "product-access"))
    assert answer.status_code == 200, answer.content
    return answer.json()["products"]


def _activate(client, slug, code, *, key=None, plan_code=None):
    body = {"plan_code": plan_code} if plan_code else {}
    return client.post(
        url(slug, f"product-access/{code}/trial"),
        body,
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=key or uuid.uuid4().hex,
    )


def _repair(client, slug, code):
    return client.post(url(slug, f"product-access/{code}/repair"))


def _launch(client, slug, code):
    return client.post(url(slug, f"apps/{code}/launch"), HTTP_ACCEPT="application/json")


def _workspace(client, **body):
    return client.post(
        "/api/v1/organizations/workspace", body, content_type="application/json"
    )


def _seats(organization_id, code):
    from workeasy360.platform.entitlements.models import SeatAssignment

    return tenant_rows(
        SeatAssignment,
        organization_id,
        seat_grant__pool_key=code,
        released_at__isnull=True,
    )


def _trials(organization_id, code):
    from workeasy360.commercial.subscriptions.models import ProductTrial

    return tenant_rows(ProductTrial, organization_id, product_code=code)


@pytest.fixture
def new_customer(four_products, user_factory, login, fake_payments):
    """A person who has just signed up: an account, no organization."""

    def make(full_name="Asha Rao"):
        user = user_factory(password=PASSWORD, full_name=full_name)
        return {"user": user, "client": login(user)}

    return make


# --- 1. a completely new customer --------------------------------------------------------


def test_a_new_customer_goes_from_signup_to_an_open_product_with_nobody_else(
    new_customer,
):
    """No organization, no seat, no Super Admin: one workspace call (what the
    Store makes when the person has none), one Start Free Trial, then Open."""
    from workeasy360.platform.organizations.models import Membership

    person = new_customer()
    client, user = person["client"], person["user"]
    assert client.get("/api/v1/organizations").json()["organizations"] == []

    made = _workspace(client, idempotency_key=uuid.uuid4().hex)
    assert made.status_code == 201, made.content
    workspace = made.json()
    assert workspace["created"] is True
    assert workspace["display_name"] == "Asha's Workspace"
    slug, org_id = workspace["slug"], uuid.UUID(workspace["organization_id"])

    # The person owns it, with the real owner role - and nothing at platform level.
    (membership,) = tenant_rows(Membership, org_id, user_id=user.id)
    assert membership.is_owner is True
    permissions = client.get(url(slug, "access")).json()["permissions"]
    assert "commercial.subscription.manage" in permissions  # the owner role's
    assert "platform.seat.assign" in permissions
    user.refresh_from_db()
    assert user.is_staff is False

    cards = _access(client, slug)
    assert {cards[c]["action"] for c in FOUR_PRODUCTS} == {"start_trial"}

    started = _activate(client, slug, "easy_crm")
    assert started.status_code == 200, started.content
    body = started.json()
    assert body["access"]["action"] == "open", body
    assert body["access"]["holding"] == "trial"
    assert body["access"]["trial_days_left"] == 14
    assert body["trial"]["status"] == "active"

    # Everything the brief lists happened by itself.
    trial = _trials(org_id, "easy_crm")[0]
    assert trial.ends_at - trial.started_at == timedelta(days=14)
    assert trial.started_by_user_id == user.id
    assert decision_for_organization(org_id, "easy_crm").status.value == "trial"
    assert [s.membership_id for s in _seats(org_id, "easy_crm")] == [membership.id]
    actions = {
        row["action"] for row in audit_of(trial.id, "action", organization_id=org_id)
    }
    assert {
        "commercial.product_trial.started",
        "commercial.product_trial.activation",
    } <= actions

    launched = _launch(client, slug, "easy_crm")
    assert launched.status_code == 200, launched.content
    # The registered product's own address (this fixture's integrations have no
    # OIDC client yet; the SSO hand-off itself is the universal_sso suites').
    assert launched.json()["redirect_url"].startswith("https://easy_crm.example.com/")


def test_the_workspace_is_created_once_however_often_it_is_asked_for(new_customer):
    person = new_customer()
    client = person["client"]
    first = _workspace(client, idempotency_key="k1")
    again = _workspace(client, idempotency_key="k1")
    other = _workspace(client, idempotency_key="k2")
    bare = _workspace(client)
    assert first.status_code == 201
    assert {r.status_code for r in (again, other, bare)} == {200}
    assert {r.json()["slug"] for r in (first, again, other, bare)} == {
        first.json()["slug"]
    }
    assert len(client.get("/api/v1/organizations").json()["organizations"]) == 1


def test_a_workspace_takes_the_name_the_customer_typed(new_customer):
    client = new_customer()["client"]
    made = _workspace(client, name="Rao Traders")
    assert made.status_code == 201 and made.json()["display_name"] == "Rao Traders"


def test_a_customer_with_several_organizations_chooses_one(console_org, login):
    from workeasy360.platform.onboarding.services import create_organization

    person = console_org()["add"]("member")["user"]
    create_organization(user_id=person.id, name="Second")
    answer = _workspace(login(person))
    assert answer.status_code == 400, answer.content
    assert answer.json()["reason"] == "organization_required"


def test_signing_in_is_required_for_a_workspace(client):
    assert _workspace(client).status_code in (401, 403)


# --- 2. an existing customer with an organization ------------------------------------------


def test_an_existing_organization_trials_several_products_independently(shop_for):
    shop = shop_for()
    client, slug = shop["client"], shop["slug"]
    for code in ("easybook", "easy_helpdesk"):
        answer = _activate(client, slug, code)
        assert answer.status_code == 200, answer.content
        assert answer.json()["access"]["action"] == "open"
    cards = _access(client, slug)
    assert cards["easybook"]["action"] == "open"
    assert cards["easy_helpdesk"]["action"] == "open"
    assert cards["easy_crm"]["action"] == "start_trial"
    assert cards["easy_project"]["action"] == "start_trial"
    for code in ("easybook", "easy_helpdesk"):
        assert [s.membership_id for s in _seats(shop["id"], code)] == [
            shop["membership"].id
        ]
    # A trial card also offers to upgrade; a paid product does not.
    assert cards["easybook"]["upgrade_available"] is True


def test_without_a_plan_the_products_trial_plan_is_used(shop_for, four_products):
    shop = shop_for()
    answer = _activate(shop["client"], shop["slug"], "easy_project")
    assert answer.status_code == 200, answer.content
    assert (
        answer.json()["trial"]["plan_code"] == four_products["easy_project"]["plan_code"]
    )


# --- 3. duplicates ---------------------------------------------------------------------------


def test_duplicate_requests_make_one_trial_and_never_an_error(shop_for):
    shop = shop_for()
    client, slug = shop["client"], shop["slug"]
    key = uuid.uuid4().hex
    first = _activate(client, slug, "easy_crm", key=key)
    replay = _activate(client, slug, "easy_crm", key=key)
    other_tab = _activate(client, slug, "easy_crm")
    assert {r.status_code for r in (first, replay, other_tab)} == {200}
    assert {r.json()["trial"]["id"] for r in (first, replay, other_tab)} == {
        first.json()["trial"]["id"]
    }
    assert {r.json()["access"]["action"] for r in (first, replay, other_tab)} == {"open"}
    assert len(_trials(shop["id"], "easy_crm")) == 1
    assert len(_seats(shop["id"], "easy_crm")) == 1


# --- 4. failed provisioning and recovery -------------------------------------------------


@pytest.fixture
def broken_provisioning(monkeypatch):
    """Every provisioning pass fails: the provisioning listener every commercial
    change notifies (the trial start's and Retry Setup's alike). ``restore()``
    mends it."""
    from workeasy360.commercial.subscriptions import hooks

    original = list(hooks._listeners)

    def boom(*args, **kwargs):
        raise RuntimeError("provisioning is down")

    monkeypatch.setattr(
        hooks,
        "_listeners",
        [(n, boom if n == "commercial.provisioning" else f) for n, f in original],
    )

    def restore():
        monkeypatch.setattr(hooks, "_listeners", original)

    return restore


def test_failed_provisioning_says_retry_setup_and_retry_finishes_it(
    shop_for, broken_provisioning, monkeypatch
):
    shop = shop_for()
    client, slug = shop["client"], shop["slug"]

    started = _activate(client, slug, "easy_crm")
    assert started.status_code == 200, started.content
    access = started.json()["access"]
    assert access["action"] == "retry_setup" and access["reason"] == "provisioning_failed"
    assert len(_trials(shop["id"], "easy_crm")) == 1  # the trial itself is kept
    assert not decision_for_organization(shop["id"], "easy_crm").allowed
    assert _launch(client, slug, "easy_crm").status_code == 409  # never "ready" early

    # Read right after: still being set up. Minutes later: Retry Setup.
    assert _access(client, slug)["easy_crm"]["action"] == "setting_up"
    later = timezone.now() + timedelta(minutes=3)
    monkeypatch.setattr(timezone, "now", lambda: later)
    assert _access(client, slug)["easy_crm"]["action"] == "retry_setup"

    # Still broken: retrying says so again, without a second trial.
    still = _repair(client, slug, "easy_crm")
    assert still.status_code == 200
    assert still.json()["access"]["action"] == "retry_setup"

    # Mended: one Retry Setup provisions, seats the starter, and it opens.
    broken_provisioning()
    fixed = _repair(client, slug, "easy_crm")
    assert fixed.status_code == 200, fixed.content
    assert fixed.json()["access"]["action"] == "open"
    assert [s.membership_id for s in _seats(shop["id"], "easy_crm")] == [
        shop["membership"].id
    ]
    assert _launch(client, slug, "easy_crm").status_code == 200
    assert len(_trials(shop["id"], "easy_crm")) == 1


def test_a_trial_started_before_automatic_seating_is_repaired(shop_for):
    """A trial whose starter never got a seat (the old best-effort step lost
    it): the card says Retry Setup, and one click seats them."""
    from workeasy360.platform.entitlements.models import SeatAssignment

    shop = shop_for()
    client, slug = shop["client"], shop["slug"]
    assert _activate(client, slug, "easybook").json()["access"]["action"] == "open"
    for seat in _seats(shop["id"], "easybook"):
        from workeasy360.platform.tenancy.db import organization_db_scope
        from workeasy360.platform.tenancy.unscoped import unscoped_access

        with (
            unscoped_access(reason="Test releases a seat", module="tests"),
            organization_db_scope(shop["id"]),
        ):
            SeatAssignment.unscoped.filter(id=seat.id).update(released_at=timezone.now())
    card = _access(client, slug)["easybook"]
    assert card["action"] == "retry_setup" and card["reason"] == "seat_missing"
    assert _repair(client, slug, "easybook").json()["access"]["action"] == "open"


# --- 5. expiry and conversion ------------------------------------------------------------


def test_expiry_offers_upgrade_and_buying_restores_access_with_the_same_seat(
    shop_for, four_products, login, monkeypatch
):
    shop = shop_for()
    client, slug = shop["client"], shop["slug"]
    assert _activate(client, slug, "easy_crm").json()["access"]["action"] == "open"
    trial = _trials(shop["id"], "easy_crm")[0]

    ended = trial.ends_at + timedelta(minutes=1)
    monkeypatch.setattr(timezone, "now", lambda: ended)
    owner = login(shop["owner"])
    card = _access(owner, slug)["easy_crm"]
    assert card["action"] == "upgrade" and card["reason"] == "trial_ended"
    assert card["trial_outcome"] == "expired"
    assert _launch(owner, slug, "easy_crm").status_code in (402, 403, 409)
    # Starting again is refused: one trial per organization and product.
    again = _activate(owner, slug, "easy_crm")
    assert again.status_code == 409 and again.json()["reason"] == "already_used"

    order = place_and_pay(
        owner,
        slug,
        shop["id"],
        plan_version_id=str(four_products["easy_crm"]["plan_version_id"]),
    )
    assert order["status"] == "fulfilled", order
    card = _access(owner, slug)["easy_crm"]
    assert card["action"] == "open" and card["holding"] == "subscription"
    assert card["upgrade_available"] is False
    # The seat the trial gave is still the starter's: nobody re-assigned it.
    assert [s.membership_id for s in _seats(shop["id"], "easy_crm")] == [
        shop["membership"].id
    ]
    assert _launch(owner, slug, "easy_crm").status_code == 200


def test_buying_during_a_trial_converts_it_and_keeps_access(shop_for, four_products):
    shop = shop_for()
    client, slug = shop["client"], shop["slug"]
    _activate(client, slug, "easy_helpdesk")
    place_and_pay(
        client,
        slug,
        shop["id"],
        plan_version_id=str(four_products["easy_helpdesk"]["plan_version_id"]),
    )
    card = _access(client, slug)["easy_helpdesk"]
    assert card["action"] == "open" and card["holding"] == "subscription"
    assert len(_trials(shop["id"], "easy_helpdesk")) == 1


# --- 6. permissions and isolation --------------------------------------------------------


def test_a_plain_member_cannot_start_a_trial_or_seat_themselves(shop_for, login):
    shop = shop_for()
    member = shop["add"]("member")["user"]
    other = login(member)
    assert _access(other, shop["slug"])["easy_crm"]["action"] == "ask_owner"
    refused = _activate(other, shop["slug"], "easy_crm")
    assert refused.status_code == 403, refused.content
    assert _trials(shop["id"], "easy_crm") == []

    # The owner starts it: the owner is seated, the member is not - and the
    # member's Retry cannot take a seat for themselves.
    _activate(shop["client"], shop["slug"], "easy_crm")
    card = _access(other, shop["slug"])["easy_crm"]
    assert card["action"] == "ask_owner" and card["reason"] == "no_seat"
    assert _repair(other, shop["slug"], "easy_crm").json()["access"]["action"] == (
        "ask_owner"
    )
    assert [s.membership_id for s in _seats(shop["id"], "easy_crm")] == [
        shop["membership"].id
    ]


def test_another_organization_is_out_of_reach(shop_for):
    mine, theirs = shop_for(), shop_for()
    for answer in (
        mine["client"].get(url(theirs["slug"], "product-access")),
        _activate(mine["client"], theirs["slug"], "easy_crm"),
        _repair(mine["client"], theirs["slug"], "easy_crm"),
    ):
        assert answer.status_code in (403, 404), answer.content
    assert _trials(theirs["id"], "easy_crm") == []
    assert _access(theirs["client"], theirs["slug"])["easy_crm"]["action"] == (
        "start_trial"
    )


def test_a_product_with_nowhere_to_open_is_not_offered_a_trial(shop_for, four_products):
    from workeasy360.platform.ecosystem import services as ecosystem_services

    with staff_scope("platform.staff_integration.manage") as ctx:
        ecosystem_services.transition(
            ctx, client_id=four_products["easy_project"]["client_id"], to_status="retired"
        )
    shop = shop_for()
    card = _access(shop["client"], shop["slug"])["easy_project"]
    assert card["action"] == "buy"  # a priced plan is still on sale
    refused = _activate(shop["client"], shop["slug"], "easy_project")
    assert refused.status_code == 409 and refused.json()["reason"] == "product_not_ready"
    assert _trials(shop["id"], "easy_project") == []


def test_a_product_the_listing_does_not_know_is_not_found(shop_for):
    shop = shop_for()
    assert _repair(shop["client"], shop["slug"], "no_such_product").status_code == 404
    assert _activate(shop["client"], shop["slug"], "no_such_product").status_code == 409


# --- 7. the Store host may use it, and nothing more ----------------------------------------


@pytest.mark.parametrize(
    ("path", "on_store"),
    [
        ("/api/v1/orgs/acme/product-access", True),
        ("/api/v1/orgs/acme/product-access/easy_crm/trial", True),
        ("/api/v1/orgs/acme/product-access/easy_crm/repair", True),
        ("/api/v1/orgs/acme/apps/easy_crm/launch", True),
        ("/api/v1/orgs/acme/apps/easy_crm/seats", False),
        ("/api/v1/orgs/acme/apps", False),
        ("/api/v1/orgs/acme/members", False),
    ],
)
def test_the_store_host_serves_product_access_and_launch_only(path, on_store):
    from workeasy360.contracts.values import portals
    from workeasy360.interfaces.api.portal_surface import allowed_portals

    hosts = allowed_portals(path) or frozenset()
    assert (portals.STORE in hosts) is on_store
    assert portals.APP in hosts

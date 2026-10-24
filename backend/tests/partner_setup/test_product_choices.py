"""The two per-product choices a partner holds: may its customers start the
platform trial, and is the product shown on its portal.

Both only ever *narrow*. A trial the platform never configured stays absent, a
trial already started is untouched, a customer that holds a product still
launches it whatever the portal shows, and a direct customer (or a product the
partner does not sell) is never affected.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.partner_setup.conftest import VIEW, patch
from workeasy360.commercial.partners import branding, services
from workeasy360.commercial.partners.models import PartnerProductGrant
from workeasy360.contracts.errors import (
    PartnerValidationError,
    PermissionDenied,
)

pytestmark = pytest.mark.django_db


def grants_url(partner_id, grant_id=None):
    base = f"/api/v1/admin/partners/{partner_id}/product-grants"
    return f"{base}/{grant_id}" if grant_id else base


def grant_of(partner_id, product="crm"):
    return PartnerProductGrant.objects.get(
        partner_id=partner_id, product__code=product, status="active"
    )


# --- the grant carries the two choices ---------------------------------------------------------------


def test_a_new_grant_defaults_to_trial_allowed_and_portal_visible(channel):
    grant = grant_of(channel["partner"].id)
    assert grant.trial_allowed is True and grant.portal_visible is True


def test_a_grant_can_be_made_with_its_choices(partner_factory, catalog):
    fx = partner_factory("subscription.sell")
    grant = services.grant_product(
        fx["staff_ctx"],
        fx["partner"].id,
        product_code="crm",
        trial_allowed=False,
        portal_visible=False,
    )
    assert (grant.trial_allowed, grant.portal_visible) == (False, False)


def test_granting_again_with_a_different_choice_changes_it_once_and_audits_it(channel):
    from tests.phase11.conftest import audit_of

    ctx, pid = channel["staff_ctx"], channel["partner"].id
    first = grant_of(pid)
    again = services.grant_product(ctx, pid, product_code="crm", trial_allowed=False)
    assert again.id == first.id and grant_of(pid).trial_allowed is False
    services.grant_product(ctx, pid, product_code="crm", trial_allowed=False)  # a retry
    updates = [
        r
        for r in audit_of(first.id, "action", "changes")
        if r["action"] == "partner.product_grant_updated"
    ]
    assert len(updates) == 1 and updates[0]["changes"]["trial_allowed"] is False


@pytest.mark.parametrize("bad", ["yes", 1, 0, "false", [], {}])
def test_a_choice_must_be_a_real_boolean(channel, bad):
    ctx, pid = channel["staff_ctx"], channel["partner"].id
    with pytest.raises(PartnerValidationError):
        services.grant_product(ctx, pid, product_code="crm", trial_allowed=bad)
    with pytest.raises(PartnerValidationError):
        services.update_product_grant(
            ctx, pid, grant_id=grant_of(pid).id, portal_visible=bad
        )


# --- the endpoint -------------------------------------------------------------------------------------------


def test_a_manager_changes_the_choices(manager, channel):
    pid = channel["partner"].id
    grant = grant_of(pid)
    response = patch(manager, grants_url(pid, grant.id), {"portal_visible": False})
    assert response.status_code == 200, response.content
    assert response.json() == {
        "id": str(grant.id),
        "trial_allowed": True,
        "portal_visible": False,
    }
    grant.refresh_from_db()
    assert grant.portal_visible is False and grant.trial_allowed is True


def test_the_new_choices_show_in_the_partner_reads(manager, channel):
    pid = channel["partner"].id
    patch(manager, grants_url(pid, grant_of(pid).id), {"trial_allowed": False})
    detail = manager.get(f"/api/v1/admin/partners/{pid}").json()
    assert detail["product_grants"][0]["trial_allowed"] is False
    summary = manager.get(f"/api/v1/admin/partners/{pid}/summary").json()
    assert summary["products"][0]["trial_allowed"] is False
    assert summary["products"][0]["portal_visible"] is True


def test_an_empty_or_unknown_body_changes_nothing(manager, channel):
    pid = channel["partner"].id
    url = grants_url(pid, grant_of(pid).id)
    assert patch(manager, url, {}).status_code == 400
    assert (
        patch(
            manager, url, {"status": "revoked", "partner_id": str(uuid.uuid4())}
        ).status_code
        == 400
    )
    assert grant_of(pid).trial_allowed is True


def test_a_grant_of_another_partner_is_not_found_under_this_one(
    manager, channel, partner_factory
):
    other = partner_factory("subscription.sell")
    services.grant_product(other["staff_ctx"], other["partner"].id, product_code="crm")
    theirs = grant_of(other["partner"].id)
    response = patch(
        manager, grants_url(channel["partner"].id, theirs.id), {"trial_allowed": False}
    )
    assert response.status_code == 404
    assert grant_of(other["partner"].id).trial_allowed is True


def test_the_choices_need_manage_and_no_partner_may_set_them(
    staff_client, partner_client, channel
):
    pid = channel["partner"].id
    url = grants_url(pid, grant_of(pid).id)
    viewer, _u = staff_client(VIEW)
    assert patch(viewer, url, {"trial_allowed": False}).status_code == 403
    own, fx = partner_client("subscription.sell")
    services.grant_product(fx["staff_ctx"], fx["partner"].id, product_code="crm")
    assert (
        patch(
            own,
            grants_url(fx["partner"].id, grant_of(fx["partner"].id).id),
            {"trial_allowed": False},
        ).status_code
        == 403
    )
    assert grant_of(pid).trial_allowed is True


def test_the_service_authorizes_in_the_service(channel, staff_factory):
    pid = channel["partner"].id
    grant_id = grant_of(pid).id
    _v, viewer = staff_factory(VIEW)
    with pytest.raises(PermissionDenied):
        services.update_product_grant(viewer, pid, grant_id=grant_id, trial_allowed=False)


def test_a_terminated_partner_cannot_be_changed(channel):
    from workeasy360.contracts.enums import PartnerStatus

    ctx, pid = channel["staff_ctx"], channel["partner"].id
    grant_id = grant_of(pid).id
    services.set_partner_status(
        ctx, pid, target=PartnerStatus.TERMINATED, reason="Ended for the test"
    )
    with pytest.raises(PartnerValidationError):
        services.update_product_grant(ctx, pid, grant_id=grant_id, trial_allowed=False)


# --- the trial gate -------------------------------------------------------------------------------------------


@pytest.fixture
def customer(console_org, login, channel, catalog):
    """An organization linked to the reseller, its owner signed in, and the CRM
    trial configured by the platform (14 days on ``crm_starter``)."""
    from tests.phase11.conftest import configure_trial

    configure_trial(catalog)
    org = console_org()
    services.link_customer(
        channel["staff_ctx"],
        channel["partner"].id,
        org["org"].id,
        reason="Customer of the reseller",
    )
    return {
        **org,
        "client": login(org["owner"]),
        "partner": channel["partner"],
        "ctx": channel["staff_ctx"],
    }


def trial(customer, **kw):
    from tests.partner_setup.conftest import start_trial

    return start_trial(customer, **kw)


def test_a_partners_customer_may_trial_a_product_by_default(customer):
    response = trial(customer)
    assert response.status_code == 201, response.content


def test_a_partner_can_turn_a_trial_off_for_its_customers(customer):
    pid = customer["partner"].id
    services.update_product_grant(
        customer["ctx"], pid, grant_id=grant_of(pid).id, trial_allowed=False
    )
    response = trial(customer)
    assert response.status_code == 409
    assert response.json()["reason"] == "not_offered"


def test_turning_it_back_on_restores_the_platform_trial(customer):
    pid = customer["partner"].id
    grant = grant_of(pid)
    services.update_product_grant(
        customer["ctx"], pid, grant_id=grant.id, trial_allowed=False
    )
    services.update_product_grant(
        customer["ctx"], pid, grant_id=grant.id, trial_allowed=True
    )
    assert trial(customer).status_code == 201


def test_a_direct_customer_is_never_affected(console_org, login, channel, catalog):
    from tests.phase11.conftest import configure_trial

    configure_trial(catalog)
    pid = channel["partner"].id
    services.update_product_grant(
        channel["staff_ctx"], pid, grant_id=grant_of(pid).id, trial_allowed=False
    )
    direct = console_org()  # linked to no partner
    shop = {**direct, "client": login(direct["owner"])}
    assert trial(shop).status_code == 201


def test_a_product_the_partner_does_not_sell_is_not_gated_by_it(customer, catalog):
    from tests.phase11.conftest import configure_trial

    configure_trial(catalog, product="books", plans=("books_professional",))
    pid = customer["partner"].id
    services.update_product_grant(
        customer["ctx"], pid, grant_id=grant_of(pid).id, trial_allowed=False
    )
    response = trial(customer, product="books", plan="books_professional")
    assert response.status_code == 201, response.content


def test_a_trial_that_already_started_keeps_running_when_the_partner_turns_trials_off(
    customer,
):
    from workeasy360.platform.entitlements.selectors import decision_for_organization

    assert trial(customer).status_code == 201
    pid = customer["partner"].id
    services.update_product_grant(
        customer["ctx"], pid, grant_id=grant_of(pid).id, trial_allowed=False
    )
    assert decision_for_organization(customer["org"].id, "crm") is not None
    listing = customer["client"].get(f"/api/v1/orgs/{customer['slug']}/trials").json()
    assert listing["results"][0]["status"] == "active"


def test_the_refusal_comes_after_the_permission_check(customer, login, console_org):
    pid = customer["partner"].id
    services.update_product_grant(
        customer["ctx"], pid, grant_id=grant_of(pid).id, trial_allowed=False
    )
    outsider = login(console_org()["owner"])  # an owner of some other organization
    response = outsider.post(
        f"/api/v1/orgs/{customer['slug']}/trials",
        json.dumps({"product_code": "crm", "plan_code": "crm_starter"}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert response.status_code in (401, 403, 404)
    assert "not_offered" not in response.content.decode()


def explore(customer):
    response = customer["client"].get(f"/api/v1/orgs/{customer['slug']}/explore")
    assert response.status_code == 200
    return {p["code"]: p for p in response.json()["products"]}


def test_explore_stops_offering_a_trial_the_partner_turned_off(customer):
    assert explore(customer)["crm"]["trial_state"] == "available"
    pid = customer["partner"].id
    services.update_product_grant(
        customer["ctx"], pid, grant_id=grant_of(pid).id, trial_allowed=False
    )
    crm = explore(customer)["crm"]
    assert crm["trial_state"] == "unavailable" and crm["trial"]["available"] is False
    assert all(plan["trial_available"] is False for plan in crm["plans"])


# --- portal visibility -----------------------------------------------------------------------------------------


@pytest.fixture
def white_label_customer(console_org, login, partner_factory, catalog):
    """A customer of a partner that is genuinely white-label: both capabilities and a brand."""
    from tests.phase11.conftest import configure_trial

    configure_trial(catalog)
    fx = partner_factory("branding.manage", "domain.manage", "subscription.sell")
    services.grant_product(
        fx["staff_ctx"], fx["partner"].id, product_code="crm", portal_visible=False
    )
    branding.update_brand(
        fx["staff_ctx"], partner_id=fx["partner"].id, brand_name="Acme Cloud"
    )
    org = console_org()
    services.link_customer(
        fx["staff_ctx"], fx["partner"].id, org["org"].id, reason="White-label customer"
    )
    return {
        **org,
        "client": login(org["owner"]),
        "partner": fx["partner"],
        "ctx": fx["staff_ctx"],
    }


def test_a_white_label_partner_can_hide_a_product_from_its_portal(white_label_customer):
    listing = explore(white_label_customer)
    assert "crm" not in listing
    assert listing, "other products are still discoverable"


def test_a_product_the_customer_already_holds_is_never_hidden(white_label_customer):
    # Starting a trial gives the customer a holding; hiding it would hide what they have.
    assert trial(white_label_customer).status_code == 201
    assert "crm" in explore(white_label_customer)


def test_visibility_is_display_only_it_never_blocks_a_launch_or_a_trial(
    white_label_customer,
):
    assert trial(white_label_customer).status_code == 201


def test_a_partner_that_is_not_white_label_hides_nothing(customer):
    pid = customer["partner"].id
    services.update_product_grant(
        customer["ctx"], pid, grant_id=grant_of(pid).id, portal_visible=False
    )
    assert "crm" in explore(customer)


def test_without_a_brand_the_partner_is_not_white_label_and_hides_nothing(
    console_org, login, partner_factory, catalog
):
    fx = partner_factory("branding.manage", "domain.manage", "subscription.sell")
    services.grant_product(
        fx["staff_ctx"], fx["partner"].id, product_code="crm", portal_visible=False
    )
    org = console_org()
    services.link_customer(
        fx["staff_ctx"], fx["partner"].id, org["org"].id, reason="No brand yet"
    )
    shop = {**org, "client": login(org["owner"])}
    assert "crm" in explore(shop)

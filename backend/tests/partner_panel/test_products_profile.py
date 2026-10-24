"""Product switches, the plans/pricing reads, the partner's own profile and the
read-only statements (authentication, communication)."""

from __future__ import annotations

import uuid

import pytest

from tests.partner_panel.conftest import PASSWORD, api, send
from workeasy360.commercial.partners import selectors, services
from workeasy360.commercial.partners.models import PartnerProductSetting
from workeasy360.contracts.errors import PermissionDenied

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner(seller, owner_client):
    return owner_client, seller


# --- products ------------------------------------------------------------------------------


def test_the_product_list_is_the_granted_products_with_the_partners_own_switches(owner):
    client, fx = owner
    body = client.get(api(fx["partner"].id, "products")).json()
    [crm] = [p for p in body["products"] if p["code"] == "crm"]
    assert crm["enabled_for_sale"] is True and crm["show_in_explore"] is True
    assert crm["grant_scope"] == "all_plans"
    assert body["can_manage"] is True
    assert all(
        p["code"] != "books" for p in body["products"]
    )  # not granted -> not listed as owned


def test_disabling_a_product_for_new_sales_stops_the_sale_and_ends_nothing(owner):
    from tests.phase7.support import provision, sell

    client, fx = owner
    pid = fx["partner"].id
    result, _o = provision(fx, name="Existing Customer")
    sell(fx, result.organization_id)
    off = send(client, "put", api(pid, "products/crm"), {"enabled_for_sale": False})
    assert off.status_code == 200 and off.json()["enabled_for_sale"] is False
    assert (
        selectors.may_sell(pid, product_code="crm", plan_code="crm_professional") is False
    )
    assert (
        selectors.may_sell_ignoring_switch(
            pid, product_code="crm", plan_code="crm_professional"
        )
        is True
    )
    other, _o2 = provision(fx, name="New Customer")
    with pytest.raises(PermissionDenied):
        sell(fx, other.organization_id)
    # The existing customer still holds and is counted.
    body = client.get(api(pid, "products")).json()
    [crm] = [p for p in body["products"] if p["code"] == "crm"]
    assert crm["active_subscriptions"] == 1 and crm["enabled_for_sale"] is False
    # Back on: the sale works again.
    send(client, "put", api(pid, "products/crm"), {"enabled_for_sale": True})
    sell(fx, other.organization_id)


def test_a_partner_cannot_switch_on_a_product_it_was_not_granted(owner):
    client, fx = owner
    pid = fx["partner"].id
    for code in ("books", "no_such_product", "../admin"):
        response = send(
            client, "put", api(pid, f"products/{code}"), {"enabled_for_sale": True}
        )
        assert response.status_code == 404, code
    assert not PartnerProductSetting.objects.exists()


def test_switches_must_be_real_booleans_and_something_must_change(owner):
    client, fx = owner
    pid = fx["partner"].id
    assert (
        send(
            client, "put", api(pid, "products/crm"), {"enabled_for_sale": "yes"}
        ).status_code
        == 400
    )
    assert send(client, "put", api(pid, "products/crm"), {}).status_code == 400


def test_only_those_who_run_the_account_can_change_products(seller, member_client):
    pid = seller["partner"].id
    for role in ("sales", "finance", "support", "marketing", "viewer"):
        client, _u = member_client(seller, role)
        assert (
            send(
                client, "put", api(pid, "products/crm"), {"enabled_for_sale": False}
            ).status_code
            == 403
        ), role
        assert client.get(api(pid, "products")).status_code == 200


def test_display_order_applies_and_rejects_duplicates_and_strangers(owner):
    client, fx = owner
    pid = fx["partner"].id
    ok = send(client, "put", api(pid, "products/order"), {"order": ["crm"]})
    assert ok.status_code == 200 and ok.json()["order"] == ["crm"]
    assert (
        send(
            client, "put", api(pid, "products/order"), {"order": ["crm", "crm"]}
        ).status_code
        == 400
    )
    assert (
        send(
            client, "put", api(pid, "products/order"), {"order": ["crm", "books"]}
        ).status_code
        == 404
    )
    assert (
        send(client, "put", api(pid, "products/order"), {"order": "crm"}).status_code
        == 400
    )


def test_presentation_never_creates_an_entitlement_or_a_price(owner):
    client, fx = owner
    from workeasy360.commercial.partners.models import PartnerProductGrant
    from workeasy360.commercial.pricing.models import PlanPrice
    from workeasy360.commercial.provisioning.models import CustomerSummary

    before = (
        PlanPrice.objects.count(),
        PartnerProductGrant.objects.count(),
        CustomerSummary.objects.count(),
    )
    send(
        client,
        "put",
        api(fx["partner"].id, "products/crm"),
        {"show_in_explore": False, "show_in_my_products": False},
    )
    after = (
        PlanPrice.objects.count(),
        PartnerProductGrant.objects.count(),
        CustomerSummary.objects.count(),
    )
    assert before == after


# --- plans, pricing, currencies -------------------------------------------------------------------


def test_plans_show_the_partner_price_beside_the_list_price(owner):
    client, fx = owner
    body = client.get(api(fx["partner"].id, "plans")).json()
    assert body["can_create_plan"] is False
    assert {p["product_code"] for p in body["plans"]} == {"crm"}
    assert "add_ons" in body["not_implemented"]
    for plan in body["plans"]:
        for price in plan["prices"]:
            for key in ("partner_price_minor", "list_price_minor"):
                assert price[key] is None or isinstance(price[key], int)


def test_an_off_product_still_lists_its_plans_marked_off(owner):
    client, fx = owner
    pid = fx["partner"].id
    send(client, "put", api(pid, "products/crm"), {"enabled_for_sale": False})
    plans = client.get(api(pid, "plans")).json()["plans"]
    assert plans and all(p["enabled_for_sale"] is False for p in plans)


def test_the_currency_registry_is_the_platforms_and_has_no_fx(owner):
    from workeasy360.contracts.values.money import CurrencyCode

    client, fx = owner
    body = client.get(api(fx["partner"].id, "currencies")).json()
    assert {c["code"] for c in body["supported"]} == {c.value for c in CurrencyCode}
    assert body["fx"] == "none"
    assert [c["code"] for c in body["supported"] if c["is_default"]] == [body["default"]]


def test_the_price_book_is_read_only_and_there_is_no_way_to_set_a_price(owner):
    client, fx = owner
    pid = fx["partner"].id
    assert client.get(api(pid, "price-book")).json()["can_edit"] is False
    for method in ("post", "put", "patch"):
        for path in (
            "price-book",
            "plans",
            "packages",
            "currencies",
            "pricing",
            "prices",
        ):
            assert send(
                client, method, api(pid, path), {"amount_minor": 1, "currency": "XXX"}
            ).status_code in (404, 405), (method, path)


def test_packages_are_read_only_and_say_so(owner):
    client, fx = owner
    body = client.get(api(fx["partner"].id, "packages")).json()
    assert body["can_create_package"] is False
    assert "partner_packages" in body["not_implemented"]


# --- the partner's own profile -----------------------------------------------------------------------


def test_a_partner_reads_its_profile_without_staff_only_fields(owner):
    client, fx = owner
    body = client.get(api(fx["partner"].id, "profile")).json()
    assert "internal_notes" not in body
    assert body["payout"] == {"currency": "GBP"}
    assert set(body["business"]) >= {"display_name", "country", "timezone", "tax_number"}


def test_a_partner_updates_its_profile_with_the_version_check(owner):
    client, fx = owner
    pid = fx["partner"].id
    profile = client.get(api(pid, "profile")).json()
    saved = send(
        client,
        "put",
        api(pid, "profile"),
        {
            "expected_version": profile["version"],
            "business": {
                "display_name": "Renamed Co",
                "country": "IN",
                "timezone": "Asia/Kolkata",
                "website": "https://renamed.example.com",
            },
            "support": {"email": "help@renamed.example.com"},
        },
    )
    assert saved.status_code == 200, saved.content
    assert saved.json()["business"]["timezone"] == "Asia/Kolkata"
    stale = send(
        client,
        "put",
        api(pid, "profile"),
        {"expected_version": profile["version"], "business": {"phone": "+4400000"}},
    )
    assert stale.status_code == 409


def test_a_partner_cannot_change_payout_notes_or_money_through_its_profile(owner):
    client, fx = owner
    pid = fx["partner"].id
    version = client.get(api(pid, "profile")).json()["version"]
    for section in (
        {"payout": {"contact_email": "thief@example.com"}},
        {"internal_notes": "x"},
        {"payout_currency": "USD"},
        {"capabilities": ["x"]},
    ):
        response = send(
            client, "put", api(pid, "profile"), {"expected_version": version, **section}
        )
        assert response.status_code == 400, section


def test_profile_input_is_validated(owner):
    client, fx = owner
    pid = fx["partner"].id
    version = client.get(api(pid, "profile")).json()["version"]
    bad = (
        {"business": {"timezone": "Mars/Olympus"}},
        {"business": {"country": "ZZ"}},
        {"business": {"website": "javascript:alert(1)"}},
        {"support": {"url": "http://insecure.example.com"}},
        {"business": {"display_name": "A\nB"}},
    )
    for body in bad:
        assert (
            send(
                client, "put", api(pid, "profile"), {"expected_version": version, **body}
            ).status_code
            == 400
        ), body


def test_a_viewer_reads_the_profile_and_cannot_change_it(seller, member_client):
    pid = seller["partner"].id
    client, _u = member_client(seller, "viewer")
    profile = client.get(api(pid, "profile")).json()
    assert (
        send(
            client,
            "put",
            api(pid, "profile"),
            {"expected_version": profile["version"], "business": {"phone": "+4400"}},
        ).status_code
        == 403
    )


def test_a_suspended_partner_cannot_edit_but_can_read(seller, owner_client):
    from workeasy360.contracts.enums import PartnerStatus

    pid = seller["partner"].id
    services.set_partner_status(
        seller["staff_ctx"],
        pid,
        target=PartnerStatus.SUSPENDED,
        reason="Suspended for the test",
    )
    assert owner_client.get(api(pid, "profile")).status_code == 200
    version = owner_client.get(api(pid, "profile")).json()["version"]
    assert send(
        owner_client,
        "put",
        api(pid, "profile"),
        {"expected_version": version, "business": {"phone": "+4400"}},
    ).status_code in (403, 409)


# --- statements -----------------------------------------------------------------------------------------


def test_authentication_is_a_statement_with_no_control(owner):
    client, fx = owner
    pid = fx["partner"].id
    body = client.get(api(pid, "authentication")).json()
    assert body["custom_domain_sign_in"] is False
    assert body["mode"] == "workeasy360_secure_sign_in"
    for method in ("post", "put", "patch", "delete"):
        assert (
            send(
                client, method, api(pid, "authentication"), {"enabled": False}
            ).status_code
            == 405
        )


def test_communication_exposes_no_secret_and_no_smtp_control(owner):
    client, fx = owner
    pid = fx["partner"].id
    body = client.get(api(pid, "communication")).json()

    def keys(node):
        if isinstance(node, dict):
            for key, value in node.items():
                yield key
                yield from keys(value)
        elif isinstance(node, list):
            for item in node:
                yield from keys(item)

    # No field anywhere in the answer is, or holds, a credential.
    for key in keys({k: v for k, v in body.items() if k != "not_implemented"}):
        assert not any(
            w in key.lower() for w in ("password", "smtp", "api_key", "secret", "token")
        ), key
    assert {c["key"] for c in body["channels"]} == {"email", "webhook", "whatsapp", "sms"}
    assert next(c for c in body["channels"] if c["status"] == "active")["key"] == "email"
    assert all(t["editable"] is False for t in body["templates"])
    for path in ("communication/smtp", "smtp", "communication/test"):
        assert (
            send(
                client,
                "post",
                api(fx["partner"].id, path),
                {"host": "10.0.0.1", "password": "x"},
            ).status_code
            == 404
        )


def test_features_registry_names_every_gap_with_a_reason(owner):
    client, fx = owner
    body = client.get(api(fx["partner"].id, "features")).json()
    for key in (
        "partner_cost",
        "customer_limit",
        "recharge",
        "payment_links",
        "add_ons",
        "partner_smtp",
        "whatsapp_sms_webhook",
        "integrations",
        "shared_resources",
        "custom_css",
    ):
        assert len(body["not_implemented"][key]) > 20, key
    assert body["hierarchy"]["max_depth"] == 1
    assert {r["key"] for r in body["roles"]} >= {
        "owner",
        "admin",
        "sales",
        "finance",
        "support",
        "marketing",
    }


def test_unknown_ids_are_not_found_not_errors(owner):
    client, fx = owner
    pid = fx["partner"].id
    assert client.get(api(uuid.uuid4(), "dashboard")).status_code == 404
    assert (
        send(
            client, "post", api(pid, f"sub-partners/{uuid.uuid4()}/activate"), {}
        ).status_code
        == 404
    )
    assert (
        send(
            client, "put", api(pid, "products/crm"), {"enabled_for_sale": True}, **{}
        ).status_code
        == 200
    )


def test_logged_in_without_partner_membership_is_denied(owner, user_factory, http_login):
    _client, fx = owner
    customer = user_factory(password=PASSWORD)
    stranger = http_login(customer)
    assert stranger.get("/api/v1/partner/portal").json() == {"partners": []}
    for path in (
        "dashboard",
        "balance",
        "members",
        "profile",
        "products",
        "activity",
        "orders",
    ):
        assert stranger.get(api(fx["partner"].id, path)).status_code == 404, path

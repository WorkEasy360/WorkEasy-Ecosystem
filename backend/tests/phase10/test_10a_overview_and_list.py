"""Phase 10a: the capability registry's presentation layer, the partner overview
and the reseller list."""

from __future__ import annotations

import pytest

from tests.phase10.conftest import count_queries, get, post
from workeasy360.commercial.partners import capabilities

pytestmark = pytest.mark.django_db


# --- the registry: groups, presets, derived labels -------------------------------


def test_every_capability_is_in_exactly_one_group():
    grouped = [key for _k, _label, keys in capabilities.CAPABILITY_GROUPS for key in keys]
    assert sorted(grouped) == sorted(capabilities.CAPABILITIES)
    assert len(grouped) == len(set(grouped))


def test_a_preset_is_only_existing_capability_keys():
    for preset in capabilities.CAPABILITY_PRESETS:
        keys = list(preset["capabilities"])
        assert keys and set(keys) <= set(capabilities.CAPABILITIES)
        assert len(keys) == len(set(keys))


def test_labels_are_derived_from_capabilities_and_combine_freely():
    def labels(*held):
        return [entry["key"] for entry in capabilities.experience_labels(set(held))]

    assert labels() == []
    assert labels("customer.provision") == ["reseller"]
    assert labels("branding.manage") == ["white_label"]
    assert labels("domain.manage") == ["white_label"]
    assert labels("referral.view") == ["affiliate"]
    # One account, several labels - and a fixed order.
    assert labels("referral.create", "subscription.sell", "domain.manage") == [
        "reseller",
        "white_label",
        "affiliate",
    ]
    # A capability that unlocks nothing on its own earns no label.
    assert labels("commission.earn", "commission.view", "pricing.view") == []


def test_the_capability_endpoint_serves_groups_and_presets(staff_client):
    client, _u = staff_client("platform.staff_partner.view")
    body = client.get("/api/v1/admin/partners/capabilities").json()
    assert {c["key"] for c in body["results"]} == set(capabilities.CAPABILITIES)
    assert all(c["group"] for c in body["results"])
    assert [g["key"] for g in body["groups"]][:4] == [
        "customer_management",
        "products_sales",
        "commercial",
        "support",
    ]
    # Every capability has a short, plain name for a setup form to list.
    assert all(c["short_label"] for c in body["results"])
    preset = body["presets"][0]
    assert preset["key"] == "standard_reseller"
    assert set(preset["capabilities"]) <= set(capabilities.CAPABILITIES)


# --- the overview -----------------------------------------------------------------


def _cards(client):
    response = client.get("/api/v1/admin/partners/overview")
    assert response.status_code == 200, response.content
    return {card["key"]: card for card in response.json()["cards"]}


def test_the_overview_states_facts_and_only_facts(console, viewer):
    cards = _cards(viewer)
    # ADR-048: the dashboard counts partners only; money, customers and domains
    # are on each partner's page (checked below through its summary).
    assert list(cards) == [
        "total_partners",
        "active_partners",
        "resellers",
        "white_label",
        "affiliates",
        "suspended_partners",
        "profiles_incomplete",
    ]
    assert cards["total_partners"]["count"] == 3  # ... and the draft
    assert cards["affiliates"]["count"] == 0
    assert cards["active_partners"]["count"] == 2  # the reseller and the white-label
    assert cards["white_label"]["count"] == 1
    assert cards["suspended_partners"]["count"] == 0
    # Each label card is the size of the list it opens.
    listed = _rows(viewer, scope="all", type="reseller", limit=100)["results"]
    assert cards["resellers"]["count"] == len(listed)
    incomplete = _rows(viewer, scope="all", profile="incomplete", limit=100)["results"]
    assert cards["profiles_incomplete"]["count"] == len(incomplete)
    # The one cleared commission is unpaid, in its own currency, unconverted.
    reseller = _summary(viewer, console["reseller"]["partner"].id)
    assert [(c["currency"], c["unpaid_minor"]) for c in reseller["commission"]] == [
        ("GBP", 500)
    ]
    assert reseller["payouts"]["open"] == 0
    assert reseller["customers"]["live"] == 1 and reseller["active_subscriptions"] == 1


def test_the_overview_invents_no_revenue_figure(console, viewer):
    text = viewer.get("/api/v1/admin/partners/overview").content.decode().lower()
    for word in ("mrr", "arr", "growth", "revenue"):
        assert word not in text


def test_a_payout_moves_commission_from_pending_to_pending_payout(
    console, viewer, staff_factory
):
    from django.utils import timezone

    from workeasy360.commercial.commissions import payouts

    _u, prep = staff_factory("platform.staff_payout.manage")
    payouts.prepare_payout(
        prep,
        partner_id=console["reseller"]["partner"].id,
        period_end=timezone.now().date(),
        idempotency_key="overview-payout",
    )
    reseller = _summary(viewer, console["reseller"]["partner"].id)
    assert reseller["payouts"]["open"] == 1  # the commission is now in a payout
    assert reseller["payouts"]["latest"]["payable_minor"] == 500


def test_ending_the_relationship_changes_the_customer_count_and_nothing_else(
    console, viewer, staff_client
):
    from workeasy360.commercial.partners import services
    from workeasy360.commercial.partners.models import PartnerOrganizationLink
    from workeasy360.contracts.enums import PartnerLinkStatus

    link = PartnerOrganizationLink.objects.get(organization_id=console["org"])
    services.change_link_status(
        console["reseller"]["staff_ctx"],
        link.id,
        target=PartnerLinkStatus.ENDED,
        reason="Relationship ended for the count test",
    )
    reseller = _summary(viewer, console["reseller"]["partner"].id)
    assert reseller["customers"]["live"] == 0
    assert reseller["active_subscriptions"] == 0
    # The commission already earned stays pending: history is not erased.
    assert [(c["currency"], c["unpaid_minor"]) for c in reseller["commission"]] == [
        ("GBP", 500)
    ]


def test_the_overview_needs_the_permission(staff_client, http_login, user_factory):
    from tests.phase10.conftest import PASSWORD

    client, _u = staff_client("platform.staff_customer.view")
    assert client.get("/api/v1/admin/partners/overview").status_code == 403
    customer = http_login(user_factory(password=PASSWORD))
    assert customer.get("/api/v1/admin/partners/overview").status_code == 403
    from django.test import Client

    assert Client().get("/api/v1/admin/partners/overview").status_code == 401


# --- the reseller list ----------------------------------------------------------------


def _rows(client, **params):
    response = get(client, "/api/v1/admin/partners/resellers", **params)
    assert response.status_code == 200, response.content
    return response.json()


def test_the_default_list_holds_resellers_and_labels_are_derived(console, viewer):
    body = _rows(viewer)
    assert [row["code"] for row in body["results"]] == [
        console["reseller"]["partner"].code
    ]
    row = body["results"][0]
    assert [e["key"] for e in row["experience"]] == ["reseller"]
    assert "type" not in row and "partner_type" not in row


def test_scope_all_lists_every_partner_so_nothing_is_invisible(console, viewer):
    rows = {r["code"]: r for r in _rows(viewer, scope="all")["results"]}
    assert set(rows) == {
        console["reseller"]["partner"].code,
        "wl-console",
        "draft-console",
    }
    assert [e["key"] for e in rows["wl-console"]["experience"]] == ["white_label"]
    assert rows["draft-console"]["experience"] == []
    assert rows["draft-console"]["status"] == "draft"


def test_one_partner_can_wear_two_labels(console, viewer):
    from workeasy360.commercial.partners import services

    services.grant_capability(
        console["reseller"]["staff_ctx"],
        console["reseller"]["partner"].id,
        capability="branding.manage",
        reason="Combined partner test",
    )
    services.grant_capability(
        console["reseller"]["staff_ctx"],
        console["reseller"]["partner"].id,
        capability="domain.manage",
        reason="Combined partner test",
    )
    row = _rows(viewer)["results"][0]
    assert [e["key"] for e in row["experience"]] == ["reseller", "white_label"]
    from workeasy360.commercial.partners.models import PartnerAccount

    assert PartnerAccount.objects.filter(code=row["code"]).count() == 1


def test_the_row_carries_the_facts_the_columns_show(console, viewer):
    row = _rows(viewer)["results"][0]
    assert row["customers"] == 1
    assert row["active_subscriptions"] == 1
    assert row["products"] == [{"product_code": "crm", "plan_code": None}]
    assert row["commission"] == [
        {"currency": "GBP", "balance_minor": 500, "unpaid_minor": 500, "entries": 1}
    ]
    assert row["payouts"] == {"open": 0, "latest": None, "paid": []}
    assert row["capability_count"] >= 8
    assert row["last_activity_at"]


def test_filters_narrow_by_status_product_capability_and_text(console, viewer):
    reseller_code = console["reseller"]["partner"].code
    assert [r["code"] for r in _rows(viewer, status="active")["results"]] == [
        reseller_code
    ]
    assert _rows(viewer, status="suspended")["results"] == []
    assert [r["code"] for r in _rows(viewer, product="crm")["results"]] == [reseller_code]
    assert _rows(viewer, product="books")["results"] == []
    assert [
        r["code"] for r in _rows(viewer, capability="commission.earn")["results"]
    ] == [reseller_code]
    assert _rows(viewer, capability="branding.manage")["results"] == []
    assert [r["code"] for r in _rows(viewer, q="wl-cons", scope="all")["results"]] == [
        "wl-console"
    ]


def test_an_unknown_filter_is_a_400_never_a_500(console, viewer):
    for params in (
        {"status": "nonsense"},
        {"scope": "everything"},
        {"capability": "root.everything"},
        {"product": "NOT A CODE!"},
    ):
        response = get(viewer, "/api/v1/admin/partners/resellers", **params)
        assert response.status_code == 400, (params, response.content)


def test_pagination_is_keyset_and_bounded(console, viewer, partner_factory):
    for _ in range(3):
        partner_factory("customer.provision")
    first = _rows(viewer, limit=2)
    assert len(first["results"]) == 2 and first["has_more"] and first["next_cursor"]
    second = _rows(viewer, limit=2, cursor=first["next_cursor"])
    seen = {r["id"] for r in first["results"]} | {r["id"] for r in second["results"]}
    assert len(seen) == len(first["results"]) + len(second["results"])
    assert len(_rows(viewer, limit=100_000)["results"]) <= 100
    # A garbage cursor is the first page, not an error.
    assert _rows(viewer, cursor="not-a-cursor")["results"]


def test_the_list_needs_the_view_permission(staff_client):
    client, _u = staff_client("platform.staff_customer.view")
    assert client.get("/api/v1/admin/partners/resellers").status_code == 403


def test_the_list_query_count_does_not_depend_on_the_page_size(
    console, viewer, partner_factory
):
    for _ in range(4):
        partner_factory("customer.provision", "subscription.sell")
    viewer.get("/api/v1/admin/partners/resellers?limit=1")  # warm the session

    def queries(limit):
        count, response = count_queries(
            lambda: viewer.get(f"/api/v1/admin/partners/resellers?limit={limit}")
        )
        assert response.status_code == 200
        return count, len(response.json()["results"])

    small, small_rows = queries(1)
    large, large_rows = queries(5)
    assert small_rows == 1 and large_rows == 5
    assert small == large, f"query count grows with the page: {small} vs {large}"


def test_creating_a_partner_is_unchanged_by_the_console(staff_client):
    client, _u = staff_client("platform.staff_partner.manage")
    response = post(
        client,
        "/api/v1/admin/partners",
        {"code": "still-works", "display_name": "Still Works", "payout_currency": "GBP"},
    )
    assert response.status_code == 201


def _summary(client, partner_id):
    response = client.get(f"/api/v1/admin/partners/{partner_id}/summary")
    assert response.status_code == 200, response.content
    return response.json()

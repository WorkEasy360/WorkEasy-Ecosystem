"""The unified dashboard's data (2026-11-03): calendar presets in the partner's
own time zone, the previous-period comparison, the product-wise split, the
commission three-way split and the open tickets - each behind the same
permission as before, and ``None`` where the caller may not see it."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.partner_dashboard.conftest import api, send
from workeasy360.commercial.controlplane import partner_portal
from workeasy360.commercial.partners import capabilities
from workeasy360.contracts.errors import PartnerValidationError

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 3, 15, 4, 0, tzinfo=timezone.utc)  # 09:30 in Kolkata


def test_calendar_presets_follow_the_partner_time_zone():
    key, start, end = partner_portal.parse_range(
        "today", None, None, now=NOW, zone="Asia/Kolkata"
    )
    assert key == "today" and end == NOW
    assert start == datetime(2026, 3, 14, 18, 30, tzinfo=timezone.utc)  # local midnight
    _, start, end = partner_portal.parse_range(
        "yesterday", None, None, now=NOW, zone="Asia/Kolkata"
    )
    assert end - start == timedelta(days=1) and end == datetime(
        2026, 3, 14, 18, 30, tzinfo=timezone.utc
    )
    _, start, end = partner_portal.parse_range(
        "this_month", None, None, now=NOW, zone="Asia/Kolkata"
    )
    assert start == datetime(2026, 2, 28, 18, 30, tzinfo=timezone.utc) and end == NOW
    _, start, end = partner_portal.parse_range(
        "last_month", None, None, now=NOW, zone="Asia/Kolkata"
    )
    assert start == datetime(2026, 1, 31, 18, 30, tzinfo=timezone.utc)
    assert end == datetime(2026, 2, 28, 18, 30, tzinfo=timezone.utc)


def test_an_unknown_zone_is_utc_and_an_unknown_range_is_still_refused():
    _, start, _ = partner_portal.parse_range(
        "today", None, None, now=NOW, zone="Mars/Olympus"
    )
    assert start == datetime(2026, 3, 15, tzinfo=timezone.utc)
    with pytest.raises(PartnerValidationError):
        partner_portal.parse_range("this_year", None, None, now=NOW)


def test_the_previous_period_is_the_same_length_just_before():
    start, end = NOW - timedelta(days=7), NOW
    assert partner_portal.previous_window(start, end) == (
        start - timedelta(days=7),
        start,
    )


def test_the_dashboard_carries_the_new_sections(reseller):
    client, fx = reseller
    response = send(
        client, "get", api(fx["partner"].id, "dashboard") + "?range=this_month"
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["range"]["key"] == "this_month"
    assert body["previous"]["to"] == body["range"]["from"]
    assert body["customers"]["new"] >= 1 and body["previous"]["new_customers"] == 0
    assert set(body["previous"]["sales"]) == {
        "transactions",
        "gross",
        "commission_earned",
    }
    assert body["products"]["active"] == 1  # the crm grant
    split = body["products"]["sales"]
    assert split and split[0]["product_code"] == "crm" and split[0]["product_name"]
    assert split[0]["sales"] == 1 and split[0]["gross_minor"] > 0
    commission = body["money"]["commission"][0]
    assert (
        commission["total_minor"]
        == commission["approved_minor"] + commission["pending_minor"]
    )
    assert body["tickets"] == {"open": 0, "breached": 0}


def test_a_role_without_money_or_customers_sees_none_of_their_new_figures(
    console, member_client
):
    client, _user = member_client(console["reseller"], "support")
    body = send(client, "get", api(console["reseller"]["partner"].id, "dashboard")).json()
    assert body["previous"]["sales"] is None and body["products"]["sales"] is None
    assert body["previous"]["new_customers"] is None
    assert body["products"]["active"] == 1  # the product list is baseline


def test_the_portal_names_the_partner_level(reseller):
    client, fx = reseller
    partners = send(client, "get", "/api/v1/partner/portal").json()["partners"]
    assert partners[0]["level"] == "reseller"
    assert (
        capabilities.partner_level({"referral.view", "branding.manage"}) == "white_label"
    )
    assert capabilities.partner_level({"referral.view"}) == "affiliate"
    assert capabilities.partner_level(set()) is None


def test_the_product_split_folds_the_tail_and_sums_on_the_server():
    rows = [
        {
            "product_code": f"p{i}",
            "product_name": f"P{i}",
            "currency": "INR",
            "sales": 1,
            "gross_minor": g,
            "commission_minor": g // 10,
        }
        for i, g in enumerate([500, 300, 100, 50, 30, 15, 5])
    ] + [
        {
            "product_code": "x",
            "product_name": "X",
            "currency": "GBP",
            "sales": 1,
            "gross_minor": 0,
            "commission_minor": 0,
        }
    ]
    split = partner_portal.product_split(rows)
    assert set(split) == {"INR"}  # a currency with no sales draws nothing
    inr = split["INR"]
    assert inr["total_minor"] == 1000 and inr["products"] == 7
    assert [p["product_name"] for p in inr["parts"]] == [
        "P0",
        "P1",
        "P2",
        "P3",
        "P4",
        "Others",
    ]
    assert (
        inr["parts"][-1]["gross_minor"] == 20 and inr["parts"][-1]["product_code"] == ""
    )
    assert [p["share_bp"] for p in inr["parts"]] == [5000, 3000, 1000, 500, 300, 200]

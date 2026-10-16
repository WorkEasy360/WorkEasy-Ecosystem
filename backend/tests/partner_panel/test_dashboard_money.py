"""The dashboard, balance, orders and customer export: server-computed, bounded,
isolated, and honest about what the platform does not have."""

from __future__ import annotations

import csv
import io
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.partner_panel.conftest import (
    PASSWORD,
    api,
    count_queries_of,
    partner_audit,
    send,
)
from workeasy360.commercial.controlplane import partner_portal
from workeasy360.contracts.errors import PartnerValidationError

pytestmark = pytest.mark.django_db


@pytest.fixture
def reseller_client(console, http_login):
    fx = console["reseller"]
    fx["user"].set_password(PASSWORD)
    fx["user"].save(update_fields=["password"])
    return http_login(fx["user"]), fx


# --- the range ----------------------------------------------------------------------------


def test_ranges_are_closed_and_bounded():
    now = timezone.now()
    key, start, end = partner_portal.parse_range("7d", None, None, now=now)
    assert key == "7d" and end == now and now - start == timedelta(days=7)
    for bad in ("1y", "", "today; drop table", 7, ["30d"]):
        if bad == "":
            assert partner_portal.parse_range(bad, None, None, now=now)[0] == "30d"
            continue
        with pytest.raises(PartnerValidationError):
            partner_portal.parse_range(bad, None, None, now=now)
    _, start, end = partner_portal.parse_range(
        "custom", "2026-01-01", "2026-01-31", now=now
    )
    assert (end - start).days == 31  # inclusive of the last day
    with pytest.raises(PartnerValidationError):
        partner_portal.parse_range("custom", "2026-02-01", "2026-01-01", now=now)
    with pytest.raises(PartnerValidationError):
        partner_portal.parse_range("custom", "2025-01-01", "2026-06-01", now=now)
    with pytest.raises(PartnerValidationError):
        partner_portal.parse_range("custom", "not-a-date", "2026-01-01", now=now)


def test_a_bad_range_is_a_400_not_a_default(reseller_client):
    client, fx = reseller_client
    response = client.get(api(fx["partner"].id, "dashboard"), {"range": "forever"})
    assert response.status_code == 400


# --- the dashboard ---------------------------------------------------------------------------


def test_the_dashboard_shows_the_ledger_and_the_customers(reseller_client):
    client, fx = reseller_client
    body = client.get(api(fx["partner"].id, "dashboard"), {"range": "90d"}).json()
    assert body["customers"]["total"] == 1
    assert body["customers"]["active_subscriptions"] == 1
    sales = body["sales"]
    assert sales["transactions"] == 1
    [earned] = sales["commission_earned"]
    assert earned == {"currency": "GBP", "amount_minor": 500}
    assert isinstance(earned["amount_minor"], int)
    [gross] = sales["gross"]
    assert gross["amount_minor"] == 4999  # the plan's price; 10% rounds to 500
    assert body["money"]["balances"][0]["balance_minor"] == 500


def test_money_is_integer_minor_units_everywhere(reseller_client):
    client, fx = reseller_client
    body = client.get(api(fx["partner"].id, "dashboard")).json()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key.endswith("_minor") or key == "amount_minor":
                    assert isinstance(value, int) and not isinstance(value, bool), key
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(body)


def test_gaps_are_declared_not_shown_as_zero(reseller_client):
    client, fx = reseller_client
    body = client.get(api(fx["partner"].id, "dashboard")).json()
    assert set(body["not_implemented"]) == {"partner_cost", "customer_limit"}
    assert "partner_cost" not in body["sales"]


def test_sections_follow_permissions_and_a_hidden_section_is_null_not_zero(
    console, member_client, http_login
):
    fx = console["reseller"]
    client, _u = member_client(fx, "marketing")
    body = client.get(api(fx["partner"].id, "dashboard")).json()
    assert body["sales"] is None and body["money"] is None and body["customers"] is None
    finance, _u = member_client(fx, "finance")
    f = finance.get(api(fx["partner"].id, "dashboard")).json()
    assert f["customers"] is None
    assert f["sales"]["commission_earned"][0]["amount_minor"] == 500


def test_the_range_excludes_what_happened_outside_it(reseller_client):
    client, fx = reseller_client
    url = api(fx["partner"].id, "dashboard")
    before = client.get(
        url, {"range": "custom", "from": "2020-01-01", "to": "2020-01-31"}
    ).json()
    assert (
        before["sales"]["transactions"] == 0
        and before["sales"]["commission_earned"] == []
    )
    assert before["charts"]["transactions"] == []
    now = client.get(url, {"range": "7d"}).json()
    assert now["sales"]["transactions"] == 1


def test_chart_data_is_summed_by_the_server(reseller_client):
    client, fx = reseller_client
    charts = client.get(api(fx["partner"].id, "dashboard")).json()["charts"]
    assert charts["gross"]["GBP"][0]["amount_minor"] == 4999
    assert charts["commission"]["GBP"][0]["amount_minor"] == 500
    assert charts["transactions"][0]["count"] == 1


def test_currencies_are_never_added_together():
    series = [
        {
            "date": "2026-01-01",
            "currency": "GBP",
            "basis_currency": "GBP",
            "sales": 1,
            "gross_minor": 100,
            "commission_minor": 10,
        },
        {
            "date": "2026-01-01",
            "currency": "USD",
            "basis_currency": "USD",
            "sales": 1,
            "gross_minor": 900,
            "commission_minor": 90,
        },
    ]
    charts = partner_portal._charts(series)
    assert charts["gross"] == {
        "GBP": [{"date": "2026-01-01", "amount_minor": 100}],
        "USD": [{"date": "2026-01-01", "amount_minor": 900}],
    }
    assert charts["transactions"] == [{"date": "2026-01-01", "count": 2}]


def test_a_partner_cannot_read_another_partners_dashboard(
    reseller_client, partner_factory
):
    client, _fx = reseller_client
    other = partner_factory("customer.provision")
    for path in (
        "dashboard",
        "balance",
        "orders",
        "activity",
        "products",
        "plans",
        "members",
        "profile",
    ):
        assert client.get(api(other["partner"].id, path)).status_code == 404, path


def test_the_dashboard_query_count_does_not_grow_with_customers(console, reseller_client):
    from tests.phase7.support import provision

    client, fx = reseller_client
    url = api(fx["partner"].id, "dashboard")
    client.get(url)  # warm: settings, the platform organization
    few, _ = count_queries_of(lambda: client.get(url))
    for n in range(8):
        provision(fx, name=f"Extra {n}")
    many, _ = count_queries_of(lambda: client.get(url))
    assert many == few


# --- balance ----------------------------------------------------------------------------------


def test_the_balance_is_the_ledger_and_its_parts_add_up(reseller_client):
    client, fx = reseller_client
    body = client.get(api(fx["partner"].id, "balance")).json()
    [gbp] = body["balances"]
    assert gbp["balance_minor"] == 500
    assert (
        gbp["pending_minor"]
        + gbp["available_minor"]
        + gbp["in_payouts_minor"]
        + gbp["paid_minor"]
        == gbp["balance_minor"]
    )
    assert "recharge" in body["not_implemented"]


def test_there_is_no_endpoint_to_write_a_balance_or_recharge(reseller_client):
    client, fx = reseller_client
    for method in ("post", "put", "patch", "delete"):
        for path in ("balance", "recharge", "payment-links", "wallet", "credit"):
            response = send(
                client, method, api(fx["partner"].id, path), {"amount_minor": 10**9}
            )
            assert response.status_code in (404, 405), (method, path)


def test_the_ledger_is_untouched_by_reading_it(reseller_client):
    from workeasy360.commercial.commissions.models import CommissionAccrual

    client, fx = reseller_client
    before = list(
        CommissionAccrual.objects.order_by("id").values_list("id", "amount_minor")
    )
    client.get(api(fx["partner"].id, "balance"))
    client.get(api(fx["partner"].id, "dashboard"))
    assert before == list(
        CommissionAccrual.objects.order_by("id").values_list("id", "amount_minor")
    )


# --- orders ---------------------------------------------------------------------------------------


def test_orders_are_only_this_partners_customers_orders(
    console, reseller_client, partner_factory
):
    client, fx = reseller_client
    body = client.get(api(fx["partner"].id, "orders")).json()
    assert isinstance(body["results"], list)
    stranger = partner_factory("customer.provision")
    stranger["user"].set_password(PASSWORD)
    stranger["user"].save(update_fields=["password"])
    assert client.get(api(stranger["partner"].id, "orders")).status_code == 404


# --- the customer export ---------------------------------------------------------------------------


def test_the_export_is_a_formula_safe_audited_download(console, reseller_client):
    from tests.phase7.support import provision

    client, fx = reseller_client
    provision(fx, name='=HYPERLINK("http://evil.example","x")')
    response = client.get(api(fx["partner"].id, "customers/export"))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert response["Content-Disposition"].startswith("attachment")
    assert response["Cache-Control"] == "private, no-store"
    rows = list(csv.reader(io.StringIO(response.content.decode())))
    assert rows[0] == list(partner_portal.EXPORT_COLUMNS)
    assert any(r[0].startswith("'=") for r in rows[1:])
    assert not any(r[0].startswith("=") for r in rows[1:])
    exported = [
        r
        for r in partner_audit(fx["partner"].id, "action", "changes")
        if r["action"] == "partner.customers_exported"
    ]
    assert exported and exported[0]["changes"]["rows"] == len(rows) - 1


def test_the_export_never_includes_another_partners_customer(
    console, reseller_client, partner_factory
):
    from workeasy360.commercial.partners import customers

    client, fx = reseller_client
    other = partner_factory("customer.provision")
    customers.provision_customer(
        other["actor"](),
        organization_name="Rival Customer",
        owner_email="rival@example.com",
        idempotency_key="k1",
    )
    text = client.get(api(fx["partner"].id, "customers/export")).content.decode()
    assert "Rival Customer" not in text and "rival@example.com" not in text


def test_the_export_needs_the_permission_and_is_rate_limited(
    console, member_client, reseller_client, settings
):
    fx = console["reseller"]
    support, _u = member_client(fx, "support")
    assert support.get(api(fx["partner"].id, "customers/export")).status_code == 403
    client, fx = reseller_client
    settings.CONTROL_PLANE_RATE_LIMITS = {
        **settings.CONTROL_PLANE_RATE_LIMITS,
        "partner_export_user": (2, 60),
    }
    statuses = [
        client.get(api(fx["partner"].id, "customers/export")).status_code
        for _ in range(4)
    ]
    assert statuses[:2] == [200, 200] and 429 in statuses[2:]


def test_the_export_is_bounded(monkeypatch, reseller_client):
    client, fx = reseller_client
    monkeypatch.setattr(partner_portal, "EXPORT_MAX_ROWS", 1)
    from tests.phase7.support import provision

    provision(fx, name="Second")
    response = client.get(api(fx["partner"].id, "customers/export"))
    assert response["X-Export-Truncated"] == "true"
    assert (
        len(list(csv.reader(io.StringIO(response.content.decode())))) == 2
    )  # header + 1


def test_csv_cells_that_would_run_as_formulas_are_neutralised():
    for leader in ("=", "+", "-", "@", "\t", "\r"):
        assert partner_portal.csv_cell(f"{leader}cmd").startswith("'")
    assert partner_portal.csv_cell("plain") == "plain"
    assert partner_portal.csv_cell(None) == ""


def test_neither_the_customer_list_nor_the_export_carries_the_owner_address(
    reseller_client, console
):
    client, fx = reseller_client
    owner_email = console["owner"].email
    listing = client.get(api(fx["partner"].id, "customers")).content.decode().lower()
    export = (
        client.get(api(fx["partner"].id, "customers/export")).content.decode().lower()
    )
    assert owner_email.lower() not in listing and owner_email.lower() not in export
    assert "owner email" not in export

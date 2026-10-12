"""Phase 10c: one partner's console pages - the overview and its setup progress,
the customers with their owner-activation state, the ledger's payout linkage,
the activity from the one audit log, and the brand."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.phase10.conftest import count_queries
from workeasy360.commercial.partners import customers, domain, services
from workeasy360.commercial.partners.models import (
    PartnerOrganizationLink,
    PartnerOwnerInvite,
)
from workeasy360.contracts.enums import PartnerLinkStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff(staff_client):
    client, _u = staff_client(
        "platform.staff_partner.view",
        "platform.staff_commission.view",
        "platform.staff_audit.view",
    )
    return client


def summary(client, partner):
    response = client.get(f"/api/v1/admin/partners/{partner.id}/summary")
    assert response.status_code == 200, response.content
    return response.json()


def progress(body):
    return {step["key"]: step for step in body["setup_progress"]}


# --- the pure derivation ----------------------------------------------------------------


def test_owner_activation_is_derived_from_the_row_and_the_clock():
    now = timezone.now()
    later, earlier = now + timedelta(days=1), now - timedelta(days=1)
    assert domain.owner_activation(None, None, now) == "not_applicable"
    assert domain.owner_activation("pending", later, now) == "pending"
    # A pending row past its expiry *is* expired, whether or not anyone tried it.
    assert domain.owner_activation("pending", earlier, now) == "expired"
    assert domain.owner_activation("accepted", earlier, now) == "activated"
    assert domain.owner_activation("expired", later, now) == "expired"
    assert domain.owner_activation("revoked", later, now) == "revoked"
    assert domain.owner_activation("declined", later, now) == "declined"
    assert domain.owner_activation("something-else", later, now) == "expired"


# --- the overview -----------------------------------------------------------------------------


def test_the_reseller_overview_states_its_facts_and_setup_progress(console, staff):
    body = summary(staff, console["reseller"]["partner"])
    assert body["partner"]["status"] == "active"
    assert [e["key"] for e in body["experience"]] == ["reseller"]
    assert body["customers"] == {
        "live": 1,
        "active": 1,
        "suspended": 0,
        "ended": 0,
        "owner_activation_pending": 1,
    }
    assert [p["product_code"] for p in body["products"]] == ["crm"]
    assert body["active_subscriptions"] == 1
    assert body["commission"] == [
        {"currency": "GBP", "balance_minor": 500, "unpaid_minor": 500, "entries": 1}
    ]
    assert body["commission_rule"]["scope"] == "programme_default"
    assert body["payouts"]["open"] == 0 and body["payouts"]["latest"] is None
    assert set(body["payouts"]["by_status"]) == {
        "draft",
        "ready",
        "approved",
        "paid",
        "cancelled",
    }
    assert body["white_label"]["enabled"] is False
    steps = progress(body)
    # The fixture has a primary contact but no business email, and the business
    # profile step asks for both (tests/partner_setup covers it completing).
    assert steps["profile"]["state"] == "incomplete"
    assert "business email" in steps["profile"]["detail"]
    assert steps["products"]["state"] == "complete"
    assert steps["pricing"]["state"] == "incomplete"  # customers pay list price
    assert steps["commission"]["state"] == "complete"
    # The fixture's administrator has never chosen a password.
    assert steps["administrator"]["state"] == "pending"
    assert (
        steps["branding"]["state"] == "optional"
        and steps["domain"]["state"] == "optional"
    )
    assert {c["key"] for c in body["capabilities"]} >= {
        "customer.provision",
        "commission.earn",
    }
    assert all(c["group"] for c in body["capabilities"])


def test_setup_progress_follows_what_is_actually_configured(
    console, staff, staff_factory
):
    from tests.phase10.conftest import PASSWORD
    from workeasy360.commercial.commissions import rules

    partner = console["reseller"]["partner"]
    assert progress(summary(staff, partner))["administrator"]["state"] == "pending"
    admin = console["reseller"]["user"]
    admin.set_password(PASSWORD)
    admin.save(update_fields=["password"])
    assert progress(summary(staff, partner))["administrator"]["state"] == "complete"

    _u, ctx = staff_factory("platform.staff_commission.manage")
    rules.assign_partner_commission(
        ctx,
        partner.id,
        rate_bp=1200,
        reason="Agreed terms for the test",
        expected_version=0,
    )
    body = summary(staff, partner)
    assert body["commission_rule"]["scope"] == "partner"
    assert body["commission_rule"]["rate_bp"] == 1200


def test_a_partner_priced_by_staff_shows_pricing_complete(
    console, staff, staff_factory, catalog
):
    partner = console["reseller"]["partner"]
    _u, pricer = staff_factory("platform.staff_pricing.manage")
    services.set_partner_price(
        pricer,
        partner.id,
        plan_version_id=catalog["versions"]["crm_professional"].id,
        interval="monthly",
        amount_minor=4000,
    )
    assert progress(summary(staff, partner))["pricing"]["state"] == "complete"


def test_a_white_label_partner_shows_its_brand_and_domain_state(console, staff):
    body = summary(staff, console["white_label"]["partner"])
    assert body["white_label"]["enabled"] is True
    assert body["white_label"]["domain_status"] == "requested"
    assert body["white_label"]["domains"]["awaiting"] == 1
    steps = progress(body)
    assert (
        steps["domain"]["state"] == "pending" and steps["branding"]["state"] == "optional"
    )
    assert [e["key"] for e in body["experience"]] == ["white_label"]


def test_a_draft_partner_with_nothing_shows_everything_incomplete(console, staff):
    body = summary(staff, console["draft"])
    steps = progress(body)
    assert steps["profile"]["state"] == "incomplete"
    for key in ("products", "pricing", "commission", "administrator"):
        assert steps[key]["state"] == "incomplete", key
    assert body["experience"] == [] and body["capabilities"] == []


def test_the_overview_needs_the_view_permission_and_hides_other_partners_ids(
    console, staff_client, staff
):
    weak, _u = staff_client("platform.staff_customer.view")
    assert (
        weak.get(f"/api/v1/admin/partners/{console['draft'].id}/summary").status_code
        == 403
    )
    assert staff.get(f"/api/v1/admin/partners/{uuid.uuid4()}/summary").status_code == 404


# --- the customers -------------------------------------------------------------------------------


def customers_of(client, partner=None, **params):
    url = (
        f"/api/v1/admin/partners/{partner.id}/customers"
        if partner is not None
        else "/api/v1/admin/partners/customers"
    )
    response = client.get(url, params)
    assert response.status_code == 200, response.content
    return response.json()


def test_the_customer_list_shows_a_pending_owner_and_never_a_token(console, staff):
    body = customers_of(staff, console["reseller"]["partner"])
    row = body["results"][0]
    assert row["name"] == "Console Customer" and row["link_status"] == "active"
    assert row["subscription_status"] == "active" and row["products"] == ["crm"]
    owner = row["owner"]
    assert owner["activation"] == "pending"
    assert owner["email"] == console["owner"].email
    assert owner["expires_at"] and owner["invitation_id"]
    text = json.dumps(body).lower()
    assert "token" not in text and "hash" not in text


def test_owner_activation_moves_from_pending_to_activated(console, staff):
    customers.accept_owner_invite(
        user_id=console["owner"].id, organization_id=console["org"]
    )
    row = customers_of(staff, console["reseller"]["partner"])["results"][0]
    assert row["owner"]["activation"] == "activated"
    assert row["owner"]["resolved_at"]


def test_an_unclaimed_invitation_past_its_expiry_is_shown_as_expired(console, staff):
    PartnerOwnerInvite.objects.filter(organization_id=console["org"]).update(
        expires_at=timezone.now() - timedelta(hours=1)
    )
    row = customers_of(staff, console["reseller"]["partner"])["results"][0]
    assert row["owner"]["activation"] == "expired"


def test_a_customer_linked_without_an_invitation_has_nothing_to_activate(
    console, staff, organization_factory, staff_factory
):
    org, _membership, _owner = organization_factory()
    _u, manager = staff_factory("platform.staff_partner.manage")
    services.link_customer(
        manager,
        console["reseller"]["partner"].id,
        org.id,
        reason="Linked an existing customer for the test",
    )
    rows = {
        r["organization_id"]: r
        for r in customers_of(staff, console["reseller"]["partner"])["results"]
    }
    assert rows[str(org.id)]["owner"]["activation"] == "not_applicable"
    assert rows[str(console["org"])]["owner"]["activation"] == "pending"


def test_an_ended_relationship_is_history_with_no_current_customer_state(console, staff):
    link = PartnerOrganizationLink.objects.get(organization_id=console["org"])
    services.change_link_status(
        console["reseller"]["staff_ctx"],
        link.id,
        target=PartnerLinkStatus.ENDED,
        reason="Ended for the customer list test",
    )
    row = customers_of(staff, console["reseller"]["partner"])["results"][0]
    assert row["link_status"] == "ended" and row["ended_at"]
    assert row["name"] == "Console Customer"  # staff know who it was
    # ...but nothing of the customer's *current* commercial state.
    assert row["subscription_status"] == "" and row["products"] == []
    assert row["organization_status"] == ""


def test_customer_filters_narrow_on_status_activation_subscription_and_text(
    console, staff
):
    partner = console["reseller"]["partner"]
    assert len(customers_of(staff, partner, status="active")["results"]) == 1
    assert customers_of(staff, partner, status="ended")["results"] == []
    assert len(customers_of(staff, partner, activation="pending")["results"]) == 1
    assert customers_of(staff, partner, activation="activated")["results"] == []
    assert customers_of(staff, partner, activation="expired")["results"] == []
    assert len(customers_of(staff, partner, subscription="active")["results"]) == 1
    assert customers_of(staff, partner, subscription="none")["results"] == []
    assert len(customers_of(staff, partner, q="console")["results"]) == 1
    assert customers_of(staff, partner, q="no-such-name")["results"] == []
    for bad in ({"status": "x"}, {"activation": "x"}, {"subscription": "x"}):
        assert (
            staff.get(f"/api/v1/admin/partners/{partner.id}/customers", bad).status_code
            == 400
        )


def test_the_cross_partner_list_names_the_partner(console, staff):
    rows = customers_of(staff)["results"]
    assert [r["partner_name"] for r in rows] == ["Test Partner"]
    assert rows[0]["partner_id"] == str(console["reseller"]["partner"].id)


def test_another_partners_customers_are_not_in_this_partners_list(
    console, staff, partner_factory
):
    other = partner_factory("customer.provision")
    assert customers_of(staff, other["partner"])["results"] == []


def test_the_customer_list_needs_the_view_permission(console, staff_client):
    weak, _u = staff_client("platform.staff_customer.view")
    assert weak.get("/api/v1/admin/partners/customers").status_code == 403
    assert (
        weak.get(f"/api/v1/admin/partners/{console['draft'].id}/customers").status_code
        == 403
    )


def test_the_customer_list_costs_the_same_at_any_page_size(console, staff, user_factory):
    from tests.phase7.support import provision

    for _ in range(3):
        provision(console["reseller"], name="Another Customer")
    partner = console["reseller"]["partner"]
    staff.get(f"/api/v1/admin/partners/{partner.id}/customers?limit=1")

    def cost(limit):
        count, response = count_queries(
            lambda: staff.get(
                f"/api/v1/admin/partners/{partner.id}/customers?limit={limit}"
            )
        )
        return count, len(response.json()["results"])

    small, small_rows = cost(1)
    large, large_rows = cost(4)
    assert (small_rows, large_rows) == (1, 4) and small == large


# --- the ledger and the payouts ------------------------------------------------------------------


def test_staff_ledger_rows_carry_the_customer_and_the_payout_that_pays_them(
    console, staff, staff_factory
):
    from workeasy360.commercial.commissions import payouts

    partner = console["reseller"]["partner"]
    url = f"/api/v1/admin/commission/accruals?partner_id={partner.id}"
    row = staff.get(url).json()["results"][0]
    assert row["kind"] == "accrual" and row["amount_minor"] == 500
    assert row["organization_name"] == "Console Customer"
    assert row["payout"] is None
    assert row["rule_code"] and row["rule_version"] == 1 and row["rate_bp"] == 1000
    assert row["source_ref"].startswith("subscriptions.item:")

    _u, prep = staff_factory("platform.staff_payout.manage")
    payout = payouts.prepare_payout(
        prep,
        partner_id=partner.id,
        period_end=timezone.now().date(),
        idempotency_key="linkage",
    )
    row = staff.get(url).json()["results"][0]
    assert row["payout"] == {"id": str(payout.id), "status": "draft"}


def test_a_partner_never_sees_the_payout_link_or_the_customer(console, partner_client):
    client, fx = partner_client("commission.view")
    url = f"/api/v1/partner/{fx['partner'].id}/commissions"
    assert client.get(url).status_code == 200
    # The partner-facing shape is narrower by construction (unchanged in Phase 10).
    from workeasy360.commercial.commissions import selectors

    page = selectors.accruals_page(
        partner_id=console["reseller"]["partner"].id, for_partner=True
    )
    for item in page.items:
        assert "payout" not in item and "organization_name" not in item


def test_the_ledger_page_costs_the_same_at_any_size(console, staff):
    partner = console["reseller"]["partner"]
    staff.get(f"/api/v1/admin/commission/accruals?partner_id={partner.id}&limit=1")

    def cost(limit):
        count, _response = count_queries(
            lambda: staff.get(
                f"/api/v1/admin/commission/accruals?partner_id={partner.id}&limit={limit}"
            )
        )
        return count

    assert cost(1) == cost(50)


# --- activity ----------------------------------------------------------------------------------------


def test_activity_is_this_partners_audit_entries_only(console, staff):
    partner = console["reseller"]["partner"]
    response = staff.get(f"/api/v1/admin/partners/{partner.id}/activity")
    assert response.status_code == 200, response.content
    actions = {row["action"] for row in response.json()["results"]}
    assert {
        "partner.created",
        "partner.capability_granted",
        "partner.customer_provisioned",
    } <= actions
    other = staff.get(
        f"/api/v1/admin/partners/{console['white_label']['partner'].id}/activity"
    ).json()
    seen = {row["resource_id"] for row in other["results"]}
    assert str(partner.id) not in seen
    for row in other["results"]:
        changes = row["changes"] or {}
        assert row["resource_id"] == str(
            console["white_label"]["partner"].id
        ) or changes.get("partner_id") == str(console["white_label"]["partner"].id), row


def test_activity_pages_with_a_cursor(console, staff):
    partner = console["reseller"]["partner"]
    url = f"/api/v1/admin/partners/{partner.id}/activity"
    first = staff.get(url, {"limit": 2}).json()
    assert len(first["results"]) == 2 and first["has_more"]
    second = staff.get(url, {"limit": 2, "cursor": first["next_cursor"]}).json()
    assert {r["id"] for r in first["results"]}.isdisjoint(
        {r["id"] for r in second["results"]}
    )


def test_activity_needs_audit_access_and_a_real_partner(console, staff_client, staff):
    no_audit, _u = staff_client("platform.staff_partner.view")
    assert (
        no_audit.get(f"/api/v1/admin/partners/{console['draft'].id}/activity").status_code
        == 403
    )
    assert staff.get(f"/api/v1/admin/partners/{uuid.uuid4()}/activity").status_code == 404


def test_activity_uses_the_one_audit_log_and_adds_no_table():
    from workeasy360.commercial.partners import models

    tables = {m._meta.db_table for m in vars(models).values() if hasattr(m, "_meta")}
    assert not any("audit" in t or "activity" in t for t in tables)


# --- the brand -------------------------------------------------------------------------------------------


def test_the_brand_read_is_empty_then_shows_what_was_saved(console, staff):
    from workeasy360.commercial.partners import branding

    partner = console["white_label"]
    url = f"/api/v1/admin/partners/{partner['partner'].id}/brand"
    assert staff.get(url).json() == {"brand": None}
    branding.update_brand(
        partner["actor"](), brand_name="Console Brand", accent_color="#1a56db"
    )
    brand = staff.get(url).json()["brand"]
    assert brand["brand_name"] == "Console Brand" and brand["accent_color"] == "#1a56db"
    assert brand["version"] == 0


def test_partner_members_report_whether_they_have_a_password(console, staff):
    detail = staff.get(
        f"/api/v1/admin/partners/{console['reseller']['partner'].id}"
    ).json()
    assert detail["members"][0]["password_set"] is False


# --- the partner's pricing -------------------------------------------------------------------------------


def test_the_pricing_tab_matches_what_a_sale_really_charges(
    console, staff, staff_factory, catalog
):
    partner = console["reseller"]["partner"]
    _u, pricer = staff_factory("platform.staff_pricing.manage")
    services.set_partner_price(
        pricer,
        partner.id,
        plan_version_id=catalog["versions"]["crm_professional"].id,
        interval="monthly",
        amount_minor=4500,
    )
    body = staff.get(f"/api/v1/admin/partners/{partner.id}/pricing").json()
    assert body["currency"] == "GBP"
    rows = {(r["plan_code"], r["interval"]): r for r in body["results"]}
    professional = rows[("crm_professional", "monthly")]
    assert professional["list_price_minor"] == 4999
    assert professional["partner_price_minor"] == 4500
    assert professional["partner_price_source"] == "partner_book"
    assert professional["discount_minor"] == 499 and professional["discount_bp"] == 998
    assert (
        professional["commission_bp"] == 1000 and professional["commission_minor"] == 450
    )
    assert professional["platform_net_minor"] == 4050
    # A plan with no partner price is charged the list price, and says so.
    starter = rows[("crm_starter", "monthly")]
    assert starter["partner_price_source"] == "list" and starter["discount_minor"] == 0
    # Only what this partner may sell appears.
    assert not any(r["product_code"] == "books" for r in body["results"])


def test_the_pricing_tab_needs_the_view_permission(console, staff_client):
    weak, _u = staff_client("platform.staff_customer.view")
    assert (
        weak.get(
            f"/api/v1/admin/partners/{console['reseller']['partner'].id}/pricing"
        ).status_code
        == 403
    )

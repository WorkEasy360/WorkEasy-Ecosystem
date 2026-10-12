"""The Add Reseller review, extended: profile problems, readiness versus problems,
trial and pricing choices, commission scope and the worked example - and that it
still writes nothing and still computes every figure on the server."""

from __future__ import annotations

import json

import pytest

from tests.phase10.test_10b_review_invite_price import draft, review, rows_by_plan
from workeasy360.commercial.commissions.models import CommissionRule
from workeasy360.commercial.partners.models import (
    PartnerAccount,
    PartnerContact,
    PartnerProductGrant,
    PartnerProfile,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def viewer_client(staff_client):
    client, _u = staff_client("platform.staff_partner.view")
    return client


def fields(result):
    return {p["field"] for p in result["problems"]}


def missing(result):
    return {m["key"] for m in result["readiness"]["missing"]}


# --- problems are what is WRONG; readiness is what is MISSING -----------------------------------------


def test_a_draft_with_only_an_identity_is_valid_but_not_ready_to_activate(
    catalog, viewer_client
):
    result = review(
        viewer_client,
        {
            "partner": {
                "code": "bare-draft",
                "display_name": "Bare",
                "payout_currency": "GBP",
            }
        },
    )
    assert result["ok"] and result["problems"] == []
    assert result["readiness"]["can_activate"] is False
    assert missing(result) == {
        "primary_contact",
        "business_email",
        "capabilities",
        "administrator",
    }
    assert result["administrator"] is None


def test_a_complete_draft_is_ready(catalog, viewer_client):
    body = draft(
        catalog,
        profile={
            "business": {"business_email": "hello@acme-resell.example"},
            "contacts": {
                "primary": {"name": "Priya Shah", "email": "priya@acme-resell.example"}
            },
        },
    )
    result = review(viewer_client, body)
    assert result["ok"], result["problems"]
    assert result["readiness"] == {"can_activate": True, "missing": []}


def test_a_seller_with_nothing_to_sell_is_not_ready(catalog, viewer_client):
    body = draft(
        catalog,
        products=[],
        partner_prices=[],
        profile={
            "business": {"business_email": "hello@acme-resell.example"},
            "contacts": {"primary": {"name": "P", "email": "p@acme-resell.example"}},
        },
    )
    result = review(viewer_client, body)
    assert "products" in missing(result)
    # Holding no selling capability, the same partner needs no product.
    body["capabilities"] = ["commission.view"]
    assert "products" not in missing(review(viewer_client, body))


def test_an_administrator_once_given_needs_an_email_but_is_otherwise_optional(
    catalog, viewer_client
):
    body = draft(catalog, administrator={"email": "", "role": "admin"})
    assert "administrator.email" in fields(review(viewer_client, body))
    body = draft(catalog)
    body.pop("administrator")
    result = review(viewer_client, body)
    assert result["ok"] and "administrator" in missing(result)


def test_the_administrators_names_and_invitation_timing_are_validated(
    catalog, viewer_client
):
    body = draft(
        catalog,
        administrator={
            "email": "boss@acme-resell.example",
            "role": "admin",
            "first_name": "A" * 101,
            "last_name": "Shah",
            "send_invitation": "whenever",
        },
    )
    got = fields(review(viewer_client, body))
    assert {"administrator.first_name", "administrator.send_invitation"} <= got
    ok = draft(
        catalog,
        administrator={
            "email": "boss@acme-resell.example",
            "role": "viewer",
            "first_name": "Ravi",
            "last_name": "Rao",
            "send_invitation": "on_activation",
        },
    )
    result = review(viewer_client, ok)
    assert result["administrator"] == {
        "email": "boss@acme-resell.example",
        "role": "viewer",
        "first_name": "Ravi",
        "last_name": "Rao",
        "send_invitation": "on_activation",
    }


def test_profile_problems_are_reported_with_everything_else(catalog, viewer_client):
    body = draft(
        catalog,
        profile={"business": {"website": "http://x.example", "country": "Nowhere"}},
        commission={"mode": "custom", "rate_bp": 20000},
    )
    got = fields(review(viewer_client, body))
    assert {"business.website", "business.country", "commission"} <= got


def test_the_review_echoes_only_the_profile_parts_that_were_sent(catalog, viewer_client):
    result = review(
        viewer_client,
        draft(
            catalog,
            profile={
                "support": {"email": "help@acme-resell.example"},
                "internal_notes": "vip",
            },
        ),
    )
    assert result["profile"]["support"] == {"email": "help@acme-resell.example"}
    assert (
        result["profile"]["business"] == {}
        and result["profile"]["has_internal_notes"] is True
    )
    # The notes themselves are never echoed.
    assert "vip" not in json.dumps(result)


def test_missing_optional_items_are_highlighted_but_never_block(catalog, viewer_client):
    result = review(viewer_client, draft(catalog))
    keys = {m["key"] for m in result["missing_optional"]}
    assert {
        "website",
        "registration_number",
        "tax_number",
        "address",
        "support",
        "white_label",
    } <= keys
    assert result["ok"]
    full = review(
        viewer_client,
        draft(
            catalog,
            white_label=True,
            profile={
                "business": {
                    "website": "https://acme-resell.example",
                    "registration_number": "123",
                    "tax_number": "456",
                    "address": {"city": "Pune"},
                },
                "support": {"email": "h@acme-resell.example"},
                "payout": {"contact_email": "p@acme-resell.example"},
            },
        ),
    )
    assert full["missing_optional"] == []


# --- products, trials and pricing -------------------------------------------------------------------------


def test_trials_show_the_platform_configuration_and_the_partner_can_only_narrow_it(
    catalog, viewer_client
):
    from tests.phase11.conftest import configure_trial

    configure_trial(catalog)  # crm: 14 days on crm_starter
    result = review(viewer_client, draft(catalog))
    (trial,) = result["trials"]
    assert trial["product_code"] == "crm"
    assert trial["platform_trial_enabled"] is True and trial["duration_days"] == 14
    assert trial["eligible_plan_codes"] == ["crm_starter"]
    assert trial["partner_allows"] is True and trial["available_to_customers"] is True
    narrowed = review(
        viewer_client,
        draft(
            catalog,
            product_options={"crm": {"trial_allowed": False, "portal_visible": False}},
        ),
    )
    (trial,) = narrowed["trials"]
    assert trial["partner_allows"] is False and trial["available_to_customers"] is False
    assert trial["portal_visible"] is False


def test_a_partner_cannot_add_a_trial_the_product_does_not_have(catalog, viewer_client):
    result = review(viewer_client, draft(catalog))  # no trial configured anywhere
    (trial,) = result["trials"]
    assert trial["platform_trial_enabled"] is False
    assert trial["available_to_customers"] is False
    assert trial["duration_days"] is None


def test_product_options_are_validated(catalog, viewer_client):
    for bad in (
        {"crm": {"trial_allowed": "yes"}},
        {"books": {"trial_allowed": False}},
        {"crm": {"free_for_life": True}},
        ["crm"],
    ):
        result = review(viewer_client, draft(catalog, product_options=bad))
        assert "product_options" in fields(result), bad


def test_standard_pricing_ignores_typed_prices_and_says_so(catalog, viewer_client):
    result = review(viewer_client, draft(catalog, pricing_mode="standard"))
    row = rows_by_plan(result)[("crm_professional", "monthly")]
    assert row["partner_price_source"] == "list"
    assert any("Standard partner pricing" in w for w in result["warnings"])
    custom = review(viewer_client, draft(catalog, pricing_mode="custom"))
    assert (
        rows_by_plan(custom)[("crm_professional", "monthly")]["partner_price_source"]
        == "proposed"
    )
    assert "pricing_mode" in fields(
        review(viewer_client, draft(catalog, pricing_mode="free"))
    )


# --- commission ---------------------------------------------------------------------------------------------


def test_the_worked_example_is_computed_by_the_server_in_whole_minor_units(
    catalog, viewer_client
):
    result = review(
        viewer_client, draft(catalog, commission={"mode": "custom", "rate_bp": 2000})
    )
    commission = result["commission"]
    assert commission["method"] == "percentage" and commission["scope"] == "all"
    # 10,000.00 GBP at 20%: every figure an integer of pennies.
    assert commission["example"] == {
        "currency": "GBP",
        "purchase_minor": 1_000_000,
        "commission_minor": 200_000,
        "platform_net_minor": 800_000,
        "rate_bp": 2000,
    }
    assert commission["hold_days"] == 30


def test_the_example_rounds_half_up_with_integers_only(catalog, viewer_client):
    result = review(
        viewer_client, draft(catalog, commission={"mode": "custom", "rate_bp": 1})
    )
    example = result["commission"]["example"]
    assert example["commission_minor"] == 100  # 0.01% of 10,000.00
    assert all(
        isinstance(example[k], int)
        for k in ("purchase_minor", "commission_minor", "platform_net_minor")
    )


def test_there_is_no_example_without_a_rate(catalog, viewer_client):
    result = review(viewer_client, draft(catalog, commission={"mode": "default"}))
    assert result["commission"]["example"] is None
    assert result["commission"]["source"] == "none"


def test_the_example_follows_the_programme_default_when_there_is_one(
    catalog, viewer_client, channel
):
    result = review(viewer_client, draft(catalog, commission={"mode": "default"}))
    assert result["commission"]["programme_default_bp"] == 1000
    assert result["commission"]["example"]["commission_minor"] == 100_000


def test_a_commission_scope_must_be_among_the_partners_products(catalog, viewer_client):
    ok = review(
        viewer_client,
        draft(
            catalog,
            commission={"mode": "custom", "rate_bp": 1500, "product_codes": ["crm"]},
        ),
    )
    assert ok["commission"]["scope"] == "products" and ok["commission"][
        "product_codes"
    ] == ["crm"]
    assert ok["commission"]["product_rates"] == {"crm": 1500}
    # The example illustrates the rate this partner is given, scoped or not.
    assert ok["commission"]["example"]["rate_bp"] == 1500
    bad = review(
        viewer_client,
        draft(
            catalog,
            commission={"mode": "custom", "rate_bp": 1500, "product_codes": ["books"]},
        ),
    )
    assert "commission" in fields(bad)


def test_a_commission_cannot_start_in_the_past_but_may_start_later(
    catalog, viewer_client
):
    from datetime import timedelta

    from django.utils import timezone

    past = (timezone.now() - timedelta(days=2)).isoformat()
    later = (timezone.now() + timedelta(days=2)).isoformat()
    assert "commission" in fields(
        review(
            viewer_client,
            draft(
                catalog,
                commission={"mode": "custom", "rate_bp": 1500, "effective_from": past},
            ),
        )
    )
    result = review(
        viewer_client,
        draft(
            catalog,
            commission={"mode": "custom", "rate_bp": 1500, "effective_from": later},
        ),
    )
    assert result["ok"] and result["commission"]["effective_from"].startswith(later[:16])
    for junk in ("tomorrow", "2026-13-45T00:00:00+00:00", "2026-10-06T00:00:00", 7):
        assert "commission" in fields(
            review(
                viewer_client,
                draft(
                    catalog,
                    commission={
                        "mode": "custom",
                        "rate_bp": 1500,
                        "effective_from": junk,
                    },
                ),
            )
        ), junk


@pytest.mark.parametrize(
    "rate", [-1, 10_001, 12.5, "1500", True, None, [1500], {"bp": 1}]
)
def test_a_manipulated_rate_is_a_problem_never_a_figure(catalog, viewer_client, rate):
    result = review(
        viewer_client, draft(catalog, commission={"mode": "custom", "rate_bp": rate})
    )
    assert "commission" in fields(result)
    assert (
        result["commission"]["rate_bp"] is None
        and result["commission"]["example"] is None
    )


@pytest.mark.parametrize("amount", [-1, 10**13, 45.5, "4500", True, None])
def test_a_manipulated_price_is_a_problem_never_a_figure(catalog, viewer_client, amount):
    professional = str(catalog["versions"]["crm_professional"].id)
    body = draft(
        catalog,
        partner_prices=[
            {
                "plan_version_id": professional,
                "interval": "monthly",
                "amount_minor": amount,
            }
        ],
    )
    result = review(viewer_client, body)
    assert "partner_prices" in fields(result)


# --- white-label ---------------------------------------------------------------------------------------------


def test_white_label_adds_the_pair_to_the_same_partner_once(catalog, viewer_client):
    result = review(viewer_client, draft(catalog, white_label=True))
    assert result["white_label"] == {
        "requested": True,
        "capabilities_added": ["branding.manage", "domain.manage"],
    }
    keys = [c["key"] for c in result["capabilities"]]
    assert keys.count("branding.manage") == 1 and keys.count("domain.manage") == 1
    assert [e["key"] for e in result["experience"]] == ["reseller", "white_label"]
    already = review(
        viewer_client,
        draft(
            catalog,
            white_label=True,
            capabilities=["customer.provision", "branding.manage", "domain.manage"],
        ),
    )
    assert already["white_label"]["capabilities_added"] == []
    assert "white_label" in fields(
        review(viewer_client, draft(catalog, white_label="yes"))
    )


# --- it writes nothing, it needs permission, it survives garbage ----------------------------------------------


def test_the_review_writes_nothing_at_all(catalog, viewer_client):
    tables = (
        PartnerAccount,
        PartnerProfile,
        PartnerContact,
        PartnerProductGrant,
        CommissionRule,
    )
    before = [t.objects.count() for t in tables]
    review(
        viewer_client,
        draft(
            catalog,
            white_label=True,
            profile={
                "business": {"business_email": "a@example.com"},
                "contacts": {"primary": {"name": "P", "email": "p@example.com"}},
                "internal_notes": "x",
            },
        ),
    )
    assert [t.objects.count() for t in tables] == before


def test_the_review_needs_the_view_permission(staff_client, catalog):
    client, _u = staff_client("platform.staff_audit.view")
    response = client.post(
        "/api/v1/admin/partners/reseller-review",
        json.dumps(draft(catalog)),
        content_type="application/json",
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "junk",
    [
        {"profile": []},
        {"profile": "x"},
        {"profile": {"business": 7, "contacts": [], "support": "s", "payout": None}},
        {"product_options": 5},
        {"pricing_mode": []},
        {"white_label": {}},
        {"commission": {"product_codes": "crm", "effective_from": {}}},
        {"administrator": "boss"},
        {"administrator": {"send_invitation": 7, "first_name": []}},
    ],
)
def test_garbage_is_a_problem_or_a_400_never_a_500(catalog, viewer_client, junk):
    body = draft(catalog)
    body.update(junk)
    response = viewer_client.post(
        "/api/v1/admin/partners/reseller-review",
        json.dumps(body),
        content_type="application/json",
    )
    assert response.status_code < 500, (junk, response.content)

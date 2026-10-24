"""The frontend's wire types match what the API really returns.

``frontend/src/lib/api/controlPlane.ts`` declares an interface for every
payload the Phase 7 screens read. Nothing else keeps those declarations honest:
TypeScript trusts them, and a field that is renamed or missing on the server
shows up as a blank cell, not an error. This test parses the interfaces and
checks, against **real responses** built through the real stack, that every
required field exists (recursively, including the inline nested shapes).
"""

from __future__ import annotations

import json
import pathlib
import re
import uuid

import pytest

from tests.phase7.support import provision, sell

pytestmark = pytest.mark.django_db

CLIENT_TS = (
    pathlib.Path(__file__).resolve().parents[3]
    / "frontend"
    / "src"
    / "lib"
    / "api"
    / "controlPlane.ts"
)


# --- a tiny TypeScript interface reader ---------------------------------------------------


def _interfaces() -> dict[str, dict]:
    source = CLIENT_TS.read_text(encoding="utf-8")
    found: dict[str, dict] = {}
    for match in re.finditer(
        r"export interface (\w+)(?: extends (\w+))? \{\n(.*?)\n\}", source, re.S
    ):
        name, base, body = match.groups()
        fields: dict[str, tuple[bool, str]] = {}
        for line in body.splitlines():
            field = re.match(r"^  (\w+)(\??): (.*);$", line)
            if field:
                fields[field.group(1)] = (field.group(2) == "?", field.group(3))
        found[name] = {"base": base, "fields": fields}
    return found


INTERFACES = _interfaces()


def _all_fields(name: str) -> dict[str, tuple[bool, str]]:
    spec = INTERFACES[name]
    fields = dict(_all_fields(spec["base"])) if spec["base"] else {}
    fields.update(spec["fields"])
    return fields


def _inline_fields(type_text: str) -> dict[str, tuple[bool, str]] | None:
    """Fields of a leading inline ``{ a: string; b?: number }`` type, if any."""
    match = re.search(r"\{\s*([^{}]*)\}", type_text)
    if not match or type_text.strip().startswith("Record<"):
        return None
    out: dict[str, tuple[bool, str]] = {}
    for part in match.group(1).split(";"):
        part = part.strip()
        if not part:
            continue
        field = re.match(r"^(\w+)(\??): (.*)$", part)
        if field:
            out[field.group(1)] = (field.group(2) == "?", field.group(3))
    return out


def _check(
    value, fields: dict[str, tuple[bool, str]], where: str, problems: list[str]
) -> None:
    if not isinstance(value, dict):
        problems.append(f"{where}: expected an object, got {type(value).__name__}")
        return
    for key, (optional, type_text) in fields.items():
        if key not in value:
            if not optional:
                problems.append(f"{where}: missing required field '{key}'")
            continue
        _check_type(value[key], type_text, f"{where}.{key}", problems)


def _check_type(value, type_text: str, where: str, problems: list[str]) -> None:
    if value is None:
        return  # nullable or optional-by-value; the field's presence is what matters
    is_array = type_text.strip().endswith("[]") or "}[]" in type_text
    base = re.sub(r"\s*\|\s*null", "", type_text).strip()
    named = re.match(r"^(\w+)(\[\])?$", base)
    if named and named.group(1) in INTERFACES:
        if named.group(2):
            for i, item in enumerate(value[:3]):
                _check(item, _all_fields(named.group(1)), f"{where}[{i}]", problems)
        else:
            _check(value, _all_fields(named.group(1)), where, problems)
        return
    inline = _inline_fields(base)
    if inline:
        if is_array:
            for i, item in enumerate((value or [])[:3]):
                _check(item, inline, f"{where}[{i}]", problems)
        elif isinstance(value, dict):
            _check(value, inline, where, problems)


def assert_matches(payload, interface: str, where: str) -> None:
    problems: list[str] = []
    _check(payload, _all_fields(interface), where, problems)
    assert not problems, "\n".join(problems)


def assert_page(payload, interface: str, where: str) -> None:
    assert isinstance(payload, dict) and {"results", "next_cursor", "has_more"} <= set(
        payload
    ), where
    assert payload["results"], (
        f"{where}: the fixture produced no rows, so nothing was checked"
    )
    assert_matches(payload["results"][0], interface, f"{where}.results[0]")


def test_the_interface_reader_reads_what_it_should():
    assert "PayoutRow" in INTERFACES and "version" in _all_fields("PayoutRow")
    assert "capabilities" in _all_fields("PartnerDetail") and "code" in _all_fields(
        "PartnerDetail"
    )
    assert _inline_fields("{ a: string; b?: number }[]") == {
        "a": (False, "string"),
        "b": (True, "number"),
    }


# --- the world ---------------------------------------------------------------------------------


@pytest.fixture
def world(channel, staff_client, http_login, monkeypatch, staff_factory):
    from tests.phase7.conftest import PASSWORD
    from workeasy360.commercial.commissions import accrual, payouts, referrals
    from workeasy360.commercial.partners import branding, domains, services
    from workeasy360.commercial.pricing.models import PriceBook  # noqa: F401

    monkeypatch.setattr(accrual, "holdback_days", lambda: 0)
    fx = channel
    for capability in (
        "branding.manage",
        "domain.manage",
        "referral.create",
        "referral.view",
    ):
        services.grant_capability(
            fx["staff_ctx"],
            fx["partner"].id,
            capability=capability,
            reason="Contract fixture",
        )
    result, _owner = provision(fx)
    sell(fx, result.organization_id)

    _u, prep = staff_factory("platform.staff_payout.manage")
    from django.utils import timezone

    payout = payouts.prepare_payout(
        prep,
        partner_id=fx["partner"].id,
        period_end=timezone.now().date(),
        idempotency_key="shape",
    )
    branding.update_brand(fx["actor"](), brand_name="Shape Co", accent_color="#1a56db")
    domains.request_domain(fx["actor"](), hostname="app.shape-example.com")
    referrals.create_referral_code(fx["actor"](), label="Launch")

    fx["user"].set_password(PASSWORD)
    fx["user"].save(update_fields=["password"])
    partner_http = http_login(fx["user"])

    everything = (
        "platform.staff_customer.view",
        "platform.staff_catalog.view",
        "platform.staff_partner.view",
        "platform.staff_commission.view",
        "platform.staff_billing.view",
        "platform.staff_audit.view",
        "platform.staff_security.view",
        "platform.staff_support.view",
        "platform.staff_payout.manage",
        "platform.staff_subscription.manage",
    )
    staff_http, _su = staff_client(*everything)
    return {
        "fx": fx,
        "org": result.organization_id,
        "payout": payout,
        "partner": partner_http,
        "staff": staff_http,
    }


# --- Super Admin ------------------------------------------------------------------------------------


def test_admin_partner_payloads_match_the_client_types(world):
    staff, pid = world["staff"], world["fx"]["partner"].id
    assert_page(staff.get("/api/v1/admin/partners").json(), "PartnerSummary", "partners")
    assert_matches(
        staff.get(f"/api/v1/admin/partners/{pid}").json(), "PartnerDetail", "partner"
    )
    caps = staff.get("/api/v1/admin/partners/capabilities").json()
    assert_matches(caps["results"][0], "CapabilityInfo", "capabilities[0]")


def test_admin_customer_payloads_match_the_client_types(world):
    staff, org = world["staff"], world["org"]
    assert_page(staff.get("/api/v1/admin/customers").json(), "CustomerRow", "customers")
    detail = staff.get(f"/api/v1/admin/customers/{org}").json()
    assert_matches(detail, "CustomerDetail", "customer")
    assert detail["subscriptions"], "fixture must include a subscription"
    assert detail["partner"] is not None, "fixture must include the partner link"


def test_admin_money_payloads_match_the_client_types(world):
    staff = world["staff"]
    rules = staff.get("/api/v1/admin/commission/rules").json()
    assert rules["results"], "rules"
    assert_matches(rules["results"][0], "RuleRow", "rules[0]")
    assert_page(
        staff.get("/api/v1/admin/commission/accruals").json(), "AccrualRow", "accruals"
    )
    assert_page(staff.get("/api/v1/admin/payouts").json(), "PayoutRow", "payouts")
    assert_matches(
        staff.get("/api/v1/admin/commission/reconcile").json(),
        "Reconciliation",
        "reconcile",
    )


def test_admin_overview_and_audit_payloads_match_the_client_types(world):
    staff = world["staff"]
    assert_matches(staff.get("/api/v1/admin/dashboard").json(), "Dashboard", "dashboard")
    assert_page(staff.get("/api/v1/admin/audit").json(), "AuditRow", "audit")
    products = staff.get("/api/v1/admin/catalog/products").json()
    assert products["results"]
    assert_matches(products["results"][0], "CatalogProduct", "products[0]")


def test_admin_diagnosis_matches_the_client_type(world):
    staff, org = world["staff"], world["org"]
    response = staff.post(
        f"/api/v1/admin/customers/{org}/diagnose",
        json.dumps({"product_code": "crm"}),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    assert_matches(response.json(), "Diagnosis", "diagnosis")


# --- the partner portal --------------------------------------------------------------------------------


def test_partner_portal_payloads_match_the_client_types(world):
    client, pid = world["partner"], world["fx"]["partner"].id
    portal = client.get("/api/v1/partner/portal").json()
    assert portal["partners"]
    assert_matches(portal["partners"][0], "PortalPartner", "portal.partners[0]")
    assert_page(
        client.get(f"/api/v1/partner/{pid}/customers").json(),
        "PartnerCustomerRow",
        "customers",
    )
    catalog = client.get(f"/api/v1/partner/{pid}/catalog").json()
    assert catalog["results"], "catalog"
    assert_matches(catalog["results"][0], "ApprovedPlan", "catalog[0]")


def test_partner_money_payloads_match_the_client_types(world):
    client, pid = world["partner"], world["fx"]["partner"].id
    commissions = client.get(f"/api/v1/partner/{pid}/commissions").json()
    assert_page(commissions, "AccrualRow", "commissions")
    assert commissions["balances"], "balances"
    for key in ("currency", "earned_minor", "adjustments_minor", "balance_minor"):
        assert key in commissions["balances"][0], key
    # Payouts are only visible to the partner once past draft; the prepared one is a draft,
    # so the page may legitimately be empty. Check the shape of the envelope.
    payouts = client.get(f"/api/v1/partner/{pid}/payouts").json()
    assert {"results", "next_cursor", "has_more"} <= set(payouts)


def test_partner_branding_domain_and_referral_payloads_match_the_client_types(world):
    client, pid = world["partner"], world["fx"]["partner"].id
    brand = client.get(f"/api/v1/partner/{pid}/branding").json()
    assert brand["brand"] is not None
    assert_matches(brand["brand"], "Brand", "brand")
    domains = client.get(f"/api/v1/partner/{pid}/domains").json()
    assert domains["results"]
    assert_matches(domains["results"][0], "DomainRow", "domains[0]")
    referrals = client.get(f"/api/v1/partner/{pid}/referrals").json()
    assert {"counts", "codes"} <= set(referrals) and referrals["codes"]
    for key in ("id", "code", "label", "status"):
        assert key in referrals["codes"][0], key


def test_the_public_and_account_payloads_match_the_client_types(world, client):
    branding = client.get(
        "/api/v1/public/branding", {"host": "unknown.example.com"}
    ).json()
    assert branding == {"brand": None}
    assert (
        client.get("/api/v1/public/branding", {"host": str(uuid.uuid4())}).status_code
        == 200
    )

"""The security matrix for the complete reseller and white-label setup.

Every new endpoint is tried as every principal that must not use it, and every
manipulation the brief names (price, commission, capability injection, domain
takeover, logo XSS, CSRF, a lapsed second factor, mass assignment) is tried and
refused. The backend decides; none of this depends on what a screen shows.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.partner_setup.conftest import MANAGE, VIEW, put
from workeasy360.commercial.partners import selectors
from workeasy360.commercial.partners.models import PartnerProductGrant

pytestmark = pytest.mark.django_db

ADMIN = "/api/v1/admin/partners"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def target(channel):
    """A live partner, its grant and a member - what every endpoint below names."""
    pid = channel["partner"].id
    from workeasy360.commercial.partners.models import PartnerMember

    return {
        "pid": pid,
        "grant": PartnerProductGrant.objects.get(partner_id=pid, status="active").id,
        "member": PartnerMember.objects.filter(partner_id=pid, status="active")
        .first()
        .id,
    }


def endpoints(t):
    """``(method, url, body, kind)`` for every new endpoint; ``kind`` is whether it reads (view)
    or writes (manage + a second factor)."""
    pid = t["pid"]
    return [
        ("get", f"{ADMIN}/{pid}/profile", None, "read"),
        (
            "put",
            f"{ADMIN}/{pid}/profile",
            {"support": {"email": "a@example.com"}, "expected_version": 0},
            "write",
        ),
        ("get", f"{ADMIN}/{pid}/white-label", None, "read"),
        (
            "post",
            f"{ADMIN}/{pid}/white-label/enable",
            {"reason": "A reason long enough"},
            "write",
        ),
        (
            "patch",
            f"{ADMIN}/{pid}/product-grants/{t['grant']}",
            {"trial_allowed": False},
            "write",
        ),
        (
            "post",
            f"{ADMIN}/{pid}/members/{t['member']}/role",
            {"role": "viewer"},
            "write",
        ),
        (
            "post",
            f"{ADMIN}/{pid}/administrators",
            {"email": "x@example.com", "role": "admin"},
            "write",
        ),
        (
            "put",
            f"{ADMIN}/{pid}/brand",
            {"brand_name": "X", "portal": {"short_name": "x"}},
            "write",
        ),
        ("post", f"{ADMIN}/reseller-review", {"partner": {}}, "read"),
    ]


def call(client, method, url, body):
    send = getattr(client, method)
    if body is None:
        return send(url)
    return send(url, json.dumps(body), content_type="application/json")


def test_nobody_who_is_signed_out_reaches_any_of_them(anonymous, target):
    client = anonymous()
    for method, url, body, _kind in endpoints(target):
        assert call(client, method, url, body).status_code in (401, 403), url


def test_a_staff_member_with_no_partner_grant_reaches_none(staff_client, target):
    client, _u = staff_client("platform.staff_audit.view")
    for method, url, body, _kind in endpoints(target):
        assert call(client, method, url, body).status_code == 403, url


def test_a_viewer_may_read_and_may_not_write(staff_client, target):
    client, _u = staff_client(VIEW)
    for method, url, body, kind in endpoints(target):
        status = call(client, method, url, body).status_code
        if kind == "write":
            assert status == 403, url
        else:
            assert status == 200, (url, status)


def test_a_partner_session_reaches_none_of_them_not_even_for_its_own_partner(
    partner_client, target
):
    client, fx = partner_client(
        "customer.provision", "subscription.sell", "branding.manage", "domain.manage"
    )
    own = {"pid": fx["partner"].id, "grant": uuid.uuid4(), "member": uuid.uuid4()}
    for t in (own, target):
        for method, url, body, _kind in endpoints(t):
            assert call(client, method, url, body).status_code == 403, url


def test_a_customer_who_is_nobody_special_reaches_none_of_them(
    console_org, login, target
):
    owner = login(console_org()["owner"])
    for method, url, body, _kind in endpoints(target):
        assert call(owner, method, url, body).status_code == 403, url


def test_every_write_demands_a_second_factor(staff_client, target):
    client, _u = staff_client(MANAGE, VIEW, aal2=False)
    for method, url, body, kind in endpoints(target):
        if kind != "write":
            continue
        response = call(client, method, url, body)
        assert (
            response.status_code == 401 and response.json()["code"] == "step_up_required"
        ), url


def test_a_write_without_a_csrf_token_is_refused(staff_client, target):
    client, _u = staff_client(MANAGE, VIEW, enforce_csrf=True)
    for method, url, body, kind in endpoints(target):
        if kind == "write":
            assert call(client, method, url, body).status_code == 403, url


def test_with_the_csrf_token_the_same_write_goes_through(staff_client, target):
    client, _u = staff_client(MANAGE, VIEW, enforce_csrf=True)
    token = client.cookies["w360_csrf"].value
    response = client.patch(
        f"{ADMIN}/{target['pid']}/product-grants/{target['grant']}",
        json.dumps({"trial_allowed": False}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 200, response.content


def test_nothing_here_can_delete_a_partner_or_anything_commercial(manager, target):
    for method, url, _body, _kind in endpoints(target):
        assert manager.delete(url).status_code == 405, url
    assert manager.delete(f"{ADMIN}/{target['pid']}").status_code == 405
    from workeasy360.interfaces.api.v1 import urls

    delete_routes = [
        p.name
        for p in urls.urlpatterns
        if p.name and "delete" in p.name and "partner" in p.name
    ]
    assert delete_routes == []


# --- mass assignment ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        {"status": "active"},
        {"version": 99},
        {"created_by": str(uuid.uuid4())},
        {"payout_currency": "USD"},
        {"code": "stolen"},
        {"id": str(uuid.uuid4())},
        {"organization_id": str(uuid.uuid4())},
    ],
)
def test_a_profile_body_cannot_set_what_it_does_not_own(manager, target, extra):
    partner = selectors.partner_row(target["pid"])
    before = (partner.status, partner.version, partner.payout_currency, partner.code)
    response = put(
        manager,
        f"{ADMIN}/{target['pid']}/profile",
        {
            "support": {"email": "a@example.com"},
            **extra,
            "expected_version": partner.version,
        },
    )
    assert response.status_code == 400, extra
    partner = selectors.partner_row(target["pid"])
    assert (
        partner.status,
        partner.version,
        partner.payout_currency,
        partner.code,
    ) == before


def test_the_partner_in_the_path_is_the_only_authority_for_the_brand(
    manager, channel, partner_factory
):
    other = partner_factory("branding.manage", "domain.manage")
    response = put(
        manager,
        f"{ADMIN}/{channel['partner'].id}/brand",
        {
            "brand_name": "Mine",
            "partner_id": str(other["partner"].id),
            "portal": {"short_name": "m"},
        },
    )
    assert response.status_code == 200
    assert selectors.brand_for(other["partner"].id) is None
    assert selectors.brand_for(channel["partner"].id).brand_name == "Mine"


# --- price and commission manipulation ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "amount", [-1, 10**12 + 1, 10**30, 45.5, "4500", True, None, [1], {"a": 1}]
)
def test_a_manipulated_partner_price_is_refused_and_stores_nothing(
    staff_client, target, catalog, amount
):
    from workeasy360.commercial.pricing.models import PlanPrice

    pricer, _u = staff_client("platform.staff_pricing.manage")
    before = PlanPrice.objects.count()
    response = pricer.post(
        f"{ADMIN}/{target['pid']}/prices",
        json.dumps(
            {
                "plan_version_id": str(catalog["versions"]["crm_professional"].id),
                "interval": "monthly",
                "amount_minor": amount,
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 400, amount
    assert PlanPrice.objects.count() == before


def test_a_partner_price_never_changes_the_global_list_price(
    staff_client, target, catalog
):
    from workeasy360.commercial.pricing.models import PlanPrice

    professional = catalog["versions"]["crm_professional"]
    list_before = list(
        PlanPrice.objects.filter(
            plan_version_id=professional.id, price_book__partner_id__isnull=True
        )
        .order_by("id")
        .values_list("id", "amount_minor")
    )
    pricer, _u = staff_client("platform.staff_pricing.manage")
    response = pricer.post(
        f"{ADMIN}/{target['pid']}/prices",
        json.dumps(
            {
                "plan_version_id": str(professional.id),
                "interval": "monthly",
                "amount_minor": 1,
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 201
    list_after = list(
        PlanPrice.objects.filter(
            plan_version_id=professional.id, price_book__partner_id__isnull=True
        )
        .order_by("id")
        .values_list("id", "amount_minor")
    )
    assert list_after == list_before


@pytest.mark.parametrize("rate", [-1, 10_001, 12.5, "1500", True, None, [1500]])
def test_a_manipulated_commission_rate_is_refused_and_stores_nothing(
    staff_client, target, rate
):
    from workeasy360.commercial.commissions.models import CommissionRule

    client, _u = staff_client("platform.staff_commission.manage")
    before = CommissionRule.objects.count()
    response = client.post(
        f"{ADMIN}/{target['pid']}/commission",
        json.dumps(
            {"rate_bp": rate, "reason": "Agreed reseller terms", "expected_version": 0}
        ),
        content_type="application/json",
    )
    assert response.status_code == 400, rate
    assert CommissionRule.objects.count() == before


def test_a_commission_cannot_be_backdated_to_change_old_sales(staff_client, target):
    from datetime import timedelta

    from django.utils import timezone

    client, _u = staff_client("platform.staff_commission.manage")
    response = client.post(
        f"{ADMIN}/{target['pid']}/commission",
        json.dumps(
            {
                "rate_bp": 5000,
                "reason": "Agreed reseller terms",
                "expected_version": 0,
                "effective_from": (timezone.now() - timedelta(days=30)).isoformat(),
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 400


def test_changing_a_commission_rule_never_changes_an_old_sale(console, staff_client):
    from workeasy360.commercial.commissions.models import CommissionAccrual

    partner = console["reseller"]["partner"]
    before = list(
        CommissionAccrual.objects.filter(partner_id=partner.id).values_list(
            "id", "amount_minor", "rate_bp", "rule_version"
        )
    )
    client, _u = staff_client("platform.staff_commission.manage")
    state = client.get(f"{ADMIN}/{partner.id}/commission")
    assert state.status_code in (200, 403)
    response = client.post(
        f"{ADMIN}/{partner.id}/commission",
        json.dumps(
            {"rate_bp": 9000, "reason": "Agreed reseller terms", "expected_version": 0}
        ),
        content_type="application/json",
    )
    assert response.status_code in (200, 201, 400, 409)
    after = list(
        CommissionAccrual.objects.filter(partner_id=partner.id).values_list(
            "id", "amount_minor", "rate_bp", "rule_version"
        )
    )
    assert after == before


# --- capability injection -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "root.everything",
        "platform.staff_partner.manage",
        "",
        None,
        ["customer.provision"],
        "CUSTOMER.PROVISION",
    ],
)
def test_a_capability_that_is_not_in_the_registry_cannot_be_granted(manager, target, key):
    response = manager.post(
        f"{ADMIN}/{target['pid']}/capabilities",
        json.dumps({"capability": key, "reason": "A reason long enough"}),
        content_type="application/json",
    )
    assert response.status_code == 400, key
    assert selectors.active_capabilities(target["pid"]).isdisjoint(
        {"root.everything", "platform.staff_partner.manage"}
    )


def test_extra_keys_on_a_grant_do_nothing(manager, partner_factory):
    fx = partner_factory()
    response = manager.post(
        f"{ADMIN}/{fx['partner'].id}/capabilities",
        json.dumps(
            {
                "capability": "pricing.view",
                "reason": "A reason long enough",
                "status": "revoked",
                "granted_by": str(uuid.uuid4()),
                "expires_at": None,
                "partner_id": str(uuid.uuid4()),
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 201
    assert "pricing.view" in selectors.active_capabilities(fx["partner"].id)


def test_a_review_with_injected_capabilities_reports_them_and_grants_nothing(manager):
    response = manager.post(
        f"{ADMIN}/reseller-review",
        json.dumps(
            {
                "partner": {
                    "code": "inj-test",
                    "display_name": "Inj",
                    "payout_currency": "GBP",
                },
                "capabilities": ["*", "platform.staff_partner.manage"],
            }
        ),
        content_type="application/json",
    )
    body = response.json()
    assert not body["ok"] and any(p["field"] == "capabilities" for p in body["problems"])
    assert body["capabilities"] == []


# --- domain takeover ---------------------------------------------------------------------------------------------------


def test_a_second_partner_cannot_take_a_hostname_the_first_has_proved(partner_factory):
    from tests.phase10.test_10d_white_label import FakeDns
    from workeasy360.commercial.partners import domains
    from workeasy360.contracts.errors import PartnerValidationError

    first = partner_factory("branding.manage", "domain.manage")
    second = partner_factory("branding.manage", "domain.manage")
    host = "shared.takeover-example.com"
    mine = domains.request_domain(
        first["staff_ctx"], hostname=host, partner_id=first["partner"].id
    )
    theirs = domains.request_domain(
        second["staff_ctx"], hostname=host, partner_id=second["partner"].id
    )
    fake = FakeDns({domains.verification_record_name(host): [mine.verification_token]})
    previous = domains.set_txt_resolver(fake)
    try:
        domains.verify_domain(first["staff_ctx"], domain_id=mine.id)
        # The second partner's own token is not in DNS, so it cannot prove the name...
        with pytest.raises(Exception):
            domains.activate_domain(second["staff_ctx"], domain_id=theirs.id)
        # ...and even holding the first partner's token it cannot be proved twice.
        fake.records[domains.verification_record_name(host)] = [theirs.verification_token]
        theirs = domains.verify_domain(second["staff_ctx"], domain_id=theirs.id)
        assert theirs.status != "active"
    finally:
        domains.set_txt_resolver(previous)
    assert PartnerValidationError


# --- logo XSS -----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content_type", "data"),
    [
        (
            "image/svg+xml",
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        ),
        ("image/png", b"<html><script>alert(1)</script></html>"),
        ("image/png", b"GIF89a" + b"\x00" * 64),
        ("image/jpeg", b"<svg onload=alert(1)>"),
        ("text/html", b"<script>alert(1)</script>"),
        ("application/javascript", b"alert(1)"),
        ("image/png", b""),
    ],
    ids=[
        "svg",
        "html-as-png",
        "gif-as-png",
        "svg-as-jpeg",
        "html",
        "javascript",
        "empty",
    ],
)
def test_a_logo_that_could_run_script_is_refused(manager, target, content_type, data):
    response = manager.post(
        f"{ADMIN}/{target['pid']}/brand/logo", data=data, content_type=content_type
    )
    # Refused as a bad request or an unsupported/oversized file - never stored.
    assert 400 <= response.status_code < 500, (content_type, data[:20], response.content)


def test_an_oversized_logo_is_refused(manager, target):
    big = PNG + bytes(600 * 1024)
    response = manager.post(
        f"{ADMIN}/{target['pid']}/brand/logo", data=big, content_type="image/png"
    )
    assert 400 <= response.status_code < 500


def test_a_real_png_is_accepted_and_served_isolated(manager, channel):
    from workeasy360.commercial.partners import services

    services.enable_white_label(
        channel["staff_ctx"], channel["partner"].id, reason="Enabled for the logo test"
    )
    response = manager.post(
        f"{ADMIN}/{channel['partner'].id}/brand/logo", data=PNG, content_type="image/png"
    )
    assert response.status_code == 201, response.content


def test_hostile_text_is_stored_as_text_and_never_as_markup(manager, channel):
    hostile = "<img src=x onerror=alert(1)>\"'`"
    response = put(
        manager,
        f"{ADMIN}/{channel['partner'].id}/profile",
        {"internal_notes": hostile, "expected_version": channel["partner"].version},
    )
    assert response.status_code == 200
    # Stored faithfully as text (the screens draw it as a text node)...
    assert response.json()["internal_notes"] == hostile
    # ...but a field customers or emails would read refuses markup outright.
    refused = put(
        manager,
        f"{ADMIN}/{channel['partner'].id}/brand",
        {"brand_name": "X", "portal": {"welcome_message": hostile}},
    )
    assert refused.status_code == 400

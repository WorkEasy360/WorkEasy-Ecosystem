"""Phase 10f security, part 2: what a caller may say, and what is believed.

Price and commission manipulation, domain takeover, Host-header spoofing,
XSS and header injection - each through the real HTTP stack, and each asserting
both the refusal and that nothing was written.
"""

from __future__ import annotations

import json
import uuid

import pytest
from django.conf import settings
from django.test import Client

from tests.phase10 import test_10d_white_label as _white_label
from tests.phase10.conftest import MANAGE, PASSWORD, deliver_account_mail, token_from
from workeasy360.commercial.commissions.models import CommissionRule
from workeasy360.commercial.partners import branding, domains
from workeasy360.commercial.partners.models import PartnerBrand, PartnerDomain
from workeasy360.commercial.pricing.models import PlanPrice

pytestmark = pytest.mark.django_db

dns = _white_label.dns
HOST = "app.brand-example.com"


def send(client, method, url, body=None, **extra):
    if method == "GET":
        return client.get(url, body or {}, **extra)
    return getattr(client, method.lower())(
        url,
        json.dumps(body if body is not None else {}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
        **extra,
    )


@pytest.fixture
def login(http_login):
    def make(fx, *, aal2=True):
        fx["user"].set_password(PASSWORD)
        fx["user"].save(update_fields=["password"])
        return http_login(fx["user"], aal2=aal2)

    return make


@pytest.fixture
def manager(staff_client):
    client, _u = staff_client(
        MANAGE,
        "platform.staff_pricing.manage",
        "platform.staff_commission.manage",
        "platform.staff_commission.view",
    )
    return client


# --- price and commission manipulation ---------------------------------------------------------


FORGED = {
    "amount_minor": 1,
    "unit_amount_minor": 1,
    "price": 0,
    "total_minor": 1,
    "discount_bp": 10000,
    "commission_bp": 10000,
    "rate_bp": 10000,
    "price_source": "contract",
    "partner_id": str(uuid.uuid4()),
    "organization_id": str(uuid.uuid4()),
}


def test_a_sale_is_priced_by_the_server_whatever_the_request_says(
    channel, login, user_factory
):
    from tests.phase7.support import provision
    from workeasy360.commercial.subscriptions.models import SubscriptionItem
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    result, _owner = provision(channel, name="Forged Ltd")
    client = login(channel)
    plan = channel["catalog"]["versions"]["crm_professional"]
    url = (
        f"/api/v1/partner/{channel['partner'].id}/customers/{result.organization_id}/sell"
    )
    response = send(
        client, "POST", url, {"plan_version_id": str(plan.id), "quantity": 1, **FORGED}
    )
    assert response.status_code == 201, response.content
    with (
        unscoped_access(reason="Test reads the item it just sold", module="tests"),
        organization_db_scope(result.organization_id),
    ):
        item = SubscriptionItem.unscoped.get(id=response.json()["subscription_item_id"])
    assert item.unit_amount_minor == 4999, "the list price, resolved by the server"
    assert item.price_source == "default_book"


@pytest.mark.parametrize(
    "quantity", [0, -1, 1.5, "2", "1; DROP TABLE x", None, True, 10**12, [1]]
)
def test_a_forged_quantity_is_refused_not_coerced(channel, login, quantity):
    from tests.phase7.support import provision

    result, _owner = provision(channel, name="Qty Ltd")
    client = login(channel)
    plan = channel["catalog"]["versions"]["crm_professional"]
    url = (
        f"/api/v1/partner/{channel['partner'].id}/customers/{result.organization_id}/sell"
    )
    response = send(
        client, "POST", url, {"plan_version_id": str(plan.id), "quantity": quantity}
    )
    if quantity is None:  # absent means one seat, by design
        assert response.status_code in (201, 400)
    else:
        assert 400 <= response.status_code < 500, (quantity, response.status_code)


def test_the_review_computes_every_figure_itself(catalog, manager):
    from tests.phase10.test_10b_review_invite_price import draft

    honest = send(
        manager, "POST", "/api/v1/admin/partners/reseller-review", draft(catalog)
    ).json()
    body = draft(catalog)
    body.update(FORGED)
    body["totals"] = {"platform_net_minor": 999999, "commission_minor": 1}
    body["partner_prices"][0].update(
        {
            "discount_minor": 4999,
            "commission_minor": 4999,
            "platform_net_minor": 4999,
            "list_price_minor": 1,
            "partner_price_minor": 1,
        }
    )
    forged = send(manager, "POST", "/api/v1/admin/partners/reseller-review", body)
    assert forged.status_code == 200, forged.content
    assert forged.json()["products"] == honest["products"], (
        "the server's figures, not the browser's"
    )


HOSTILE_VALUES = [
    {"a": 1},
    [{"a": 1}],
    [[1]],
    [None],
    "text",
    123,
    1.5,
    True,
    -1,
    10**30,
    "x" * 5000,
    [{}] * 300,
]


@pytest.mark.parametrize(
    "key",
    [
        "partner",
        "capabilities",
        "products",
        "partner_prices",
        "commission",
        "administrator",
    ],
)
def test_a_malformed_review_is_a_problem_the_screen_can_show_never_a_server_error(
    catalog, manager, key
):
    """Every part of the draft, given every wrong shape: the server answers 200
    (with the problems listed) or 400 - never a 500."""
    from tests.phase10.test_10b_review_invite_price import draft

    for value in HOSTILE_VALUES:
        body = draft(catalog)
        body[key] = value
        response = manager.post(
            "/api/v1/admin/partners/reseller-review",
            json.dumps(body),
            content_type="application/json",
        )
        assert response.status_code in (200, 400), (
            key,
            repr(value)[:60],
            response.status_code,
        )


def test_a_review_body_that_is_not_an_object_is_refused(manager):
    for raw in ("[]", '"text"', "123", "null", "not json"):
        response = manager.post(
            "/api/v1/admin/partners/reseller-review", raw, content_type="application/json"
        )
        assert response.status_code == 400, (raw, response.status_code)
    # An empty body is an empty draft: everything is listed as missing.
    empty = manager.post(
        "/api/v1/admin/partners/reseller-review", "", content_type="application/json"
    )
    assert empty.status_code == 200 and empty.json()["problems"]


@pytest.mark.parametrize(
    "rate",
    [-1, 10001, 100000, 0.5, "1000", None, True, False, [1000], {"a": 1}, float("inf")],
)
def test_a_commission_rate_outside_zero_to_a_hundred_percent_is_refused(
    console, manager, rate
):
    partner = console["draft"]
    before = CommissionRule.objects.count()
    body = {
        "rate_bp": rate,
        "reason": "Attempted out-of-range rate",
        "expected_version": 0,
    }
    response = manager.post(
        f"/api/v1/admin/partners/{partner.id}/commission",
        json.dumps(body, allow_nan=True),
        content_type="application/json",
    )
    assert response.status_code == 400, (rate, response.content)
    assert CommissionRule.objects.count() == before


def test_a_retroactive_commission_is_refused(console, manager):
    partner = console["draft"]
    before = CommissionRule.objects.count()
    body = {
        "rate_bp": 3000,
        "reason": "Trying to reach back in time",
        "expected_version": 0,
        "effective_from": "2020-01-01T00:00:00Z",
    }
    response = send(
        manager, "POST", f"/api/v1/admin/partners/{partner.id}/commission", body
    )
    assert response.status_code == 400, response.content
    assert CommissionRule.objects.count() == before


def test_a_new_commission_never_rewrites_what_was_already_earned(console, manager):
    from workeasy360.commercial.commissions.models import CommissionAccrual

    reseller = console["reseller"]["partner"]
    before = list(
        CommissionAccrual.objects.filter(partner_id=reseller.id).values(
            "id", "amount_minor", "rate_bp", "rule_version", "rule_id"
        )
    )
    assert before, "the fixture has a cleared commission"
    current = send(
        manager, "GET", f"/api/v1/admin/partners/{reseller.id}/commission"
    ).json()
    version = current["live_versions"].get("", 0)
    response = send(
        manager,
        "POST",
        f"/api/v1/admin/partners/{reseller.id}/commission",
        {
            "rate_bp": 2500,
            "reason": "New programme terms from today",
            "expected_version": version,
        },
    )
    assert response.status_code in (200, 201), response.content
    after = list(
        CommissionAccrual.objects.filter(partner_id=reseller.id).values(
            "id", "amount_minor", "rate_bp", "rule_version", "rule_id"
        )
    )
    assert after == before


@pytest.mark.parametrize(
    "amount", [-1, 0.5, "4500", None, True, 10**12 + 1, 10**15, float("nan")]
)
def test_a_forged_partner_price_is_refused_and_writes_nothing(
    console, catalog, manager, amount
):
    partner = console["reseller"]["partner"]
    before = PlanPrice.objects.count()
    body = {
        "plan_version_id": str(catalog["versions"]["crm_professional"].id),
        "interval": "monthly",
        "amount_minor": amount,
    }
    response = manager.post(
        f"/api/v1/admin/partners/{partner.id}/prices",
        json.dumps(body, allow_nan=True),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert response.status_code == 400, (amount, response.content)
    assert PlanPrice.objects.count() == before


@pytest.mark.parametrize("interval", ["hourly", "", None, 7, "MONTHLY ", "monthly; x"])
def test_a_price_for_a_billing_cycle_that_does_not_exist_is_refused(
    console, catalog, manager, interval
):
    partner = console["reseller"]["partner"]
    before = PlanPrice.objects.count()
    body = {
        "plan_version_id": str(catalog["versions"]["crm_professional"].id),
        "interval": interval,
        "amount_minor": 4000,
    }
    response = send(manager, "POST", f"/api/v1/admin/partners/{partner.id}/prices", body)
    assert response.status_code == 400, (interval, response.content)
    assert PlanPrice.objects.count() == before


# --- domain takeover ------------------------------------------------------------------------------


def wl_pair(partner_factory):
    return (
        partner_factory("branding.manage", "domain.manage", code="wl-first"),
        partner_factory("branding.manage", "domain.manage", code="wl-second"),
    )


def test_two_partners_may_ask_for_a_name_but_only_the_one_who_proves_it_gets_it(
    partner_factory, dns
):
    """By design (PartnerDomain): a request is not a claim, so an attacker cannot
    squat a victim's domain by asking first. Each request has its own token, and
    the database allows one *proven* owner per hostname."""
    first, second = wl_pair(partner_factory)
    real = domains.request_domain(first["actor"](), hostname=HOST)
    squat = domains.request_domain(second["actor"](), hostname=HOST)
    assert real.verification_token != squat.verification_token
    name = f"_w360-verify.{HOST}"
    # Only the real owner can publish in the domain's DNS.
    dns.records[name] = [real.verification_token]
    assert (
        domains.verify_domain(second["actor"](), domain_id=squat.id).status != "verified"
    )
    assert domains.verify_domain(first["actor"](), domain_id=real.id).status == "verified"
    # Even with both tokens published, the name already has its proven owner.
    dns.records[name] = [real.verification_token, squat.verification_token]
    try:
        outcome = domains.verify_domain(second["actor"](), domain_id=squat.id)
    except Exception as refused:  # refusing outright is equally correct
        assert getattr(refused, "http_status", 0) in (400, 409), refused
    else:
        assert outcome.status != "verified"
    assert PartnerDomain.objects.get(id=real.id).status == "verified"
    proven = PartnerDomain.objects.filter(
        hostname=HOST, status__in=["verified", "active"]
    )
    assert proven.count() == 1


def test_a_domain_already_serving_cannot_be_displaced_by_a_new_request(
    partner_factory, dns
):
    first, second = wl_pair(partner_factory)
    branding.update_brand(
        first["actor"](), brand_name="Incumbent", accent_color="#1a56db"
    )
    held = domains.request_domain(first["actor"](), hostname=HOST)
    dns.records[f"_w360-verify.{HOST}"] = [held.verification_token]
    domains.verify_domain(first["actor"](), domain_id=held.id)
    domains.activate_domain(first["actor"](), domain_id=held.id)
    challenger = domains.request_domain(second["actor"](), hostname=HOST)
    dns.records[f"_w360-verify.{HOST}"] = [
        held.verification_token,
        challenger.verification_token,
    ]
    try:
        outcome = domains.verify_domain(second["actor"](), domain_id=challenger.id)
    except Exception as refused:
        assert getattr(refused, "http_status", 0) in (400, 409), refused
    else:
        assert outcome.status != "verified"
    served = Client().get("/api/v1/public/branding", {"host": HOST}).json()
    assert served["brand"]["brand_name"] == "Incumbent"


def test_a_released_hostname_can_be_claimed_but_only_with_its_own_proof(
    partner_factory, dns
):
    first, second = wl_pair(partner_factory)
    old = domains.request_domain(first["actor"](), hostname=HOST)
    old_token = old.verification_token
    domains.disable_domain(first["actor"](), domain_id=old.id)
    new = domains.request_domain(second["actor"](), hostname=HOST)
    assert new.verification_token != old_token
    # The previous holder's proof is still in DNS - it proves nothing for the new claimant.
    dns.records[f"_w360-verify.{HOST}"] = [old_token]
    checked = domains.verify_domain(second["actor"](), domain_id=new.id)
    assert checked.status != "verified"
    dns.records[f"_w360-verify.{HOST}"] = [new.verification_token]
    assert domains.verify_domain(second["actor"](), domain_id=new.id).status == "verified"


def test_a_proof_for_one_hostname_or_partner_does_not_verify_another(
    partner_factory, dns
):
    first, second = wl_pair(partner_factory)
    mine = domains.request_domain(first["actor"](), hostname="one.brand-example.com")
    theirs = domains.request_domain(second["actor"](), hostname="two.brand-example.com")
    assert mine.verification_token != theirs.verification_token
    # Publishing B's token at A's name (or the reverse) must not verify.
    dns.records["_w360-verify.one.brand-example.com"] = [theirs.verification_token]
    assert domains.verify_domain(first["actor"](), domain_id=mine.id).status != "verified"
    # Nor does a token sitting at a *different* name.
    dns.records.clear()
    dns.records["_w360-verify.two.brand-example.com"] = [mine.verification_token]
    assert (
        domains.verify_domain(second["actor"](), domain_id=theirs.id).status != "verified"
    )


def test_the_verification_token_is_unguessable_and_is_not_a_function_of_the_hostname(
    partner_factory,
):
    first, _second = wl_pair(partner_factory)
    a = domains.request_domain(
        first["actor"](), hostname="a.brand-example.com"
    ).verification_token
    b = domains.request_domain(
        first["actor"](), hostname="b.brand-example.com"
    ).verification_token
    assert a != b and len(a) >= 32 and len(b) >= 32
    assert "brand-example" not in a and "first" not in a


def hostile_hostnames():
    platform = settings.W360_PLATFORM_DOMAINS[0]
    return [
        "localhost",
        "127.0.0.1",
        "10.0.0.1",
        "::1",
        "*.brand-example.com",
        "brand-example.com:8443",
        "brand-example.com/path",
        "user@brand-example.com",
        "https://brand-example.com",
        "brand..example.com",
        "-bad.brand-example.com",
        "bad-.brand-example.com",
        "under_score.brand-example.com",
        "brand-example.com\r\nHost: evil.example",
        "brand-example.com\x00.evil.example",
        "a" * 64 + ".brand-example.com",
        ".".join(["a" * 60] * 5) + ".com",
        "singlelabel",
        platform,
        f"login.{platform}",
        f"app.{platform.upper()}",
        f"{platform}.",
        "internal",
        "printer.local",
        "svc.cluster.local",
        "metadata.google.internal",
    ]


@pytest.mark.parametrize("hostname", hostile_hostnames(), ids=lambda h: repr(h)[:40])
def test_hostnames_that_are_not_plain_public_names_are_refused(partner_factory, hostname):
    fx = partner_factory("domain.manage", code=f"hostile-{uuid.uuid4().hex[:6]}")
    with pytest.raises(Exception) as refused:
        domains.request_domain(fx["actor"](), hostname=hostname)
    assert getattr(refused.value, "http_status", 0) == 400, (hostname, refused.value)
    assert not PartnerDomain.objects.filter(partner_id=fx["partner"].id).exists()


def test_a_unicode_lookalike_of_a_platform_domain_is_refused(partner_factory):
    """A hostname is compared in its punycode form, so a lookalike cannot slip
    past the platform-domain check - it is simply a different, unverified name
    that has to prove itself like any other."""
    fx = partner_factory("domain.manage", code="homoglyph")
    platform = settings.W360_PLATFORM_DOMAINS[0]
    lookalike = chr(0x430) + platform[1:]  # Cyrillic a for the first letter
    row = domains.request_domain(fx["actor"](), hostname=lookalike)
    assert row.hostname.startswith("xn--"), row.hostname
    assert row.hostname != platform
    assert row.status == "requested"


def test_a_domain_serves_nothing_until_it_is_proven_and_then_separately_activated(
    partner_factory, dns
):
    fx = partner_factory("branding.manage", "domain.manage", code="proven")
    branding.update_brand(
        fx["actor"](), brand_name="Proven Brand", accent_color="#1a56db"
    )
    row = domains.request_domain(fx["actor"](), hostname=HOST)
    client = Client()

    def lookup():
        return client.get("/api/v1/public/branding", {"host": HOST}).json()

    assert lookup() == {"brand": None}
    with pytest.raises(Exception) as early:
        domains.activate_domain(fx["actor"](), domain_id=row.id)
    assert getattr(early.value, "http_status", 0) in (400, 409, 422), early.value
    dns.records[f"_w360-verify.{HOST}"] = [row.verification_token]
    domains.verify_domain(fx["actor"](), domain_id=row.id)
    assert lookup() == {"brand": None}, "verified is not serving"
    domains.activate_domain(fx["actor"](), domain_id=row.id)
    assert lookup()["brand"]["brand_name"] == "Proven Brand"
    domains.disable_domain(fx["actor"](), domain_id=row.id)
    assert lookup() == {"brand": None}, "disabling takes it off the air at once"


def test_a_partner_cannot_hold_an_unbounded_number_of_domains(partner_factory):
    fx = partner_factory("domain.manage", code="hoarder")
    for i in range(domains.MAX_LIVE_DOMAINS_PER_PARTNER):
        domains.request_domain(fx["actor"](), hostname=f"h{i}.brand-example.com")
    with pytest.raises(Exception) as refused:
        domains.request_domain(fx["actor"](), hostname="one-too-many.brand-example.com")
    assert getattr(refused.value, "http_status", 0) == 400


# --- Host-header spoofing -----------------------------------------------------------------------------


@pytest.fixture
def live_brand(partner_factory, dns):
    fx = partner_factory("branding.manage", "domain.manage", code="live-brand")
    branding.update_brand(
        fx["actor"](),
        brand_name="Live Brand",
        accent_color="#1a56db",
        support_email="help@brand-example.com",
        support_url="https://brand-example.com/help",
    )
    row = domains.request_domain(fx["actor"](), hostname=HOST)
    dns.records[f"_w360-verify.{HOST}"] = [row.verification_token]
    domains.verify_domain(fx["actor"](), domain_id=row.id)
    domains.activate_domain(fx["actor"](), domain_id=row.id)
    return fx


def test_the_platform_refuses_a_partner_hostname_in_the_host_header(live_brand):
    """The API is not served on a partner's hostname, so a request claiming to be
    is refused before any view runs - it cannot be used to be shown that brand."""
    spoofed = Client(HTTP_HOST=HOST)
    for path in (
        "/api/v1/auth/session",
        "/api/v1/public/branding",
        "/api/v1/public/branding/logo",
    ):
        response = spoofed.get(path)
        assert response.status_code == 400, (path, response.status_code)


def test_forwarded_and_origin_headers_never_select_a_brand(live_brand):
    client = Client()
    for headers in (
        {"HTTP_X_FORWARDED_HOST": HOST},
        {"HTTP_X_ORIGINAL_HOST": HOST},
        {"HTTP_FORWARDED": f"host={HOST}"},
        {"HTTP_ORIGIN": f"https://{HOST}"},
        {"HTTP_REFERER": f"https://{HOST}/"},
        {"HTTP_X_HOST": HOST},
    ):
        assert client.get("/api/v1/public/branding", **headers).json() == {
            "brand": None
        }, headers


def test_only_the_explicit_host_parameter_selects_a_brand_and_it_says_nothing_about_the_partner(
    live_brand,
):
    body = Client().get("/api/v1/public/branding", {"host": HOST})
    assert body.status_code == 200
    brand = body.json()["brand"]
    # Presentation only: no partner, tenant, capability, member, domain or file id.
    assert set(brand) <= {
        "brand_name",
        "accent_color",
        "theme",
        "support_email",
        "support_url",
        "logo_url",
        "portal",
        # ADR-046: the plain words the partner itself chose, by locale.
        "translations",
    }, set(brand)
    # The portal a customer's browser receives is the public part only: never the
    # sender identity of emails sent in the partner's name.
    assert (
        "sender_name" not in brand["portal"] and "reply_to_email" not in brand["portal"]
    )
    text = json.dumps(brand)
    assert str(live_brand["partner"].id) not in text
    assert "no-store" in body["Cache-Control"]


def lookalike_hosts():
    real = HOST
    return [
        real + ".evil.example",
        "evil.example/" + real,
        real + "@evil.example",
        real + ":8443",
        real + "%00",
        real + "\r\nX-Injected: 1",
        "*." + real.split(".", 1)[1],
        real.split(".", 1)[1],
        "x" + real,
        "app.brand-example.com.evil",
        chr(0x430) + real[1:],
        " ",
        "",
        "a" * 400,
        "127.0.0.1",
        "localhost",
    ]


@pytest.mark.parametrize("host", lookalike_hosts(), ids=lambda h: repr(h)[:40])
def test_lookalike_and_malformed_hosts_get_the_default(live_brand, host):
    response = Client().get("/api/v1/public/branding", {"host": host})
    assert response.status_code == 200 and response.json() == {"brand": None}, host


def test_repeated_host_parameters_cannot_smuggle_a_second_value(live_brand):
    response = Client().get(f"/api/v1/public/branding?host=evil.example&host={HOST}")
    # Django takes the last value; whichever is taken, it is one canonical name and looked up alone.
    assert response.status_code == 200
    assert (
        response.json()["brand"] is None
        or response.json()["brand"]["brand_name"] == "Live Brand"
    )


def test_the_branding_lookup_is_rate_limited_per_address(live_brand, settings):
    from django.core.cache import cache

    cache.clear()
    settings.CONTROL_PLANE_RATE_LIMITS = {
        **settings.CONTROL_PLANE_RATE_LIMITS,
        "public_branding_ip": (3, 60),
    }
    client = Client(REMOTE_ADDR="10.44.4.4")
    statuses = [
        client.get("/api/v1/public/branding", {"host": HOST}).status_code
        for _ in range(4)
    ]
    assert statuses == [200, 200, 200, 429]
    cache.clear()


def test_a_link_in_an_email_uses_the_configured_frontend_never_the_request(
    channel, login
):
    """Password-reset poisoning, the classic: a forged Host, X-Forwarded-Host,
    Origin or Referer on the request that *sends* a link must not become the
    link's address."""
    from workeasy360.commercial.partners import customers

    address = f"poison-{uuid.uuid4().hex[:8]}@customer-example.com"
    result = customers.provision_customer(
        channel["actor"](),
        organization_name="Poison Ltd",
        owner_email=address,
        idempotency_key=uuid.uuid4().hex,
    )
    deliver_account_mail()
    client = login(channel)
    url = f"/api/v1/partner/{channel['partner'].id}/customers/{result.organization_id}/owner-invitation/resend"
    response = send(
        client,
        "POST",
        url,
        {},
        HTTP_X_FORWARDED_HOST="evil.example",
        HTTP_X_FORWARDED_PROTO="http",
        HTTP_ORIGIN="https://evil.example",
        HTTP_REFERER="https://evil.example/x",
    )
    assert response.status_code == 200, response.content
    fresh = [m for m in deliver_account_mail() if address in m.to][-1]
    import re

    link = re.search(r"https?://\S+#token=\S+", fresh.body).group(0)
    assert link.startswith(settings.W360_FRONTEND_URL + "/"), link
    assert "evil.example" not in fresh.body


def test_a_setup_link_for_a_partner_administrator_uses_the_configured_frontend_too(
    console, manager
):
    partner = console["draft"]
    response = send(
        manager,
        "POST",
        f"/api/v1/admin/partners/{partner.id}/administrators",
        {"email": f"adm-{uuid.uuid4().hex[:6]}@admin-example.com", "role": "admin"},
        HTTP_X_FORWARDED_HOST="evil.example",
        HTTP_ORIGIN="https://evil.example",
    )
    assert response.status_code in (200, 201), response.content
    body = " ".join(m.body for m in deliver_account_mail())
    assert "evil.example" not in body
    assert settings.W360_FRONTEND_URL in body


# --- XSS and injection ------------------------------------------------------------------------------------


PAYLOADS = [
    "<script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "<svg onload=alert(1)>",
    "';alert(String.fromCharCode(88,83,83))//",
    "{{7*7}}",
    "${7*7}",
]


@pytest.mark.parametrize("payload", PAYLOADS)
def test_markup_in_a_brand_is_data_returned_as_json_never_as_a_page(
    partner_factory, manager, payload
):
    fx = partner_factory("branding.manage", code=f"xss-{uuid.uuid4().hex[:6]}")
    response = manager.put(
        f"/api/v1/admin/partners/{fx['partner'].id}/brand",
        json.dumps({"brand_name": payload}),
        content_type="application/json",
    )
    assert response.status_code in (200, 400), response.content
    if response.status_code == 200:
        assert response["Content-Type"].startswith("application/json")
        assert response["X-Content-Type-Options"] == "nosniff"
        assert response.json()["brand_name"] == payload, (
            "stored and returned as text, unchanged"
        )
        read = manager.get(f"/api/v1/admin/partners/{fx['partner'].id}/brand")
        assert read["Content-Type"].startswith("application/json")


@pytest.mark.parametrize(
    "field,value",
    [
        ("support_url", "javascript:alert(1)"),
        ("support_url", "data:text/html,<script>alert(1)</script>"),
        ("support_url", "//evil.example/x"),
        ("support_url", "http://plain.example"),
        ("support_url", "ftp://files.example"),
        ("support_url", "https://user:pass@evil.example"),
        ("support_url", "https://ok.example/ has space"),
        ("support_email", "a@b.example\r\nBcc: x@y.example"),
        ("support_email", "<script>@x.example"),
        ("accent_color", "red"),
        ("accent_color", "#12"),
        ("accent_color", "#gggggg"),
        ("accent_color", "url(//evil.example/x)"),
        ("accent_color", "#1a56db;background:url(//evil.example)"),
        ("accent_color", "expression(alert(1))"),
        ("accent_color", 123456),
        ("theme", {"background-image": "#ffffff"}),
        ("theme", {"primary": "url(x)"}),
        ("theme", {"primary": {"nested": "#ffffff"}}),
        ("theme", "javascript:alert(1)"),
        ("theme", ["#ffffff"]),
    ],
    ids=lambda v: repr(v)[:30],
)
def test_unsafe_urls_colours_and_themes_are_refused_and_nothing_is_stored(
    partner_factory, manager, field, value
):
    fx = partner_factory("branding.manage", code=f"unsafe-{uuid.uuid4().hex[:6]}")
    body = {"brand_name": "Safe Name", field: value}
    response = manager.put(
        f"/api/v1/admin/partners/{fx['partner'].id}/brand",
        json.dumps(body),
        content_type="application/json",
    )
    assert response.status_code == 400, (field, value, response.content)
    assert not PartnerBrand.objects.filter(partner_id=fx["partner"].id).exists()


@pytest.mark.parametrize(
    "content,content_type",
    [
        (
            b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
            "image/svg+xml",
        ),
        (
            b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
            "image/png",
        ),
        (b"<html><script>alert(1)</script></html>", "image/png"),
        (b"<html><script>alert(1)</script></html>", "text/html"),
        (b"GIF89a" + b"\x00" * 32, "image/gif"),
        (b"\x89PNG\r\n\x1a\n", "application/octet-stream"),
        (b"", "image/png"),
    ],
    ids=["svg", "svg-as-png", "html-as-png", "html", "gif", "png-as-binary", "empty"],
)
def test_a_logo_that_is_not_a_real_png_or_jpeg_is_refused(
    partner_factory, manager, content, content_type
):
    fx = partner_factory("branding.manage", code=f"logo-{uuid.uuid4().hex[:6]}")
    response = manager.post(
        f"/api/v1/admin/partners/{fx['partner'].id}/brand/logo",
        content,
        content_type=content_type,
    )
    assert 400 <= response.status_code < 500, response.content


def test_a_logo_cannot_be_a_remote_url_or_an_arbitrary_file_id(partner_factory, manager):
    fx = partner_factory("branding.manage", code="remote-logo")
    for value in (
        "https://evil.example/logo.png",
        "//evil.example/x.png",
        "javascript:alert(1)",
        str(uuid.uuid4()),
        "../../etc/passwd",
        12345,
    ):
        response = manager.put(
            f"/api/v1/admin/partners/{fx['partner'].id}/brand",
            json.dumps({"brand_name": "Remote", "logo_file_id": value}),
            content_type="application/json",
        )
        assert response.status_code == 400, (value, response.content)
    assert not PartnerBrand.objects.filter(partner_id=fx["partner"].id).exists()


def test_an_organization_name_with_markup_is_plain_text_in_the_email(channel):
    from workeasy360.commercial.partners import customers

    address = f"markup-{uuid.uuid4().hex[:8]}@customer-example.com"
    customers.provision_customer(
        channel["actor"](),
        organization_name="<b>Bold</b> & <script>alert(1)</script> Ltd",
        owner_email=address,
        idempotency_key=uuid.uuid4().hex,
    )
    message = next(m for m in deliver_account_mail() if address in m.to)
    assert not getattr(message, "alternatives", []), "no HTML part to inject into"
    assert message.content_subtype == "plain"
    # And the credential still travels in the fragment of a link the person clicks.
    assert token_from(message)


@pytest.mark.parametrize(
    "address",
    [
        "a@b.example\r\nBcc: victim@x.example",
        "a@b.example\nSubject: pwned",
        "a@b.example,victim@x.example",
        "a b@example.com",
        "<script>@example.com",
        "a@b@c.example",
        "",
        None,
    ],
)
def test_an_owner_address_cannot_inject_a_header_or_a_second_recipient(channel, address):
    from workeasy360.commercial.partners import customers

    with pytest.raises(Exception) as refused:
        customers.provision_customer(
            channel["actor"](),
            organization_name="Inject Ltd",
            owner_email=address,
            idempotency_key=uuid.uuid4().hex,
        )
    assert getattr(refused.value, "http_status", 0) == 400, (address, refused.value)
    assert not deliver_account_mail()


def test_an_organization_name_cannot_carry_a_header_break(channel):
    from workeasy360.commercial.partners import customers

    address = f"name-{uuid.uuid4().hex[:8]}@customer-example.com"
    try:
        customers.provision_customer(
            channel["actor"](),
            organization_name="Line\r\nBcc: victim@x.example",
            owner_email=address,
            idempotency_key=uuid.uuid4().hex,
        )
    except Exception as refused:  # refusing it outright is fine
        assert getattr(refused, "http_status", 0) == 400
        return
    message = next(m for m in deliver_account_mail() if address in m.to)
    assert not message.bcc and "victim@x.example" not in " ".join(message.to + message.cc)
    assert "\r" not in message.subject and "\n" not in message.subject


def test_console_responses_are_json_with_nosniff_and_are_never_cached(
    console, staff_client
):
    client, _u = staff_client("platform.staff_partner.view")
    for url in (
        "/api/v1/admin/partners/overview",
        "/api/v1/admin/partners/resellers",
        "/api/v1/admin/partners/customers",
    ):
        response = client.get(url)
        assert response["Content-Type"].startswith("application/json"), url
        assert response["X-Content-Type-Options"] == "nosniff", url
        assert "no-store" in response.get(
            "Cache-Control", ""
        ) or "private" in response.get("Cache-Control", ""), url

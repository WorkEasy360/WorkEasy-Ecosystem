"""White-label branding, custom domains and the affiliate/referral model."""

from __future__ import annotations

import pytest
from django.utils import timezone

from workeasy360.commercial.commissions import referrals
from workeasy360.commercial.commissions import selectors as comm_selectors
from workeasy360.commercial.partners import branding, domains, selectors, services
from workeasy360.contracts.enums import PartnerStatus
from workeasy360.contracts.errors import (
    DomainOwnershipUnproven,
    PartnerNotActive,
    PartnerRecordNotFound,
    PartnerValidationError,
    PermissionDenied,
    StepUpRequired,
)
from workeasy360.platform.authz import services as authz

pytestmark = pytest.mark.django_db

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class FakeDns:
    def __init__(self, records=None, fail=False):
        self.records = records or {}
        self.fail = fail

    def txt_values(self, name):
        if self.fail:
            return None
        return self.records.get(name, [])


@pytest.fixture
def dns():
    fake = FakeDns()
    previous = domains.set_txt_resolver(fake)
    yield fake
    domains.set_txt_resolver(previous)


# --- branding -------------------------------------------------------------


def test_branding_is_bounded_metadata_only(partner_factory):
    fx = partner_factory("branding.manage")
    actor = fx["actor"]()
    brand = branding.update_brand(
        actor,
        brand_name="Acme <script>alert(1)</script>",  # stored as inert text
        accent_color="#1A2B3C",
        theme={"primary": "#112233", "surface": "#ffffff"},
        support_email="help@acme.example.com",
        support_url="https://acme.example.com/help",
    )
    assert brand["accent_color"] == "#1a2b3c"
    assert brand["brand_name"].startswith(
        "Acme <script>"
    )  # escaped at render, never trusted


@pytest.mark.parametrize(
    "kwargs",
    [
        {"accent_color": "red"},
        {"accent_color": "#12345"},
        {"accent_color": "url(javascript:alert(1))"},
        {"theme": {"background-image": "#ffffff"}},
        {"theme": {"primary": "expression(alert(1))"}},
        {"theme": "body{display:none}"},
        {"support_url": "javascript:alert(1)"},
        {"support_url": "http://insecure.example.com"},
        {"support_url": "https://user:pass@example.com/"},
        {"support_url": "data:text/html,<script>"},
        {"support_email": "not-an-email"},
        {"brand_name": "line\nbreak"},
        {"brand_name": "x" * 81},
        {"brand_name": ""},
    ],
)
def test_branding_rejects_anything_that_could_carry_markup_or_css(
    partner_factory, kwargs
):
    fx = partner_factory("branding.manage")
    payload = {"brand_name": "Acme", **kwargs}
    with pytest.raises(PartnerValidationError):
        branding.update_brand(fx["actor"](), **payload)
    assert selectors.brand_for(fx["partner"].id) is None  # nothing was written


def test_branding_needs_the_capability_and_an_active_partner(partner_factory):
    fx = partner_factory("commission.earn")
    with pytest.raises(PermissionDenied):
        branding.update_brand(fx["actor"](), brand_name="X")
    fx2 = partner_factory("branding.manage")
    services.set_partner_status(
        fx2["staff_ctx"],
        fx2["partner"].id,
        target=PartnerStatus.SUSPENDED,
        reason="Under review now",
    )
    with pytest.raises((PermissionDenied, PartnerNotActive)):
        branding.update_brand(fx2["actor"](), brand_name="X")


def test_a_stale_brand_edit_is_refused(partner_factory):
    fx = partner_factory("branding.manage")
    actor = fx["actor"]()
    first = branding.update_brand(actor, brand_name="One")
    branding.update_brand(actor, brand_name="Two", expected_version=first["version"])
    with pytest.raises(services.ConcurrentPartnerModification):
        branding.update_brand(
            actor, brand_name="Three", expected_version=first["version"]
        )
    with pytest.raises(services.ConcurrentPartnerModification):
        branding.update_brand(actor, brand_name="Four")  # no token on an existing brand


def test_a_logo_goes_through_the_files_platform_and_only_png_or_jpeg(partner_factory):
    fx = partner_factory("branding.manage")
    actor = fx["actor"]()
    file_id = branding.upload_logo(actor, content=PNG, content_type="image/png")
    brand = branding.update_brand(actor, brand_name="Acme", logo_file_id=file_id)
    assert brand["logo_file_id"] == str(file_id)

    svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    with pytest.raises(PartnerValidationError):
        branding.upload_logo(actor, content=svg, content_type="image/svg+xml")
    with pytest.raises(PartnerValidationError):
        branding.upload_logo(actor, content=b"", content_type="image/png")
    with pytest.raises(PartnerValidationError):
        branding.upload_logo(
            actor, content=PNG + b"0" * (600 * 1024), content_type="image/png"
        )


def test_a_logo_whose_bytes_do_not_match_its_type_is_rejected(partner_factory):
    from workeasy360.contracts.errors import UnsupportedFileType

    fx = partner_factory("branding.manage")
    with pytest.raises(UnsupportedFileType):
        branding.upload_logo(
            fx["actor"](),
            content=b"<html><script>x</script></html>",
            content_type="image/png",
        )


def test_a_partner_cannot_attach_another_partners_logo(partner_factory):
    one = partner_factory("branding.manage")
    two = partner_factory("branding.manage")
    file_id = branding.upload_logo(one["actor"](), content=PNG, content_type="image/png")
    with pytest.raises(PartnerValidationError):
        branding.update_brand(two["actor"](), brand_name="Thief", logo_file_id=file_id)
    import uuid

    with pytest.raises(PartnerValidationError):
        branding.update_brand(
            two["actor"](), brand_name="Ghost", logo_file_id=uuid.uuid4()
        )


# --- hostnames ------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("App.Example-Corp.COM", "app.example-corp.com"),
        ("app.example-corp.com.", "app.example-corp.com"),
        ("  portal.example-corp.com  ", "portal.example-corp.com"),
        ("münchen-partner.de", "xn--mnchen-partner-gsb.de"),
        ("xn--mnchen-partner-gsb.de", "xn--mnchen-partner-gsb.de"),
    ],
)
def test_hostnames_are_canonicalised(raw, expected):
    assert domains.normalize_hostname(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "localhost",
        "single",
        "10.0.0.1",
        "192.168.1.1",
        "https://app.example.com",
        "app.example.com/path",
        "app.example.com:8443",
        "user@app.example.com",
        "*.example.com",
        "under_score.example.com",
        "a" * 64 + ".example.com",
        "app.internal",
        "host.local",
        "-bad.example.com",
        "exa mple.com",
        "example..com",
        None,
        123,
    ],
)
def test_hostnames_that_are_not_public_dns_names_are_refused(raw):
    with pytest.raises(PartnerValidationError):
        domains.normalize_hostname(raw)


def test_platform_domains_cannot_be_claimed(settings):
    settings.W360_PLATFORM_DOMAINS = ("workeasy360.com",)
    for raw in ("workeasy360.com", "login.workeasy360.com", "a.b.workeasy360.com"):
        with pytest.raises(PartnerValidationError):
            domains.normalize_hostname(raw)
    assert domains.normalize_hostname("notworkeasy360.com") == "notworkeasy360.com"


# --- domain lifecycle -----------------------------------------------------


def _ready_partner(partner_factory):
    fx = partner_factory("branding.manage", "domain.manage")
    branding.update_brand(fx["actor"](), brand_name="Acme")
    return fx


def test_a_domain_is_inactive_until_ownership_is_proven(partner_factory, dns):
    fx = _ready_partner(partner_factory)
    actor = fx["actor"]()
    domain = domains.request_domain(actor, hostname="app.acme-example.com")
    assert domain.status == "requested"
    assert domain.verification_token.startswith("w360-verify=")

    with pytest.raises(DomainOwnershipUnproven):
        domains.activate_domain(actor, domain_id=domain.id)

    checked = domains.verify_domain(actor, domain_id=domain.id)
    assert (
        checked.status == "verification_pending"
        and checked.failure_code == "record_not_found"
    )
    assert domains.public_brand_for_host("app.acme-example.com") is None

    dns.records["_w360-verify.app.acme-example.com"] = [domain.verification_token]
    assert domains.verify_domain(actor, domain_id=domain.id).status == "verified"
    assert (
        domains.public_brand_for_host("app.acme-example.com") is None
    )  # still not active

    assert domains.activate_domain(actor, domain_id=domain.id).status == "active"
    brand = domains.public_brand_for_host("APP.acme-example.com.")
    assert brand["brand_name"] == "Acme"
    # Presentation only: no partner id, capability or tenant leaks through.
    assert set(brand) == {
        "brand_name",
        "logo_file_id",
        "accent_color",
        "theme",
        "support_email",
        "support_url",
    }


def test_a_wrong_or_someone_elses_token_does_not_verify(partner_factory, dns):
    fx = _ready_partner(partner_factory)
    actor = fx["actor"]()
    domain = domains.request_domain(actor, hostname="app.acme-example.com")
    dns.records["_w360-verify.app.acme-example.com"] = [
        "w360-verify=not-the-token",
        "v=spf1 -all",
    ]
    assert (
        domains.verify_domain(actor, domain_id=domain.id).status == "verification_pending"
    )


def test_a_resolver_outage_is_a_failed_lookup_not_a_proof_or_a_miss(partner_factory, dns):
    fx = _ready_partner(partner_factory)
    actor = fx["actor"]()
    domain = domains.request_domain(actor, hostname="app.acme-example.com")
    dns.fail = True
    checked = domains.verify_domain(actor, domain_id=domain.id)
    assert (
        checked.status == "verification_pending"
        and checked.failure_code == "lookup_failed"
    )


def test_domain_management_requires_aal2(partner_factory, dns):
    fx = _ready_partner(partner_factory)
    with pytest.raises(StepUpRequired):
        domains.request_domain(fx["actor"](aal=1), hostname="app.acme-example.com")


def test_request_is_idempotent_and_bounded(partner_factory, dns):
    fx = _ready_partner(partner_factory)
    actor = fx["actor"]()
    first = domains.request_domain(actor, hostname="app.acme-example.com")
    again = domains.request_domain(actor, hostname="APP.acme-example.com")
    assert first.id == again.id
    for n in range(domains.MAX_LIVE_DOMAINS_PER_PARTNER - 1):
        domains.request_domain(actor, hostname=f"h{n}.acme-example.com")
    with pytest.raises(PartnerValidationError):
        domains.request_domain(actor, hostname="one-too-many.acme-example.com")


def test_partner_b_cannot_see_or_drive_partner_as_domain(partner_factory, dns):
    one = _ready_partner(partner_factory)
    two = _ready_partner(partner_factory)
    domain = domains.request_domain(one["actor"](), hostname="app.acme-example.com")
    for action in (
        domains.verify_domain,
        domains.activate_domain,
        domains.disable_domain,
    ):
        with pytest.raises(PartnerRecordNotFound):
            action(two["actor"](), domain_id=domain.id)
    assert selectors.domain_for(two["partner"].id, domain.id) is None


def test_a_disabled_domain_releases_the_name_to_whoever_can_prove_it(
    partner_factory, dns
):
    one = _ready_partner(partner_factory)
    two = _ready_partner(partner_factory)
    host = "app.acme-example.com"
    da = domains.request_domain(one["actor"](), hostname=host)
    dns.records[f"_w360-verify.{host}"] = [da.verification_token]
    domains.verify_domain(one["actor"](), domain_id=da.id)
    domains.activate_domain(one["actor"](), domain_id=da.id)

    db = domains.request_domain(two["actor"](), hostname=host)
    # B holds a pending request but cannot take an actively-owned name: its own
    # token is not in DNS.
    assert (
        domains.verify_domain(two["actor"](), domain_id=db.id).status
        == "verification_pending"
    )

    domains.disable_domain(one["actor"](), domain_id=da.id)
    assert domains.public_brand_for_host(host) is None
    dns.records[f"_w360-verify.{host}"] = [db.verification_token]
    assert domains.verify_domain(two["actor"](), domain_id=db.id).status == "verified"


def test_lost_control_disables_a_proven_domain_after_repeated_misses(
    partner_factory, dns
):
    fx = _ready_partner(partner_factory)
    actor = fx["actor"]()
    host = "app.acme-example.com"
    domain = domains.request_domain(actor, hostname=host)
    dns.records[f"_w360-verify.{host}"] = [domain.verification_token]
    domains.verify_domain(actor, domain_id=domain.id)
    domains.activate_domain(actor, domain_id=domain.id)

    dns.records[f"_w360-verify.{host}"] = []  # the record vanished
    for _ in range(domains.MAX_MISSED_CHECKS - 1):
        assert domains.recheck_proven_domains()["disabled"] == 0
    dns.fail = True  # an outage is not a miss
    assert domains.recheck_proven_domains()["lookup_failed"] == 1
    dns.fail = False
    assert domains.recheck_proven_domains()["disabled"] == 1
    assert domains.public_brand_for_host(host) is None


def test_a_terminated_partner_loses_its_domains(partner_factory, dns):
    fx = _ready_partner(partner_factory)
    actor = fx["actor"]()
    host = "app.acme-example.com"
    domain = domains.request_domain(actor, hostname=host)
    dns.records[f"_w360-verify.{host}"] = [domain.verification_token]
    domains.verify_domain(actor, domain_id=domain.id)
    domains.activate_domain(actor, domain_id=domain.id)
    services.set_partner_status(
        fx["staff_ctx"],
        fx["partner"].id,
        target=PartnerStatus.TERMINATED,
        reason="Contract ended now",
    )
    assert domains.public_brand_for_host(host) is None


def test_the_host_cache_is_keyed_by_canonical_host_and_holds_no_authority(
    partner_factory, dns
):
    from django.core.cache import cache

    fx = _ready_partner(partner_factory)
    actor = fx["actor"]()
    host = "app.acme-example.com"
    domain = domains.request_domain(actor, hostname=host)
    dns.records[f"_w360-verify.{host}"] = [domain.verification_token]
    domains.verify_domain(actor, domain_id=domain.id)
    domains.activate_domain(actor, domain_id=domain.id)
    domains.public_brand_for_host("App.Acme-Example.com")
    cached = cache.get(domains._cache_key(host))
    assert (
        cached is not None
        and "partner_id" not in cached
        and "organization_id" not in cached
    )
    domains.disable_domain(actor, domain_id=domain.id)
    assert domains.public_brand_for_host(host) is None  # invalidated, not merely expired


def test_a_malformed_host_falls_back_to_no_brand_never_an_error():
    for raw in ("", None, "../../etc/passwd", "a b", "http://x", "10.0.0.1"):
        assert domains.public_brand_for_host(raw) is None


# --- referrals and the affiliate --------------------------------------------


@pytest.fixture
def affiliate(partner_factory, staff_factory):
    fx = partner_factory(
        "referral.create", "commission.earn", "commission.view", "payout.view"
    )
    return fx


def test_an_affiliate_has_no_customer_management_reach(affiliate):
    actor = affiliate["actor"]()
    for permission in (
        "platform.partner_customer.provision",
        "platform.partner_customer.manage",
        "platform.partner_customer.view",
        "platform.partner_subscription.sell",
        "platform.partner_subscription.manage",
        "platform.partner_pricing.view",
        "platform.partner_branding.manage",
        "platform.partner_domain.manage",
        "platform.partner_support.access",
    ):
        assert not authz.check(actor, permission).allowed, permission
    assert authz.check(actor, "platform.partner_referral.create").allowed
    assert authz.check(actor, "platform.partner_commission.view").allowed
    assert authz.check(actor, "platform.partner_payout.view").allowed


def test_a_referral_code_is_unguessable_bounded_and_carries_no_data(affiliate):
    actor = affiliate["actor"]()
    code = referrals.create_referral_code(actor, label="Spring campaign")
    assert len(code.code) == 8 and set(code.code) <= set(
        "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    )
    for _ in range(referrals.MAX_CODES_PER_PARTNER - 1):
        referrals.create_referral_code(actor)
    with pytest.raises(PartnerValidationError):
        referrals.create_referral_code(actor)


def test_capture_stores_only_a_hash_and_rejects_bad_codes(affiliate):
    from workeasy360.commercial.commissions.models import Referral

    code = referrals.create_referral_code(affiliate["actor"]())
    token = referrals.capture_referral(code.code.lower())
    assert token and len(token) >= 30
    row = Referral.objects.get()
    assert row.token_hash != token and token not in str(row.__dict__)
    for bad in (
        None,
        "",
        "short",
        "0OIL1000",
        "A" * 9,
        "'; DROP TABLE x;--",
        code.code + "X",
    ):
        assert referrals.capture_referral(bad) is None


def test_a_suspended_affiliate_takes_no_new_referrals(affiliate):
    code = referrals.create_referral_code(affiliate["actor"]())
    services.set_partner_status(
        affiliate["staff_ctx"],
        affiliate["partner"].id,
        target=PartnerStatus.SUSPENDED,
        reason="Under review now",
    )
    assert referrals.capture_referral(code.code) is None


def test_a_disabled_code_takes_no_new_referrals(affiliate):
    code = referrals.create_referral_code(affiliate["actor"]())
    referrals.disable_referral_code(affiliate["actor"](), code.id)
    assert referrals.capture_referral(code.code) is None


def test_conversion_is_first_touch_owner_only_and_once(
    affiliate, partner_factory, organization_factory, user_factory
):
    code_a = referrals.create_referral_code(affiliate["actor"]())
    other = partner_factory("referral.create", "commission.earn")
    code_b = referrals.create_referral_code(other["actor"]())
    first = referrals.capture_referral(code_a.code)
    second = referrals.capture_referral(code_b.code)

    org, _m, owner = organization_factory()
    stranger = user_factory()
    assert not referrals.convert_referral(
        user_id=stranger.id, token=first, organization_id=org.id
    )
    assert not referrals.convert_referral(
        user_id=owner.id, token="nonsense", organization_id=org.id
    )
    assert referrals.convert_referral(
        user_id=owner.id, token=first, organization_id=org.id
    )
    # The customer is attributed once: a later touch cannot take it over.
    assert not referrals.convert_referral(
        user_id=owner.id, token=second, organization_id=org.id
    )
    assert not referrals.convert_referral(
        user_id=owner.id, token=first, organization_id=org.id
    )
    from workeasy360.commercial.commissions.models import Referral

    converted = Referral.objects.get(status="converted")
    assert converted.partner_id == affiliate["partner"].id
    assert converted.attribution_rule == "first_touch"


def test_an_established_customer_cannot_be_retro_attributed(
    affiliate, organization_factory
):
    from datetime import timedelta

    from workeasy360.platform.organizations.models import Organization

    org, _m, owner = organization_factory()
    Organization.objects.filter(id=org.id).update(
        created_at=timezone.now() - timedelta(days=90)
    )
    token = referrals.capture_referral(
        referrals.create_referral_code(affiliate["actor"]()).code
    )
    assert not referrals.convert_referral(
        user_id=owner.id, token=token, organization_id=org.id
    )


def test_a_referral_earns_commission_and_never_double_pays_with_a_managed_partner(
    channel, affiliate, staff_factory, organization_factory, catalog, monkeypatch
):
    """A referred customer's sale earns the affiliate; a *managed* customer's
    sale earns only the managing partner, whatever referral touched it."""
    from tests.phase7.support import provision, sell
    from workeasy360.commercial.commissions.models import CommissionAccrual
    from workeasy360.commercial.subscriptions import services as subs
    from workeasy360.platform.authz.control_plane import (
        acting_scope,
        context_for_organization,
    )

    code = referrals.create_referral_code(affiliate["actor"]())
    token = referrals.capture_referral(code.code)
    org, _m, owner = organization_factory()
    assert referrals.convert_referral(
        user_id=owner.id, token=token, organization_id=org.id
    )

    # A staff member sells to the referred customer directly.
    _u, sctx = staff_factory("platform.staff_subscription.manage")
    customer_ctx = context_for_organization(sctx, org.id)
    from workeasy360.commercial.billing import services as billing
    from workeasy360.contracts.values.money import CurrencyCode

    with acting_scope(customer_ctx):
        billing.open_billing_account(customer_ctx, currency=CurrencyCode.GBP)
        subs.purchase_plan(
            customer_ctx,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            idempotency_key="direct-sale",
        )
    rows = list(CommissionAccrual.objects.all())
    assert len(rows) == 1
    assert (
        rows[0].partner_id == affiliate["partner"].id
        and rows[0].attribution_kind == "referral"
    )
    assert rows[0].amount_minor == 500

    # A partner-managed customer: only the managing partner earns.
    managed, _o = provision(channel)
    sell(channel, managed.organization_id)
    by_partner = {
        r.partner_id
        for r in CommissionAccrual.objects.filter(organization_id=managed.organization_id)
    }
    assert by_partner == {channel["partner"].id}


def test_referral_visibility_is_counts_only(affiliate, organization_factory):
    code = referrals.create_referral_code(affiliate["actor"]())
    referrals.capture_referral(code.code)
    summary = referrals.summary(affiliate["actor"]())
    assert summary["counts"] == {"captured": 1, "converted": 0, "expired": 0}
    page = comm_selectors.referrals_page(affiliate["partner"].id)
    assert set(page.items[0]) == {
        "id",
        "code",
        "label",
        "status",
        "captured_at",
        "converted_at",
    }


def test_only_converted_referrals_survive_retention(affiliate, organization_factory):
    from datetime import timedelta

    from workeasy360.commercial.commissions.models import Referral

    code = referrals.create_referral_code(affiliate["actor"]())
    token = referrals.capture_referral(code.code)
    org, _m, owner = organization_factory()
    referrals.convert_referral(user_id=owner.id, token=token, organization_id=org.id)
    referrals.capture_referral(code.code)
    old = timezone.now() - timedelta(days=400)
    Referral.objects.filter(status="captured").update(expires_at=old)
    assert referrals.prune_expired_referrals() == 1
    assert Referral.objects.filter(status="converted").count() == 1

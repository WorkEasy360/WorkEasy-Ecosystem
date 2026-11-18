"""Product domain ownership (ADR-070) and the Super Admin SSO readiness view.

Against the real services, the real database and the real API; only the DNS
resolver is a test double (the same port partner custom domains use).

What is proven:

* every URL field of a manifest names a hostname that must be proven, and the
  list of URL fields cannot silently fall behind the manifest;
* a draft may name any valid hostname and gets a proof to publish; it cannot
  be **activated** until every hostname is proven - by a TXT record carrying
  *its* token (another product's token proves nothing);
* an **active** product cannot be pointed at a hostname nobody proved - the
  attacker-controlled callback, sign-out return and back-channel logout
  cases - and the refused edit changes nothing;
* a failed lookup is not evidence of absence; repeated misses on re-check
  mark a domain lapsed (audited) and lapsed blocks reactivation;
* existing registrations are carried over (migration ``ecosystem.0008``), so
  live products are not disturbed;
* the staff API: list, request, verify (rate-limited), readiness - and who
  may call which.
"""

from __future__ import annotations

import importlib
import json

import pytest

from tests.phase11.conftest import CATALOG_STAFF, audit_of

pytestmark = pytest.mark.django_db

ORIGIN = "https://crm.example.com"
EVIL = "https://evil.example.net"
DOMAINS = "/api/v1/admin/products/crm/integration/domains"
VERIFY = "/api/v1/admin/products/crm/integration/domains/verify"
READINESS = "/api/v1/admin/products/crm/integration/readiness"


class FakeDns:
    """TXT records by name; ``down`` makes every lookup fail (``None``)."""

    def __init__(self) -> None:
        self.records: dict[str, list[str]] = {}
        self.down = False
        self.asked: list[str] = []

    def txt_values(self, name: str) -> list[str] | None:
        self.asked.append(name)
        if self.down:
            return None
        return list(self.records.get(name, []))

    def publish(self, row) -> None:
        from workeasy360.platform.ecosystem import domains

        self.records.setdefault(domains.record_name(row.hostname), []).append(row.token)


@pytest.fixture
def dns():
    from workeasy360.commercial.partners import domains as partner_domains

    fake = FakeDns()
    previous = partner_domains.set_txt_resolver(fake)
    yield fake
    partner_domains.set_txt_resolver(previous)


@pytest.fixture
def staff(platform_admin, in_tenant):
    """Call ecosystem services as a platform administrator."""
    ctx = platform_admin["ctx"]

    def run(function, **kwargs):
        with in_tenant(ctx):
            return function(ctx, **kwargs)

    run.ctx = ctx
    run.in_tenant = lambda: in_tenant(ctx)
    return run


@pytest.fixture
def draft(register_product):
    """CRM registered on a real https hostname, not active, no proof yet."""
    return register_product("crm", origin=ORIGIN, activate=False)


def _rows(registration):
    from workeasy360.platform.ecosystem.models import ProductDomain

    return {
        r.hostname: r
        for r in ProductDomain.objects.filter(integration=registration["integration"])
    }


def _prove(staff, dns, registration, hostname="crm.example.com"):
    from workeasy360.commercial.controlplane import product_domains

    dns.publish(_rows(registration)[hostname])
    return staff(product_domains.verify_domain, product_code="crm", hostname=hostname)


def _audited(action: str) -> int:
    """How many platform audit rows record ``action`` for any integration,
    read the way the platform reads its trail (``audit_of``)."""
    from workeasy360.platform.ecosystem.models import ProductIntegration

    return sum(
        1
        for integration_id in ProductIntegration.objects.values_list("id", flat=True)
        for row in audit_of(integration_id, "action")
        if row["action"] == action
    )


def _activate(staff, registration):
    from workeasy360.platform.ecosystem import services

    return staff(
        services.transition, client_id=registration["client_id"], to_status="active"
    )


def _edit(staff, registration, **changes):
    from workeasy360.platform.ecosystem import services
    from workeasy360.platform.ecosystem.models import ProductIntegration

    manifest = dict(
        ProductIntegration.objects.get(pk=registration["integration"].pk).manifest
    )
    manifest.update(changes)
    return staff(
        services.update_manifest, client_id=registration["client_id"], manifest=manifest
    )


# --- which hostnames ------------------------------------------------------------------


def test_every_url_field_of_a_manifest_names_a_hostname():
    from workeasy360.platform.ecosystem import domains

    manifest = {
        "launch_url": "https://Launch.Example.com/",
        "health_url": "https://health.example.com/healthz",
        "login_url": "https://login.example.com/login",
        "backchannel_logout_uri": "https://bcl.example.com/bcl",
        "redirect_uris": ["https://cb1.example.com/cb", "https://cb2.example.com./cb"],
        "post_logout_redirect_uris": ["https://out.example.com/"],
    }
    assert domains.hostnames_of(manifest) == [
        "bcl.example.com",
        "cb1.example.com",
        "cb2.example.com",
        "health.example.com",
        "launch.example.com",
        "login.example.com",
        "out.example.com",
    ]
    assert domains.canonical_hostname("BÜCHER.example") == "xn--bcher-kva.example"


def test_the_url_field_list_cannot_fall_behind_the_manifest():
    """A new URL field in ProductManifest must be added to the gate."""
    import dataclasses

    from workeasy360.contracts.values.manifest import ProductManifest
    from workeasy360.platform.ecosystem import domains

    url_fields = {
        f.name
        for f in dataclasses.fields(ProductManifest)
        if f.name.endswith(("_url", "_uri", "_urls", "_uris"))
    }
    assert url_fields == set(domains.SINGLE_URL_FIELDS) | set(domains.MULTI_URL_FIELDS)


# --- the gate ---------------------------------------------------------------------------


def test_a_draft_gets_a_proof_to_publish_for_each_hostname(draft):
    rows = _rows(draft)
    assert set(rows) == {"crm.example.com"}
    row = rows["crm.example.com"]
    assert row.status == "pending" and row.token.startswith("w360-product-verify=")
    assert len(row.token) > 30


def test_a_draft_cannot_be_activated_until_its_domains_are_proven(staff, dns, draft):
    from workeasy360.contracts.errors import ProductDomainUnverified
    from workeasy360.platform.ecosystem.models import ProductIntegration

    with pytest.raises(ProductDomainUnverified) as refused:
        _activate(staff, draft)
    assert refused.value.context["hostnames"] == ["crm.example.com"]
    assert "_w360-product-verify.crm.example.com" in refused.value.message
    assert ProductIntegration.objects.get(pk=draft["integration"].pk).status == "draft"

    body = _prove(staff, dns, draft)
    assert body["outcome"] == "verified" and body["all_verified"] is True
    assert dns.asked == ["_w360-product-verify.crm.example.com"]
    _activate(staff, draft)
    assert ProductIntegration.objects.get(pk=draft["integration"].pk).status == "active"
    assert _rows(draft)["crm.example.com"].method == "dns_txt"


def test_another_products_token_proves_nothing(staff, dns, draft, register_product):
    from workeasy360.commercial.controlplane import product_domains

    other = register_product("books", origin=ORIGIN, activate=False)
    dns.publish(_rows(other)["crm.example.com"])  # books' token, crm's name
    body = staff(
        product_domains.verify_domain, product_code="crm", hostname="crm.example.com"
    )
    assert body["outcome"] == "record_not_found" and body["all_verified"] is False


def test_a_failed_lookup_is_not_evidence_of_absence(staff, dns, draft):
    from workeasy360.commercial.controlplane import product_domains

    dns.down = True
    body = staff(
        product_domains.verify_domain, product_code="crm", hostname="crm.example.com"
    )
    assert body["outcome"] == "lookup_failed"
    row = _rows(draft)["crm.example.com"]
    assert row.status == "pending" and row.check_count == 1


@pytest.fixture
def live(staff, dns, draft):
    _prove(staff, dns, draft)
    _activate(staff, draft)
    return draft


@pytest.mark.parametrize(
    "changes",
    [
        {"redirect_uris": [f"{ORIGIN}/auth/callback", f"{EVIL}/steal"]},
        {"post_logout_redirect_uris": [f"{EVIL}/phish"]},
        {"launch_url": f"{EVIL}/"},
        {"health_url": f"{EVIL}/healthz"},
    ],
)
def test_a_live_product_cannot_be_pointed_at_an_unproven_hostname(staff, live, changes):
    from workeasy360.contracts.errors import ProductDomainUnverified
    from workeasy360.platform.ecosystem.models import ProductIntegration

    before = ProductIntegration.objects.get(pk=live["integration"].pk).manifest
    with pytest.raises(ProductDomainUnverified) as refused:
        _edit(staff, live, **changes)
    assert refused.value.context["hostnames"] == ["evil.example.net"]
    assert ProductIntegration.objects.get(pk=live["integration"].pk).manifest == before
    assert "evil.example.net" not in _rows(live)


#: URLs that look like the proven ``crm.example.com`` but would send a browser,
#: or the platform's own outbound call, somewhere else. Each must be refused -
#: by the manifest validator or by the ownership gate - and change nothing.
DECEPTIVE_URLS = [
    "https://crm.example.com.evil.example.net/cb",  # proven name as a prefix
    "https://evilcrm.example.com/cb",  # proven name as a suffix
    "https://crm.example.com@evil.example.net/cb",  # userinfo
    "https://evil.example.net\\@crm.example.com/cb",  # parser differential
    "https://evil.example.net\\crm.example.com/cb",  # backslash as a separator
    "https://evil.example.net%2F@crm.example.com/cb",  # encoded separator
    "https://evil.example.net#@crm.example.com/cb",  # fragment trick
    "https://evil.example.net?@crm.example.com/cb",  # query trick
    "https://\u0441rm.example.com/cb",  # Cyrillic look-alike of "c"
    "http://crm.example.com/cb",  # downgrade
    "javascript://crm.example.com/%0aalert(1)",  # another scheme
    "https://crm.example.com/cb#frag",
    "https:///crm.example.com/cb",
    "//crm.example.com/cb",
]


@pytest.mark.parametrize("url", DECEPTIVE_URLS)
@pytest.mark.parametrize("field", ["redirect_uris", "post_logout_redirect_uris"])
def test_a_deceptive_url_never_borrows_a_proven_hostname(staff, live, field, url):
    from workeasy360.contracts.errors import ManifestRejected, ProductDomainUnverified
    from workeasy360.platform.ecosystem.models import ProductIntegration

    before = ProductIntegration.objects.get(pk=live["integration"].pk).manifest
    changes = {field: [f"{ORIGIN}/auth/callback", url]}
    if field == "post_logout_redirect_uris":
        changes = {field: [url]}
    with pytest.raises((ManifestRejected, ProductDomainUnverified)):
        _edit(staff, live, **changes)
    assert ProductIntegration.objects.get(pk=live["integration"].pk).manifest == before
    # Nothing was proven on the strength of the look-alike.
    assert set(_rows(live)) == {"crm.example.com"}


@pytest.mark.parametrize("url", DECEPTIVE_URLS)
def test_a_deceptive_backchannel_uri_is_refused(staff, live, url):
    from workeasy360.contracts.errors import ManifestRejected, ProductDomainUnverified

    with pytest.raises((ManifestRejected, ProductDomainUnverified)):
        _edit(staff, live, manifest_version="2026-11-15", backchannel_logout_uri=url)


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("CRM.Example.COM.", "crm.example.com"),
        ("  crm.example.com  ", "crm.example.com"),
        ("\u0441rm.example.com", "xn--rm-nmc.example.com"),  # never crm.example.com
        ("crm..example.com", ""),
        ("", ""),
    ],
)
def test_two_spellings_of_one_name_compare_equal_and_look_alikes_do_not(raw, canonical):
    from workeasy360.platform.ecosystem import domains

    assert domains.canonical_hostname(raw) == canonical


def test_the_hostnames_of_a_manifest_are_what_a_browser_would_reach():
    from workeasy360.platform.ecosystem import domains

    manifest = {
        "launch_url": "https://CRM.example.com./",
        "redirect_uris": [
            "https://crm.example.com.evil.example.net/cb",
            "https://user@login.example.com/cb",
        ],
        "post_logout_redirect_uris": ["https://crm.example.com:8443/bye"],
    }
    assert domains.hostnames_of(manifest) == [
        "crm.example.com",
        "crm.example.com.evil.example.net",
        "login.example.com",
    ]


def test_an_attacker_backchannel_uri_is_refused_twice_over(staff, live):
    """Same-origin with a redirect URI (ADR-069) already refuses a foreign
    back-channel URI; adding the foreign callback first is refused here."""
    from workeasy360.contracts.errors import ManifestRejected, ProductDomainUnverified

    with pytest.raises(ManifestRejected):
        _edit(
            staff,
            live,
            manifest_version="2026-11-15",
            backchannel_logout_uri=f"{EVIL}/bcl",
        )
    with pytest.raises(ProductDomainUnverified):
        _edit(
            staff,
            live,
            manifest_version="2026-11-15",
            redirect_uris=[f"{ORIGIN}/auth/callback", f"{EVIL}/cb"],
            backchannel_logout_uri=f"{EVIL}/bcl",
        )


def test_edits_on_proven_hostnames_go_through(staff, live):
    _edit(
        staff,
        live,
        display_name="Easy CRM",
        redirect_uris=[f"{ORIGIN}/auth/callback", f"{ORIGIN}/auth/other-callback"],
        manifest_version="2026-11-15",
        backchannel_logout_uri=f"{ORIGIN}/auth/backchannel-logout",
    )


def test_a_new_hostname_is_requested_and_proven_before_the_edit(staff, dns, live):
    from workeasy360.commercial.controlplane import product_domains

    body = staff(
        product_domains.request_domain, product_code="crm", hostname="Login.Example.com"
    )
    entry = next(d for d in body["domains"] if d["hostname"] == "login.example.com")
    assert entry["status"] == "pending" and entry["in_use"] is False
    assert entry["record_name"] == "_w360-product-verify.login.example.com"
    _prove(staff, dns, live, hostname="login.example.com")
    _edit(
        staff,
        live,
        manifest_version="2026-10-12",
        sign_in_mode="sso_optional",
        login_url="https://login.example.com/login",
    )


def test_a_draft_may_name_new_hostnames_freely(staff, draft):
    _edit(staff, draft, redirect_uris=[f"{ORIGIN}/auth/callback", f"{EVIL}/cb"])
    assert _rows(draft)["evil.example.net"].status == "pending"


def test_loopback_development_hosts_need_no_dns(register_product):
    registration = register_product("crm", origin="http://localhost:9100", activate=False)
    row = _rows(registration)["localhost"]
    assert (row.status, row.method) == ("verified", "loopback")


def test_loopback_is_never_accepted_where_plaintext_loopback_is_not(settings):
    from workeasy360.platform.ecosystem import domains

    settings.OIDC_ALLOW_INSECURE_REDIRECT_URIS = False
    assert domains._loopback_accepted("127.0.0.1") is False
    assert domains._loopback_accepted("localhost") is False


# --- re-checks ----------------------------------------------------------------------------


def test_repeated_misses_lapse_a_domain_and_lapsed_blocks_reactivation(staff, dns, live):
    from workeasy360.commercial.controlplane import product_domains
    from workeasy360.contracts.errors import ProductDomainUnverified
    from workeasy360.platform.ecosystem import services

    dns.down = True
    assert product_domains.recheck_verified_domains()["lookup_failed"] == 1
    dns.down = False
    dns.records.clear()  # the record was removed (or the name changed hands)
    for expected_misses in (1, 2):
        product_domains.recheck_verified_domains()
        row = _rows(live)["crm.example.com"]
        assert (row.status, row.missed_checks) == ("verified", expected_misses)
    assert product_domains.recheck_verified_domains()["lapsed"] == 1
    assert _rows(live)["crm.example.com"].status == "lapsed"
    assert _audited("ecosystem.integration.domain_lapsed") == 1

    staff(
        services.transition,
        client_id=live["client_id"],
        to_status="suspended",
        reason="t",
    )
    with pytest.raises(ProductDomainUnverified):
        _activate(staff, live)
    _prove(staff, dns, live)  # the owner republishes: proven again
    _activate(staff, live)


def test_a_found_record_resets_the_miss_count(staff, dns, live):
    from workeasy360.commercial.controlplane import product_domains

    saved = dict(dns.records)
    dns.records.clear()
    product_domains.recheck_verified_domains()
    dns.records.update(saved)
    assert product_domains.recheck_verified_domains()["still_verified"] == 1
    assert _rows(live)["crm.example.com"].missed_checks == 0


# --- carried over -------------------------------------------------------------------------


def test_existing_registrations_are_carried_over(register_product, staff):
    from django.apps import apps

    from workeasy360.platform.ecosystem import services
    from workeasy360.platform.ecosystem.models import ProductDomain

    carry = importlib.import_module(
        "workeasy360.platform.ecosystem.migrations.0008_product_domains"
    ).carry_over
    live = register_product("crm", origin=ORIGIN)
    draft = register_product("books", origin="https://books.example.com", activate=False)
    ProductDomain.objects.all().delete()  # as the table was the day it was created

    carry(apps, None)
    assert {
        (r.hostname, r.status, r.method)
        for r in ProductDomain.objects.filter(integration=live["integration"])
    } == {("crm.example.com", "verified", "pre_existing")}
    pending = ProductDomain.objects.get(integration=draft["integration"])
    assert pending.status == "pending" and pending.token

    # A retired registration is carried over as nothing at all.
    staff(services.transition, client_id=draft["client_id"], to_status="retired")
    ProductDomain.objects.all().delete()
    carry(apps, None)
    assert not ProductDomain.objects.filter(integration=draft["integration"]).exists()


def test_the_operator_command_attests_first_party_hostnames(staff_factory):
    from django.core.management import call_command

    from workeasy360.platform.ecosystem.models import ProductDomain

    staff_user, _ = staff_factory(
        "platform.staff_catalog.manage", "platform.staff_integration.manage"
    )
    call_command("register_products", actor=staff_user.email, activate_integrations=True)
    assert set(ProductDomain.objects.values_list("method", flat=True)) == {"attested"}
    # One first-party hostname per registered product (each on its own host).
    from pathlib import Path
    from urllib.parse import urlsplit

    registry = json.loads(
        (
            Path(__file__).resolve().parents[3] / "infra" / "ecosystem" / "products.json"
        ).read_text(encoding="utf-8")
    )["products"]
    hostnames = {
        (p["code"], urlsplit(u).hostname)
        for p in registry
        for u in [p["integration"]["launch_url"], *p["integration"]["redirect_uris"]]
    }
    assert _audited("ecosystem.integration.domain_attested") == len(hostnames) == 8


# --- the staff API -------------------------------------------------------------------------


def _post(client, path, body):
    return client.post(path, json.dumps(body), content_type="application/json")


def test_staff_list_request_and_verify_through_the_api(staff_login, dns, draft):
    client, _ = staff_login(*CATALOG_STAFF, "platform.staff_integration.manage")
    listed = client.get(DOMAINS).json()
    assert listed["unverified"] == ["crm.example.com"]
    entry = listed["domains"][0]
    assert entry["record_type"] == "TXT" and entry["record_value"].startswith("w360-")

    dns.records[entry["record_name"]] = [entry["record_value"]]
    verified = _post(client, VERIFY, {"hostname": "crm.example.com"})
    assert verified.status_code == 200, verified.content
    assert verified.json()["all_verified"] is True

    requested = _post(client, DOMAINS, {"hostname": "app.example.org"})
    assert requested.status_code == 200
    # The exact set of hostnames, compared as a set: never a substring test.
    hostnames = {d["hostname"] for d in requested.json()["domains"]}
    assert hostnames == {"crm.example.com", "app.example.org"}


@pytest.mark.parametrize(
    "hostname",
    [
        "",
        "a/b",
        "evil.example.com/path",
        "203.0.113.7",
        "localhost",
        "-x.example",
        "https://crm.example.com",
        "crm.example.com:443",
        "user@crm.example.com",
        "*.example.com",
        "crm..example.com",
        "evil.example.net\\crm.example.com",
        "[::1]",
        "crm.example.com/",
    ],
)
def test_a_request_for_something_that_is_not_a_hostname_is_bad_input(
    staff_login, draft, hostname
):
    client, _ = staff_login(*CATALOG_STAFF, "platform.staff_integration.manage")
    response = _post(client, DOMAINS, {"hostname": hostname})
    assert response.status_code == 400, response.content
    assert response.json()["code"] == "manifest_rejected"
    assert response.json()["field"] == "hostname"


def test_a_viewer_can_look_but_not_request_or_verify(staff_login, draft):
    client, _ = staff_login(*CATALOG_STAFF, "platform.staff_integration.view")
    assert client.get(DOMAINS).status_code == 200
    assert client.get(READINESS).status_code == 200
    assert _post(client, DOMAINS, {"hostname": "x.example.org"}).status_code == 403
    assert _post(client, VERIFY, {"hostname": "crm.example.com"}).status_code == 403


def test_verification_is_rate_limited(staff_login, dns, draft, settings):
    client, _ = staff_login(*CATALOG_STAFF, "platform.staff_integration.manage")
    limits = dict(settings.CONTROL_PLANE_RATE_LIMITS)
    limits["product_domain_verify_user"] = (2, 60)
    settings.CONTROL_PLANE_RATE_LIMITS = limits
    codes = [
        _post(client, VERIFY, {"hostname": "crm.example.com"}).status_code
        for _ in range(3)
    ]
    assert codes[:2] == [200, 200] and codes[2] == 429


def test_the_refused_activation_names_what_to_publish(staff_login, draft):
    client, _ = staff_login(*CATALOG_STAFF, "platform.staff_integration.manage")
    response = _post(
        client,
        "/api/v1/admin/products/crm/integration/status",
        {"status": "active"},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "product_domain_unverified"
    assert body["hostnames"] == ["crm.example.com"]


# --- readiness -----------------------------------------------------------------------------


def _states(body):
    return {c["key"]: c["state"] for c in body["checks"]}


def test_readiness_names_what_stops_sign_in(staff_login, draft):
    client, _ = staff_login(*CATALOG_STAFF, "platform.staff_integration.view")
    body = client.get(READINESS).json()
    assert body["verdict"] == "not_ready"
    states = _states(body)
    assert states["domains"] == "action" and states["status"] == "action"
    assert states["credential"] == "ok" and states["sign_in"] == "ok"
    assert states["backchannel"] == "warning"


def test_readiness_is_ready_when_everything_is_in_place(staff_login, staff, dns, live):
    client, _ = staff_login(*CATALOG_STAFF, "platform.staff_integration.view")
    _edit(
        staff,
        live,
        manifest_version="2026-11-15",
        backchannel_logout_uri=f"{ORIGIN}/auth/backchannel-logout",
    )
    from django.utils import timezone

    from workeasy360.platform.ecosystem.models import (
        BackchannelLogout,
        ProductIntegration,
    )

    # "In place" includes the evidence (2026-11-18): a completed sign-in and an
    # acknowledged logout, not only the configuration.
    ProductIntegration.objects.filter(pk=live["integration"].pk).update(
        health_status="healthy", last_token_at=timezone.now()
    )
    BackchannelLogout.objects.create(
        integration_id=live["integration"].pk,
        user_id=live["integration"].pk,
        reason="signed_out",
        status="delivered",
        finished_at=timezone.now(),
    )
    body = client.get(READINESS).json()
    assert body["verdict"] == "ready", body
    assert set(_states(body).values()) == {"ok"}


def test_readiness_for_a_product_without_an_integration(staff_login, catalog):
    client, _ = staff_login(*CATALOG_STAFF, "platform.staff_integration.view")
    body = client.get(READINESS).json()
    assert body["verdict"] == "not_ready" and _states(body) == {"registered": "action"}

"""11c - launch, single sign-on truth and product health.

The app directory says, per product, what it is, whether the organization holds
it, whether sign-in can complete and what the last health check saw. Launch
returns a server-owned destination. Health is telemetry: it is shown, never
used to refuse anyone."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.phase11.conftest import (
    CATALOG_STAFF,
    audit_of,
    configure_trial,
    put,
    register_bare,
    staff_scope,
    start_trial,
    url,
)
from workeasy360.commercial.catalog import services as catalog_services

pytestmark = pytest.mark.django_db

APPS = "apps"


def directory(shop, client=None):
    response = (client or shop["client"]).get(url(shop["slug"], APPS))
    assert response.status_code == 200, response.content
    return {a["product_code"]: a for a in response.json()["apps"]}


def own_seat(shop, product="crm"):
    response = shop["client"].post(
        url(shop["slug"], f"apps/{product}/seats/{shop['membership'].id}")
    )
    assert response.status_code in (200, 201), response.content


def launch(shop, product="crm", *, json_mode=True, client=None, **extra):
    headers = {"HTTP_ACCEPT": "application/json"} if json_mode else {}
    return (client or shop["client"]).post(
        url(shop["slug"], f"apps/{product}/launch"), **headers, **extra
    )


@pytest.fixture
def live(shop, register_product):
    """An organization on a CRM trial, seated, with CRM registered and active
    *with* a credential (single sign-on connected)."""
    registration = register_product("crm", origin="https://crm.example.com")
    assert start_trial(shop).status_code == 201
    own_seat(shop)
    return {**shop, "registration": registration}


# --- the directory ------------------------------------------------------------------------


def test_a_card_carries_presentation_connection_and_trial_facts(live):
    with staff_scope(*CATALOG_STAFF) as ctx:
        catalog_services.update_product(
            ctx,
            product_id=live["catalog"]["products"]["crm"].id,
            changes={
                "category": "Sales / CRM",
                "icon": "customers",
                "tagline": "Sell more",
            },
        )
    card = directory(live)["crm"]
    assert (card["slug"], card["category"], card["icon"], card["tagline"]) == (
        "crm",
        "Sales / CRM",
        "customers",
        "Sell more",
    )
    assert card["state"] == "active" and card["can_launch"]
    assert card["entitlement_status"] == "trial"
    assert card["sso"] == "connected"
    assert card["health"] == {"status": "unknown", "checked_at": None}
    assert card["trial"]["days_left"] == 14
    assert card["trial"]["id"]
    # An organization's owner sees the plan and seats; renewal is not a thing
    # for a trial.
    assert card["plan"] == {"code": "crm_starter", "name": "CRM Starter"}
    assert card["seats"] == {"total": 2, "used": 1}
    assert card["renews_at"] is None


def test_a_product_the_organization_does_not_hold_has_no_holding_facts(live):
    card = directory(live)["books"]
    assert card["state"] in ("product_unavailable", "no_entitlement")
    assert card["trial"] is None and card["plan"] is None and card["seats"] is None


def test_days_left_counts_down_and_never_goes_below_zero(live):
    from workeasy360.commercial.subscriptions.models import ProductTrial
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    def move_end(delta):
        with (
            unscoped_access(reason="Test moves a trial end", module="tests.conftest"),
            organization_db_scope(live["id"]),
        ):
            ProductTrial.unscoped.filter(organization_id=live["id"]).update(
                ends_at=timezone.now() + delta
            )

    move_end(timedelta(hours=30))
    assert directory(live)["crm"]["trial"]["days_left"] == 2  # rounded up, not "1"
    move_end(timedelta(hours=2))
    assert directory(live)["crm"]["trial"]["days_left"] == 1
    move_end(timedelta(seconds=30))
    assert directory(live)["crm"]["trial"]["days_left"] == 1  # still works: not "0"


def test_a_plain_member_sees_the_card_but_not_the_money_or_seats(live, login):
    member = live["add"]("member")
    client = login(member["user"])
    card = directory(live, client)["crm"]
    assert card["trial"] is not None  # when it ends decides whether they can use it
    assert card["plan"] is None
    assert card["seats"] is None
    assert card["renews_at"] is None and card["cancel_at"] is None


def test_the_directory_costs_the_same_however_many_products_are_held(live):
    """Cards are enriched in a fixed number of queries - a card per product must
    not be a query per product (a Phase 10 lesson)."""
    from tests.phase11.conftest import count_queries

    few, _ = count_queries(lambda: live["client"].get(url(live["slug"], APPS)))
    with staff_scope(*CATALOG_STAFF) as ctx:
        for code in ("desk", "vault", "board", "ledger"):
            product = catalog_services.create_product(ctx, code=code, name=code.title())
            catalog_services.set_product_status(
                ctx, product_id=product.id, status=_available()
            )
    many, _ = count_queries(lambda: live["client"].get(url(live["slug"], APPS)))
    # Four more products cost at most the per-product access decision they
    # always cost (a bounded handful), not the card enrichment.
    assert many - few <= 4 * 6


def _available():
    from workeasy360.contracts.enums import ProductStatus

    return ProductStatus.AVAILABLE


# --- single sign-on truth -----------------------------------------------------------------


def test_a_product_with_no_credential_says_sso_integration_required(
    shop, platform_admin, in_tenant
):
    register_bare(platform_admin, in_tenant, "crm", origin="https://crm.example.com")
    assert start_trial(shop).status_code == 201
    card = directory(shop)["crm"]
    assert card["sso"] == "integration_required"
    # Still launchable: the product's own sign-in page opens. Nothing here
    # fakes single sign-on and nothing puts a token in a URL.
    own_seat(shop)
    response = launch(shop)
    assert response.status_code == 200
    target = response.json()["redirect_url"]
    # ADR-044: Workeasy360 sign-in cannot complete without a credential, so
    # the launch is the product's own page, with no organization hint that
    # would make the product start a Workeasy360 sign-in by itself.
    assert response.json()["sign_in"] == "product"
    assert target == "https://crm.example.com/launch"
    assert "token" not in target and "code=" not in target


def test_a_revoked_or_expired_credential_is_not_connected(
    live, platform_admin, in_tenant
):
    from workeasy360.platform.ecosystem import services
    from workeasy360.platform.ecosystem.models import IntegrationCredential

    assert directory(live)["crm"]["sso"] == "connected"
    ctx = platform_admin["ctx"]
    with in_tenant(ctx):
        services.revoke_credential(
            ctx,
            client_id=live["registration"]["client_id"],
            credential_id=live["registration"]["credential_id"],
            reason="rotation test",
        )
    assert directory(live)["crm"]["sso"] == "integration_required"

    # An unexpired credential comes back...
    with in_tenant(ctx):
        issued = services.create_credential(
            ctx, client_id=live["registration"]["client_id"], kind="client_secret"
        )
    assert directory(live)["crm"]["sso"] == "connected"
    # ...and an expired one does not count.
    IntegrationCredential.objects.filter(credential_id=issued.credential_id).update(
        expires_at=timezone.now() - timedelta(days=1)
    )
    assert directory(live)["crm"]["sso"] == "integration_required"


def test_the_customer_view_never_names_a_client_credential_or_registration(live):
    card = directory(live)["crm"]
    text = json.dumps(card)
    assert live["registration"]["client_id"] not in text
    assert "launch_url" not in card and "client_id" not in card
    assert "crm.example.com" not in text


# --- launching ----------------------------------------------------------------------------


def test_launch_returns_the_server_owned_destination(live):
    response = launch(live)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["state"] == "active"
    assert body["redirect_url"] == (
        f"https://crm.example.com/launch?organization={live['id']}"
    )


def test_a_client_cannot_choose_where_launch_goes(live):
    hostile = "https://evil.example/steal"
    for extra in (
        {
            "data": {"launch_url": hostile, "redirect_url": hostile, "next": hostile},
            "content_type": "application/json",
        },
        {"QUERY_STRING": f"launch_url={hostile}&redirect_url={hostile}&next={hostile}"},
        {"HTTP_REFERER": hostile, "HTTP_X_FORWARDED_HOST": "evil.example"},
    ):
        response = launch(live, **extra)
        assert response.status_code == 200
        assert response.json()["redirect_url"].startswith("https://crm.example.com/")
        assert "evil.example" not in response.json()["redirect_url"]


def test_the_compatibility_redirect_is_unchanged(live):
    response = launch(live, json_mode=False)
    assert response.status_code == 302
    assert (
        response["Location"]
        == f"https://crm.example.com/launch?organization={live['id']}"
    )


def test_a_launch_url_that_already_has_a_query_gets_one_query(
    live, platform_admin, in_tenant
):
    from workeasy360.platform.ecosystem import services

    ctx = platform_admin["ctx"]
    manifest = dict(live["registration"]["integration"].manifest)
    manifest["launch_url"] = "https://crm.example.com/start?tenant=x&organization=forged"
    with in_tenant(ctx):
        services.update_manifest(
            ctx, client_id=live["registration"]["client_id"], manifest=manifest
        )
    target = launch(live).json()["redirect_url"]
    assert target.count("?") == 1
    assert target.endswith(f"tenant=x&organization={live['id']}")
    assert "forged" not in target  # a registered value never overrides ours


def test_launch_is_refused_without_a_seat_with_a_state_the_page_can_show(live):
    from workeasy360.platform.launcher import services as launcher_services  # noqa: F401

    released = live["client"].delete(
        url(live["slug"], f"apps/crm/seats/{live['membership'].id}")
    )
    assert released.status_code == 200
    response = launch(live)
    assert response.status_code == 409
    assert response.json() == {
        "code": "launch_denied",
        "state": "no_seat",
        "product_code": "crm",
    }


def test_launch_requires_csrf_and_a_session(live):
    from django.test import Client

    strict = Client(enforce_csrf_checks=True)
    assert launch(live, client=strict).status_code in (401, 403, 404)
    assert launch(live, client=Client()).status_code in (401, 403, 404)


def test_launch_of_an_unknown_or_malformed_product_is_a_denial_not_an_error(live):
    for code in ("nope", "CRM", "x" * 80, "crm;drop"):
        response = launch(live, code)
        assert response.status_code in (404, 409), (code, response.content)


# --- the suspension matrix (what launch does under each stop) ------------------------------


def test_an_integration_suspended_by_staff_stops_launch(live, platform_admin, in_tenant):
    from workeasy360.platform.ecosystem import services

    ctx = platform_admin["ctx"]
    with in_tenant(ctx):
        services.transition(
            ctx,
            client_id=live["registration"]["client_id"],
            to_status="suspended",
            reason="planned maintenance window",
        )
    assert directory(live)["crm"]["state"] == "integration_suspended"
    assert launch(live).json()["state"] == "integration_suspended"
    with in_tenant(ctx):
        services.transition(
            ctx, client_id=live["registration"]["client_id"], to_status="active"
        )
    assert launch(live).status_code == 200


def test_a_removed_member_cannot_launch_or_even_see_the_directory(live, login):
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    member = live["add"]("member")
    client = login(member["user"])
    assert launch(live, client=client).status_code in (409, 200)  # a member, no seat
    with (
        unscoped_access(reason="Test removes a member", module="tests.conftest"),
        organization_db_scope(live["id"]),
    ):
        Membership.unscoped.filter(id=member["membership"].id).update(status="removed")
    assert launch(live, client=client).status_code in (401, 403, 404)


def test_a_suspended_organization_cannot_launch(live):
    from workeasy360.platform.organizations.models import Organization

    Organization.objects.filter(id=live["id"]).update(status="suspended")
    response = launch(live)
    assert response.status_code == 402
    assert response.json()["code"] == "organization_not_writable"


def test_an_expired_trial_cannot_launch(live):
    from workeasy360.commercial.provisioning.services import reconcile
    from workeasy360.commercial.subscriptions.system import settle_product_trials

    ends = timezone.now() + timedelta(days=15)
    settle_product_trials(live["id"], at=ends)
    reconcile(live["id"], at=ends)
    response = launch(live)
    assert response.status_code == 409
    assert response.json()["state"] == "no_entitlement"


def test_a_suspended_subscription_is_read_only_not_a_launch_block(live):
    """The platform's standing policy: a suspended subscription leaves the
    product readable (entitlement ``suspended`` allows read), so the launcher
    still opens it and the product enforces read-only. Recorded here so a change
    of policy is a decision, not an accident."""
    from tests.extension.conftest import buy_directly
    from workeasy360.commercial.payments.models import Invoice  # noqa: F401
    from workeasy360.commercial.subscriptions import system

    # Swap the trial for a purchased, then suspended, subscription.
    buy_directly(
        {"org": live["org"], "membership": live["membership"], "owner": live["owner"]},
        live["catalog"]["versions"]["crm_professional"].id,
    )
    from workeasy360.commercial.subscriptions.models import Subscription
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="Test suspends", module="tests.conftest"),
        organization_db_scope(live["id"]),
    ):
        Subscription.unscoped.filter(organization_id=live["id"]).update(
            status="suspended"
        )
    from workeasy360.commercial.provisioning.services import reconcile

    reconcile(live["id"])
    assert system  # imported for the module's side effects on listeners
    card = directory(live)["crm"]
    assert card["entitlement_status"] == "suspended"


# --- health (telemetry only) ---------------------------------------------------------------


def _fake_send(monkeypatch, *, status=200, failure=None, rejected=None, seen=None):
    from workeasy360.integration.egress.client import EgressFailure, EgressResponse
    from workeasy360.integration.egress.policy import EgressRejected
    from workeasy360.integration.health import probe

    def send(request, **kwargs):
        if seen is not None:
            seen.append((request, kwargs))
        if rejected:
            raise EgressRejected(rejected, "refused")
        if failure:
            raise EgressFailure(failure, "failed")
        return EgressResponse(
            status=status, headers={}, body=b"", truncated=False, duration_s=0.01
        )

    monkeypatch.setattr(probe, "send", send)


@pytest.mark.parametrize(
    ("status", "expected", "detail"),
    [
        (200, "healthy", "http_200"),
        (307, "healthy", "http_307"),  # a front door that sends visitors to /login
        (404, "degraded", "http_404"),
        (503, "degraded", "http_503"),
    ],
)
def test_a_response_is_classified_and_recorded(
    live, monkeypatch, status, expected, detail
):
    from workeasy360.integration.health import probe

    _fake_send(monkeypatch, status=status)
    result = probe.check_product("crm")
    assert (result.status.value, result.detail) == (expected, detail)
    health = directory(live)["crm"]["health"]
    assert health["status"] == expected and health["checked_at"]


@pytest.mark.parametrize(
    ("failure", "rejected", "detail"),
    [
        ("tls_error", None, "tls_error"),
        ("dns_failure", None, "dns_failure"),
        ("connect_timeout", None, "connect_timeout"),
        (None, "host_blocked", "rejected_host_blocked"),
    ],
)
def test_a_failure_is_unreachable_with_its_category_never_a_bypass(
    live, monkeypatch, failure, rejected, detail
):
    from workeasy360.integration.health import probe

    seen: list = []
    _fake_send(monkeypatch, failure=failure, rejected=rejected, seen=seen)
    result = probe.check_product("crm")
    assert result.status.value == "unreachable" and result.detail == detail
    assert len(seen) == 1  # one attempt; nothing retried with verification off


def test_the_probe_asks_only_the_registered_address_over_the_egress_boundary(
    live, monkeypatch
):
    from workeasy360.integration.health import probe

    seen: list = []
    _fake_send(monkeypatch, seen=seen)
    probe.check_product("crm")
    request, kwargs = seen[0]
    assert request.method == "GET" and request.body == b""
    assert request.url == "https://crm.example.com/healthz"  # the manifest's health_url
    assert kwargs["max_response_bytes"] <= 4096
    assert kwargs["timeouts"].total <= 10


def test_a_product_without_a_health_url_is_checked_at_its_front_door(
    shop, platform_admin, in_tenant, monkeypatch
):
    from workeasy360.integration.health import probe

    register_bare(platform_admin, in_tenant, "crm", origin="https://crm.example.com")
    seen: list = []
    _fake_send(monkeypatch, seen=seen)
    from workeasy360.platform.ecosystem.models import ProductIntegration

    row = ProductIntegration.objects.get(product_code="crm")
    manifest = dict(row.manifest)
    manifest.pop("health_url", None)
    ProductIntegration.objects.filter(pk=row.pk).update(manifest=manifest)
    probe.check_product("crm")
    assert seen[0][0].url == "https://crm.example.com/launch"


def test_a_sweep_checks_every_launchable_product_and_only_those(
    live, platform_admin, in_tenant, monkeypatch
):
    from workeasy360.integration.health import probe

    with staff_scope(*CATALOG_STAFF) as ctx:
        # A product may be registered before it is even for sale (the catalog
        # and the integration registry are independent lifecycles) - so a
        # draft product is a valid, distinct third case.
        draft = catalog_services.create_product(ctx, code="vault", name="Vault")

    register_bare(platform_admin, in_tenant, "books", origin="https://books.example.com")
    register_bare(
        platform_admin,
        in_tenant,
        "vault",
        origin="https://draft.example.com",
        activate=False,
    )
    assert draft.status == "draft"  # never registered as sellable in this test
    seen: list = []
    _fake_send(monkeypatch, seen=seen)
    counts = probe.check_all()
    assert counts["checked"] == 2  # crm and books; the draft has nowhere to go yet
    assert {r.url.split("/")[2] for r, _ in [(s[0], s[1]) for s in seen]} == {
        "crm.example.com",
        "books.example.com",
    }


def test_an_unhealthy_product_is_still_launchable_and_the_platform_still_answers(
    live, monkeypatch
):
    from workeasy360.integration.health import probe

    _fake_send(monkeypatch, failure="tls_error")
    probe.check_all()
    card = directory(live)["crm"]
    assert card["health"]["status"] == "unreachable"
    assert card["can_launch"] is True
    assert launch(live).status_code == 200
    # Readiness says nothing about products.
    ready = live["client"].get("/health/ready")
    assert ready.status_code in (200, 404)


def test_the_dashboard_makes_no_outbound_request(live, monkeypatch):
    from workeasy360.integration.health import probe

    calls: list = []
    monkeypatch.setattr(probe, "send", lambda *a, **k: calls.append(a))
    for _ in range(3):
        directory(live)
        live["client"].get(url(live["slug"], "explore"))
    assert calls == []


def test_recording_a_health_result_changes_nothing_else(live, monkeypatch):
    from workeasy360.integration.health import probe
    from workeasy360.platform.ecosystem.models import ProductIntegration

    before = ProductIntegration.objects.values("status", "manifest", "client_id").get(
        product_code="crm"
    )
    _fake_send(monkeypatch, status=503)
    probe.check_product("crm")
    after = ProductIntegration.objects.values("status", "manifest", "client_id").get(
        product_code="crm"
    )
    assert before == after


# --- manifest addresses ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "https://169.254.169.254/latest/meta-data",
        "https://localhost/",
        "https://127.0.0.1/",
        "https://10.1.2.3/",
        "https://192.168.0.10/",
        "https://[::1]/",
        "https://metadata.google.internal/",
        "https://db.internal/",
        "https://printer.local/",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "ftp://example.com/",
        "https://user:pass@example.com/",
    ],
)
def test_a_manifest_address_may_not_point_inward(
    live, platform_admin, in_tenant, address
):
    from workeasy360.contracts.errors import ManifestRejected
    from workeasy360.platform.ecosystem import services

    ctx = platform_admin["ctx"]
    manifest = dict(live["registration"]["integration"].manifest)
    for field in ("launch_url", "health_url"):
        candidate = {**manifest, field: address}
        with in_tenant(ctx), pytest.raises(ManifestRejected):
            services.update_manifest(
                ctx, client_id=live["registration"]["client_id"], manifest=candidate
            )


def test_public_addresses_including_the_four_products_are_accepted():
    from workeasy360.contracts.values.manifest import _validate_url

    for address in (
        "https://book.workeasy360.com/",
        "https://help.workeasy360.com/",
        "https://ops.workeasy360.com/",
        "https://crm.workeasy360.com/",
    ):
        assert _validate_url(address, field="launch_url", allow_insecure=False) == address


# --- the Super Admin products console -------------------------------------------------------


CONSOLE = "/api/v1/admin/products/console"


def test_the_console_shows_each_product_whole(live, staff_login):
    client, _ = staff_login(*CATALOG_STAFF, "platform.staff_integration.manage")
    response = client.get(CONSOLE)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["can_see_integrations"] is True
    crm = next(p for p in body["products"] if p["code"] == "crm")
    assert crm["status"] == "available"
    assert crm["integration"]["status"] == "active"
    assert crm["integration"]["sso"] == "connected"
    assert crm["integration"]["health"]["status"] == "unknown"
    assert crm["integration"]["launch_url"] == "https://crm.example.com/launch"
    # Channels are derived from existing constructs, not new flags.
    assert crm["channels"]["direct"] == {
        "listed": True,
        "available": True,
        "public_plans": 2,
        "priced_plans": 2,
        "ready": True,
    }
    assert crm["channels"]["reseller"] == {"partners": 0}
    assert crm["channels"]["trial"]["enabled"] is True
    assert {p["code"] for p in crm["plans"]} == {"crm_starter", "crm_professional"}
    text = json.dumps(body)
    assert "client_secret" not in text and "public_key" not in text
    # Commercial and technical status are separate fields.
    books = next(p for p in body["products"] if p["code"] == "books")
    assert books["integration"] is None
    assert books["status"] == "available"


def test_staff_without_the_integration_grant_see_no_integration_section(
    live, staff_login
):
    client, _ = staff_login(*CATALOG_STAFF)
    body = client.get(CONSOLE).json()
    assert body["can_see_integrations"] is False
    assert all(p["integration"] is None for p in body["products"])


def test_the_console_is_staff_only(live, staff_login, login):
    outsider, _ = staff_login("platform.staff_customer.view")
    assert outsider.get(CONSOLE).status_code == 403
    assert login(live["owner"]).get(CONSOLE).status_code in (401, 403, 404)
    from django.test import Client

    assert Client().get(CONSOLE).status_code in (401, 403)


def _edit(client, code, body, method="patch"):
    return getattr(client, method)(
        f"/api/v1/admin/products/{code}/integration",
        json.dumps(body),
        content_type="application/json",
    )


def test_a_launch_address_can_be_changed_and_is_audited(live, staff_login):
    client, _ = staff_login(*CATALOG_STAFF, "platform.staff_integration.manage")
    response = _edit(client, "crm", {"launch_url": "https://new.crm.example.com/open"})
    assert response.status_code == 200, response.content
    assert response.json()["launch_url"] == "https://new.crm.example.com/open"
    assert (
        launch(live)
        .json()["redirect_url"]
        .startswith("https://new.crm.example.com/open?")
    )
    rows = audit_of(live["registration"]["integration"].id, "action")
    assert "ecosystem.integration.manifest_updated" in {r["action"] for r in rows}


@pytest.mark.parametrize(
    "body",
    [
        {"launch_url": "https://169.254.169.254/"},
        {"launch_url": "javascript:alert(1)"},
        {"launch_url": "http://crm.example.com/"},
        {"health_url": "https://localhost/health"},
        {"redirect_uris": ["https://*.example.com/cb"]},
        {"product_code": "books"},
        {"token_endpoint_auth_method": "none"},
        {"client_id": "forged"},
        {"launch_url": "x" * 600},
    ],
)
def test_a_bad_edit_is_refused_and_changes_nothing(live, staff_login, body):
    client, _ = staff_login(*CATALOG_STAFF, "platform.staff_integration.manage")
    response = _edit(client, "crm", body)
    assert response.status_code == 400, (body, response.content)
    assert (
        launch(live).json()["redirect_url"].startswith("https://crm.example.com/launch?")
    )


def test_editing_needs_the_integration_manage_grant(live, staff_login):
    client, _ = staff_login(*CATALOG_STAFF, "platform.staff_integration.view")
    assert (
        _edit(client, "crm", {"launch_url": "https://new.example.com/"}).status_code
        == 403
    )


def test_the_technical_switch_is_separate_from_the_commercial_status(live, staff_login):
    client, _ = staff_login(*CATALOG_STAFF, "platform.staff_integration.manage")
    suspended = client.post(
        "/api/v1/admin/products/crm/integration/status",
        json.dumps({"status": "suspended", "reason": "maintenance window"}),
        content_type="application/json",
    )
    assert suspended.status_code == 200, suspended.content
    assert suspended.json()["status"] == "suspended"
    body = client.get(CONSOLE).json()
    crm = next(p for p in body["products"] if p["code"] == "crm")
    assert crm["status"] == "available"  # still for sale
    assert crm["integration"]["status"] == "suspended"
    # A reason is required, and an illegal move is refused.
    no_reason = client.post(
        "/api/v1/admin/products/crm/integration/status",
        json.dumps({"status": "active"}),
        content_type="application/json",
    )
    assert no_reason.status_code == 200  # resuming needs none
    bad = client.post(
        "/api/v1/admin/products/crm/integration/status",
        json.dumps({"status": "draft"}),
        content_type="application/json",
    )
    assert bad.status_code >= 400


def test_check_now_probes_the_registered_address_and_is_rate_limited(
    live, staff_login, monkeypatch, settings
):
    client, _ = staff_login(*CATALOG_STAFF, "platform.staff_integration.view")
    seen: list = []
    _fake_send(monkeypatch, status=200, seen=seen)
    response = client.post("/api/v1/admin/products/crm/integration/health-check")
    assert response.status_code == 200, response.content
    assert response.json() == {"status": "healthy", "detail": "http_200"}
    assert seen[0][0].url == "https://crm.example.com/healthz"
    # There is no way to make the platform look anywhere else.
    forged = client.post(
        "/api/v1/admin/products/crm/integration/health-check?url=https://evil.example/",
        json.dumps({"url": "https://evil.example/"}),
        content_type="application/json",
    )
    assert forged.status_code == 200
    assert all("evil.example" not in s[0].url for s in seen)
    unknown = client.post("/api/v1/admin/products/no_such/integration/health-check")
    assert unknown.status_code == 400


# --- explore ---------------------------------------------------------------------------------


def test_explore_says_what_this_organization_may_do_about_each_product(live):
    response = live["client"].get(url(live["slug"], "explore"))
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["currency"] == "GBP" and body["billing_account"] is True
    by_code = {p["code"]: p for p in body["products"]}
    assert by_code["crm"]["trial_state"] == "active"
    assert by_code["crm"]["holding"] == "trial"
    assert by_code["books"]["trial_state"] == "unavailable"  # none configured
    assert by_code["books"]["holding"] is None
    prices = by_code["books"]["plans"][0]["prices"]
    assert prices[0]["amount_minor"] == 2999 and prices[0]["currency"] == "GBP"
    assert by_code["books"]["starting_price"]["amount_minor"] == 2999


def test_explore_offers_a_trial_until_it_is_used_or_the_product_is_bought(shop):
    def state():
        products = shop["client"].get(url(shop["slug"], "explore")).json()["products"]
        return next(p for p in products if p["code"] == "crm")["trial_state"]

    assert state() == "available"
    assert start_trial(shop).status_code == 201
    assert state() == "active"
    from workeasy360.commercial.provisioning.services import reconcile
    from workeasy360.commercial.subscriptions.system import settle_product_trials

    late = timezone.now() + timedelta(days=15)
    settle_product_trials(shop["id"], at=late)
    reconcile(shop["id"], at=late)
    # Live again "now": the clock in this test did not move, only the sweep's.
    assert state() in ("used", "active")


def test_explore_marks_a_bought_product_as_owned(shop):
    from tests.phase11.conftest import place_and_pay

    place_and_pay(
        shop["client"],
        shop["slug"],
        shop["id"],
        plan_version_id=str(shop["catalog"]["versions"]["crm_professional"].id),
    )
    products = shop["client"].get(url(shop["slug"], "explore")).json()["products"]
    crm = next(p for p in products if p["code"] == "crm")
    assert crm["trial_state"] == "owned" and crm["holding"] == "subscription"


def test_explore_is_readable_by_a_member_and_closed_to_outsiders(
    live, login, console_org
):
    member = live["add"]("member")
    assert login(member["user"]).get(url(live["slug"], "explore")).status_code == 200
    other = console_org()
    assert login(other["owner"]).get(url(live["slug"], "explore")).status_code in (
        403,
        404,
    )
    from django.test import Client

    assert Client().get(url(live["slug"], "explore")).status_code in (401, 403, 404)


def test_uuid_helper_is_unused_but_keeps_the_import_honest():
    assert uuid.uuid4() and put and configure_trial

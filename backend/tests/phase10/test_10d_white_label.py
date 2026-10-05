"""Phase 10d: the white-label list, and staff preparing a partner's brand and
domain through the very commands the partner portal uses."""

from __future__ import annotations

import json
import uuid

import pytest

from tests.phase10.conftest import PASSWORD
from workeasy360.commercial.partners import branding, domains, services
from workeasy360.commercial.partners.models import (
    PartnerBrand,
    PartnerDomain,
    PartnerLogoUpload,
)
from workeasy360.contracts.enums import PartnerStatus

pytestmark = pytest.mark.django_db

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
HOSTNAME = "app.wl-example.com"


class FakeDns:
    def __init__(self, records=None):
        self.records = records or {}

    def txt_values(self, name):
        return self.records.get(name, [])


@pytest.fixture
def dns():
    fake = FakeDns()
    previous = domains.set_txt_resolver(fake)
    yield fake
    domains.set_txt_resolver(previous)


@pytest.fixture
def manager(staff_client):
    client, user = staff_client(
        "platform.staff_partner.manage", "platform.staff_partner.view"
    )
    return client


@pytest.fixture
def wl(partner_factory):
    return partner_factory("branding.manage", "domain.manage")


def put(client, url, body):
    return client.put(url, json.dumps(body), content_type="application/json")


def post(client, url, body=None, **extra):
    return client.post(
        url, json.dumps(body or {}), content_type="application/json", **extra
    )


def urls(partner):
    base = f"/api/v1/admin/partners/{partner.id}"
    return {
        "brand": f"{base}/brand",
        "logo": f"{base}/brand/logo",
        "logo_view": f"{base}/brand/logo/view",
        "domain": f"{base}/domains/request",
        "domains": f"{base}/domains",
    }


# --- the list ---------------------------------------------------------------------------------


def rows(client, **params):
    response = client.get("/api/v1/admin/partners/white-label", params)
    assert response.status_code == 200, response.content
    return response.json()["results"]


def test_the_list_is_a_view_of_partners_holding_branding_or_domain_capability(
    console, staff_client
):
    client, _u = staff_client("platform.staff_partner.view")
    codes = {r["code"] for r in rows(client)}
    assert codes == {"wl-console"}  # the reseller and the draft hold neither capability
    row = rows(client)[0]
    assert [e["key"] for e in row["experience"]] == ["white_label"]
    assert row["brand"] == {"configured": False, "name": "", "has_logo": False}
    assert row["domain"] == {
        "hostname": "brand.console-example.com",
        "status": "requested",
    }
    assert row["customers"] == 0 and row["updated_at"]


def test_a_partner_with_only_one_of_the_two_capabilities_still_appears(
    partner_factory, staff_client
):
    only_brand = partner_factory("branding.manage", code="only-brand")
    only_domain = partner_factory("domain.manage", code="only-domain")
    partner_factory("customer.provision", code="reseller-only")
    client, _u = staff_client("platform.staff_partner.view")
    assert {r["code"] for r in rows(client)} == {"only-brand", "only-domain"}
    assert only_brand and only_domain


def test_revoking_both_capabilities_removes_a_partner_from_the_list(wl, staff_client):
    client, _u = staff_client("platform.staff_partner.view")
    assert len(rows(client)) == 1
    for capability in ("branding.manage", "domain.manage"):
        services.revoke_capability(
            wl["staff_ctx"],
            wl["partner"].id,
            capability=capability,
            reason="Revoked for the list test",
        )
    assert rows(client) == []


def test_a_combined_partner_is_one_row_wearing_both_labels(console, staff_client):
    client, _u = staff_client("platform.staff_partner.view")
    reseller = console["reseller"]
    for capability in ("branding.manage", "domain.manage"):
        services.grant_capability(
            reseller["staff_ctx"],
            reseller["partner"].id,
            capability=capability,
            reason="Combined partner",
        )
    found = [r for r in rows(client) if r["code"] == reseller["partner"].code]
    assert len(found) == 1
    assert [e["key"] for e in found[0]["experience"]] == ["reseller", "white_label"]
    assert found[0]["customers"] == 1


def test_the_list_filters_by_brand_domain_status_text_and_partner_status(
    console, staff_client
):
    client, _u = staff_client("platform.staff_partner.view")
    assert len(rows(client, domain_status="awaiting")) == 1
    assert len(rows(client, domain_status="requested")) == 1
    assert rows(client, domain_status="active") == []
    assert rows(client, domain_status="none") == []
    assert len(rows(client, brand="none")) == 1
    assert rows(client, brand="configured") == []
    assert len(rows(client, q="console-example")) == 1  # a hostname
    assert len(rows(client, q="wl-cons")) == 1
    assert rows(client, q="nothing-like-this") == []
    assert len(rows(client, status="active")) == 1
    assert rows(client, status="draft") == []
    for bad in ({"domain_status": "x"}, {"brand": "x"}, {"status": "x"}):
        assert client.get("/api/v1/admin/partners/white-label", bad).status_code == 400


def test_the_list_needs_the_view_permission(console, staff_client):
    weak, _u = staff_client("platform.staff_customer.view")
    assert weak.get("/api/v1/admin/partners/white-label").status_code == 403


def test_the_list_costs_the_same_at_any_page_size(partner_factory, staff_client):
    from tests.phase10.conftest import count_queries

    for i in range(5):
        partner_factory("branding.manage", code=f"wl-{i}")
    client, _u = staff_client("platform.staff_partner.view")
    client.get("/api/v1/admin/partners/white-label?limit=1")

    def cost(limit):
        count, response = count_queries(
            lambda: client.get(f"/api/v1/admin/partners/white-label?limit={limit}")
        )
        return count, len(response.json()["results"])

    small, small_rows = cost(1)
    large, large_rows = cost(5)
    assert (small_rows, large_rows) == (1, 5) and small == large


# --- staff prepare the brand ---------------------------------------------------------------------


def test_staff_set_a_partners_brand_through_the_partner_command(wl, manager):
    response = put(
        manager,
        urls(wl["partner"])["brand"],
        {
            "brand_name": "Acme Brand",
            "accent_color": "#1A56DB",
            "support_email": "help@acme.example",
        },
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert (
        body["brand_name"] == "Acme Brand"
        and body["accent_color"] == "#1a56db"
        and body["version"] == 0
    )
    row = PartnerBrand.objects.get(partner=wl["partner"])
    assert row.updated_by_id != wl["user"].id  # a staff member wrote it
    # It reads back the same way the partner portal would show it.
    assert branding.get_brand(wl["actor"]())["brand_name"] == "Acme Brand"


def test_the_partner_in_the_path_is_the_only_authority(wl, partner_factory, manager):
    other = partner_factory("branding.manage")
    put(
        manager,
        urls(wl["partner"])["brand"],
        {
            "brand_name": "Mine",
            "partner_id": str(other["partner"].id),
            "updated_by": str(uuid.uuid4()),
        },
    )
    assert PartnerBrand.objects.filter(partner=wl["partner"]).exists()
    assert not PartnerBrand.objects.filter(partner=other["partner"]).exists()


def test_a_staff_brand_write_needs_manage_and_a_second_factor(wl, staff_client):
    view_only, _u = staff_client("platform.staff_partner.view")
    assert (
        put(view_only, urls(wl["partner"])["brand"], {"brand_name": "X"}).status_code
        == 403
    )
    weak, _u2 = staff_client("platform.staff_partner.manage", aal2=False)
    response = put(weak, urls(wl["partner"])["brand"], {"brand_name": "X"})
    assert response.status_code == 401 and response.json()["code"] == "step_up_required"
    assert not PartnerBrand.objects.exists()


def test_staff_brand_validation_is_the_partner_commands_validation(wl, manager):
    for body in (
        {"brand_name": "Acme", "accent_color": "red"},
        {"brand_name": "Acme", "theme": {"background-image": "#ffffff"}},
        {"brand_name": "Acme", "support_url": "javascript:alert(1)"},
        {"brand_name": "Acme", "support_url": "http://insecure.example.com"},
        {"brand_name": "line\nbreak"},
        {"brand_name": ""},
    ):
        assert put(manager, urls(wl["partner"])["brand"], body).status_code == 400, body
    assert not PartnerBrand.objects.exists()


def test_a_staff_edit_carries_the_version_token(wl, manager):
    first = put(manager, urls(wl["partner"])["brand"], {"brand_name": "One"}).json()
    ok = put(
        manager,
        urls(wl["partner"])["brand"],
        {"brand_name": "Two", "expected_version": first["version"]},
    )
    assert ok.status_code == 200 and ok.json()["version"] == 1
    stale = put(
        manager,
        urls(wl["partner"])["brand"],
        {"brand_name": "Three", "expected_version": first["version"]},
    )
    assert stale.status_code == 409 and stale.json()["code"] == "concurrent_modification"


def test_a_terminated_or_unknown_partner_takes_no_brand(wl, manager):
    services.set_partner_status(
        wl["staff_ctx"],
        wl["partner"].id,
        target=PartnerStatus.TERMINATED,
        reason="Terminated for the brand test",
    )
    refused = put(manager, urls(wl["partner"])["brand"], {"brand_name": "X"})
    assert refused.status_code == 403
    assert refused.json()["code"] == "partner_not_active"
    assert (
        put(
            manager, f"/api/v1/admin/partners/{uuid.uuid4()}/brand", {"brand_name": "X"}
        ).status_code
        == 404
    )


def test_a_partner_user_cannot_use_the_staff_brand_endpoint(wl, http_login):
    wl["user"].set_password(PASSWORD)
    wl["user"].save(update_fields=["password"])
    client = http_login(wl["user"])
    assert (
        put(client, urls(wl["partner"])["brand"], {"brand_name": "X"}).status_code == 403
    )


# --- the logo -----------------------------------------------------------------------------------------


def test_staff_upload_a_png_or_jpeg_logo_and_attach_it(wl, manager):
    upload = manager.post(urls(wl["partner"])["logo"], PNG, content_type="image/png")
    assert upload.status_code == 201, upload.content
    file_id = upload.json()["logo_file_id"]
    assert PartnerLogoUpload.objects.filter(
        file_id=file_id, partner=wl["partner"]
    ).exists()
    saved = put(
        manager,
        urls(wl["partner"])["brand"],
        {"brand_name": "Acme", "logo_file_id": file_id},
    )
    assert saved.status_code == 200 and saved.json()["logo_file_id"] == file_id
    assert (
        manager.post(
            urls(wl["partner"])["logo"], JPEG, content_type="image/jpeg"
        ).status_code
        == 201
    )


def test_a_logo_is_png_or_jpeg_within_the_size_and_matching_its_bytes(wl, manager):
    url = urls(wl["partner"])["logo"]
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    for payload, kind in (
        (svg, "image/svg+xml"),
        (b"", "image/png"),
        (PNG + b"0" * (600 * 1024), "image/png"),
        (
            b"<html><script>alert(1)</script></html>",
            "image/png",
        ),  # declared PNG, HTML inside
        (PNG, "text/html"),
        (PNG, "image/gif"),
    ):
        response = manager.post(url, payload, content_type=kind)
        assert response.status_code in (400, 415), (kind, response.status_code)
    assert not PartnerLogoUpload.objects.exists()


def test_a_logo_uploaded_for_one_partner_cannot_be_attached_to_another(
    wl, partner_factory, manager
):
    other = partner_factory("branding.manage")
    file_id = manager.post(
        urls(wl["partner"])["logo"], PNG, content_type="image/png"
    ).json()["logo_file_id"]
    stolen = put(
        manager,
        urls(other["partner"])["brand"],
        {"brand_name": "Other", "logo_file_id": file_id},
    )
    assert stolen.status_code == 400
    assert not PartnerBrand.objects.filter(partner=other["partner"]).exists()


def test_the_logo_preview_is_the_partners_own_and_served_isolated(
    wl, partner_factory, manager
):
    url = urls(wl["partner"])["logo_view"]
    assert manager.get(url).status_code == 404  # no brand yet
    file_id = manager.post(
        urls(wl["partner"])["logo"], PNG, content_type="image/png"
    ).json()["logo_file_id"]
    put(
        manager,
        urls(wl["partner"])["brand"],
        {"brand_name": "Acme", "logo_file_id": file_id},
    )
    response = manager.get(url)
    assert response.status_code == 200 and response["Content-Type"] == "image/png"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in response["Content-Security-Policy"]
    assert response.content == PNG
    # There is no file-id parameter: another partner's preview is its own logo or nothing.
    other = partner_factory("branding.manage")
    assert manager.get(urls(other["partner"])["logo_view"]).status_code == 404


# --- staff prepare the domain --------------------------------------------------------------------------


def test_staff_request_a_domain_and_get_the_dns_instruction(wl, manager):
    response = post(manager, urls(wl["partner"])["domain"], {"hostname": HOSTNAME})
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["hostname"] == HOSTNAME and body["status"] == "requested"
    record = body["verification_record"]
    assert record["type"] == "TXT" and record["name"] == f"_w360-verify.{HOSTNAME}"
    assert record["value"].startswith("w360-verify=")
    again = post(manager, urls(wl["partner"])["domain"], {"hostname": HOSTNAME.upper()})
    assert again.json()["id"] == body["id"]  # idempotent per partner and name
    assert (
        PartnerDomain.objects.filter(partner=wl["partner"], hostname=HOSTNAME).count()
        == 1
    )


def test_only_a_public_bare_hostname_can_be_requested(wl, manager):
    for bad in (
        "http://x.example.com",
        "x.example.com/path",
        "localhost",
        "127.0.0.1",
        "a.local",
        "*.example.com",
        "",
        "user@x.example.com",
    ):
        assert (
            post(manager, urls(wl["partner"])["domain"], {"hostname": bad}).status_code
            == 400
        ), bad


def test_verifying_needs_the_proof_in_dns_and_activating_needs_verification(
    wl, manager, dns
):
    domain_id = post(
        manager, urls(wl["partner"])["domain"], {"hostname": HOSTNAME}
    ).json()["id"]
    verify = f"/api/v1/admin/domains/{domain_id}/verify"
    activate = f"/api/v1/admin/domains/{domain_id}/activate"

    # Nothing published: pending, and never active.
    assert post(manager, verify).json()["status"] == "verification_pending"
    put(manager, urls(wl["partner"])["brand"], {"brand_name": "Acme"})
    early = post(manager, activate)
    assert (
        early.status_code in (400, 409)
        and PartnerDomain.objects.get(id=domain_id).status == "verification_pending"
    )

    token = PartnerDomain.objects.get(id=domain_id).verification_token
    dns.records[f"_w360-verify.{HOSTNAME}"] = [token]
    assert post(manager, verify).json()["status"] == "verified"
    # Verified is not active: activation is its own, deliberate step.
    assert PartnerDomain.objects.get(id=domain_id).status == "verified"
    assert post(manager, activate).json()["status"] == "active"


def test_activation_needs_a_brand_first(wl, manager, dns):
    domain_id = post(
        manager, urls(wl["partner"])["domain"], {"hostname": HOSTNAME}
    ).json()["id"]
    dns.records[f"_w360-verify.{HOSTNAME}"] = [
        PartnerDomain.objects.get(id=domain_id).verification_token
    ]
    post(manager, f"/api/v1/admin/domains/{domain_id}/verify")
    response = post(manager, f"/api/v1/admin/domains/{domain_id}/activate")
    assert response.status_code == 400
    assert PartnerDomain.objects.get(id=domain_id).status == "verified"


def test_every_staff_domain_action_needs_a_second_factor(wl, staff_client, manager, dns):
    domain_id = post(
        manager, urls(wl["partner"])["domain"], {"hostname": HOSTNAME}
    ).json()["id"]
    weak, _u = staff_client("platform.staff_partner.manage", aal2=False)
    for url, body in (
        (urls(wl["partner"])["domain"], {"hostname": "other.wl-example.com"}),
        (f"/api/v1/admin/domains/{domain_id}/verify", {}),
        (f"/api/v1/admin/domains/{domain_id}/activate", {}),
        (f"/api/v1/admin/domains/{domain_id}/disable", {}),
    ):
        response = post(weak, url, body)
        assert (
            response.status_code == 401 and response.json()["code"] == "step_up_required"
        ), url
    view_only, _u2 = staff_client("platform.staff_partner.view")
    assert post(view_only, f"/api/v1/admin/domains/{domain_id}/verify").status_code == 403


def test_another_partner_cannot_take_a_hostname_a_partner_has_proved(
    wl, partner_factory, manager, dns
):
    first = post(manager, urls(wl["partner"])["domain"], {"hostname": HOSTNAME}).json()[
        "id"
    ]
    dns.records[f"_w360-verify.{HOSTNAME}"] = [
        PartnerDomain.objects.get(id=first).verification_token
    ]
    assert (
        post(manager, f"/api/v1/admin/domains/{first}/verify").json()["status"]
        == "verified"
    )

    rival = partner_factory("domain.manage")
    second = post(
        manager, urls(rival["partner"])["domain"], {"hostname": HOSTNAME}
    ).json()["id"]
    dns.records[f"_w360-verify.{HOSTNAME}"] = [
        PartnerDomain.objects.get(id=first).verification_token,
        PartnerDomain.objects.get(id=second).verification_token,
    ]
    result = post(manager, f"/api/v1/admin/domains/{second}/verify").json()
    assert result["status"] == "failed" and result["failure_code"] == "hostname_claimed"
    assert PartnerDomain.objects.get(id=first).status == "verified"


def test_terminating_a_partner_takes_its_domains_off_air(wl, manager, dns):
    domain_id = post(
        manager, urls(wl["partner"])["domain"], {"hostname": HOSTNAME}
    ).json()["id"]
    services.set_partner_status(
        wl["staff_ctx"],
        wl["partner"].id,
        target=PartnerStatus.TERMINATED,
        reason="Terminated for the domain test",
    )
    assert PartnerDomain.objects.get(id=domain_id).status == "disabled"
    assert (
        post(
            manager, urls(wl["partner"])["domain"], {"hostname": "new.wl-example.com"}
        ).status_code
        == 403
    )


def test_a_domain_is_served_only_once_active_and_only_as_presentation(
    wl, manager, dns, client
):
    put(
        manager,
        urls(wl["partner"])["brand"],
        {"brand_name": "Acme", "accent_color": "#1a56db"},
    )
    domain_id = post(
        manager, urls(wl["partner"])["domain"], {"hostname": HOSTNAME}
    ).json()["id"]
    lookup = lambda: client.get("/api/v1/public/branding", {"host": HOSTNAME}).json()  # noqa: E731
    assert lookup() == {"brand": None}
    dns.records[f"_w360-verify.{HOSTNAME}"] = [
        PartnerDomain.objects.get(id=domain_id).verification_token
    ]
    post(manager, f"/api/v1/admin/domains/{domain_id}/verify")
    assert lookup() == {"brand": None}  # verified is not serving
    post(manager, f"/api/v1/admin/domains/{domain_id}/activate")
    brand = lookup()["brand"]
    assert brand["brand_name"] == "Acme" and brand["accent_color"] == "#1a56db"
    text = json.dumps(brand)
    assert (
        str(wl["partner"].id) not in text and "capabilit" not in text
    )  # no authority in it


# --- the AAL2 asymmetry, from the partner's own portal ---------------------------------------------------------------


def test_a_partner_saves_a_brand_without_a_second_factor_but_a_domain_needs_one(
    partner_client,
):
    client, fx = partner_client("branding.manage", "domain.manage", aal2=False)
    base = f"/api/v1/partner/{fx['partner'].id}"
    brand = put(
        client, f"{base}/branding", {"brand_name": "Acme", "accent_color": "#1a56db"}
    )
    assert brand.status_code == 200, brand.content
    domain = post(client, f"{base}/domains", {"hostname": HOSTNAME})
    assert domain.status_code == 401 and domain.json()["code"] == "step_up_required"
    assert not PartnerDomain.objects.exists()


def test_a_partner_with_a_second_factor_does_both_separately(partner_client):
    client, fx = partner_client("branding.manage", "domain.manage")
    base = f"/api/v1/partner/{fx['partner'].id}"
    assert put(client, f"{base}/branding", {"brand_name": "Acme"}).status_code == 200
    assert post(client, f"{base}/domains", {"hostname": HOSTNAME}).status_code == 201


def test_the_partner_portal_still_cannot_reach_another_partners_domain(
    partner_client, partner_factory, manager, dns
):
    other = partner_factory("domain.manage")
    theirs = post(
        manager, urls(other["partner"])["domain"], {"hostname": HOSTNAME}
    ).json()["id"]
    client, fx = partner_client("domain.manage")
    base = f"/api/v1/partner/{fx['partner'].id}"
    for action in ("verify", "activate", "disable"):
        assert post(client, f"{base}/domains/{theirs}/{action}").status_code == 404


def test_a_partner_cannot_pass_another_partner_to_the_shared_commands(
    wl, partner_factory
):
    """The portal never takes a partner id from the body, and the command refuses
    an explicit one that is not the caller's - the same 404 as any other record."""
    from workeasy360.contracts.errors import PartnerRecordNotFound

    other = partner_factory("branding.manage", "domain.manage")
    actor = wl["actor"]()
    with pytest.raises(PartnerRecordNotFound):
        branding.update_brand(actor, brand_name="X", partner_id=other["partner"].id)
    with pytest.raises(PartnerRecordNotFound):
        domains.request_domain(actor, hostname=HOSTNAME, partner_id=other["partner"].id)
    assert not PartnerBrand.objects.exists() and not PartnerDomain.objects.exists()


def test_a_staff_context_must_name_the_partner(wl, staff_factory):
    from workeasy360.contracts.errors import PartnerValidationError

    _u, ctx = staff_factory("platform.staff_partner.manage")
    with pytest.raises(PartnerValidationError):
        branding.update_brand(ctx, brand_name="X")
    with pytest.raises(PartnerValidationError):
        domains.request_domain(ctx, hostname=HOSTNAME)

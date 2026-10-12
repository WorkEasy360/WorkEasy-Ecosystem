"""The white-label wizard's one read, and enabling white-label.

White-label is a label two capabilities earn, so enabling it is two ordinary
grants made together on the SAME partner: never a new account, never a type.
"""

from __future__ import annotations

import pytest

from tests.partner_setup.conftest import MANAGE, VIEW, post
from workeasy360.commercial.partners import selectors, services
from workeasy360.commercial.partners.models import PartnerAccount, PartnerCapabilityGrant
from workeasy360.contracts.errors import (
    PartnerValidationError,
    PermissionDenied,
    StepUpRequired,
)

pytestmark = pytest.mark.django_db

REASON = "Enabled through the white-label wizard"


def setup_url(partner_id) -> str:
    return f"/api/v1/admin/partners/{partner_id}/white-label"


def enable_url(partner_id) -> str:
    return f"{setup_url(partner_id)}/enable"


def read(client, partner_id):
    response = client.get(setup_url(partner_id))
    assert response.status_code == 200, response.content
    return response.json()


# --- the read -------------------------------------------------------------------------------------------


def test_a_reseller_that_is_not_white_label_says_what_it_lacks(manager, channel):
    body = read(manager, channel["partner"].id)
    assert [e["key"] for e in body["experience"]] == ["reseller"]
    assert body["enabled"] is False
    assert body["missing_capabilities"] == ["branding.manage", "domain.manage"]
    assert body["brand"] is None and body["domain"] is None
    assert body["branding_on_custom_domain"] == "none"
    assert body["checks"] == {
        "brand_saved": False,
        "logo_saved": False,
        "domain_requested": False,
    }


def test_every_catalog_product_is_a_row_with_the_four_facts(manager, channel):
    body = read(manager, channel["partner"].id)
    crm = next(p for p in body["products"] if p["product_code"] == "crm")
    assert crm["available_to_partner"] is True and crm["grant_id"]
    assert crm["sell_allowed"] is True  # it holds subscription.sell and the grant
    assert crm["visible_in_portal"] is True
    assert crm["trial_available"] is False  # the platform has configured no trial
    others = [p for p in body["products"] if p["product_code"] != "crm"]
    assert others, "the catalog has more than one product"
    assert all(
        not p["available_to_partner"] and not p["sell_allowed"] and p["grant_id"] is None
        for p in others
    )


def test_a_product_added_to_the_catalog_later_appears_with_no_change(manager, channel):
    from workeasy360.commercial.catalog.models import Product

    before = {p["product_code"] for p in read(manager, channel["partner"].id)["products"]}
    Product.objects.create(
        code="newprod", name="New Product", status="available", slug="newprod"
    )
    after = {p["product_code"] for p in read(manager, channel["partner"].id)["products"]}
    assert after - before == {"newprod"}


def test_drafts_and_retired_products_are_not_offered_unless_already_granted(
    manager, channel
):
    from workeasy360.commercial.catalog.models import Product

    Product.objects.create(code="hidden", name="Hidden", status="draft", slug="hidden")
    codes = {p["product_code"] for p in read(manager, channel["partner"].id)["products"]}
    assert "hidden" not in codes


def test_trial_available_needs_both_the_platform_and_the_partner(
    manager, channel, catalog
):
    from tests.phase11.conftest import configure_trial

    configure_trial(catalog)
    pid = channel["partner"].id

    def crm():
        return next(
            p for p in read(manager, pid)["products"] if p["product_code"] == "crm"
        )

    row = crm()
    assert row["platform_trial_enabled"] is True and row["trial_available"] is True
    grant = PartnerCapabilityGrant  # noqa: F841 - documents that capabilities are not involved
    from workeasy360.commercial.partners.models import PartnerProductGrant

    services.update_product_grant(
        channel["staff_ctx"],
        pid,
        grant_id=PartnerProductGrant.objects.get(partner_id=pid, status="active").id,
        trial_allowed=False,
    )
    row = crm()
    assert row["partner_allows_trial"] is False and row["trial_available"] is False
    assert row["platform_trial_enabled"] is True


def test_visibility_never_implies_a_right_to_sell_or_an_entitlement(
    manager, partner_factory, catalog
):
    fx = partner_factory("branding.manage", "domain.manage")  # no selling capability
    services.grant_product(fx["staff_ctx"], fx["partner"].id, product_code="crm")
    row = next(
        p
        for p in read(manager, fx["partner"].id)["products"]
        if p["product_code"] == "crm"
    )
    assert row["available_to_partner"] is True and row["visible_in_portal"] is True
    assert row["sell_allowed"] is False


def test_the_authentication_statement_is_the_servers_and_is_honest(manager, channel):
    body = read(manager, channel["partner"].id)
    assert body["authentication"]["mode"] == "workeasy360_secure_sign_in"
    assert body["authentication"]["custom_domain_sign_in"] is False
    assert (
        "no password is ever typed on a partner's domain"
        in body["authentication"]["summary"]
    )


def test_the_custom_domain_state_follows_the_domain(manager, partner_factory):
    from tests.phase10.test_10d_white_label import FakeDns
    from workeasy360.commercial.partners import branding, domains

    fx = partner_factory("branding.manage", "domain.manage")
    ctx, pid = fx["staff_ctx"], fx["partner"].id
    branding.update_brand(ctx, partner_id=pid, brand_name="Acme")
    row = domains.request_domain(ctx, hostname="app.state-example.com", partner_id=pid)
    body = read(manager, pid)
    assert (
        body["domain"]["status"] == "requested"
        and body["branding_on_custom_domain"] == "pending"
    )
    assert body["domain"]["verification_record"]["type"] == "TXT"
    fake = FakeDns(
        {domains.verification_record_name(row.hostname): [row.verification_token]}
    )
    previous = domains.set_txt_resolver(fake)
    try:
        domains.verify_domain(ctx, domain_id=row.id)
        # Verification proves the name. It does NOT make it serve anything.
        body = read(manager, pid)
        assert body["domain"]["status"] == "verified"
        assert body["branding_on_custom_domain"] == "verified_not_active"
        assert selectors.active_brand_for_host("app.state-example.com") is None
        domains.activate_domain(ctx, domain_id=row.id)
    finally:
        domains.set_txt_resolver(previous)
    body = read(manager, pid)
    assert body["branding_on_custom_domain"] == "active"
    assert (
        body["checks"]["domain_requested"] is True
        and body["checks"]["brand_saved"] is True
    )


def test_the_read_costs_a_fixed_number_of_queries(manager, channel, partner_factory):
    from tests.partner_setup.conftest import count_queries

    pid = channel["partner"].id
    small, _ = count_queries(lambda: manager.get(setup_url(pid)))
    from workeasy360.commercial.catalog.models import Product

    for i in range(6):
        Product.objects.create(
            code=f"extra{i}", name=f"Extra {i}", status="available", slug=f"extra{i}"
        )
    large, _ = count_queries(lambda: manager.get(setup_url(pid)))
    assert large == small, (small, large)


# --- enabling -----------------------------------------------------------------------------------------------


def test_enabling_white_label_gives_the_same_partner_the_pair(manager, channel):
    pid = channel["partner"].id
    accounts = PartnerAccount.objects.count()
    response = post(manager, enable_url(pid), {"reason": REASON}, key=False)
    assert response.status_code == 201, response.content
    assert response.json() == {"granted": ["branding.manage", "domain.manage"]}
    assert PartnerAccount.objects.count() == accounts  # no second partner, ever
    body = read(manager, pid)
    assert body["enabled"] is True and body["missing_capabilities"] == []
    assert [e["key"] for e in body["experience"]] == ["reseller", "white_label"]
    assert body["partner"]["id"] == str(pid)


def test_enabling_is_idempotent_and_audited_once(manager, channel):
    from tests.phase11.conftest import audit_of

    pid = channel["partner"].id
    post(manager, enable_url(pid), {"reason": REASON}, key=False)
    again = post(manager, enable_url(pid), {"reason": REASON}, key=False)
    assert again.status_code == 200 and again.json() == {"granted": []}
    entries = [
        r
        for r in audit_of(pid, "action", "changes")
        if r["action"] == "partner.white_label_enabled"
    ]
    assert len(entries) == 1
    assert entries[0]["changes"]["capabilities"] == ["branding.manage", "domain.manage"]
    held = PartnerCapabilityGrant.objects.filter(
        partner_id=pid,
        status="active",
        capability__in=["branding.manage", "domain.manage"],
    )
    assert held.count() == 2


def test_a_partner_holding_one_of_the_two_gets_only_the_other(manager, partner_factory):
    fx = partner_factory("branding.manage")
    response = post(manager, enable_url(fx["partner"].id), {"reason": REASON}, key=False)
    assert response.json() == {"granted": ["domain.manage"]}


def test_enabling_later_keeps_every_other_capability(manager, channel):
    pid = channel["partner"].id
    before = set(selectors.active_capabilities(pid))
    post(manager, enable_url(pid), {"reason": REASON}, key=False)
    assert before <= set(selectors.active_capabilities(pid))


def test_a_combined_partner_is_one_row_with_all_three_labels(manager, channel):
    pid = channel["partner"].id
    post(manager, enable_url(pid), {"reason": REASON}, key=False)
    services.grant_capability(
        channel["staff_ctx"], pid, capability="referral.create", reason="Affiliate too"
    )
    labels = [e["key"] for e in read(manager, pid)["experience"]]
    assert labels == ["reseller", "white_label", "affiliate"]
    rows = manager.get("/api/v1/admin/partners/resellers", {"scope": "all"}).json()[
        "results"
    ]
    assert [r["id"] for r in rows].count(str(pid)) == 1


def test_a_reason_is_required_and_a_terminated_partner_cannot_be_enabled(channel):
    from workeasy360.contracts.enums import PartnerStatus

    ctx, pid = channel["staff_ctx"], channel["partner"].id
    with pytest.raises(PartnerValidationError):
        services.enable_white_label(ctx, pid, reason="short")
    services.set_partner_status(
        ctx, pid, target=PartnerStatus.TERMINATED, reason="Ended for the test"
    )
    with pytest.raises(PartnerValidationError):
        services.enable_white_label(ctx, pid, reason=REASON)


def test_enabling_needs_manage_a_second_factor_and_is_never_a_partners_own_act(
    staff_client, partner_client, channel, staff_factory
):
    pid = channel["partner"].id
    viewer, _u = staff_client(VIEW)
    assert post(viewer, enable_url(pid), {"reason": REASON}, key=False).status_code == 403
    weak, _w = staff_client(MANAGE, aal2=False)
    stepped = post(weak, enable_url(pid), {"reason": REASON}, key=False)
    assert stepped.status_code == 401 and stepped.json()["code"] == "step_up_required"
    own, fx = partner_client("customer.provision")
    assert (
        post(own, enable_url(fx["partner"].id), {"reason": REASON}, key=False).status_code
        == 403
    )
    assert read_forbidden(own, fx["partner"].id)
    _v, plain = staff_factory(VIEW)
    with pytest.raises(PermissionDenied):
        services.enable_white_label(plain, pid, reason=REASON)
    _m, lapsed = staff_factory(MANAGE, aal=1)
    with pytest.raises(StepUpRequired):
        services.enable_white_label(lapsed, pid, reason=REASON)
    assert set(selectors.active_capabilities(pid)).isdisjoint(
        {"branding.manage", "domain.manage"}
    )


def read_forbidden(client, partner_id) -> bool:
    return client.get(setup_url(partner_id)).status_code == 403


def test_the_read_needs_the_view_permission(staff_client, channel):
    client, _u = staff_client("platform.staff_audit.view")
    assert client.get(setup_url(channel["partner"].id)).status_code == 403
    import uuid

    viewer, _v = staff_client(VIEW)
    assert viewer.get(setup_url(uuid.uuid4())).status_code == 404

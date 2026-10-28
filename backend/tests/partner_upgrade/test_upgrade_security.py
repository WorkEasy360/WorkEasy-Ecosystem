"""A partner cannot upgrade itself (ADR-049, brief section 13).

The only thing a partner can do is *ask*. Every route below tries to get a
Reseller or White-label power without staff approval - through the capability
and experience APIs, through a forged request body, through another partner's
request, through a limited role - and each must leave the partner's grants
exactly as they were. The server decides; nothing a browser sends (a URL, a
stored "partner_type") is read by any of it.
"""

from __future__ import annotations

import json

import pytest
from django.db import IntegrityError, transaction

from tests.partner_center.conftest import PASSWORD
from tests.partner_upgrade.conftest import (
    admin_path,
    decide,
    fill,
    make_affiliate,
    pp,
    put,
    request_upgrade,
    save,
    send,
    start,
    submit,
)
from workeasy360.commercial.partners import capabilities as caps
from workeasy360.commercial.partners.models import (
    PartnerAccount,
    PartnerCapabilityGrant,
    PartnerMember,
    PartnerProductGrant,
    PartnerUpgradeRequest,
)

pytestmark = pytest.mark.django_db

AFFILIATE_CAPS = set(caps.EXPERIENCE_BUNDLES["affiliate"])
EMAIL = "owner@af-up.example.com"


def _held(partner_id: str) -> set[str]:
    return set(
        PartnerCapabilityGrant.objects.filter(
            partner_id=partner_id, status="active"
        ).values_list("capability", flat=True)
    )


# --- no route lets a partner grant itself anything ----------------------------------------------


def test_an_affiliate_cannot_reach_the_staff_grant_routes(catalog, admin):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    request = request_upgrade(client, partner_id, "reseller")
    for url, body in (
        (
            admin_path(partner_id, "/experience"),
            {"action": "grant", "experience": "reseller"},
        ),
        (
            admin_path(partner_id, "/experience"),
            {"action": "default", "experience": "reseller"},
        ),
        (
            admin_path(partner_id, "/capabilities"),
            {"capability": "customer.provision", "reason": "Let me in please"},
        ),
        (admin_path(partner_id, "/product-grants"), {"product_code": "crm"}),
        (
            admin_path(partner_id, f"/upgrades/{request['id']}/decision"),
            {"action": "approve"},
        ),
    ):
        response = send(client, url, body)
        assert response.status_code in (401, 403), (url, response.status_code)
    assert _held(partner_id) == AFFILIATE_CAPS
    assert not PartnerProductGrant.objects.filter(partner_id=partner_id).exists()
    assert (
        PartnerUpgradeRequest.objects.get(id=request["id"]).status == "pending_approval"
    )


def test_submitting_a_request_changes_no_grant(catalog, admin):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    before = _held(partner_id)
    request_upgrade(client, partner_id, "white_label")
    assert _held(partner_id) == before
    state = client.get(pp(partner_id, "/upgrade")).json()
    assert state["current"] == "affiliate"
    # The partner still lands in, and is authorized for, the affiliate workspace only.
    entry = client.get("/api/v1/partner/portal").json()["partners"][0]
    assert [w["key"] for w in entry["workspaces"]] == ["affiliate"]
    assert client.get(pp(partner_id, "/customers")).status_code == 403
    # The white-label status read answers every member, but only with the halves
    # its permissions allow: an affiliate's are both withheld, not zero.
    status = client.get(pp(partner_id, "/white-label/status"))
    assert status.status_code == 200 and status.json() == {
        "brand": None,
        "domain": None,
        "storefront": None,
    }


def test_a_staff_member_without_the_partner_manage_right_cannot_decide(
    catalog, admin, staff_client
):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    request = request_upgrade(client, partner_id, "reseller")
    viewer, _user = staff_client("platform.staff_partner.view")
    response = send(
        viewer,
        admin_path(partner_id, f"/upgrades/{request['id']}/decision"),
        {"action": "approve"},
    )
    assert response.status_code == 403
    assert viewer.get(admin_path(partner_id, "/upgrades")).status_code == 200
    assert _held(partner_id) == AFFILIATE_CAPS


def test_the_request_body_cannot_smuggle_a_capability_or_a_section(catalog, admin):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    started = start(client, partner_id, "reseller").json()
    for section, data in (
        ("capabilities", {"capabilities": ["customer.provision"]}),
        ("status", {"status": "approved"}),
        ("brand", {"brand_name": "Nope"}),  # not a section of a Reseller upgrade
        ("pricing", {"preference": "standard", "capabilities": ["pricing.view"]}),
        ("pricing", {"preference": "free-for-all"}),
        ("commercial", {"expected_customers": -5}),
        ("commercial", {"expected_customers": True}),
        ("products", {"products": ["no-such-product"]}),
        ("products", {"products": "crm"}),
    ):
        response = save(client, partner_id, started, section, data)
        assert response.status_code == 400, (section, data, response.content)
    assert PartnerUpgradeRequest.objects.get(id=started["id"]).proposal == {}
    assert _held(partner_id) == AFFILIATE_CAPS


def test_a_white_label_only_section_is_refused_for_a_reseller_upgrade(catalog, admin):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    started = start(client, partner_id, "reseller").json()
    assert (
        save(
            client, partner_id, started, "domain", {"hostname": "x.example.org"}
        ).status_code
        == 400
    )


def test_an_affiliate_cannot_ask_for_what_it_already_is_or_what_does_not_exist(
    catalog, admin
):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    for target in ("affiliate", "admin", "", None, 7):
        assert (
            send(
                client, pp(partner_id, "/upgrade/requests"), {"experience": target}
            ).status_code
            == 400
        )
    assert not PartnerUpgradeRequest.objects.filter(partner_id=partner_id).exists()


# --- whose request is it ------------------------------------------------------------------------------


def test_another_partners_request_is_a_404_not_a_403(catalog, admin):
    one, one_id = make_affiliate(admin, email=EMAIL, code="af-one")
    two, two_id = make_affiliate(admin, email="owner@af-two.example.com", code="af-two")
    mine = start(one, one_id, "reseller").json()
    for method_url in (
        ("put", pp(two_id, f"/upgrade/requests/{mine['id']}/sections/pricing")),
        ("post", pp(two_id, f"/upgrade/requests/{mine['id']}/submit")),
        ("post", pp(two_id, f"/upgrade/requests/{mine['id']}/cancel")),
    ):
        method, url = method_url
        response = (
            put(two, url, {"data": {}, "expected_version": 0})
            if method == "put"
            else send(two, url, {"expected_version": 0})
        )
        assert response.status_code == 404, (url, response.status_code)
    # Nor can it reach the first partner's state through its own id in the path.
    assert two.get(pp(one_id, "/upgrade")).status_code == 404
    assert PartnerUpgradeRequest.objects.get(id=mine["id"]).status == "requested"


def test_staff_deciding_with_the_wrong_partner_in_the_path_is_a_404(catalog, admin):
    one, one_id = make_affiliate(admin, email=EMAIL, code="af-one")
    _two, two_id = make_affiliate(admin, email="owner@af-two.example.com", code="af-two")
    request = request_upgrade(one, one_id, "reseller")
    response = decide(admin, two_id, request["id"], "approve")
    assert response.status_code == 404
    assert _held(one_id) == AFFILIATE_CAPS and _held(two_id) == AFFILIATE_CAPS


def test_a_member_without_the_profile_manage_right_cannot_open_or_edit_a_request(
    catalog, admin, user_factory, http_login
):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    started = start(client, partner_id, "reseller").json()
    viewer_user = user_factory(password=PASSWORD)
    from django.utils import timezone

    PartnerMember.objects.create(
        partner_id=partner_id,
        user=viewer_user,
        role="viewer",
        status="active",
        added_by_id=viewer_user.id,
        added_at=timezone.now(),
    )
    viewer = http_login(viewer_user)
    state = viewer.get(pp(partner_id, "/upgrade")).json()
    # A viewer may see where the partner stands, not act on it.
    assert state["can_request"] is False
    assert (
        send(
            viewer, pp(partner_id, "/upgrade/requests"), {"experience": "white_label"}
        ).status_code
        == 403
    )
    assert (
        save(
            viewer, partner_id, started, "pricing", {"preference": "standard"}
        ).status_code
        == 403
    )
    assert submit(viewer, partner_id, started).status_code == 403
    assert (
        send(
            viewer, pp(partner_id, f"/upgrade/requests/{started['id']}/cancel")
        ).status_code
        == 403
    )


def test_an_inactive_partner_cannot_open_an_upgrade(catalog, admin):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    suspended = send(
        admin,
        admin_path(partner_id, "/status"),
        {
            "status": "suspended",
            "reason": "Suspended while the upgrade is checked",
            "expected_version": admin.get(admin_path(partner_id)).json()["version"],
        },
    )
    assert suspended.status_code == 200, suspended.content
    assert start(client, partner_id, "reseller").status_code == 403
    assert not PartnerUpgradeRequest.objects.filter(partner_id=partner_id).exists()


# --- one open request, in the right order ------------------------------------------------------------


def test_one_open_request_per_partner_even_when_asked_twice(catalog, admin):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    first = start(client, partner_id, "reseller")
    again = start(client, partner_id, "reseller")
    assert first.status_code == 201 and again.status_code == 201
    assert first.json()["id"] == again.json()["id"]
    other = start(client, partner_id, "white_label")
    assert other.status_code == 409
    assert PartnerUpgradeRequest.objects.filter(partner_id=partner_id).count() == 1


def test_the_database_itself_refuses_a_second_open_request(catalog, admin):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    first = start(client, partner_id, "reseller").json()
    row = PartnerUpgradeRequest.objects.get(id=first["id"])
    with pytest.raises(IntegrityError), transaction.atomic():
        PartnerUpgradeRequest.objects.create(
            partner_id=partner_id,
            from_experience="affiliate",
            to_experience="white_label",
            status="requested",
            requested_by_id=row.requested_by_id,
            requested_at=row.requested_at,
        )
    for bad in (
        {"to_experience": "affiliate"},
        {"status": "weird"},
        {"status": "approved"},
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            PartnerUpgradeRequest.objects.create(
                partner_id=partner_id,
                from_experience="affiliate",
                requested_by_id=row.requested_by_id,
                requested_at=row.requested_at,
                **{"to_experience": "reseller", "status": "cancelled", **bad},
            )


def test_a_stale_or_missing_version_is_refused(catalog, admin):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    started = start(client, partner_id, "reseller").json()
    first = save(client, partner_id, started, "pricing", {"preference": "standard"})
    assert first.status_code == 200
    # The same version again: someone else (or another tab) already saved.
    stale = save(client, partner_id, started, "pricing", {"preference": "negotiated"})
    assert stale.status_code == 409
    missing = put(
        client,
        pp(partner_id, f"/upgrade/requests/{started['id']}/sections/pricing"),
        {"data": {"preference": "standard"}},
    )
    assert missing.status_code == 400


def test_a_submitted_request_is_frozen_and_cannot_be_submitted_twice(catalog, admin):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    request = request_upgrade(client, partner_id, "reseller")
    assert (
        save(
            client, partner_id, request, "pricing", {"preference": "standard"}
        ).status_code
        == 409
    )
    assert submit(client, partner_id, request).status_code == 409


def test_submit_names_exactly_what_is_missing(catalog, admin):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    started = start(client, partner_id, "white_label").json()
    refused = submit(client, partner_id, started)
    assert refused.status_code == 400
    body = refused.json()
    assert set(body["missing"]) >= {"business", "products", "brand"}
    # Once it is all filled in, the same call goes through.
    filled = fill(client, partner_id, started, "white_label")
    assert submit(client, partner_id, filled).status_code == 200


# --- the approval is all-or-nothing ----------------------------------------------------------------------


def test_approval_needs_a_complete_profile_and_changes_nothing_when_it_is_not(
    catalog, admin
):
    from workeasy360.commercial.partners.models import PartnerProfile

    client, partner_id = make_affiliate(admin, email=EMAIL)
    request = request_upgrade(client, partner_id, "reseller")
    # The business email is withdrawn between the submission and the decision.
    PartnerProfile.objects.filter(partner_id=partner_id).update(business_email="")
    refused = decide(admin, partner_id, request["id"], "approve")
    assert refused.status_code == 400, refused.content
    assert "business" in refused.json()["missing"]
    # Nothing was granted, no product was given, and the request is still waiting.
    assert _held(partner_id) == AFFILIATE_CAPS
    assert not PartnerProductGrant.objects.filter(partner_id=partner_id).exists()
    assert (
        PartnerUpgradeRequest.objects.get(id=request["id"]).status == "pending_approval"
    )
    assert PartnerAccount.objects.get(id=partner_id).default_experience == "affiliate"


def test_approval_without_a_product_is_refused_and_rolled_back(catalog, admin):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    request = request_upgrade(client, partner_id, "reseller")
    refused = decide(admin, partner_id, request["id"], "approve", products=[])
    assert refused.status_code == 400
    assert "products" in refused.json()["missing"]
    assert _held(partner_id) == AFFILIATE_CAPS


def test_a_proposed_domain_claims_nothing_until_dns_proves_it(catalog, admin):
    from workeasy360.commercial.partners.models import PartnerDomain

    one, one_id = make_affiliate(admin, email=EMAIL, code="af-one")
    two, two_id = make_affiliate(admin, email="owner@af-two.example.com", code="af-two")
    for client, partner_id in ((one, one_id), (two, two_id)):
        request = request_upgrade(client, partner_id, "white_label")
        assert decide(admin, partner_id, request["id"], "approve").status_code == 200
    # Both asked for the same hostname; approval only recorded the requests. No
    # domain is served, verified or active, so neither partner owns it yet.
    rows = PartnerDomain.objects.filter(hostname="app.acme-partner.example.org")
    assert rows.count() == 2
    assert set(rows.values_list("status", flat=True)) <= {
        "requested",
        "verification_pending",
    }
    assert not rows.filter(status__in=["verified", "active"]).exists()


def test_a_decided_request_cannot_be_decided_again(catalog, admin):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    request = request_upgrade(client, partner_id, "reseller")
    assert decide(admin, partner_id, request["id"], "approve").status_code == 200
    held = _held(partner_id)
    for action, extra in (
        ("approve", {}),
        ("reject", {"note": "Changed our mind"}),
        ("request_info", {"note": "More please"}),
    ):
        assert (
            decide(admin, partner_id, request["id"], action, **extra).status_code == 409
        ), action
    assert _held(partner_id) == held


def test_reject_and_request_info_need_a_note_the_partner_can_read(catalog, admin):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    request = request_upgrade(client, partner_id, "reseller")
    assert decide(admin, partner_id, request["id"], "reject").status_code == 400
    assert (
        decide(admin, partner_id, request["id"], "request_info", note="  ").status_code
        == 400
    )
    assert decide(admin, partner_id, request["id"], "launch").status_code == 400
    assert (
        PartnerUpgradeRequest.objects.get(id=request["id"]).status == "pending_approval"
    )


def test_a_partner_never_sees_the_staff_only_facts_of_its_request(catalog, admin):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    request = request_upgrade(client, partner_id, "reseller")
    assert (
        decide(
            admin, partner_id, request["id"], "approve", commercial_reference="PO-77"
        ).status_code
        == 200
    )
    seen = json.dumps(client.get(pp(partner_id, "/upgrade")).json())
    assert "PO-77" not in seen and "commercial_reference" not in seen
    assert "correlation_id" not in seen and "requested_by" not in seen
    staff = admin.get(admin_path(partner_id, "/upgrades")).json()
    assert staff["history"][0]["commercial_reference"] == "PO-77"


def test_the_queue_lists_open_requests_for_staff_only(catalog, admin):
    client, partner_id = make_affiliate(admin, email=EMAIL)
    request = request_upgrade(client, partner_id, "reseller")
    queue = admin.get("/api/v1/admin/partner-upgrades").json()
    assert [i["id"] for i in queue["items"]] == [request["id"]]
    assert queue["items"][0]["partner_code"] == "af-up"
    assert queue["counts"]["pending_approval"] == 1
    assert client.get("/api/v1/admin/partner-upgrades").status_code in (401, 403)
    assert admin.get("/api/v1/admin/partner-upgrades?status=bogus").status_code == 400

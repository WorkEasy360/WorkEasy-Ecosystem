"""The requests the Phase 7 frontend actually sends, end to end.

Each body below mirrors a function in ``frontend/src/lib/api/controlPlane.ts``
(named in the comment). The point is not to re-test the services but to prove
the client and the API agree on paths, verbs, headers and field names - the
class of bug a typed client cannot catch because the types are hand-written.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.phase7.support import provision, sell

pytestmark = pytest.mark.django_db

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def send(client, method, url, body=None, *, key=False):
    extra = {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())} if key else {}
    return getattr(client, method)(
        url,
        json.dumps(body if body is not None else {}),
        content_type="application/json",
        **extra,
    )


def test_staff_can_create_activate_and_equip_a_partner_using_the_clients_bodies(
    staff_client, user_factory, catalog
):
    staff, _u = staff_client(
        "platform.staff_partner.manage", "platform.staff_commission.manage"
    )
    # adminApi.createPartner
    created = send(
        staff,
        "post",
        "/api/v1/admin/partners",
        {"code": "flow-co", "display_name": "Flow Co", "payout_currency": "GBP"},
    )
    assert created.status_code == 201, created.content
    partner = created.json()
    pid = partner["id"]
    assert partner["status"] == "draft"

    # adminApi.addMember (role from the select: "admin" | "viewer")
    member = user_factory()
    assert (
        send(
            staff,
            "post",
            f"/api/v1/admin/partners/{pid}/members",
            {"email": member.email, "role": "admin"},
        ).status_code
        == 201
    )
    # adminApi.grantCapability
    granted = send(
        staff,
        "post",
        f"/api/v1/admin/partners/{pid}/capabilities",
        {"capability": "commission.earn", "reason": "Programme membership approved"},
    )
    assert granted.status_code == 201, granted.content
    # adminApi.grantProduct (no plan_code: the whole product)
    assert (
        send(
            staff,
            "post",
            f"/api/v1/admin/partners/{pid}/product-grants",
            {"product_code": "crm"},
        ).status_code
        == 201
    )

    # adminApi.setPartnerStatus (version comes from the row the screen showed)
    activated = send(
        staff,
        "post",
        f"/api/v1/admin/partners/{pid}/status",
        {
            "status": "active",
            "reason": "Onboarding complete",
            "expected_version": partner["version"],
        },
    )
    assert activated.status_code == 200, activated.content
    assert activated.json()["status"] == "active"
    # A stale version is a 409, not an overwrite.
    stale = send(
        staff,
        "post",
        f"/api/v1/admin/partners/{pid}/status",
        {
            "status": "suspended",
            "reason": "Stale screen attempt",
            "expected_version": partner["version"],
        },
    )
    assert stale.status_code == 409

    # adminApi.revokeCapability
    revoked = send(
        staff,
        "post",
        f"/api/v1/admin/partners/{pid}/capabilities/revoke",
        {"capability": "commission.earn", "reason": "Programme change"},
    )
    assert revoked.status_code == 200, revoked.content

    # adminApi.createRule
    rule = send(
        staff,
        "post",
        "/api/v1/admin/commission/rules",
        {"code": "default", "rate_bp": 1000, "reason": "Programme default rate"},
    )
    assert rule.status_code == 201, rule.content


def test_the_payout_lifecycle_works_with_the_clients_bodies(
    channel, staff_client, monkeypatch
):
    from django.utils import timezone

    from workeasy360.commercial.commissions import accrual

    monkeypatch.setattr(accrual, "holdback_days", lambda: 0)
    result, _o = provision(channel)
    sell(channel, result.organization_id)
    preparer, _p = staff_client("platform.staff_payout.manage")
    approver, _a = staff_client("platform.staff_payout.approve")

    # adminApi.preparePayout: only a partner and a date, plus a command id.
    prepared = send(
        preparer,
        "post",
        "/api/v1/admin/payouts",
        {
            "partner_id": str(channel["partner"].id),
            "period_end": timezone.now().date().isoformat(),
        },
        key=True,
    )
    assert prepared.status_code == 201, prepared.content
    payout = prepared.json()
    assert payout["status"] == "draft" and "version" in payout

    def act(client, action, extra):
        # adminApi.payoutAction: {expected_version, ...extra}
        return send(
            client,
            "post",
            f"/api/v1/admin/payouts/{payout['id']}/{action}",
            {"expected_version": payout["version"], **extra},
        )

    ready = act(preparer, "ready", {})
    assert ready.status_code == 200, ready.content
    payout = ready.json()
    # The preparer cannot approve (and does not hold the permission anyway).
    assert (
        act(preparer, "approve", {"reason": "Reviewed the ledger lines."}).status_code
        == 403
    )
    approved = act(approver, "approve", {"reason": "Reviewed the ledger lines."})
    assert approved.status_code == 200, approved.content
    payout = approved.json()
    paid = act(preparer, "paid", {"reference": "BANK-REF-0001"})
    assert paid.status_code == 200, paid.content
    assert paid.json()["status"] == "paid"


def test_a_partner_can_sell_brand_and_refer_using_the_clients_bodies(
    channel, http_login, staff_client
):
    from tests.phase7.conftest import PASSWORD
    from workeasy360.commercial.partners import services

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
            reason="Flow fixture",
        )
    fx["user"].set_password(PASSWORD)
    fx["user"].save(update_fields=["password"])
    client = http_login(fx["user"])
    base = f"/api/v1/partner/{fx['partner'].id}"

    # partnerApi.provision
    owner = fx["user_factory"]()
    provisioned = send(
        client,
        "post",
        f"{base}/customers",
        {"organization_name": "Flow Customer", "owner_email": owner.email},
        key=True,
    )
    assert provisioned.status_code in (200, 201), provisioned.content
    org = provisioned.json()["organization_id"]

    # partnerApi.catalog -> partnerApi.sell (a plan-version id and a quantity, nothing else)
    plans = client.get(f"{base}/catalog").json()["results"]
    plan = next(p for p in plans if p["plan_code"] == "crm_professional")
    sold = send(
        client,
        "post",
        f"{base}/customers/{org}/sell",
        {"plan_version_id": plan["plan_version_id"], "quantity": 2},
        key=True,
    )
    assert sold.status_code in (200, 201), sold.content

    # partnerApi.uploadLogo -> partnerApi.saveBrand (first save takes no version)
    logo = client.post(f"{base}/branding/logo", PNG, content_type="image/png")
    assert logo.status_code == 201, logo.content
    logo_id = logo.json()["logo_file_id"]
    first = send(
        client,
        "put",
        f"{base}/branding",
        {
            "brand_name": "Flow Brand",
            "accent_color": "#1a56db",
            "support_email": "",
            "support_url": "",
            "theme": {},
            "logo_file_id": logo_id,
        },
    )
    assert first.status_code == 200, first.content
    version = first.json()["version"]

    # A later save that resends the logo id keeps the logo; one that omits it clears it.
    keep = send(
        client,
        "put",
        f"{base}/branding",
        {
            "brand_name": "Flow Brand 2",
            "accent_color": "",
            "support_email": "",
            "support_url": "",
            "theme": {},
            "logo_file_id": logo_id,
            "expected_version": version,
        },
    )
    assert keep.status_code == 200 and keep.json()["logo_file_id"] == logo_id
    clear = send(
        client,
        "put",
        f"{base}/branding",
        {
            "brand_name": "Flow Brand 3",
            "theme": {},
            "expected_version": keep.json()["version"],
        },
    )
    assert clear.json()["logo_file_id"] is None

    # partnerApi.requestDomain / domainAction
    dom = send(client, "post", f"{base}/domains", {"hostname": "app.flow-example.com"})
    assert dom.status_code in (200, 201), dom.content
    assert dom.json()["status"] == "requested"
    # Verification asks real DNS; with no record published it must be a clean, non-error outcome.
    from workeasy360.commercial.partners import domains as domain_service

    class NoRecords:
        def txt_values(self, name):
            return []

    previous = domain_service.set_txt_resolver(NoRecords())
    try:
        verify = send(client, "post", f"{base}/domains/{dom.json()['id']}/verify")
    finally:
        domain_service.set_txt_resolver(previous)
    assert verify.status_code == 200, verify.content
    assert verify.json()["status"] in ("requested", "verification_pending", "failed")

    # partnerApi.createReferralCode
    code = send(client, "post", f"{base}/referrals", {"label": "Launch"})
    assert code.status_code in (200, 201), code.content

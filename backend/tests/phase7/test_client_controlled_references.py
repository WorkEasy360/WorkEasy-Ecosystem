"""Client-supplied references, through a real COMMIT.

A rollback-wrapped test never commits, so a foreign key that Postgres checks
at commit time (Django's are ``DEFERRABLE INITIALLY DEFERRED``) can be violated
by a request and the test still passes. These tests run every request in its
own committed transaction (``transaction=True``), so a reference the service
failed to validate surfaces as a 500 or a leaked row - which is what they
assert against.

For each reference a client can name (a partner, an organization, a plan
version, a file, an accrual, a product, a user), the answer must be a clean 4xx
and nothing may be written.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.phase7.support import provision, sell

pytestmark = pytest.mark.django_db(transaction=True)

GHOST = str(uuid.uuid4())


def send(client, method, url, body=None, *, key=False):
    extra = {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())} if key else {}
    return getattr(client, method)(
        url,
        json.dumps(body if body is not None else {}),
        content_type="application/json",
        **extra,
    )


def _clean(response):
    """A 4xx with a machine-readable code - never a 500 and never a success."""
    assert 400 <= response.status_code < 500, (
        response.status_code,
        response.content[:300],
    )
    assert response.json().get("code"), response.content[:300]


def test_staff_references_to_things_that_do_not_exist_are_clean_4xx_and_write_nothing(
    staff_client, channel
):
    from workeasy360.commercial.commissions.models import (
        CommissionAccrual,
        CommissionRule,
        Payout,
    )
    from workeasy360.commercial.partners.models import (
        PartnerCapabilityGrant,
        PartnerMember,
        PartnerOrganizationLink,
        PartnerProductGrant,
    )

    staff, _u = staff_client(
        "platform.staff_partner.manage",
        "platform.staff_commission.manage",
        "platform.staff_payout.manage",
    )
    counts = lambda: (  # noqa: E731 - a snapshot of everything a bad reference must not touch
        Payout.objects.count(),
        CommissionRule.objects.count(),
        CommissionAccrual.objects.count(),
        PartnerMember.objects.count(),
        PartnerOrganizationLink.objects.count(),
        PartnerProductGrant.objects.count(),
        PartnerCapabilityGrant.objects.count(),
    )
    before = counts()
    real_partner = str(channel["partner"].id)

    _clean(
        send(
            staff,
            "post",
            "/api/v1/admin/payouts",
            {"partner_id": GHOST, "period_end": "2026-06-30"},
            key=True,
        )
    )
    _clean(
        send(
            staff,
            "post",
            "/api/v1/admin/commission/rules",
            {
                "code": "ghost",
                "rate_bp": 500,
                "reason": "A rule for nobody",
                "partner_id": GHOST,
            },
        )
    )
    _clean(
        send(
            staff,
            "post",
            f"/api/v1/admin/commission/accruals/{GHOST}/reverse",
            {"reason_code": "refund", "reason": "Reverse an entry that is not there"},
            key=True,
        )
    )
    _clean(
        send(
            staff,
            "post",
            f"/api/v1/admin/partners/{GHOST}/members",
            {"email": "x@example.com", "role": "admin"},
        )
    )
    _clean(
        send(
            staff,
            "post",
            f"/api/v1/admin/partners/{real_partner}/members",
            {"email": "nobody@nowhere.example", "role": "admin"},
        )
    )
    _clean(
        send(
            staff,
            "post",
            f"/api/v1/admin/partners/{real_partner}/links",
            {
                "organization_id": GHOST,
                "reason": "Link an organization that does not exist",
            },
        )
    )
    _clean(
        send(
            staff,
            "post",
            f"/api/v1/admin/partners/{real_partner}/product-grants",
            {"product_code": "no_such_product"},
        )
    )
    _clean(
        send(
            staff,
            "post",
            f"/api/v1/admin/partners/{real_partner}/capabilities",
            {"capability": "not.a.capability", "reason": "Invented capability"},
        )
    )
    _clean(
        send(
            staff,
            "post",
            f"/api/v1/admin/partners/{GHOST}/capabilities",
            {"capability": "commission.earn", "reason": "Grant to a ghost"},
        )
    )
    _clean(
        send(
            staff, "post", f"/api/v1/admin/payouts/{GHOST}/ready", {"expected_version": 1}
        )
    )
    _clean(
        send(
            staff,
            "post",
            f"/api/v1/admin/partners/{GHOST}/status",
            {"status": "active", "reason": "Activate a ghost", "expected_version": 1},
        )
    )

    assert counts() == before


def test_partner_references_to_things_they_may_not_use_are_clean_4xx_and_write_nothing(
    channel, http_login, staff_client
):
    from tests.phase7.conftest import PASSWORD
    from workeasy360.commercial.partners import services
    from workeasy360.commercial.partners.models import (
        PartnerBrand,
        PartnerOrganizationLink,
    )
    from workeasy360.platform.organizations.models import Organization  # noqa: F401

    fx = channel
    services.grant_capability(
        fx["staff_ctx"],
        fx["partner"].id,
        capability="branding.manage",
        reason="Fixture capability grant",
    )
    fx["user"].set_password(PASSWORD)
    fx["user"].save(update_fields=["password"])
    client = http_login(fx["user"])
    base = f"/api/v1/partner/{fx['partner'].id}"

    result, _o = provision(fx)
    links = PartnerOrganizationLink.objects.count()

    # An organization that is not this partner's customer.
    _clean(
        send(
            client,
            "post",
            f"{base}/customers/{GHOST}/sell",
            {"plan_version_id": GHOST, "quantity": 1},
            key=True,
        )
    )
    _clean(client.get(f"{base}/customers/{GHOST}/subscription"))
    # A plan version that does not exist, then an implausible quantity.
    _clean(
        send(
            client,
            "post",
            f"{base}/customers/{result.organization_id}/sell",
            {"plan_version_id": GHOST, "quantity": 1},
            key=True,
        )
    )
    real_plan = str(fx["catalog"]["versions"]["crm_professional"].id)
    for bad in (0, -3, 10**9, "many", 1.5, True):
        _clean(
            send(
                client,
                "post",
                f"{base}/customers/{result.organization_id}/sell",
                {"plan_version_id": real_plan, "quantity": bad},
                key=True,
            )
        )
    # A logo that is not a file, and one that is not this partner's.
    _clean(
        send(
            client, "put", f"{base}/branding", {"brand_name": "X", "logo_file_id": GHOST}
        )
    )
    assert PartnerBrand.objects.count() == 0
    # An owner e-mail that is not an e-mail.
    _clean(
        send(
            client,
            "post",
            f"{base}/customers",
            {"organization_name": "Bad", "owner_email": "not-an-email"},
            key=True,
        )
    )
    _clean(
        send(
            client,
            "post",
            f"{base}/customers",
            {"organization_name": "", "owner_email": "a@example.com"},
            key=True,
        )
    )
    assert (
        PartnerOrganizationLink.objects.count() == links
    )  # the failed provisionings left nothing

    # A referral conversion naming an organization the caller does not own converts nothing.
    stranger = send(
        client,
        "post",
        "/api/v1/referrals/convert",
        {"token": "x" * 40, "organization_id": GHOST},
    )
    assert stranger.status_code == 200 and stranger.json() == {"converted": False}


def test_a_real_sale_commits_with_every_reference_intact(channel, monkeypatch):
    """The happy path under a real commit: deferred foreign keys are satisfied."""
    from workeasy360.commercial.commissions.models import CommissionAccrual

    result, _o = provision(channel)
    sell(channel, result.organization_id)
    row = CommissionAccrual.objects.get()
    assert row.partner_id == channel["partner"].id
    assert row.rule_id is not None and row.organization_id == result.organization_id


def test_flags_must_be_real_booleans(channel, http_login, staff_client):
    """``"false"`` is a truthy string: it must not enable a trial or make a
    cancellation immediate."""
    from tests.phase7.conftest import PASSWORD

    fx = channel
    fx["user"].set_password(PASSWORD)
    fx["user"].save(update_fields=["password"])
    client = http_login(fx["user"])
    base = f"/api/v1/partner/{fx['partner'].id}"
    result, _o = provision(fx)
    plan = str(fx["catalog"]["versions"]["crm_professional"].id)
    body = {"plan_version_id": plan, "quantity": 1, "with_trial": "false"}
    _clean(
        send(
            client,
            "post",
            f"{base}/customers/{result.organization_id}/sell",
            body,
            key=True,
        )
    )

    staff, _u = staff_client("platform.staff_subscription.manage")
    cancel = send(
        staff,
        "post",
        f"/api/v1/admin/customers/{result.organization_id}/subscriptions/{GHOST}/cancel",
        {"reason": "Customer asked us to stop", "immediate": "false"},
        key=True,
    )
    _clean(cancel)

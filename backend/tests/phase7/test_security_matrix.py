"""The Phase 7 security matrix: which permissions are dangerous, that the AAL
rule holds for every one of them, that revocation is immediate, and that a
partner cannot reach another partner's resources by guessing ids."""

from __future__ import annotations

import json
import uuid

import pytest

from tests.phase7.support import CAPS, provision

pytestmark = pytest.mark.django_db

#: The complete list, pinned. Downgrading one of these is a security decision
#: and must change this test in the same commit.
DANGEROUS_STAFF = frozenset(
    {
        "platform.staff_customer.manage",
        "platform.staff_catalog.manage",
        "platform.staff_pricing.manage",
        "platform.staff_subscription.manage",
        "platform.staff_partner.manage",
        "platform.staff_commission.manage",
        "platform.staff_payout.manage",
        "platform.staff_payout.approve",
        "platform.staff_security.manage",
        # Added by the final audit: integration administration moved from
        # Django's is_staff onto an explicit grant.
        "platform.staff_integration.manage",
        # Phase 8: providers, models, prices and an organization's AI policy.
        "platform.staff_ai.manage",
        # Phase 8 extension: account actions reach every organization the
        # person belongs to; what every platform email says; money.
        "platform.staff_user.manage",
        "platform.staff_communication.manage",
        "platform.staff_payment.manage",
        # Phase 9: closing signup or changing where customers are told to write.
        "platform.staff_settings.manage",
    }
)
DANGEROUS_PARTNER = frozenset(
    {
        "platform.partner_domain.manage",
        "platform.partner_customer.provision",
        "platform.partner_customer.manage",
        "platform.partner_subscription.sell",
        "platform.partner_subscription.manage",
    }
)


def _control_plane_keys():
    from workeasy360.platform.authz.registry import permission_registry

    return [d for d in permission_registry.all() if d.control_plane]


def test_the_dangerous_permissions_are_exactly_the_pinned_set():
    declared = {d.key for d in _control_plane_keys() if d.dangerous}
    assert declared == DANGEROUS_STAFF | DANGEROUS_PARTNER


def test_no_read_only_permission_is_dangerous():
    """A `.view` that demanded AAL 2 would train people to approve prompts."""
    assert not [
        d.key for d in _control_plane_keys() if d.key.endswith(".view") and d.dangerous
    ]


@pytest.mark.parametrize("permission", sorted(DANGEROUS_STAFF))
def test_every_dangerous_staff_permission_needs_a_second_factor(
    staff_factory, permission
):
    from workeasy360.contracts.errors import StepUpRequired
    from workeasy360.platform.authz import services as authz

    _u, low = staff_factory(permission, aal=1)
    with pytest.raises(StepUpRequired):
        authz.require(low, permission)
    _u, high = staff_factory(permission, aal=2)
    authz.require(high, permission)  # does not raise


@pytest.mark.parametrize("permission", sorted(DANGEROUS_PARTNER))
def test_every_dangerous_partner_permission_needs_a_second_factor(
    partner_factory, permission
):
    from workeasy360.commercial.partners import capabilities
    from workeasy360.contracts.errors import StepUpRequired
    from workeasy360.platform.authz import services as authz

    capability = next(
        key
        for key, spec in capabilities.CAPABILITIES.items()
        if permission in spec.manage or permission in spec.view
    )
    fx = partner_factory(capability)
    with pytest.raises(StepUpRequired):
        authz.require(fx["actor"](aal=1), permission)
    authz.require(fx["actor"](aal=2), permission)


def test_a_stolen_low_assurance_partner_session_cannot_provision_or_sell(partner_client):
    client, fx = partner_client(*CAPS, aal2=False)
    body = {"organization_name": "Sneaky Ltd", "owner_email": "x@sneaky.example.com"}
    response = client.post(
        f"/api/v1/partner/{fx['partner'].id}/customers",
        json.dumps(body),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert response.status_code == 401 and response.json()["code"] == "step_up_required"
    from workeasy360.commercial.partners.models import PartnerOrganizationLink

    assert PartnerOrganizationLink.objects.count() == 0


# --- revocation is immediate --------------------------------------------------------


def test_a_revoked_staff_grant_stops_working_on_the_very_next_request(
    staff_client, user_factory
):
    import uuid as _uuid

    from workeasy360.platform.authz import staff

    client, user = staff_client("platform.staff_customer.view")
    assert client.get("/api/v1/admin/customers").status_code == 200
    grant = staff.live_grants(user_id=user.id)[0]
    root = user_factory(is_staff=True)
    staff.end_grant(
        grant_id=_uuid.UUID(grant["id"]),
        revoked_by_id=root.id,
        reason="Access review removed this",
    )
    assert client.get("/api/v1/admin/customers").status_code == 403


def test_a_revoked_partner_capability_stops_working_on_the_very_next_request(
    partner_client, staff_factory
):
    from workeasy360.commercial.partners import services

    client, fx = partner_client("commission.view")
    url = f"/api/v1/partner/{fx['partner'].id}/commissions"
    assert client.get(url).status_code == 200
    services.revoke_capability(
        fx["staff_ctx"],
        fx["partner"].id,
        capability="commission.view",
        reason="Programme change",
    )
    assert client.get(url).status_code == 403


def test_a_removed_partner_member_loses_access_on_the_next_request(
    partner_client, user_factory
):
    from workeasy360.commercial.partners import services
    from workeasy360.commercial.partners.models import PartnerMember

    client, fx = partner_client("commission.view")
    url = f"/api/v1/partner/{fx['partner'].id}/commissions"
    assert client.get(url).status_code == 200
    services.add_member(
        fx["staff_ctx"], fx["partner"].id, email=user_factory().email, role="admin"
    )
    member = PartnerMember.objects.get(partner_id=fx["partner"].id, user_id=fx["user"].id)
    services.remove_member(fx["staff_ctx"], fx["partner"].id, member_id=member.id)
    assert client.get(url).status_code in (403, 404)


# --- partners cannot reach each other's resources by id ---------------------------------


def test_a_partner_cannot_touch_another_partners_domain(partner_factory, partner_client):
    from workeasy360.commercial.partners import domains

    victim = partner_factory("domain.manage")
    theirs = domains.request_domain(victim["actor"](), hostname="victim.idor-example.com")
    before = theirs.status

    client, mine = partner_client("domain.manage")
    base = f"/api/v1/partner/{mine['partner'].id}/domains/{theirs.id}"
    for action in ("verify", "activate", "disable"):
        response = client.post(f"{base}/{action}", "{}", content_type="application/json")
        assert response.status_code == 404, action
    theirs.refresh_from_db()
    assert theirs.status == before  # untouched


def test_a_partner_cannot_name_another_partner_in_the_url(partner_client):
    """Their own id in the body or query cannot stand in for membership."""
    client, _mine = partner_client("commission.view")
    stranger = uuid.uuid4()
    for path in (
        "commissions",
        "payouts",
        "customers",
        "domains",
        "referrals",
        "branding",
    ):
        assert client.get(f"/api/v1/partner/{stranger}/{path}").status_code == 404, path


def test_partner_payout_and_ledger_lists_hold_only_their_own_rows(
    channel, partner_client
):
    from tests.phase7.support import sell
    from workeasy360.commercial.commissions.models import CommissionAccrual

    result, _org = provision(channel)
    sell(channel, result.organization_id)
    assert CommissionAccrual.objects.count() >= 1

    client, other = partner_client("commission.view", "payout.view")
    for path in ("commissions", "payouts"):
        body = client.get(f"/api/v1/partner/{other['partner'].id}/{path}").json()
        assert body["results"] == [], path


def test_a_partner_cannot_reach_staff_money_endpoints(partner_client):
    client, fx = partner_client(*CAPS)
    for method, url in (
        ("get", "/api/v1/admin/payouts"),
        ("get", "/api/v1/admin/commission/accruals"),
        ("post", f"/api/v1/admin/commission/accruals/{uuid.uuid4()}/reverse"),
        ("post", f"/api/v1/admin/payouts/{uuid.uuid4()}/approve"),
        ("post", "/api/v1/admin/commission/rules"),
    ):
        response = getattr(client, method)(url)
        assert response.status_code == 403, url


# --- staff catalog administration over HTTP ------------------------------------------------


def test_catalog_administration_over_http_needs_its_own_permission(staff_client):
    reader, _ = staff_client("platform.staff_catalog.view")
    assert reader.get("/api/v1/admin/catalog/products").status_code == 200
    denied = reader.post(
        "/api/v1/admin/catalog/products",
        json.dumps({"code": "widget", "name": "Widget"}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert denied.status_code == 403

    writer, _ = staff_client("platform.staff_catalog.manage")
    created = writer.post(
        "/api/v1/admin/catalog/products",
        json.dumps({"code": "widget", "name": "Widget"}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert created.status_code in (200, 201), created.content
    # `manage` implies `view`, so the writer can also read what they wrote.
    assert writer.get("/api/v1/admin/catalog/products").status_code == 200

"""The final Phase 7 adversarial audit: one regression per defect it found.

Each test names an attack and asserts the platform refuses it. They were
written before the fixes and failed on the audited code - that failure was the
finding - so each one pins a real hole, not a hypothetical one. Grouped by the
audit section that found it.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.phase7.conftest import PASSWORD
from tests.phase7.support import provision, sell
from workeasy360.commercial.commissions import accrual, ledger, payouts, referrals
from workeasy360.commercial.commissions.models import CommissionAccrual
from workeasy360.commercial.partners import customers, domains, selectors, services
from workeasy360.contracts.enums import PartnerLinkStatus, PartnerStatus
from workeasy360.contracts.errors import (
    PartnerValidationError,
    PermissionDenied,
    SeparationOfDutiesViolation,
)
from workeasy360.platform.authz import staff

pytestmark = pytest.mark.django_db


def _accruals(partner):
    return list(
        CommissionAccrual.objects.filter(partner=partner).order_by("event_at", "id")
    )


def _json(client, method, url, body=None, **headers):
    return getattr(client, method)(
        url, body or {}, content_type="application/json", **headers
    )


# --- 1-3. Super Admin authority is a StaffGrant, never Django's is_staff ------


def _own_organization(user):
    """An organization the user creates for themselves, as any customer can."""
    from workeasy360.platform.onboarding.services import create_organization

    return create_organization(user_id=user.id, name="A staff member's own org")


def test_is_staff_alone_does_not_unlock_the_legacy_catalog_routes(
    user_factory, http_login
):
    """Django's ``is_staff`` plus ownership of *any* organization used to be
    enough to edit the global catalog and price books through the Phase 2
    ``/orgs/<slug>/commercial/*`` routes: the owner role's ``*`` carries
    ``commercial.catalog.manage`` and the only other gate was ``is_staff``.
    No StaffGrant was involved, so revoking every grant did not remove it."""
    person = user_factory(is_staff=True, password=PASSWORD)
    org = _own_organization(person)
    client = http_login(person)
    base = f"/api/v1/orgs/{org.slug}"

    product = _json(
        client, "post", f"{base}/commercial/products", {"code": "rogue", "name": "Rogue"}
    )
    book = _json(
        client,
        "post",
        f"{base}/commercial/price-books",
        {"code": "rogue_book", "name": "Rogue", "currency": "GBP"},
    )
    listing = client.get(f"{base}/integrations")
    assert (product.status_code, book.status_code, listing.status_code) == (403, 403, 403)

    from workeasy360.commercial.catalog.models import Product

    assert not Product.objects.filter(code="rogue").exists()


def test_the_legacy_catalog_route_follows_the_staff_grant_and_its_revocation(
    user_factory, http_login
):
    """With a real grant the older route works; the next request after the
    grant is revoked is refused - there is no second, un-revocable authority."""
    root = user_factory(is_staff=True)
    person = user_factory(is_staff=True, password=PASSWORD)
    grant, _ = staff.create_grant(
        user_id=person.id,
        permission="platform.staff_catalog.manage",
        granted_by_id=root.id,
        reason="Catalog administration for the launch",
    )
    org = _own_organization(person)
    client = http_login(person)
    url = f"/api/v1/orgs/{org.slug}/commercial/products"

    assert (
        _json(client, "post", url, {"code": "granted_p", "name": "Granted"}).status_code
        == 201
    )
    staff.end_grant(
        grant_id=grant.id, revoked_by_id=root.id, reason="No longer on the catalog team"
    )
    assert (
        _json(client, "post", url, {"code": "after_revoke", "name": "Late"}).status_code
        == 403
    )


def test_a_support_only_staff_member_cannot_reach_catalog_writes(
    user_factory, http_login
):
    root = user_factory(is_staff=True)
    person = user_factory(is_staff=True, password=PASSWORD)
    staff.create_grant(
        user_id=person.id,
        permission="platform.staff_support.view",
        granted_by_id=root.id,
        reason="Front-line support rota member",
    )
    org = _own_organization(person)
    client = http_login(person)
    response = _json(
        client,
        "post",
        f"/api/v1/orgs/{org.slug}/commercial/products",
        {"code": "support_p", "name": "Nope"},
    )
    assert response.status_code == 403


def test_a_staff_grant_cannot_carry_a_partner_permission(user_factory):
    root = user_factory(is_staff=True)
    person = user_factory(is_staff=True)
    from workeasy360.contracts.errors import ControlPlaneInvariantViolation

    with pytest.raises(ControlPlaneInvariantViolation):
        staff.create_grant(
            user_id=person.id,
            permission="platform.partner_subscription.sell",
            granted_by_id=root.id,
            reason="Trying to hold a partner key as staff",
        )


# --- 2, 56. StaffGrant expiry --------------------------------------------------


def test_a_staff_grant_can_expire_and_an_expired_grant_confers_nothing(user_factory):
    from workeasy360.platform.authz.models import StaffGrant

    root = user_factory(is_staff=True)
    person = user_factory(is_staff=True)
    soon = timezone.now() + timedelta(hours=1)
    grant, created = staff.create_grant(
        user_id=person.id,
        permission="platform.staff_partner.view",
        granted_by_id=root.id,
        reason="Temporary cover for a colleague",
        expires_at=soon,
    )
    assert created and grant.expires_at == soon
    assert "platform.staff_partner.view" in staff.staff_permissions(person.id)

    # The boundary: at the expiry instant the grant is already over.
    past = StaffGrant.objects.create(
        user=person,
        permission="platform.staff_commission.view",
        granted_by=root,
        granted_at=timezone.now() - timedelta(days=2),
        reason="An expired grant, as the database holds it",
        expires_at=timezone.now(),
    )
    assert "platform.staff_commission.view" not in staff.staff_permissions(person.id)
    assert past.revoked_at is None  # expiry needs no revocation to take effect

    from workeasy360.contracts.errors import ControlPlaneInvariantViolation

    with pytest.raises(ControlPlaneInvariantViolation):
        staff.create_grant(
            user_id=person.id,
            permission="platform.staff_audit.view",
            granted_by_id=root.id,
            reason="Backdated grant attempt",
            expires_at=timezone.now() - timedelta(minutes=1),
        )


def test_an_expired_grant_fails_the_very_next_http_request(staff_client):
    from workeasy360.platform.authz.models import StaffGrant

    client, user = staff_client("platform.staff_partner.view")
    assert client.get("/api/v1/admin/partners").status_code == 200
    # Replace the live grant with one that has just run out.
    grant = StaffGrant.objects.get(user=user, permission="platform.staff_partner.view")
    staff.end_grant(
        grant_id=grant.id,
        revoked_by_id=grant.granted_by_id,
        reason="Swap for an expiring one",
    )
    StaffGrant.objects.create(
        user=user,
        permission="platform.staff_partner.view",
        granted_by_id=grant.granted_by_id,
        granted_at=timezone.now() - timedelta(hours=1),
        reason="A grant that has just run out",
        expires_at=timezone.now() - timedelta(seconds=1),
    )
    assert client.get("/api/v1/admin/partners").status_code == 403


def test_the_last_permanent_security_administrator_cannot_be_removed_while_a_temporary_one_exists(
    user_factory,
):
    from workeasy360.contracts.errors import ControlPlaneInvariantViolation

    root = user_factory(is_staff=True)
    permanent = user_factory(is_staff=True)
    temporary = user_factory(is_staff=True)
    keep, _ = staff.create_grant(
        user_id=permanent.id,
        permission=staff.SECURITY_ADMIN,
        granted_by_id=root.id,
        reason="The standing security administrator",
    )
    staff.create_grant(
        user_id=temporary.id,
        permission=staff.SECURITY_ADMIN,
        granted_by_id=root.id,
        reason="Cover for one week only",
        expires_at=timezone.now() + timedelta(days=7),
    )
    # The temporary holder will lapse on its own: removing the only permanent
    # one would strand the control plane a week from now.
    with pytest.raises(ControlPlaneInvariantViolation):
        staff.end_grant(
            grant_id=keep.id,
            revoked_by_id=temporary.id,
            reason="Removing the standing admin",
        )


# --- 36-37. Payout approval: stale totals and separation of duties ---------------


@pytest.fixture
def owed(channel, staff_factory, monkeypatch):
    monkeypatch.setattr(accrual, "holdback_days", lambda: 0)
    result, _o = provision(channel)
    sell(channel, result.organization_id)
    _pu, prep = staff_factory("platform.staff_payout.manage")
    _au, approver = staff_factory("platform.staff_payout.approve")
    return {**channel, "prep": prep, "approver": approver, "org": result.organization_id}


def _prepare(owed, key="p1"):
    return payouts.prepare_payout(
        owed["prep"],
        partner_id=owed["partner"].id,
        period_end=timezone.now().date(),
        idempotency_key=key,
    )


def test_a_payout_whose_entry_was_reversed_after_preparation_cannot_be_approved(owed):
    """Refund recorded between preparation and approval: approving the stale
    batch would pay a commission the ledger has already clawed back."""
    payout = _prepare(owed)
    payouts.mark_payout_ready(owed["prep"], payout.id)
    original = _accruals(owed["partner"])[0]
    ledger.reverse_accrual(
        owed["commission_ctx"],
        original.id,
        reason_code="refund",
        reason="Customer refunded in full",
        idempotency_key="refund-1",
    )
    report = payouts.reconcile_payout(payout.id)
    assert not report["ok"] and "entry_reversed_after_preparation" in report["problems"]
    with pytest.raises(payouts.PayoutReconciliationFailed):
        payouts.approve_payout(owed["approver"], payout.id, reason="Reviewed and correct")

    # Cancelling and preparing again nets the reversal: nothing is left to pay.
    payouts.cancel_payout(owed["prep"], payout.id, reason="Superseded by the refund")
    with pytest.raises(PartnerValidationError):
        _prepare(owed, "again")


def test_the_author_of_a_manual_entry_cannot_approve_the_payout_that_pays_it(
    owed, staff_factory
):
    """One person holding commission.manage and payout.approve could mint an
    adjustment and approve its payment; preparation by a colleague is routine
    and is no review of that entry."""
    _u, author = staff_factory(
        "platform.staff_commission.manage", "platform.staff_payout.approve"
    )
    ledger.record_adjustment(
        author,
        owed["partner"].id,
        amount_minor=250_000,
        currency="GBP",
        reason="Bonus agreed on a phone call",
        idempotency_key="bonus",
    )
    payout = _prepare(owed)
    payouts.mark_payout_ready(owed["prep"], payout.id)
    with pytest.raises(SeparationOfDutiesViolation):
        payouts.approve_payout(author, payout.id, reason="Looks right to me")
    approved = payouts.approve_payout(
        owed["approver"], payout.id, reason="Independently reviewed"
    )
    assert approved.status == "approved"


def test_commission_owed_to_a_terminated_partner_can_still_be_settled(owed):
    """Termination ends the relationship, not the debt: money earned before it
    must remain payable (docs/phase-7/02-partners.md)."""
    services.set_partner_status(
        owed["staff_ctx"],
        owed["partner"].id,
        target=PartnerStatus.TERMINATED,
        reason="Programme membership ended",
    )
    payout = _prepare(owed)
    payouts.mark_payout_ready(owed["prep"], payout.id)
    assert (
        payouts.approve_payout(
            owed["approver"], payout.id, reason="Final settlement"
        ).status
        == "approved"
    )


# --- 25. Reseller product scope on every commercial action ------------------------


def test_a_partner_cannot_change_seats_of_a_product_it_may_not_sell(channel):
    from workeasy360.commercial.partners.models import PartnerProductGrant

    result, _o = provision(channel)
    services.grant_product(
        channel["staff_ctx"], channel["partner"].id, product_code="books"
    )
    item = sell(channel, result.organization_id, plan="books_professional")
    grant = PartnerProductGrant.objects.get(
        partner=channel["partner"], product__code="books", status="active"
    )
    services.revoke_product_grant(
        channel["staff_ctx"], channel["partner"].id, grant_id=grant.id
    )

    before = len(_accruals(channel["partner"]))
    with pytest.raises(PermissionDenied):
        customers.change_customer_quantity(
            channel["actor"](),
            result.organization_id,
            subscription_item_id=item.id,
            quantity=500,
            idempotency_key="upsell",
        )
    assert len(_accruals(channel["partner"])) == before


def test_a_partner_cannot_convert_a_trial_of_a_product_it_may_no_longer_sell(channel):
    from workeasy360.commercial.partners.models import PartnerProductGrant
    from workeasy360.commercial.subscriptions.selectors import (
        subscriptions_for_organization,
    )
    from workeasy360.platform.authz.control_plane import (
        acting_scope,
        context_for_organization,
    )

    result, _o = provision(channel)
    sell(channel, result.organization_id, with_trial=True)
    grant = PartnerProductGrant.objects.get(partner=channel["partner"], status="active")
    services.revoke_product_grant(
        channel["staff_ctx"], channel["partner"].id, grant_id=grant.id
    )
    with acting_scope(
        context_for_organization(channel["staff_ctx"], result.organization_id)
    ):
        subscription = subscriptions_for_organization()[0]
    with pytest.raises(PermissionDenied):
        customers.activate_customer_subscription(
            channel["actor"](),
            result.organization_id,
            subscription_id=subscription.id,
            idempotency_key="convert",
        )


# --- 27. Self-dealing: a trial that never converts earns nothing -------------------


def test_a_trial_that_never_converts_earns_no_commission(channel):
    from workeasy360.commercial.subscriptions import system

    result, _o = provision(channel)
    sell(channel, result.organization_id, with_trial=True)
    assert _accruals(channel["partner"]) == []  # nothing is owed on a free trial

    later = timezone.now() + timedelta(days=45)
    system.process_due_subscriptions(at=later)  # the trial lapses unconverted
    accrual.accrue_for_organization(result.organization_id, now=later)
    assert _accruals(channel["partner"]) == []
    assert accrual.reconcile(organization_ids=[result.organization_id]).is_clean


def test_a_converted_trial_earns_from_the_conversion(channel):
    from workeasy360.commercial.subscriptions.selectors import (
        subscriptions_for_organization,
    )
    from workeasy360.platform.authz.control_plane import (
        acting_scope,
        context_for_organization,
    )

    result, _o = provision(channel)
    sell(channel, result.organization_id, with_trial=True)
    customer = context_for_organization(channel["staff_ctx"], result.organization_id)
    with acting_scope(customer):
        subscription = subscriptions_for_organization()[0]
    converted = customers.activate_customer_subscription(
        channel["actor"](),
        result.organization_id,
        subscription_id=subscription.id,
        idempotency_key="convert",
    )
    rows = _accruals(channel["partner"])
    assert [r.amount_minor for r in rows] == [500]
    # The holdback runs from the conversion, when money is first owed.
    assert rows[0].event_at == converted.activated_at
    assert rows[0].clears_at == converted.activated_at + timedelta(
        days=accrual.holdback_days()
    )
    assert accrual.reconcile(organization_ids=[result.organization_id]).is_clean


# --- 27, 29. Self-dealing by the trivial routes ----------------------------------------


def test_a_partner_cannot_provision_a_customer_owned_by_its_own_member(channel):
    with pytest.raises(PartnerValidationError):
        customers.provision_customer(
            channel["actor"](),
            organization_name="Myself Ltd",
            owner_email=channel["user"].email,
            idempotency_key="self",
        )
    assert selectors.partner_customers_page(channel["partner"].id).items == []


@pytest.fixture
def affiliate(partner_factory):
    return partner_factory(
        "referral.create", "commission.earn", "commission.view", "payout.view"
    )


def test_a_partner_member_cannot_convert_their_own_referral(
    affiliate, organization_factory
):
    code = referrals.create_referral_code(affiliate["actor"]())
    token = referrals.capture_referral(code.code)
    org, _m, owner = organization_factory(owner=affiliate["user"])
    assert not referrals.convert_referral(
        user_id=owner.id, token=token, organization_id=org.id
    )


# --- 16, 20. Capability expiry and revocation reach every surface -------------------


def test_an_expired_capability_can_be_granted_again(partner_factory):
    from workeasy360.commercial.partners.models import PartnerCapabilityGrant

    fx = partner_factory("customer.provision")
    PartnerCapabilityGrant.objects.create(
        partner=fx["partner"],
        capability="support.access",
        granted_by_id=fx["staff_ctx"].user_id,
        granted_at=timezone.now() - timedelta(days=30),
        expires_at=timezone.now() - timedelta(days=1),
        reason="A grant that has run out",
    )
    assert "support.access" not in selectors.active_capabilities(fx["partner"].id)
    services.grant_capability(
        fx["staff_ctx"],
        fx["partner"].id,
        capability="support.access",
        reason="Renewed for another quarter",
    )
    assert "support.access" in selectors.active_capabilities(fx["partner"].id)


class _Dns:
    def __init__(self):
        self.records = {}

    def txt_values(self, name):
        return self.records.get(name, [])


def _serving_domain(partner_factory):
    from workeasy360.commercial.partners import branding

    fx = partner_factory("branding.manage", "domain.manage")
    actor = fx["actor"]()
    branding.update_brand(actor, brand_name="Acme")
    fake = _Dns()
    previous = domains.set_txt_resolver(fake)
    try:
        row = domains.request_domain(actor, hostname="app.acme-example.com")
        fake.records["_w360-verify.app.acme-example.com"] = [row.verification_token]
        domains.verify_domain(actor, domain_id=row.id)
        domains.activate_domain(actor, domain_id=row.id)
    finally:
        domains.set_txt_resolver(previous)
    assert domains.public_brand_for_host("app.acme-example.com")["brand_name"] == "Acme"
    return fx


@pytest.mark.parametrize("capability", ["domain.manage", "branding.manage"])
def test_revoking_the_capability_stops_a_live_custom_domain(partner_factory, capability):
    fx = _serving_domain(partner_factory)
    services.revoke_capability(
        fx["staff_ctx"],
        fx["partner"].id,
        capability=capability,
        reason="Suspected phishing use",
    )
    assert domains.public_brand_for_host("app.acme-example.com") is None


def test_suspending_the_partner_stops_its_domain_at_once(partner_factory):
    fx = _serving_domain(partner_factory)
    services.set_partner_status(
        fx["staff_ctx"],
        fx["partner"].id,
        target=PartnerStatus.SUSPENDED,
        reason="Suspected phishing use",
    )
    # No wait for the host cache to expire.
    assert domains.public_brand_for_host("app.acme-example.com") is None


# --- 21-22. A partner's reach ends with the relationship ------------------------------


def test_an_ended_relationship_shows_the_partner_no_live_customer_data(channel):
    from workeasy360.commercial.partners.models import PartnerOrganizationLink

    result, _o = provision(channel)
    sell(channel, result.organization_id)
    link = PartnerOrganizationLink.objects.get(organization_id=result.organization_id)
    services.change_link_status(
        channel["staff_ctx"],
        link.id,
        target=PartnerLinkStatus.ENDED,
        reason="Customer moved to direct",
    )
    [row] = selectors.partner_customers_page(channel["partner"].id).items
    assert row["link_status"] == "ended" and row["ended_at"]
    assert (
        row["name"],
        row["organization_status"],
        row["subscription_status"],
        row["products"],
    ) == (
        "",
        "",
        "",
        [],
    )


def test_partner_diagnostics_is_not_an_account_directory(channel, user_factory):
    """A partner may diagnose its own customer's *members*; it must not learn
    whether an arbitrary address has a Workeasy360 account, or its lock state."""
    from workeasy360.commercial.controlplane import diagnostics

    services.grant_capability(
        channel["staff_ctx"],
        channel["partner"].id,
        capability="support.access",
        reason="Runs first-line support",
    )
    result, _o = provision(channel)
    stranger = user_factory()  # exists, but belongs to no customer of this partner
    known = diagnostics.diagnose(
        channel["actor"](),
        result.organization_id,
        product_code="crm",
        member_email=stranger.email,
    )
    unknown = diagnostics.diagnose(
        channel["actor"](),
        result.organization_id,
        product_code="crm",
        member_email=f"nobody-{uuid.uuid4().hex[:6]}@example.com",
    )
    assert known["chain"] == unknown["chain"]
    assert all(step["step"] != "user" for step in known["chain"])


# --- 10. Idempotent retry of a versioned staff command --------------------------------


def test_a_retried_staff_suspension_replays_instead_of_conflicting(
    channel, staff_factory
):
    from workeasy360.commercial.controlplane import subscriptions as cp_subs
    from workeasy360.commercial.subscriptions.selectors import (
        subscriptions_for_organization,
    )
    from workeasy360.platform.authz.control_plane import (
        acting_scope,
        context_for_organization,
    )

    result, _o = provision(channel)
    sell(channel, result.organization_id)
    _u, sctx = staff_factory("platform.staff_subscription.manage")
    with acting_scope(context_for_organization(sctx, result.organization_id)):
        subscription = subscriptions_for_organization()[0]
    args = {
        "reason": "Chargeback under review",
        "idempotency_key": "suspend-1",
        "expected_version": subscription.version,
    }
    first = cp_subs.suspend(sctx, result.organization_id, subscription.id, **args)
    again = cp_subs.suspend(sctx, result.organization_id, subscription.id, **args)
    assert (first.id, first.version) == (again.id, again.version)


# --- 50. Display strings cannot carry bidirectional overrides -----------------------


@pytest.mark.parametrize("name", ["Acme\u202etxt.exe", "Acme\u2066x", "Line\u2028Two"])
def test_display_strings_refuse_bidi_overrides_and_line_separators(partner_factory, name):
    from workeasy360.commercial.partners import branding

    fx = partner_factory("branding.manage")
    with pytest.raises(PartnerValidationError):
        branding.update_brand(fx["actor"](), brand_name=name)


# --- 63. Unknown references are a clean 4xx, never a 500 -------------------------------


def test_unknown_catalog_references_are_a_client_error(staff_client):
    client, _u = staff_client(
        "platform.staff_pricing.manage", "platform.staff_catalog.manage"
    )
    ghost = str(uuid.uuid4())
    price = _json(
        client,
        "post",
        "/api/v1/admin/catalog/prices",
        {
            "price_book_id": ghost,
            "plan_version_id": ghost,
            "interval": "monthly",
            "amount_minor": 100,
        },
    )
    feature = _json(
        client,
        "post",
        "/api/v1/admin/catalog/features",
        {"product_id": ghost, "key": "crm.x.y", "name": "X", "kind": "boolean"},
    )
    assert price.status_code in (400, 404) and feature.status_code in (400, 404)


# --- 48, 65. Public logo is never cached by a shared cache -----------------------------


def test_the_public_logo_is_not_publicly_cacheable(client, monkeypatch):
    """A signed-in browser's logo response also carries its refreshed session
    cookie (the sliding idle window); ``public`` would invite a shared
    cache to store both."""
    from workeasy360.interfaces.api.v1 import public_views

    monkeypatch.setattr(
        public_views.domains,
        "public_brand_for_host",
        lambda host: {"brand_name": "Acme", "logo_file_id": str(uuid.uuid4())},
    )
    monkeypatch.setattr(
        public_views.branding, "public_logo", lambda file_id: (b"\x89PNG", "image/png")
    )
    response = client.get(
        "/api/v1/public/branding/logo", {"host": "app.acme-example.com"}
    )
    assert response.status_code == 200
    assert "public" not in response["Cache-Control"]
    assert "private" in response["Cache-Control"]


def test_session_and_account_responses_are_not_stored_by_shared_caches(client):
    response = client.get("/api/v1/auth/session")
    assert "no-store" in response.get("Cache-Control", "")


# --- 7. A suspended customer takes no new partner sale ------------------------------------


def test_a_partner_cannot_sell_to_a_suspended_customer(channel, staff_factory):
    from workeasy360.commercial.controlplane import customers as cp_customers
    from workeasy360.contracts.errors import OrganizationNotWritable

    result, _o = provision(channel)
    _u, sctx = staff_factory("platform.staff_customer.manage")
    cp_customers.suspend_organization(
        sctx, result.organization_id, reason="Fraud review in progress"
    )
    with pytest.raises(OrganizationNotWritable):
        sell(channel, result.organization_id)
    assert _accruals(channel["partner"]) == []


# --- 8. A partner-channel price book always names its partner ---------------------------


def test_a_partner_channel_price_book_cannot_be_made_global(staff_client):
    client, _u = staff_client("platform.staff_pricing.manage")
    response = _json(
        client,
        "post",
        "/api/v1/admin/catalog/price-books",
        {
            "code": "ptr_global",
            "name": "Looks global",
            "currency": "GBP",
            "channel": "partner",
        },
    )
    assert response.status_code == 400
    from workeasy360.commercial.pricing.models import PriceBook

    assert not PriceBook.objects.filter(code="ptr_global").exists()


# --- 55. Self-revocation of the last permanent security administrator ---------------------


def test_the_last_permanent_security_administrator_cannot_revoke_themself(user_factory):
    from workeasy360.contracts.errors import ControlPlaneInvariantViolation

    root = user_factory(is_staff=True)
    only = user_factory(is_staff=True)
    grant, _ = staff.create_grant(
        user_id=only.id,
        permission=staff.SECURITY_ADMIN,
        granted_by_id=root.id,
        reason="The only security administrator",
    )
    with pytest.raises(ControlPlaneInvariantViolation):
        staff.end_grant(
            grant_id=grant.id, revoked_by_id=only.id, reason="Stepping down myself"
        )
    assert staff.security_administrator_exists()

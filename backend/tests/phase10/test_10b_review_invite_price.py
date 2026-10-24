"""Phase 10b: the server-backed review, the partner administrator invitation
(the existing identity path) and the partner price command."""

from __future__ import annotations

import json
import re
import uuid
from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from tests.phase10.conftest import PASSWORD, deliver_account_mail, token_from
from workeasy360.commercial.commissions import accrual, rules
from workeasy360.commercial.commissions.models import CommissionAccrual
from workeasy360.commercial.partners import services
from workeasy360.commercial.partners.models import PartnerAccount, PartnerMember
from workeasy360.commercial.pricing.models import PlanPrice, PriceBook
from workeasy360.commercial.pricing.resolution import resolve_price
from workeasy360.contracts.enums import BillingInterval, PartnerStatus
from workeasy360.contracts.errors import (
    AccountLinkInvalid,
    PartnerValidationError,
    PermissionDenied,
)
from workeasy360.contracts.values.money import CurrencyCode

pytestmark = pytest.mark.django_db

REASON = "Agreed reseller terms for the test"


def draft(catalog, **overrides):
    """A complete, valid Add Reseller draft over the fixture catalog."""
    professional = str(catalog["versions"]["crm_professional"].id)
    body = {
        "partner": {
            "code": "acme-resell",
            "display_name": "Acme Resell",
            "legal_name": "Acme Resell Ltd",
            "contact_name": "Priya Shah",
            "contact_email": "priya@acme-resell.example",
            "payout_currency": "GBP",
        },
        "capabilities": ["customer.provision", "subscription.sell", "commission.earn"],
        "products": ["crm"],
        "partner_prices": [
            {"plan_version_id": professional, "interval": "monthly", "amount_minor": 4500}
        ],
        "commission": {"mode": "custom", "rate_bp": 1500},
        "administrator": {"email": "boss@acme-resell.example", "role": "admin"},
    }
    body.update(overrides)
    return body


@pytest.fixture
def viewer_client(staff_client):
    client, _u = staff_client("platform.staff_partner.view")
    return client


def review(client, body):
    response = client.post(
        "/api/v1/admin/partners/reseller-review",
        json.dumps(body),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    return response.json()


def rows_by_plan(result):
    return {(r["plan_code"], r["interval"]): r for r in result["pricing"]}


# --- the review computes every figure ------------------------------------------------------


def test_a_valid_draft_is_ok_and_the_server_computes_the_money(catalog, viewer_client):
    result = review(viewer_client, draft(catalog))
    assert result["ok"] and result["problems"] == []
    rows = rows_by_plan(result)
    professional = rows[("crm_professional", "monthly")]
    assert professional["list_price_minor"] == 4999
    assert professional["partner_price_minor"] == 4500
    assert professional["partner_price_source"] == "proposed"
    assert professional["discount_minor"] == 499
    assert professional["discount_bp"] == 998  # 499 / 4999, whole basis points
    assert professional["commission_bp"] == 1500
    assert professional["commission_minor"] == 675  # 15% of 4500
    assert professional["platform_net_minor"] == 3825
    # A plan the operator gave no price for is offered at the list price.
    starter = rows[("crm_starter", "monthly")]
    assert (
        starter["list_price_minor"] == 1499 and starter["partner_price_source"] == "list"
    )
    assert starter["discount_minor"] == 0 and starter["commission_minor"] == 225


def test_the_default_commission_is_the_programme_rule_and_says_so(
    catalog, viewer_client, staff_factory
):
    _u, manager = staff_factory("platform.staff_commission.manage")
    rules.create_rule_version(
        manager, code="default", rate_bp=1000, reason="Programme default"
    )
    body = draft(catalog, commission={"mode": "default"})
    result = review(viewer_client, body)
    assert result["commission"]["source"] == "programme_default"
    assert result["commission"]["programme_default_bp"] == 1000
    assert (
        rows_by_plan(result)[("crm_professional", "monthly")]["commission_minor"] == 450
    )


def test_no_programme_rule_means_no_commission_and_a_warning(catalog, viewer_client):
    result = review(viewer_client, draft(catalog, commission={"mode": "default"}))
    assert result["commission"]["source"] == "none"
    row = rows_by_plan(result)[("crm_professional", "monthly")]
    assert row["commission_bp"] is None and row["commission_minor"] is None
    assert any("no programme default" in w for w in result["warnings"])


def test_every_problem_is_reported_at_once_by_field(catalog, viewer_client):
    bad = draft(
        catalog,
        partner={
            "code": "X",
            "display_name": "",
            "contact_email": "not-an-address",
            "payout_currency": "XXX",
        },
        capabilities=["root.everything"],
        products=["nonexistent"],
        partner_prices=[
            {"plan_version_id": "nope", "interval": "monthly", "amount_minor": 1}
        ],
        commission={"mode": "custom", "rate_bp": 12.5},
        administrator={"email": "", "role": "god"},
    )
    result = review(viewer_client, bad)
    assert not result["ok"]
    fields = {p["field"] for p in result["problems"]}
    assert {
        "code",
        "display_name",
        "contact_email",
        "payout_currency",
        "capabilities",
        "products",
        "partner_prices",
        "commission",
        "administrator.email",
        "administrator.role",
    } <= fields


def test_a_partner_code_in_use_is_reported(catalog, viewer_client, partner_factory):
    existing = partner_factory().get("partner")
    body = draft(catalog)
    body["partner"]["code"] = existing.code
    result = review(viewer_client, body)
    assert any(
        p["field"] == "code" and "already in use" in p["message"]
        for p in result["problems"]
    )


def test_a_price_for_a_plan_that_is_not_chosen_is_a_problem(catalog, viewer_client):
    books = str(catalog["versions"]["books_professional"].id)
    result = review(
        viewer_client,
        draft(
            catalog,
            partner_prices=[
                {"plan_version_id": books, "interval": "monthly", "amount_minor": 100}
            ],
        ),
    )
    assert any(p["field"] == "partner_prices" for p in result["problems"])


def test_a_price_above_list_and_a_missing_capability_are_warned_not_hidden(
    catalog, viewer_client
):
    professional = str(catalog["versions"]["crm_professional"].id)
    result = review(
        viewer_client,
        draft(
            catalog,
            capabilities=["customer.provision"],
            partner_prices=[
                {
                    "plan_version_id": professional,
                    "interval": "monthly",
                    "amount_minor": 9999,
                }
            ],
        ),
    )
    assert result["ok"]
    joined = " ".join(result["warnings"])
    assert "above the list price" in joined
    assert "earns nothing" in joined
    assert rows_by_plan(result)[("crm_professional", "monthly")]["discount_minor"] < 0


def test_a_currency_with_no_list_price_is_flagged(catalog, viewer_client):
    body = draft(catalog, partner_prices=[])
    body["partner"]["payout_currency"] = "EUR"
    result = review(viewer_client, body)
    row = rows_by_plan(result)[("crm_professional", "monthly")]
    assert row["list_price_minor"] is None and row["partner_price_minor"] is None
    assert any("no list price in EUR" in w for w in result["warnings"])


def test_the_review_writes_nothing(catalog, viewer_client, partner_factory):
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.notifications.models import Notification
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    def counts():
        # The audit trail and the notification queue are row-level-secured, so
        # they are counted inside the platform organization's scope - counted
        # any other way they read 0 before and after, and prove nothing.
        with (
            tenant_context(routing_context(PLATFORM_ORGANIZATION_ID)),
            organization_db_scope(PLATFORM_ORGANIZATION_ID),
        ):
            secured = (AuditEvent.objects.count(), Notification.objects.count())
        return (
            PartnerAccount.objects.count(),
            PartnerMember.objects.count(),
            PriceBook.objects.count(),
            PlanPrice.objects.count(),
            *secured,
        )

    partner_factory("customer.provision")  # an audited write the counters must see
    before = counts()
    assert before[-2] > 0, "the audit counter is live, so 'unchanged' means something"
    for _ in range(3):
        review(viewer_client, draft(catalog))
    assert counts() == before


def test_the_review_needs_the_view_permission(
    catalog, staff_client, http_login, user_factory
):
    client, _u = staff_client("platform.staff_customer.view")
    response = client.post(
        "/api/v1/admin/partners/reseller-review",
        json.dumps(draft(catalog)),
        content_type="application/json",
    )
    assert response.status_code == 403
    customer = http_login(user_factory(password=PASSWORD))
    assert (
        customer.post(
            "/api/v1/admin/partners/reseller-review",
            "{}",
            content_type="application/json",
        ).status_code
        == 403
    )


def test_garbage_bodies_are_problems_or_400s_never_500s(catalog, viewer_client):
    for body in (
        {},
        {"partner": "x"},
        {"products": "crm"},
        {"capabilities": {"a": 1}},
        {"partner_prices": 5},
    ):
        response = viewer_client.post(
            "/api/v1/admin/partners/reseller-review",
            json.dumps(body),
            content_type="application/json",
        )
        assert response.status_code == 200 and response.json()["ok"] is False
    assert (
        viewer_client.post(
            "/api/v1/admin/partners/reseller-review",
            "not json",
            content_type="application/json",
        ).status_code
        == 400
    )


# --- what the review says is what really happens ----------------------------------------------


def test_the_reviewed_figures_are_what_a_real_sale_charges_and_earns(
    catalog, viewer_client, staff_factory, monkeypatch
):
    """The dry run is only worth having if it is *right*: build the reseller for
    real through the commands, sell through it, and compare."""
    monkeypatch.setattr(accrual, "holdback_days", lambda: 0)
    body = draft(catalog)
    reviewed = rows_by_plan(review(viewer_client, body))[("crm_professional", "monthly")]

    _m, manage = staff_factory(
        "platform.staff_partner.manage",
        "platform.staff_pricing.manage",
        "platform.staff_commission.manage",
    )
    details = body["partner"]
    partner = services.create_partner(
        manage,
        code=details["code"],
        display_name=details["display_name"],
        payout_currency=details["payout_currency"],
    )
    for key in body["capabilities"]:
        services.grant_capability(manage, partner.id, capability=key, reason=REASON)
    services.grant_product(manage, partner.id, product_code="crm")
    price = body["partner_prices"][0]
    services.set_partner_price(
        manage,
        partner.id,
        plan_version_id=price["plan_version_id"],
        interval=price["interval"],
        amount_minor=price["amount_minor"],
    )
    rules.assign_partner_commission(
        manage,
        partner.id,
        rate_bp=body["commission"]["rate_bp"],
        reason=REASON,
        expected_version=0,
    )
    invite = services.invite_member(
        manage, partner.id, email=body["administrator"]["email"], role="admin"
    )
    services.set_partner_status(
        manage, partner.id, target=PartnerStatus.ACTIVE, reason=REASON
    )

    from workeasy360.commercial.partners.access import partner_actor

    actor = partner_actor(user_id=invite.member.user_id, partner_id=partner.id)
    from workeasy360.commercial.partners import customers

    owner_email = f"owner-{uuid.uuid4().hex[:6]}@example.com"
    made = customers.provision_customer(
        actor,
        organization_name="Reviewed Customer",
        owner_email=owner_email,
        idempotency_key="rv1",
    )
    item = customers.sell_plan(
        actor,
        made.organization_id,
        plan_version_id=catalog["versions"]["crm_professional"].id,
        quantity=1,
        idempotency_key="rv2",
    )
    assert item.total_amount_minor == reviewed["partner_price_minor"]
    ledger_row = CommissionAccrual.objects.get(partner=partner)
    assert ledger_row.amount_minor == reviewed["commission_minor"]
    assert ledger_row.rate_bp == reviewed["commission_bp"]


# --- the partner administrator: the existing identity path ------------------------------------------------


@pytest.fixture
def manager(staff_factory):
    _u, ctx = staff_factory("platform.staff_partner.manage")
    return ctx


def outbox_for(address):
    deliver_account_mail()
    return [m for m in mail.outbox if address in m.to]


def test_a_new_administrator_gets_an_account_a_membership_and_one_link(
    partner_factory, manager, caplog
):
    fx = partner_factory()
    address = f"new-admin-{uuid.uuid4().hex[:6]}@example.com"
    with caplog.at_level("DEBUG"):
        result = services.invite_member(
            manager, fx["partner"].id, email=address, role="admin"
        )
    assert result.account_created and result.setup_link_sent
    member = PartnerMember.objects.get(id=result.member.id)
    assert member.role == "admin" and member.status == "active"
    user = member.user
    assert not user.has_usable_password()  # cannot sign in yet
    assert user.acquisition_source == "partner"

    sent = outbox_for(address)
    assert len(sent) == 1
    assert "Choose your password" in sent[0].body
    token = token_from(sent[0])
    # The raw link is never in a log line.
    assert token not in caplog.text
    assert all(token not in r.getMessage() for r in caplog.records)


def test_the_link_sets_the_first_password_once_and_the_person_can_then_act(
    partner_factory, manager
):
    from tests.phase10.conftest import PASSWORD as NEW_PASSWORD
    from workeasy360.commercial.partners.access import partner_actor
    from workeasy360.platform.administration import self_service

    fx = partner_factory("customer.provision")
    address = f"first-{uuid.uuid4().hex[:6]}@example.com"
    result = services.invite_member(
        manager, fx["partner"].id, email=address, role="admin"
    )
    token = token_from(outbox_for(address)[0])

    user = self_service.reset_password(token, NEW_PASSWORD)
    assert user.check_password(NEW_PASSWORD) and user.email_verified_at is not None
    with pytest.raises(AccountLinkInvalid):
        self_service.reset_password(token, NEW_PASSWORD + "x")  # single use
    actor = partner_actor(user_id=user.id, partner_id=fx["partner"].id)
    assert actor.partner_id == fx["partner"].id and result.member.user_id == user.id


def test_a_known_account_with_a_password_is_attached_without_an_email(
    partner_factory, manager, user_factory
):
    fx = partner_factory()
    person = user_factory(password=PASSWORD)
    result = services.invite_member(
        manager, fx["partner"].id, email=person.email, role="viewer"
    )
    assert not result.account_created and not result.setup_link_sent
    assert outbox_for(person.email) == []
    assert PartnerMember.objects.filter(
        partner=fx["partner"], user=person, role="viewer"
    ).exists()


def test_a_repeat_invitation_is_the_same_success_and_sends_nothing_more(
    partner_factory, manager
):
    """The wizard resumes its steps after a lost response: inviting the person
    already attached in that role must not fail and must not email twice."""
    fx = partner_factory()
    address = f"again-{uuid.uuid4().hex[:6]}@example.com"
    first = services.invite_member(manager, fx["partner"].id, email=address, role="admin")
    outbox_for(address)
    mail.outbox.clear()
    again = services.invite_member(manager, fx["partner"].id, email=address, role="admin")
    assert again.member.id == first.member.id
    assert not again.account_created and not again.setup_link_sent
    assert outbox_for(address) == []
    assert (
        PartnerMember.objects.filter(
            partner=fx["partner"], user=first.member.user
        ).count()
        == 1
    )


def test_a_different_role_for_a_member_is_refused_and_a_disabled_account_too(
    partner_factory, manager, user_factory
):
    fx = partner_factory()
    person = user_factory(password=PASSWORD)
    services.invite_member(manager, fx["partner"].id, email=person.email, role="admin")
    with pytest.raises(PartnerValidationError) as changed:
        services.invite_member(
            manager, fx["partner"].id, email=person.email, role="viewer"
        )
    disabled = user_factory(status="disabled")
    with pytest.raises(PartnerValidationError) as gone:
        services.invite_member(
            manager, fx["partner"].id, email=disabled.email, role="admin"
        )
    assert changed.value.message == gone.value.message == "That account cannot be added."


def test_a_terminated_partner_takes_no_one(partner_factory, manager):
    fx = partner_factory()
    services.set_partner_status(
        manager,
        fx["partner"].id,
        target=PartnerStatus.TERMINATED,
        reason="Terminated for the invite test",
    )
    with pytest.raises(PartnerValidationError):
        services.invite_member(
            manager, fx["partner"].id, email="late@example.com", role="admin"
        )


def test_only_a_partner_manager_can_invite(partner_factory, staff_factory):
    fx = partner_factory()
    _v, viewer = staff_factory("platform.staff_partner.view")
    with pytest.raises(PermissionDenied):
        services.invite_member(
            viewer, fx["partner"].id, email="a@example.com", role="admin"
        )
    # A partner's own administrator may now invite into *its own* partner
    # (ADR-045, tests/partner_panel); into any other partner it is a 404.
    from workeasy360.contracts.errors import PartnerRecordNotFound

    other = partner_factory()
    with pytest.raises(PartnerRecordNotFound):
        services.invite_member(
            fx["actor"](), other["partner"].id, email="a@example.com", role="admin"
        )


def test_resending_replaces_the_link_and_is_silent_for_someone_who_can_sign_in(
    partner_factory, manager, user_factory
):
    from workeasy360.platform.administration import self_service

    fx = partner_factory()
    address = f"resend-{uuid.uuid4().hex[:6]}@example.com"
    result = services.invite_member(
        manager, fx["partner"].id, email=address, role="admin"
    )
    first = token_from(outbox_for(address)[0])
    assert services.resend_member_setup(
        manager, fx["partner"].id, member_id=result.member.id
    )
    mail.outbox.clear()
    second = token_from(outbox_for(address)[0])
    assert first != second
    with pytest.raises(AccountLinkInvalid):
        self_service.reset_password(first, PASSWORD)  # the older link died
    self_service.reset_password(second, PASSWORD)
    assert (
        services.resend_member_setup(
            manager, fx["partner"].id, member_id=result.member.id
        )
        is False
    )


def test_the_setup_link_expires_after_the_configured_days(
    partner_factory, manager, settings
):
    from workeasy360.platform.identity.models import AccountToken

    settings.PARTNER_ADMIN_INVITE_DAYS = 3
    fx = partner_factory()
    address = f"ttl-{uuid.uuid4().hex[:6]}@example.com"
    services.invite_member(manager, fx["partner"].id, email=address, role="admin")
    row = AccountToken.objects.get(user__email=address)
    assert (
        timedelta(days=2, hours=23) < row.expires_at - timezone.now() <= timedelta(days=3)
    )
    assert row.token_hash and len(row.token_hash) == 64  # a hash, not the token


def test_the_invite_endpoint_answers_the_same_for_new_and_known_addresses(
    partner_factory, staff_client, user_factory
):
    fx = partner_factory()
    client, _u = staff_client("platform.staff_partner.manage")
    url = f"/api/v1/admin/partners/{fx['partner'].id}/administrators"
    known = user_factory(password=PASSWORD)

    def call(email):
        return client.post(
            url,
            json.dumps({"email": email, "role": "admin"}),
            content_type="application/json",
        )

    fresh = call(f"fresh-{uuid.uuid4().hex[:6]}@example.com")
    existing = call(known.email)
    assert fresh.status_code == existing.status_code == 201
    assert set(fresh.json()) == set(existing.json()) == {"id", "role", "invited"}


def test_the_activation_email_contains_no_password_and_the_link_is_in_the_fragment(
    partner_factory, manager
):
    fx = partner_factory()
    address = f"frag-{uuid.uuid4().hex[:6]}@example.com"
    services.invite_member(manager, fx["partner"].id, email=address, role="admin")
    body = outbox_for(address)[0].body
    assert re.search(r"/reset-password#token=", body)
    assert "?token=" not in body and "password:" not in body.lower()


# --- the partner price command ---------------------------------------------------------------------


@pytest.fixture
def pricer(staff_factory):
    _u, ctx = staff_factory("platform.staff_pricing.manage")
    return ctx


def price_of(catalog, partner, cycle=BillingInterval.MONTHLY, plan="crm_professional"):
    return resolve_price(
        plan_version_id=catalog["versions"][plan].id,
        organization_id=None,
        currency=CurrencyCode.GBP,
        interval=cycle,
        quantity=1,
        at=timezone.now(),
        partner_id=partner.id if partner else None,
    )


def test_a_partner_price_is_used_for_that_partner_only(catalog, partner_factory, pricer):
    mine = partner_factory()["partner"]
    other = partner_factory()["partner"]
    version = catalog["versions"]["crm_professional"].id
    services.set_partner_price(
        pricer, mine.id, plan_version_id=version, interval="monthly", amount_minor=4200
    )
    assert price_of(catalog, mine).total.minor_units == 4200
    assert price_of(catalog, mine).price_source.value == "partner_book"
    assert price_of(catalog, other).total.minor_units == 4999  # the list price
    assert price_of(catalog, None).total.minor_units == 4999  # a direct customer
    # A cycle with no partner price falls through to the list price.
    assert price_of(catalog, mine, plan="crm_starter").total.minor_units == 1499


def test_setting_a_price_is_idempotent_and_a_change_supersedes_history(
    catalog, partner_factory, pricer
):
    partner = partner_factory()["partner"]
    version = catalog["versions"]["crm_professional"].id
    first = services.set_partner_price(
        pricer, partner.id, plan_version_id=version, interval="monthly", amount_minor=4200
    )
    again = services.set_partner_price(
        pricer, partner.id, plan_version_id=version, interval="monthly", amount_minor=4200
    )
    assert first.id == again.id
    assert PriceBook.objects.filter(partner_id=partner.id).count() == 1
    changed = services.set_partner_price(
        pricer, partner.id, plan_version_id=version, interval="monthly", amount_minor=3900
    )
    assert changed.id != first.id
    first.refresh_from_db()
    assert (
        first.valid_to is not None and first.amount_minor == 4200
    )  # closed, never edited
    assert price_of(catalog, partner).total.minor_units == 3900


def test_the_price_command_validates_before_writing(catalog, partner_factory, pricer):
    partner = partner_factory()["partner"]
    version = catalog["versions"]["crm_professional"].id
    for amount in (-1, 12.5, "4200", True, None):
        with pytest.raises(PartnerValidationError):
            services.set_partner_price(
                pricer,
                partner.id,
                plan_version_id=version,
                interval="monthly",
                amount_minor=amount,
            )
    with pytest.raises(PartnerValidationError):
        services.set_partner_price(
            pricer,
            partner.id,
            plan_version_id=version,
            interval="fortnightly",
            amount_minor=100,
        )
    assert not PriceBook.objects.filter(partner_id=partner.id).exists()


def test_the_price_command_needs_pricing_authority_and_a_live_partner(
    catalog, partner_factory, staff_factory, pricer
):
    fx = partner_factory()
    version = catalog["versions"]["crm_professional"].id
    _m, manage_only = staff_factory("platform.staff_partner.manage")
    with pytest.raises(PermissionDenied):
        services.set_partner_price(
            manage_only,
            fx["partner"].id,
            plan_version_id=version,
            interval="monthly",
            amount_minor=1,
        )
    with pytest.raises(PermissionDenied):
        services.set_partner_price(
            fx["actor"](),
            fx["partner"].id,
            plan_version_id=version,
            interval="monthly",
            amount_minor=1,
        )
    services.set_partner_status(
        fx["staff_ctx"],
        fx["partner"].id,
        target=PartnerStatus.TERMINATED,
        reason="Terminated for the price test",
    )
    with pytest.raises(PartnerValidationError):
        services.set_partner_price(
            pricer,
            fx["partner"].id,
            plan_version_id=version,
            interval="monthly",
            amount_minor=1,
        )


def test_a_partner_cannot_change_its_own_price_over_http(catalog, partner_client):
    client, fx = partner_client("pricing.view", "subscription.sell")
    url = f"/api/v1/admin/partners/{fx['partner'].id}/prices"
    body = {
        "plan_version_id": str(catalog["versions"]["crm_professional"].id),
        "interval": "monthly",
        "amount_minor": 1,
    }
    assert (
        client.post(url, json.dumps(body), content_type="application/json").status_code
        == 403
    )


def test_the_price_endpoint_sets_and_reports_the_stored_amount(
    catalog, partner_factory, staff_client
):
    partner = partner_factory()["partner"]
    client, _u = staff_client("platform.staff_pricing.manage")
    response = client.post(
        f"/api/v1/admin/partners/{partner.id}/prices",
        json.dumps(
            {
                "plan_version_id": str(catalog["versions"]["crm_professional"].id),
                "interval": "monthly",
                "amount_minor": 4321,
                # Forged fields are ignored:
                "price_book_id": str(uuid.uuid4()),
                "currency": "",
                "published_at": "2001-01-01T00:00:00Z",
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 201, response.content
    assert response.json()["amount_minor"] == 4321
    assert price_of(catalog, partner).total.minor_units == 4321

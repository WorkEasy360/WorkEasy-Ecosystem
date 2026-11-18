"""Renewal and plan changes of a wallet-paid customer (ADR-063, the
specification's 6.3 "renewal and permitted plan changes" and 7.1): each period is
paid once, by "Renew now" or by the clock; an unpaid renewal ends the lines with
the paid period; an upgrade pays the difference for the rest of the period."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.partner_admin_panel.conftest import balance, in_org, plan_id, top_up
from tests.phase7.support import provision
from workeasy360.commercial.controlplane import partner_wallet
from workeasy360.commercial.wallets.ledger import InsufficientBalance

pytestmark = pytest.mark.django_db

PRO_COST = 3999  # 49.99 less 20%
STARTER_COST = 1199  # 14.99 less 20%


def _activate(fx, plan="crm_professional", cost=PRO_COST):
    managed, _owner = provision(fx)
    partner_wallet.activate(
        fx["actor"](),
        managed.organization_id,
        {
            "plan_version_id": plan_id(fx, plan),
            "billing_interval": "monthly",
            "expected_charge_minor": cost,
        },
        idempotency_key=uuid.uuid4().hex,
    )
    return managed.organization_id


def _subscription(org_id):
    from workeasy360.commercial.subscriptions.models import Subscription

    with in_org(org_id):
        return Subscription.unscoped.get(organization_id=org_id)


def _ends_in(org_id, days):
    """Move the paid period so that it ends ``days`` from now."""
    from workeasy360.commercial.subscriptions.models import Subscription

    now = timezone.now()
    with in_org(org_id):
        Subscription.unscoped.filter(organization_id=org_id).update(
            current_period_start=now - timedelta(days=30 - days),
            current_period_end=now + timedelta(days=days),
        )
    return _subscription(org_id)


def _live_lines(org_id):
    from workeasy360.commercial.subscriptions.models import SubscriptionItem

    with in_org(org_id):
        return list(
            SubscriptionItem.unscoped.filter(
                organization_id=org_id, removed_at__isnull=True
            ).values_list("plan_code", flat=True)
        )


def test_renewal_opens_thirty_days_before_the_period_ends(wallet_channel):
    fx = wallet_channel
    top_up(fx, 20_000)
    org = _activate(fx)
    sub = _subscription(org)
    quote = partner_wallet.renewal_quote(fx["actor"](), org, sub.id)
    assert quote["blocked"] in ("too_early", None)
    _ends_in(org, 5)
    quote = partner_wallet.renewal_quote(fx["actor"](), org, sub.id)
    assert quote["blocked"] is None
    assert quote["charge_minor"] == PRO_COST
    assert quote["already_paid"] is False


def test_renew_now_pays_the_next_period_once(wallet_channel):
    fx = wallet_channel
    top_up(fx, 20_000)
    org = _activate(fx)
    sub = _ends_in(org, 5)
    after_activation = balance(fx)
    paid = partner_wallet.renew(
        fx["actor"](), org, sub.id, expected_charge_minor=PRO_COST
    )
    assert paid["already_paid"] is True
    assert balance(fx) == after_activation - PRO_COST
    # A second click is no second charge.
    again = partner_wallet.renew(fx["actor"](), org, sub.id, expected_charge_minor=0)
    assert again["already_paid"] is True
    assert balance(fx) == after_activation - PRO_COST


def test_the_clock_does_not_charge_a_period_renewed_ahead(wallet_channel):
    from workeasy360.commercial.subscriptions import system

    fx = wallet_channel
    top_up(fx, 20_000)
    org = _activate(fx)
    sub = _ends_in(org, 2)
    partner_wallet.renew(fx["actor"](), org, sub.id, expected_charge_minor=PRO_COST)
    paid = balance(fx)
    system.process_due_subscriptions(at=sub.current_period_end + timedelta(minutes=1))
    rolled = _subscription(org)
    assert rolled.current_period_end > sub.current_period_end
    assert balance(fx) == paid
    assert _live_lines(org) == ["crm_professional"]


def test_the_clock_collects_an_unpaid_renewal_from_the_wallet(wallet_channel):
    from workeasy360.commercial.subscriptions import system

    fx = wallet_channel
    top_up(fx, 20_000)
    org = _activate(fx)
    sub = _ends_in(org, 1)
    before = balance(fx)
    system.process_due_subscriptions(at=sub.current_period_end + timedelta(minutes=1))
    assert balance(fx) == before - PRO_COST
    assert _subscription(org).current_period_end > sub.current_period_end
    assert _live_lines(org) == ["crm_professional"]


def test_an_unpaid_renewal_ends_the_lines_with_the_paid_period(wallet_channel):
    from workeasy360.commercial.subscriptions import system
    from workeasy360.platform.audit.models import AuditEvent

    fx = wallet_channel
    top_up(fx, PRO_COST)  # exactly the first period, nothing left for a renewal
    org = _activate(fx)
    sub = _ends_in(org, 1)
    system.process_due_subscriptions(at=sub.current_period_end + timedelta(minutes=1))
    assert balance(fx) == 0
    assert _live_lines(org) == []
    with in_org(org):
        assert AuditEvent.unscoped.filter(
            organization_id=org, action="commercial.subscription.renewal_unpaid"
        ).exists()


def test_an_upgrade_pays_the_difference_for_the_rest_of_the_period(wallet_channel):
    fx = wallet_channel
    top_up(fx, 20_000)
    org = _activate(fx, plan="crm_starter", cost=STARTER_COST)
    _ends_in(org, 15)  # half the period left
    item = next(
        i
        for i in partner_wallet.subscription_selectors.commercial_state(org)[0].items
        if i.removed_at is None
    )
    body = {"subscription_item_id": str(item.id), "to_plan_version_id": plan_id(fx)}
    quote = partner_wallet.change_quote(fx["actor"](), org, body)
    assert quote["timing"] == "immediate"
    assert quote["from_cost_minor"] == STARTER_COST
    assert quote["to_cost_minor"] == PRO_COST
    assert 0 < quote["charge_minor"] < PRO_COST - STARTER_COST
    before = balance(fx)
    partner_wallet.change_plan(
        fx["actor"](),
        org,
        {**body, "expected_charge_minor": quote["charge_minor"]},
        idempotency_key=uuid.uuid4().hex,
    )
    assert balance(fx) == before - quote["charge_minor"]
    assert _live_lines(org) == ["crm_professional"]


def test_an_upgrade_the_wallet_cannot_pay_changes_nothing(wallet_channel):
    fx = wallet_channel
    top_up(fx, STARTER_COST)
    org = _activate(fx, plan="crm_starter", cost=STARTER_COST)
    _ends_in(org, 15)
    item = partner_wallet.subscription_selectors.commercial_state(org)[0].items[0]
    body = {"subscription_item_id": str(item.id), "to_plan_version_id": plan_id(fx)}
    quote = partner_wallet.change_quote(fx["actor"](), org, body)
    with pytest.raises(InsufficientBalance):
        partner_wallet.change_plan(
            fx["actor"](),
            org,
            {**body, "expected_charge_minor": quote["charge_minor"]},
            idempotency_key=uuid.uuid4().hex,
        )
    assert _live_lines(org) == ["crm_starter"]
    assert balance(fx) == 0

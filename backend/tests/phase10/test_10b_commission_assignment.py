"""Phase 10b: assigning a commission rule to a partner.

The relationship (``CommissionRule.partner``) already existed; this proves the
*use case* built on it keeps every promise the brief lists - version-safe,
effective-date aware, audited, permission protected, AAL2, historical accruals
untouched, server authoritative and concurrency safe.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from tests.phase7.support import provision, sell
from workeasy360.commercial.commissions import accrual, rules
from workeasy360.commercial.commissions.models import CommissionAccrual, CommissionRule
from workeasy360.commercial.partners.services import ConcurrentPartnerModification
from workeasy360.contracts.errors import (
    PartnerRecordNotFound,
    PartnerValidationError,
    PermissionDenied,
    StepUpRequired,
)

pytestmark = pytest.mark.django_db

REASON = "Agreed reseller commission terms"


@pytest.fixture
def manager(staff_factory):
    _u, ctx = staff_factory("platform.staff_commission.manage")
    return ctx


@pytest.fixture
def partner(partner_factory):
    return partner_factory("customer.provision", "subscription.sell", "commission.earn")


def assign(ctx, partner, rate=2000, *, expected=0, **kw):
    return rules.assign_partner_commission(
        ctx,
        partner["partner"].id,
        rate_bp=rate,
        reason=kw.pop("reason", REASON),
        expected_version=expected,
        **kw,
    )


# --- the first assignment ---------------------------------------------------------


def test_a_first_assignment_creates_the_partners_own_rule(manager, partner):
    result = assign(manager, partner, 2000)
    assert result.created and result.previous_rate_bp is None
    rule = result.rule
    assert rule.partner_id == partner["partner"].id and rule.version == 1
    assert rule.rate_bp == 2000 and rule.product_code == "" and rule.retired_at is None
    assert rule.code.startswith("pc-")


def test_the_rule_governs_this_partners_sales_and_no_others(
    manager, partner, partner_factory
):
    other = partner_factory("customer.provision", "commission.earn")
    assign(manager, partner, 2500)
    now = timezone.now()
    from workeasy360.commercial.commissions import selectors

    mine = selectors.effective_rule(partner["partner"].id, product_code="crm", at=now)
    theirs = selectors.effective_rule(other["partner"].id, product_code="crm", at=now)
    assert mine["rate_bp"] == 2500 and mine["scope"] == "partner"
    assert theirs is None  # no programme default exists in this fixture


def test_a_partner_rule_beats_the_programme_default(channel, manager):
    from workeasy360.commercial.commissions import selectors

    # The channel fixture has a 10% programme default.
    partner = channel["partner"]
    now = timezone.now()
    before = selectors.effective_rule(partner.id, product_code="crm", at=now)
    assert before["rate_bp"] == 1000 and before["scope"] == "programme_default"
    assign(manager, channel, 1500)
    after = selectors.effective_rule(partner.id, product_code="crm", at=timezone.now())
    assert after["rate_bp"] == 1500 and after["scope"] == "partner"


# --- versions and the effective date ----------------------------------------------------


def test_a_change_appends_a_version_and_retires_its_predecessor(manager, partner):
    first = assign(manager, partner, 2000).rule
    second = assign(manager, partner, 3000, expected=1).rule
    assert (second.version, second.code) == (2, first.code)
    first.refresh_from_db()
    assert first.retired_at is not None and first.rate_bp == 2000  # never edited
    live = CommissionRule.objects.filter(
        partner=partner["partner"], retired_at__isnull=True
    )
    assert [r.version for r in live] == [2]


def test_a_future_change_leaves_todays_rate_in_force_until_then(manager, partner):
    from workeasy360.commercial.commissions import selectors

    assign(manager, partner, 2000)
    when = timezone.now() + timedelta(days=10)
    assign(manager, partner, 4000, expected=1, effective_from=when)
    now = selectors.effective_rule(
        partner["partner"].id, product_code="", at=timezone.now()
    )
    later = selectors.effective_rule(
        partner["partner"].id, product_code="", at=when + timedelta(seconds=1)
    )
    assert now["rate_bp"] == 2000 and later["rate_bp"] == 4000
    states = {
        r["version"]: r["state"]
        for r in selectors.partner_rules(partner["partner"].id, now=timezone.now())
    }
    assert states == {1: "in_force", 2: "scheduled"}


def test_a_change_can_never_take_effect_in_the_past(manager, partner):
    assign(manager, partner, 2000)
    with pytest.raises(PartnerValidationError) as raised:
        assign(
            manager,
            partner,
            3000,
            expected=1,
            effective_from=timezone.now() - timedelta(days=1),
        )
    assert raised.value.context["field"] == "effective_from"
    assert CommissionRule.objects.filter(partner=partner["partner"]).count() == 1


def test_old_sales_keep_the_rate_they_were_computed_under(channel, manager, monkeypatch):
    monkeypatch.setattr(accrual, "holdback_days", lambda: 0)
    result, _owner = provision(channel)
    sell(channel, result.organization_id)
    old = CommissionAccrual.objects.get(partner=channel["partner"])
    assert (old.rate_bp, old.amount_minor) == (1000, 500)
    original = (old.rule_id, old.rule_version, old.rate_bp, old.amount_minor)

    assign(manager, channel, 2500)  # a much higher rate, from now on
    accrual.accrue_for_organization(result.organization_id)  # re-running changes nothing

    old.refresh_from_db()
    assert (old.rule_id, old.rule_version, old.rate_bp, old.amount_minor) == original
    assert CommissionAccrual.objects.filter(partner=channel["partner"]).count() == 1
    # And the reconciliation, which re-derives from the *rules in force at the
    # sale*, still agrees with the ledger.
    assert accrual.reconcile(organization_ids=[result.organization_id]).is_clean


def test_a_later_sale_earns_the_new_rate(channel, manager, monkeypatch):
    monkeypatch.setattr(accrual, "holdback_days", lambda: 0)
    assign(manager, channel, 2500)
    result, _owner = provision(channel)
    sell(channel, result.organization_id)
    row = CommissionAccrual.objects.get(partner=channel["partner"])
    assert (row.rate_bp, row.amount_minor) == (2500, 1250)


# --- optimistic concurrency and idempotency ------------------------------------------------


def test_a_stale_token_is_refused_not_merged(manager, partner):
    assign(manager, partner, 2000)
    assign(manager, partner, 3000, expected=1)
    with pytest.raises(ConcurrentPartnerModification):
        assign(manager, partner, 5000, expected=1)  # read before the second change
    live = CommissionRule.objects.get(partner=partner["partner"], retired_at__isnull=True)
    assert live.rate_bp == 3000


def test_the_token_is_required_and_must_be_a_whole_number(manager, partner):
    for bad in (None, "0", 1.5, True, -1):
        with pytest.raises(PartnerValidationError):
            rules.assign_partner_commission(
                manager,
                partner["partner"].id,
                rate_bp=2000,
                reason=REASON,
                expected_version=bad,
            )
    assert not CommissionRule.objects.filter(partner=partner["partner"]).exists()


def test_repeating_the_rate_already_in_force_is_a_no_op(manager, partner):
    first = assign(manager, partner, 2000)
    again = assign(manager, partner, 2000, expected=0)  # a retry after a lost response
    same = assign(manager, partner, 2000, expected=1)
    assert first.created and not again.created and not same.created
    assert CommissionRule.objects.filter(partner=partner["partner"]).count() == 1


# --- scope, validation and authority --------------------------------------------------------


def test_a_product_scope_is_its_own_chain(manager, partner):
    everything = assign(manager, partner, 2000).rule
    crm = assign(manager, partner, 3500, product_code="crm").rule
    assert everything.code != crm.code and crm.product_code == "crm"
    from workeasy360.commercial.commissions import selectors

    now = timezone.now()
    assert (
        selectors.effective_rule(partner["partner"].id, product_code="crm", at=now)[
            "rate_bp"
        ]
        == 3500
    )
    assert (
        selectors.effective_rule(partner["partner"].id, product_code="books", at=now)[
            "rate_bp"
        ]
        == 2000
    )


def test_a_rate_is_whole_basis_points_between_zero_and_a_hundred_percent(
    manager, partner
):
    for bad in (-1, 10001, 12.5, "1000", True, None):
        with pytest.raises(PartnerValidationError):
            assign(manager, partner, bad)
    assert assign(manager, partner, 0).rule.rate_bp == 0
    assert assign(manager, partner, 10000, expected=1).rule.rate_bp == 10000


def test_only_a_manager_may_assign_and_it_needs_a_second_factor(staff_factory, partner):
    _v, viewer = staff_factory("platform.staff_commission.view")
    with pytest.raises(PermissionDenied):
        assign(viewer, partner, 2000)
    _m, no_step_up = staff_factory("platform.staff_commission.manage", aal=1)
    with pytest.raises(StepUpRequired):
        assign(no_step_up, partner, 2000)
    assert not CommissionRule.objects.filter(partner=partner["partner"]).exists()


def test_a_partner_cannot_assign_itself_a_rate(partner):
    with pytest.raises(PermissionDenied):
        assign(partner["actor"](), partner, 9000)


def test_an_unknown_or_terminated_partner_is_refused(manager, partner):
    from workeasy360.commercial.partners import services
    from workeasy360.contracts.enums import PartnerStatus

    with pytest.raises(PartnerRecordNotFound):
        rules.assign_partner_commission(
            manager, uuid.uuid4(), rate_bp=1000, reason=REASON, expected_version=0
        )
    services.set_partner_status(
        partner["staff_ctx"],
        partner["partner"].id,
        target=PartnerStatus.TERMINATED,
        reason="Terminated for the commission test",
    )
    with pytest.raises(PartnerValidationError):
        assign(manager, partner, 1000)


def test_the_reason_is_required_and_audited(manager, partner):
    with pytest.raises(PartnerValidationError):
        assign(manager, partner, 2000, reason="short")
    result = assign(manager, partner, 2000)
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.authz.control_plane import acting_scope

    with acting_scope(manager):
        actions = list(
            AuditEvent.objects.filter(resource_id=str(result.rule.id)).values_list(
                "action", flat=True
            )
        )
    assert "commission.rule_created" in actions
    assert "commission.partner_rule_assigned" in actions


# --- over HTTP ------------------------------------------------------------------------------------


def _post(client, url, body):
    import json

    return client.post(url, json.dumps(body), content_type="application/json")


def test_the_endpoint_reads_and_assigns_and_forged_fields_are_ignored(
    partner, staff_client
):
    client, user = staff_client("platform.staff_commission.manage")
    url = f"/api/v1/admin/partners/{partner['partner'].id}/commission"
    state = client.get(url).json()
    assert (
        state["rules"] == [] and state["live_versions"] == {} and state["partner_earns"]
    )
    response = _post(
        client,
        url,
        {
            "rate_bp": 1800,
            "reason": REASON,
            "expected_version": 0,
            # None of these may be honoured:
            "partner_id": str(uuid.uuid4()),
            "created_by": str(uuid.uuid4()),
            "version": 99,
            "retired_at": "2020-01-01T00:00:00Z",
            "code": "attacker-chosen",
        },
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["version"] == 1 and body["created"] is True
    row = CommissionRule.objects.get(id=body["id"])
    assert row.partner_id == partner["partner"].id and row.created_by_id == user.id
    assert row.code != "attacker-chosen"
    after = client.get(url).json()
    assert after["live_versions"] == {"": 1}
    assert after["in_force"]["rate_bp"] == 1800


def test_the_endpoint_needs_step_up_and_answers_409_on_a_stale_token(
    partner, staff_client
):
    url = f"/api/v1/admin/partners/{partner['partner'].id}/commission"
    weak, _u = staff_client("platform.staff_commission.manage", aal2=False)
    response = _post(
        weak, url, {"rate_bp": 1800, "reason": REASON, "expected_version": 0}
    )
    assert response.status_code == 401 and response.json()["code"] == "step_up_required"
    client, _u = staff_client("platform.staff_commission.manage")
    assert (
        _post(
            client, url, {"rate_bp": 1800, "reason": REASON, "expected_version": 0}
        ).status_code
        == 201
    )
    stale = _post(client, url, {"rate_bp": 2100, "reason": REASON, "expected_version": 0})
    assert stale.status_code == 409 and stale.json()["code"] == "concurrent_modification"


def test_a_viewer_can_read_but_not_assign(partner, staff_client):
    client, _u = staff_client("platform.staff_commission.view")
    url = f"/api/v1/admin/partners/{partner['partner'].id}/commission"
    assert client.get(url).status_code == 200
    assert (
        _post(
            client, url, {"rate_bp": 1, "reason": REASON, "expected_version": 0}
        ).status_code
        == 403
    )


def test_a_partner_user_cannot_reach_the_staff_commission_endpoint(partner_client):
    client, fx = partner_client("commission.view")
    url = f"/api/v1/admin/partners/{fx['partner'].id}/commission"
    assert client.get(url).status_code == 403
    assert connection.vendor == "postgresql"

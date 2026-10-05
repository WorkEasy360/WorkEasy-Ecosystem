"""Phase 7 concurrency: real threads, real connections, real commits.

Every test here uses ``transaction=True`` - a rollback-wrapped test gives each
thread a different, invisible view of the data and proves nothing. A test
passes only when the *invariant* holds, not merely when the code ran.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from django.db import connections
from django.utils import timezone

from tests.phase7.support import links_for, provision, sell
from workeasy360.commercial.commissions import accrual, payouts
from workeasy360.commercial.commissions.models import CommissionAccrual, Payout
from workeasy360.commercial.partners import customers, domains, selectors, services
from workeasy360.contracts.enums import PartnerLinkStatus

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.concurrency]

WORKERS = 8


def _run(worker, count=WORKERS):
    results: list = []
    errors: list = []
    lock = threading.Lock()
    barrier = threading.Barrier(count)

    def wrapped(index: int) -> None:
        try:
            barrier.wait(timeout=30)
            outcome = worker(index)
            with lock:
                results.append(outcome)
        except Exception as exc:
            with lock:
                errors.append(exc)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=count) as pool:
        for future in as_completed([pool.submit(wrapped, i) for i in range(count)]):
            future.result()
    return results, errors


# --- commission -----------------------------------------------------------


def test_eight_way_accrual_creates_exactly_one_commission(channel, monkeypatch):
    result, _o = provision(channel)
    # The listener is silenced so the accrual is left for the race to make.
    monkeypatch.setattr(accrual, "accrue_for_organization", lambda *a, **k: None)
    sell(channel, result.organization_id)
    monkeypatch.undo()
    assert CommissionAccrual.objects.count() == 0

    results, errors = _run(
        lambda i: accrual.accrue_for_organization(result.organization_id)
    )
    assert errors == []
    assert sum(r.created for r in results) == 1
    assert sum(r.duplicates for r in results) == WORKERS - 1
    assert CommissionAccrual.objects.count() == 1


def test_eight_way_duplicate_sale_event_creates_one_commission(channel):
    """The same commercial event delivered eight times pays once."""
    result, _o = provision(channel)
    sell(channel, result.organization_id)
    assert CommissionAccrual.objects.count() == 1
    from workeasy360.commercial.subscriptions.hooks import notify_commercial_change

    _run(lambda i: notify_commercial_change(result.organization_id))
    assert CommissionAccrual.objects.count() == 1


def test_racing_manual_reversals_never_exceed_the_entry(channel):
    from workeasy360.commercial.commissions import ledger
    from workeasy360.contracts.errors import PartnerValidationError

    result, _o = provision(channel)
    sell(channel, result.organization_id)
    original = CommissionAccrual.objects.get(kind="accrual")

    def worker(i):
        try:
            ledger.reverse_accrual(
                channel["commission_ctx"],
                original.id,
                reason_code="correction",
                reason="Racing partial correction",
                idempotency_key=f"k{i}",
                amount_minor=300,
            )
            return "ok"
        except PartnerValidationError:
            return "refused"

    results, errors = _run(worker)
    assert errors == []
    total = sum(r.amount_minor for r in CommissionAccrual.objects.filter(kind="reversal"))
    assert total >= -original.amount_minor  # never more than was earned
    assert results.count("ok") == 1  # 300 fits once in 500


def test_payout_preparation_race_makes_one_payout_and_no_double_payment(owed_race):
    fx = owed_race

    def worker(i):
        try:
            return payouts.prepare_payout(
                fx["prep"],
                partner_id=fx["partner"].id,
                period_end=timezone.now().date(),
                idempotency_key=f"race-{i}",
            ).id
        except Exception as exc:
            return type(exc).__name__

    results, errors = _run(worker)
    assert errors == []
    assert Payout.objects.count() == 1
    from workeasy360.commercial.commissions.models import PayoutLine

    assert PayoutLine.objects.filter(released_at__isnull=True).count() == 1


def test_payout_approval_race_has_one_approval(owed_race):
    fx = owed_race
    payout = payouts.prepare_payout(
        fx["prep"],
        partner_id=fx["partner"].id,
        period_end=timezone.now().date(),
        idempotency_key="p",
    )
    payouts.mark_payout_ready(fx["prep"], payout.id)

    def worker(i):
        ctx = fx["approvers"][i % len(fx["approvers"])]
        return payouts.approve_payout(
            ctx, payout.id, reason="Racing approval of one payout"
        ).status

    results, errors = _run(worker, count=4)
    assert errors == []
    assert set(results) == {"approved"}
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="Test counts approval audit entries", module="tests"),
        organization_db_scope(PLATFORM_ORGANIZATION_ID),
    ):
        approvals = AuditEvent.unscoped.filter(
            organization_id=PLATFORM_ORGANIZATION_ID, action="payout.approved"
        ).count()
    assert approvals == 1


@pytest.fixture
def owed_race(channel, staff_factory, monkeypatch):
    monkeypatch.setattr(accrual, "holdback_days", lambda: 0)
    result, _o = provision(channel)
    sell(channel, result.organization_id)
    _pu, prep = staff_factory("platform.staff_payout.manage")
    approvers = [staff_factory("platform.staff_payout.approve")[1] for _ in range(4)]
    return {**channel, "prep": prep, "approvers": approvers}


# --- provisioning and relationship ---------------------------------------


def test_same_provisioning_request_eight_times_makes_one_customer(channel):
    owner = channel["user_factory"]()
    key = uuid.uuid4().hex

    def worker(i):
        return customers.provision_customer(
            channel["actor"](),
            organization_name="Race Co",
            owner_email=owner.email,
            idempotency_key=key,
        ).organization_id

    results, errors = _run(worker)
    assert errors == []
    assert len(set(results)) == 1
    assert len(links_for(channel["partner"].id)) == 1


def test_no_partner_write_commits_after_the_link_ends(channel):
    """Sales racing the end of the relationship: any sale that commits did so
    before the link ended, never after."""
    from workeasy360.commercial.subscriptions.selectors import sale_facts

    result, _o = provision(channel)
    link = selectors.link_for(channel["partner"].id, result.organization_id)

    def worker(i):
        if i == 0:
            services.change_link_status(
                channel["staff_ctx"],
                link.id,
                target=PartnerLinkStatus.ENDED,
                reason="Ending mid-sale now",
            )
            return "ended"
        try:
            sell(channel, result.organization_id, key=f"sale-{i}")
            return "sold"
        except Exception as exc:
            return type(exc).__name__

    results, errors = _run(worker, count=4)
    assert errors == []
    ended_at = links_for(channel["partner"].id, live_only=False)[0].ended_at
    assert ended_at is not None
    for fact in sale_facts(result.organization_id):
        assert fact.added_at <= ended_at


def test_racing_partner_updates_have_one_winner(channel):
    version = channel["partner"].version

    def worker(i):
        try:
            services.update_partner(
                channel["staff_ctx"],
                channel["partner"].id,
                expected_version=version,
                display_name=f"Name {i}",
            )
            return "ok"
        except services.ConcurrentPartnerModification:
            return "stale"

    results, errors = _run(worker)
    assert errors == []
    assert results.count("ok") == 1 and results.count("stale") == WORKERS - 1


def test_capability_grant_revoke_race_leaves_a_consistent_state(channel):
    from workeasy360.commercial.partners.models import PartnerCapabilityGrant
    from workeasy360.contracts.errors import PartnerRecordNotFound

    def worker(i):
        try:
            if i % 2 == 0:
                services.grant_capability(
                    channel["staff_ctx"],
                    channel["partner"].id,
                    capability="branding.manage",
                    reason="Racing grant of a capability",
                )
            else:
                services.revoke_capability(
                    channel["staff_ctx"],
                    channel["partner"].id,
                    capability="branding.manage",
                    reason="Racing revoke of a capability",
                )
            return "ok"
        except PartnerRecordNotFound:
            return "nothing to revoke"

    _results, errors = _run(worker)
    assert errors == []
    live = PartnerCapabilityGrant.objects.filter(
        partner=channel["partner"], capability="branding.manage", status="active"
    ).count()
    assert live in (0, 1)
    held = "branding.manage" in selectors.active_capabilities(channel["partner"].id)
    assert held == (live == 1)


def test_removing_the_last_security_administrator_races_safely(staff_factory):
    from workeasy360.contracts.errors import ControlPlaneInvariantViolation
    from workeasy360.platform.authz import staff

    a, _ = staff_factory("platform.staff_security.manage")
    b, _ = staff_factory("platform.staff_security.manage")
    ga = staff.live_grants(user_id=a.id)[0]["id"]
    gb = staff.live_grants(user_id=b.id)[0]["id"]

    def worker(i):
        try:
            staff.end_grant(
                grant_id=(ga, gb)[i],
                revoked_by_id=(b, a)[i].id,
                reason="Mutual removal in a race",
            )
            return "removed"
        except ControlPlaneInvariantViolation:
            return "refused"

    results, errors = _run(worker, count=2)
    assert errors == []
    assert results.count("removed") == 1 and results.count("refused") == 1
    assert len(staff.live_grants()) >= 1


def test_two_partners_racing_to_verify_one_domain_produce_one_owner(
    partner_factory, monkeypatch
):
    from workeasy360.commercial.partners.models import PartnerDomain

    one = partner_factory("branding.manage", "domain.manage")
    two = partner_factory("branding.manage", "domain.manage")
    from workeasy360.commercial.partners import branding

    for fx in (one, two):
        branding.update_brand(fx["actor"](), brand_name="Brand")
    host = "app.contested-example.com"
    da = domains.request_domain(one["actor"](), hostname=host)
    db = domains.request_domain(two["actor"](), hostname=host)

    class BothTokens:
        def txt_values(self, name):
            return [da.verification_token, db.verification_token]

    domains.set_txt_resolver(BothTokens())
    try:

        def worker(i):
            fx, dom = ((one, da), (two, db))[i]
            return domains.verify_domain(fx["actor"](), domain_id=dom.id).status

        results, errors = _run(worker, count=2)
    finally:
        domains.set_txt_resolver(domains.DnsPythonResolver())
    assert errors == []
    assert sorted(results) == ["failed", "verified"]
    proven = PartnerDomain.objects.filter(
        hostname=host, status__in=["verified", "active"]
    ).count()
    assert proven == 1


def test_domain_activation_race_activates_once(partner_factory):
    from workeasy360.commercial.partners import branding

    fx = partner_factory("branding.manage", "domain.manage")
    branding.update_brand(fx["actor"](), brand_name="Brand")
    domain = domains.request_domain(fx["actor"](), hostname="www.race-example.com")

    class Proves:
        def txt_values(self, name):
            return [domain.verification_token]

    domains.set_txt_resolver(Proves())
    try:
        domains.verify_domain(fx["actor"](), domain_id=domain.id)

        def worker(i):
            return domains.activate_domain(fx["actor"](), domain_id=domain.id).status

        results, errors = _run(worker)
    finally:
        domains.set_txt_resolver(domains.DnsPythonResolver())
    assert errors == []
    assert set(results) == {"active"}


def test_one_prepare_key_sent_eight_times_at_once_replays_one_payout(owed_race):
    """Final audit: the key is checked under the partner lock, so the losers of
    a same-key race wait and then replay the winner - no 'nothing payable'
    refusal and no unique-key error for a request that was merely repeated."""
    fx = owed_race

    def worker(i):
        return payouts.prepare_payout(
            fx["prep"],
            partner_id=fx["partner"].id,
            period_end=timezone.now().date(),
            idempotency_key="same-key",
        ).id

    results, errors = _run(worker)
    assert errors == []
    assert len(set(results)) == 1 and Payout.objects.count() == 1

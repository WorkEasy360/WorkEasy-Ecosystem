"""Phase 10b concurrency: real threads, real connections, real commits.

A rollback-wrapped test gives each thread a different, invisible view of the
data and proves nothing, so every test here commits for real.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from django.db import connections

from workeasy360.commercial.commissions import rules
from workeasy360.commercial.commissions.models import CommissionRule
from workeasy360.commercial.partners.services import ConcurrentPartnerModification

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.concurrency]

WORKERS = 8
REASON = "Racing commission assignment"


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


@pytest.fixture
def world(partner_factory, staff_factory):
    fx = partner_factory("customer.provision", "commission.earn")
    _u, manager = staff_factory("platform.staff_commission.manage")
    return fx, manager


def test_eight_simultaneous_first_assignments_make_one_rule(world):
    """All eight read 'no rule yet' and want different rates. The advisory lock
    on (partner, product) serialises them: one creates the chain, the other
    seven find it and are refused as stale - never a second parallel chain."""
    fx, manager = world

    def worker(i):
        try:
            rules.assign_partner_commission(
                manager,
                fx["partner"].id,
                rate_bp=1000 + i * 100,
                reason=REASON,
                expected_version=0,
            )
            return "assigned"
        except ConcurrentPartnerModification:
            return "stale"

    results, errors = _run(worker)
    assert errors == []
    assert results.count("assigned") == 1 and results.count("stale") == WORKERS - 1
    rows = CommissionRule.objects.filter(partner=fx["partner"])
    assert rows.count() == 1
    assert rows.filter(retired_at__isnull=True).count() == 1
    assert len({r.code for r in rows}) == 1


def test_eight_simultaneous_changes_from_one_version_make_one_new_version(world):
    fx, manager = world
    rules.assign_partner_commission(
        manager, fx["partner"].id, rate_bp=1000, reason=REASON, expected_version=0
    )

    def worker(i):
        try:
            rules.assign_partner_commission(
                manager,
                fx["partner"].id,
                rate_bp=2000 + i * 100,
                reason=REASON,
                expected_version=1,
            )
            return "assigned"
        except ConcurrentPartnerModification:
            return "stale"

    results, errors = _run(worker)
    assert errors == []
    assert results.count("assigned") == 1 and results.count("stale") == WORKERS - 1
    versions = sorted(
        CommissionRule.objects.filter(partner=fx["partner"]).values_list(
            "version", flat=True
        )
    )
    assert versions == [1, 2]
    assert (
        CommissionRule.objects.filter(
            partner=fx["partner"], retired_at__isnull=True
        ).count()
        == 1
    )


def test_eight_identical_retries_are_one_assignment(world):
    fx, manager = world

    def worker(i):
        return rules.assign_partner_commission(
            manager,
            fx["partner"].id,
            rate_bp=1750,
            reason=REASON,
            expected_version=0,
        ).created

    results, errors = _run(worker)
    assert errors == []
    assert results.count(True) == 1 and results.count(False) == WORKERS - 1
    assert CommissionRule.objects.filter(partner=fx["partner"]).count() == 1

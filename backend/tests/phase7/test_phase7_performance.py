"""Phase 7 read paths at 10 / 100 / 500 rows: query count, latency, and plan.

Every list in the control plane is a keyset page, so the cost of *one page*
must not depend on how many rows exist. This test proves it three ways, for
each list, at three table sizes:

1. **Query count is identical at every size** (the N+1 check - a per-row lookup
   would make it grow).
2. **Latency** over repeated calls through the real HTTP stack (printed; run
   with ``-s``; recorded in docs/phase-7/07-performance.md).
3. **The plan can use an index.** The page's own SQL - captured from the real
   request, not hand-written - is run under ``EXPLAIN`` with sequential scans
   disabled; if that plan still needs a ``Seq Scan`` of the big table, no index
   serves the query and the test fails. (A near-empty table is always
   sequentially scanned by the planner, correctly, so the unforced plan says
   nothing at these sizes.)
"""

from __future__ import annotations

import json
import statistics
import time
import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

pytestmark = [pytest.mark.django_db, pytest.mark.slow]

SIZES = (10, 100, 500)
REPEATS = 7


def _measure(client, url):
    """(query count, p50 ms, max ms, response) for a warm GET."""
    client.get(url)  # warm caches and the session
    timings = []
    for _ in range(REPEATS):
        start = time.perf_counter()
        response = client.get(url)
        timings.append((time.perf_counter() - start) * 1000)
        assert response.status_code == 200, response.content[:200]
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url)
    return (
        len(queries.captured_queries),
        statistics.median(timings),
        max(timings),
        response,
        [q["sql"] for q in queries.captured_queries],
    )


def _seq_scans(node, table):
    """Every Seq Scan of ``table`` anywhere in an EXPLAIN (FORMAT JSON) tree."""
    found = []
    if isinstance(node, list):
        for item in node:
            found += _seq_scans(item, table)
    elif isinstance(node, dict):
        if node.get("Node Type") == "Seq Scan" and node.get("Relation Name") == table:
            found.append(node)
        for value in node.values():
            found += _seq_scans(value, table)
    return found


def _assert_indexable(sqls, table):
    """The page query on ``table`` has a plan with no Seq Scan when scans are off."""
    candidates = [s for s in sqls if f'FROM "{table}"' in s and "LIMIT" in s]
    assert candidates, (
        f"no LIMIT query on {table} was issued; got: {[q[:200] for q in sqls]}"
    )
    with connection.cursor() as cur:
        cur.execute("SET enable_seqscan = off")
        try:
            for sql in candidates:
                cur.execute("EXPLAIN (FORMAT JSON) " + sql)
                plan = cur.fetchone()[0]
                if isinstance(plan, str):
                    plan = json.loads(plan)
                assert not _seq_scans(plan, table), (
                    f"{table}: page query needs a sequential scan even when discouraged: {sql}"
                )
        finally:
            cur.execute("SET enable_seqscan = on")


def _report(label, results):
    print(f"\n  {label}")
    for size, (queries, p50, worst) in results.items():
        print(
            f"    rows={size:<4} queries={queries:<3} p50={p50:6.1f}ms max={worst:6.1f}ms"
        )


def _run(label, client, url, grow, table):
    """Grow the data to each size, measure, and assert the shape is constant."""
    results, counts = {}, set()
    last_sqls = []
    for size in SIZES:
        grow(size)
        queries, p50, worst, response, sqls = _measure(client, url)
        results[size] = (queries, p50, worst)
        counts.add(queries)
        last_sqls = sqls
        assert response.json()["results"], f"{label}: empty page at {size} rows"
    _report(label, results)
    assert len(counts) == 1, f"{label}: query count changes with data size: {results}"
    if table:
        _assert_indexable(last_sqls, table)
    return results


# --- fixtures ------------------------------------------------------------------------------


@pytest.fixture
def staff(staff_client):
    client, user = staff_client(
        "platform.staff_customer.view",
        "platform.staff_partner.view",
        "platform.staff_commission.view",
        "platform.staff_audit.view",
    )
    return client, user


def _organizations(count, *, owner):
    from workeasy360.platform.organizations.models import Organization
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    stamp = uuid.uuid4().hex[:6]
    rows = [
        Organization(
            slug=f"perf-{stamp}-{i}",
            legal_name=f"Perf {i}",
            display_name=f"Perf {stamp} {i}",
            status="active",
            created_by=owner,
        )
        for i in range(count)
    ]
    with unscoped_access(reason="Performance fixture", module="tests.performance"):
        return Organization.objects.bulk_create(rows, batch_size=250)


# --- the tests -------------------------------------------------------------------------------


def test_the_customer_directory_is_constant_cost(staff, user_factory):
    from workeasy360.commercial.provisioning.models import CustomerSummary

    client, user = staff
    owner = user_factory()
    made = []

    def grow(target):
        new = _organizations(target - len(made), owner=owner)
        CustomerSummary.objects.bulk_create(
            [
                CustomerSummary(
                    organization_id=o.id,
                    subscription_status="active",
                    product_codes=["crm"],
                    seat_capacity=5,
                    computed_at=timezone.now(),
                )
                for o in new
            ]
        )
        made.extend(new)

    _run(
        "admin customer directory (limit 25)",
        client,
        "/api/v1/admin/customers?limit=25",
        grow,
        "organizations_organization",
    )
    # A page of 100 costs the same number of queries as a page of 25.
    small = _measure(client, "/api/v1/admin/customers?limit=25")[0]
    large = _measure(client, "/api/v1/admin/customers?limit=100")[0]
    assert small == large


def test_the_partner_list_is_constant_cost(staff, staff_factory):
    from workeasy360.commercial.partners.models import PartnerAccount

    client, user = staff
    made = []

    def grow(target):
        stamp = uuid.uuid4().hex[:6]
        rows = [
            PartnerAccount(
                code=f"pf-{stamp}-{i}",
                display_name=f"Partner {i}",
                payout_currency="GBP",
                status="active",
                created_by=user,
            )
            for i in range(target - len(made))
        ]
        made.extend(PartnerAccount.objects.bulk_create(rows, batch_size=250))

    _run(
        "admin partner list (limit 25)",
        client,
        "/api/v1/admin/partners?limit=25",
        grow,
        "partners_account",
    )


@pytest.fixture
def ledger_world(channel, staff_client):
    """A partner, an admin view of all rows, and a helper that grows the ledger."""
    from workeasy360.commercial.commissions.models import CommissionAccrual

    fx = channel
    made = []

    def grow(target):
        base = timezone.now() - timedelta(days=400)
        rows = [
            CommissionAccrual(
                partner_id=fx["partner"].id,
                organization_id=uuid.uuid4(),
                kind="accrual",
                amount_minor=500,
                currency="GBP",
                source_ref=f"perf:{uuid.uuid4()}",
                event_at=base + timedelta(minutes=len(made) + i),
                clears_at=base + timedelta(days=30, minutes=len(made) + i),
                idempotency_key=f"perf:{uuid.uuid4()}",
            )
            for i in range(target - len(made))
        ]
        made.extend(CommissionAccrual.objects.bulk_create(rows, batch_size=250))

    return fx, grow


def test_the_staff_ledger_is_constant_cost(ledger_world, staff):
    _fx, grow = ledger_world
    client, _u = staff
    _run(
        "admin commission ledger (limit 25)",
        client,
        "/api/v1/admin/commission/accruals?limit=25",
        grow,
        "commissions_accrual",
    )


def test_the_partner_ledger_is_constant_cost(ledger_world, http_login):
    from tests.phase7.conftest import PASSWORD

    fx, grow = ledger_world
    fx["user"].set_password(PASSWORD)
    fx["user"].save(update_fields=["password"])
    client = http_login(fx["user"])
    _run(
        "partner commission ledger (limit 25)",
        client,
        f"/api/v1/partner/{fx['partner'].id}/commissions?limit=25",
        grow,
        "commissions_accrual",
    )


def test_the_partner_customer_list_is_constant_cost(channel, http_login, user_factory):
    from tests.phase7.conftest import PASSWORD
    from workeasy360.commercial.partners.models import PartnerOrganizationLink

    fx = channel
    fx["user"].set_password(PASSWORD)
    fx["user"].save(update_fields=["password"])
    client = http_login(fx["user"])
    owner = user_factory()
    made = []

    def grow(target):
        new = _organizations(target - len(made), owner=owner)
        PartnerOrganizationLink.objects.bulk_create(
            [
                PartnerOrganizationLink(
                    partner_id=fx["partner"].id,
                    organization_id=o.id,
                    status="active",
                    started_at=timezone.now(),
                    created_by=owner,
                )
                for o in new
            ]
        )
        made.extend(new)

    _run(
        "partner customer list (limit 25)",
        client,
        f"/api/v1/partner/{fx['partner'].id}/customers?limit=25",
        grow,
        "partners_organization_link",
    )


def test_the_payout_list_is_constant_cost(channel, staff, staff_factory):
    from workeasy360.commercial.commissions.models import Payout

    fx = channel
    client, user = staff
    made = []

    def grow(target):
        rows = [
            Payout(
                partner_id=fx["partner"].id,
                currency="GBP",
                period_end=timezone.now().date() - timedelta(days=len(made) + i),
                status="draft",
                gross_minor=1000,
                payable_minor=1000,
                line_count=1,
                created_by=user,
                idempotency_key=f"perf:{uuid.uuid4()}",
            )
            for i in range(target - len(made))
        ]
        made.extend(Payout.objects.bulk_create(rows, batch_size=250))

    _run(
        "admin payout list (limit 25)",
        client,
        "/api/v1/admin/payouts?limit=25",
        grow,
        "commissions_payout",
    )


# --- the other hot lookups (Task 121): every one must be servable by an index ------------------


def _explain_selects(sqls, table):
    """No statement that reads ``table`` needs a Seq Scan of it when scans are discouraged."""
    found = [
        s
        for s in sqls
        if s.lstrip().upper().startswith(("SELECT", "UPDATE")) and f'"{table}"' in s
    ]
    assert found, f"no SELECT or UPDATE on {table} was issued"
    with connection.cursor() as cur:
        cur.execute("SET enable_seqscan = off")
        try:
            for sql in found:
                cur.execute("EXPLAIN (FORMAT JSON) " + sql)
                plan = cur.fetchone()[0]
                if isinstance(plan, str):
                    plan = json.loads(plan)
                assert not _seq_scans(plan, table), (
                    f"{table}: needs a sequential scan: {sql[:300]}"
                )
        finally:
            cur.execute("SET enable_seqscan = on")


def _capture(fn):
    with CaptureQueriesContext(connection) as queries:
        fn()
    return [q["sql"] for q in queries.captured_queries]


def test_every_other_hot_lookup_is_index_served(
    channel, monkeypatch, staff_factory, organization_factory
):
    """Partner status, partner-customer relation, partner-product grant, commission
    eligibility, payout status, domain lookup and referral conversion."""
    from tests.phase7.support import provision, sell
    from workeasy360.commercial.commissions import accrual, payouts, referrals
    from workeasy360.commercial.commissions import selectors as commission_selectors
    from workeasy360.commercial.partners import domains
    from workeasy360.commercial.partners import selectors as partner_selectors

    fx = channel
    monkeypatch.setattr(accrual, "holdback_days", lambda: 0)
    result, owner = provision(fx)
    sell(fx, result.organization_id)
    _u, prep = staff_factory("platform.staff_payout.manage")

    # partner status
    sqls = _capture(lambda: partner_selectors.partners_page(status="active", limit=25))
    _explain_selects(sqls, "partners_account")

    # partner <-> customer relation (both directions)
    sqls = _capture(
        lambda: partner_selectors.partner_customers_page(fx["partner"].id, limit=25)
    )
    _explain_selects(sqls, "partners_organization_link")
    sqls = _capture(
        lambda: partner_selectors.live_link_for_organization(result.organization_id)
    )
    _explain_selects(sqls, "partners_organization_link")

    # partner <-> product grant
    sqls = _capture(
        lambda: partner_selectors.may_sell(
            fx["partner"].id, product_code="crm", plan_code="crm_professional"
        )
    )
    _explain_selects(sqls, "partners_product_grant")

    # commission eligibility and payout status
    sqls = _capture(
        lambda: payouts.prepare_payout(
            prep,
            partner_id=fx["partner"].id,
            period_end=timezone.now().date(),
            idempotency_key="explain",
        )
    )
    _explain_selects(sqls, "commissions_accrual")
    sqls = _capture(
        lambda: commission_selectors.payouts_page(
            partner_id=None, for_partner=False, status="draft"
        )
    )
    _explain_selects(sqls, "commissions_payout")

    # domain lookup by hostname
    domains.invalidate_host("app.explain-example.com")
    sqls = _capture(lambda: domains.public_brand_for_host("app.explain-example.com"))
    _explain_selects(sqls, "partners_domain")

    # referral conversion by code / token
    from workeasy360.commercial.partners import services as partner_services

    partner_services.grant_capability(
        fx["staff_ctx"],
        fx["partner"].id,
        capability="referral.create",
        reason="Fixture capability for the plan check",
    )
    code = referrals.create_referral_code(fx["actor"](), label="explain")
    token = referrals.capture_referral(code.code)
    assert token, "the fixture must capture a referral or the plan check is vacuous"
    referred_org, _membership, referred_owner = organization_factory()
    sqls = _capture(
        lambda: referrals.convert_referral(
            user_id=referred_owner.id, token=token, organization_id=referred_org.id
        )
    )
    _explain_selects(sqls, "commissions_referral")

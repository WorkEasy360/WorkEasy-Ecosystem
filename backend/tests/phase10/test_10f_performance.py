"""Phase 10f performance: the consoles at 10, 100 and 500 partners.

**What this is, and is not.** It pins the *shape* of the cost: every console
read answers in the same number of queries whether there are 10 partners or 500
(no N+1), and it prints the median time it took here, on one developer machine,
inside a rollback-wrapped test, through Django's test client. It makes **no
capacity claim** - the times are not a service level and say nothing about a
production database, connection pool or concurrent load. Run with ``-s`` to see
the table.

The data is grown with bulk inserts into the tables the consoles read (partners,
capability grants, product grants, brands, domains, customer links, owner
invitations, the customer summary projection, ledger rows and payouts), so the
queries run against realistic joins and not against empty tables.
"""

from __future__ import annotations

import statistics
import time
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.phase10.conftest import VIEW, count_queries

pytestmark = [pytest.mark.django_db, pytest.mark.slow]

SIZES = (10, 100, 500)
CUSTOMERS_PER_PARTNER = 3
RUNS = 5

RESELLER = (
    "customer.provision",
    "subscription.sell",
    "commission.earn",
    "commission.view",
)
WHITE_LABEL = ("branding.manage", "domain.manage")


@pytest.fixture
def world(channel, staff_factory):
    """The Phase 7 channel (catalog, product, default commission rule) plus a
    place to grow partners."""
    user = channel["user"]
    return {"creator": user, "channel": channel}


def _grow_to(total: int, world) -> None:
    from workeasy360.commercial.catalog.models import Product
    from workeasy360.commercial.commissions.models import (
        CommissionAccrual,
        CommissionRule,
        Payout,
    )
    from workeasy360.commercial.partners.models import (
        PartnerAccount,
        PartnerBrand,
        PartnerCapabilityGrant,
        PartnerDomain,
        PartnerOrganizationLink,
        PartnerOwnerInvite,
        PartnerProductGrant,
    )
    from workeasy360.commercial.provisioning.models import CustomerSummary
    from workeasy360.platform.organizations.models import Organization
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    creator = world["creator"]
    now = timezone.now()
    have = PartnerAccount.objects.filter(code__startswith="perf-").count()
    if have >= total:
        return
    product = Product.objects.get(code="crm")
    rule = CommissionRule.objects.filter(code="default").order_by("-version").first()

    partners = [
        PartnerAccount(
            code=f"perf-{i:05d}",
            display_name=f"Perf Partner {i}",
            legal_name=f"Perf Partner {i} Ltd",
            payout_currency="GBP",
            status="active" if i % 7 else "suspended",
            created_by=creator,
            activated_at=now,
        )
        for i in range(have, total)
    ]
    PartnerAccount.objects.bulk_create(partners, batch_size=500)

    grants, product_grants, brands, domains = [], [], [], []
    for n, partner in enumerate(partners):
        caps = RESELLER + (WHITE_LABEL if n % 3 == 0 else ())
        grants += [
            PartnerCapabilityGrant(
                partner=partner,
                capability=cap,
                status="active",
                granted_by=creator,
                granted_at=now,
                reason="Perf seed",
            )
            for cap in caps
        ]
        product_grants.append(
            PartnerProductGrant(
                partner=partner,
                product=product,
                status="active",
                granted_by=creator,
                granted_at=now,
            )
        )
        if n % 3 == 0:
            brands.append(
                PartnerBrand(
                    partner=partner,
                    brand_name=f"Brand {partner.code}",
                    accent_color="#1a56db",
                    updated_by=creator,
                )
            )
            domains.append(
                PartnerDomain(
                    partner=partner,
                    hostname=f"{partner.code}.perf-brand.com",
                    status=("requested", "verification_pending", "verified", "active")[
                        n % 4
                    ],
                    verification_token=f"w360-verify={uuid.uuid4().hex}",
                    requested_by=creator,
                    verified_at=now if n % 4 >= 2 else None,
                    activated_at=now if n % 4 == 3 else None,
                )
            )
    PartnerCapabilityGrant.objects.bulk_create(grants, batch_size=1000)
    PartnerProductGrant.objects.bulk_create(product_grants, batch_size=1000)
    PartnerBrand.objects.bulk_create(brands, batch_size=500)
    PartnerDomain.objects.bulk_create(domains, batch_size=500)

    with unscoped_access(
        reason="Perf test seeds customer organizations", module="tests.phase10"
    ):
        orgs = [
            Organization(
                slug=f"perf-{uuid.uuid4().hex[:14]}",
                legal_name=f"Perf Customer {i}",
                display_name=f"Perf Customer {i}",
                status="active",
                created_by=creator,
            )
            for i in range(len(partners) * CUSTOMERS_PER_PARTNER)
        ]
        Organization.objects.bulk_create(orgs, batch_size=1000)
        links, invites, summaries = [], [], []
        it = iter(orgs)
        for partner in partners:
            for k in range(CUSTOMERS_PER_PARTNER):
                org = next(it)
                links.append(
                    PartnerOrganizationLink(
                        partner=partner,
                        organization=org,
                        status="active" if k else "suspended",
                        started_at=now - timedelta(days=k + 1),
                        created_by=creator,
                    )
                )
                invites.append(
                    PartnerOwnerInvite(
                        organization=org,
                        user=creator,
                        partner=partner,
                        status=("pending", "accepted", "revoked")[k % 3],
                        expires_at=now + timedelta(days=14 - 20 * (k % 2)),
                    )
                )
                summaries.append(
                    CustomerSummary(
                        organization=org,
                        subscription_status=("active", "trialing", "")[k % 3],
                        product_codes=["crm"],
                        seat_capacity=5,
                        access_class="customer",
                        computed_at=now,
                    )
                )
        PartnerOrganizationLink.objects.bulk_create(links, batch_size=1000)
        PartnerOwnerInvite.objects.bulk_create(invites, batch_size=1000)
        CustomerSummary.objects.bulk_create(summaries, batch_size=1000)

    CommissionAccrual.objects.bulk_create(
        [
            CommissionAccrual(
                partner=partner,
                kind="accrual",
                amount_minor=500 + j,
                currency="GBP",
                source_ref=f"perf:{partner.code}:{j}",
                event_at=now - timedelta(days=40 + j),
                clears_at=now - timedelta(days=10 + j),
                rule=rule,
                rule_version=rule.version,
                rate_bp=1000,
                basis_minor=5000,
                basis_currency="GBP",
                attribution_kind="link",
                attribution_ref=str(uuid.uuid4()),
                created_by=creator,
                idempotency_key=f"perf-{uuid.uuid4().hex}",
            )
            for partner in partners
            for j in range(2)
        ],
        batch_size=1000,
    )
    Payout.objects.bulk_create(
        [
            Payout(
                partner=partner,
                currency="GBP",
                period_end=now.date(),
                status=("draft", "ready", "approved")[n % 3] if n % 2 else "draft",
                gross_minor=1000,
                payable_minor=1000,
                line_count=2,
                created_by=creator,
                idempotency_key=f"perf-payout-{uuid.uuid4().hex}",
            )
            for n, partner in enumerate(partners)
        ],
        batch_size=1000,
    )


def _grow_audit_to(total: int, partner_ids: list) -> None:
    """Audit entries that name a partner in ``changes`` (as every partner action's does)."""
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    now = timezone.now()
    with (
        tenant_context(routing_context(PLATFORM_ORGANIZATION_ID)),
        organization_db_scope(PLATFORM_ORGANIZATION_ID),
    ):
        have = AuditEvent.objects.count()
        AuditEvent.objects.bulk_create(
            [
                AuditEvent(
                    organization_id=PLATFORM_ORGANIZATION_ID,
                    occurred_at=now - timedelta(seconds=total - i),
                    actor_kind="user",
                    action="partner.perf_seed",
                    resource_type="partners.account",
                    resource_id=str(partner_ids[i % len(partner_ids)]),
                    changes={"partner_id": str(partner_ids[i % len(partner_ids)])},
                    outcome="success",
                    entry_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                )
                for i in range(have, total)
            ],
            batch_size=2000,
        )


def _median_ms(fn, runs: int = RUNS) -> float:
    fn()  # warm the session, the plan cache and the import graph
    samples = []
    for _ in range(runs):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples)


def _endpoints(partner_id):
    base = "/api/v1/admin/partners"
    return {
        "overview": f"{base}/overview",
        "resellers (25)": f"{base}/resellers?limit=25",
        "resellers (100)": f"{base}/resellers?limit=100",
        "resellers filtered": f"{base}/resellers?q=perf&status=active&capability=customer.provision&product=crm&limit=25",
        "white-label (25)": f"{base}/white-label?limit=25",
        "white-label filtered": f"{base}/white-label?domain_status=awaiting&brand=configured&limit=25",
        "customers, all (25)": f"{base}/customers?limit=25",
        "customers, all (100)": f"{base}/customers?limit=100",
        "customers filtered": f"{base}/customers?activation=pending&subscription=active&status=live&limit=25",
        "customers, one partner": f"{base}/{partner_id}/customers",
        "partner summary": f"{base}/{partner_id}/summary",
        "partner pricing": f"{base}/{partner_id}/pricing",
        "partner activity": f"{base}/{partner_id}/activity",
        "staff ledger, one partner": f"/api/v1/admin/commission/accruals?partner_id={partner_id}",
    }


def test_console_reads_are_constant_query_from_10_to_500_partners(world, staff_client):
    from workeasy360.commercial.partners.models import PartnerAccount

    client, _user = staff_client(
        VIEW, "platform.staff_audit.view", "platform.staff_commission.view"
    )
    table: dict[str, list[tuple[int, int, float]]] = {}
    for size in SIZES:
        _grow_to(size, world)
        target = PartnerAccount.objects.filter(code="perf-00000").first()
        _grow_audit_to(
            size * 20,
            [p.id for p in PartnerAccount.objects.filter(code__startswith="perf-")[:50]],
        )
        for name, url in _endpoints(target.id).items():
            count, response = count_queries(lambda url=url: client.get(url))
            assert response.status_code == 200, (name, size, response.content[:200])
            ms = _median_ms(lambda url=url: client.get(url))
            table.setdefault(name, []).append((size, count, ms))

    print(
        "\nConsole reads on this machine (rollback-wrapped, Django test client) - "
        "the shape of the cost, not a capacity claim"
    )
    print(f"{'endpoint':<28}" + "".join(f"| {s:>4} partners: q / p50 ms " for s in SIZES))
    for name, rows in table.items():
        print(f"{name:<28}" + "".join(f"| {q:>16} / {ms:>7.1f} " for _s, q, ms in rows))

    for name, rows in table.items():
        counts = {q for _s, q, _ms in rows}
        assert len(counts) == 1, f"{name}: the query count grows with the data: {rows}"
    # A generous ceiling, so a regression to an unbounded scan or an N+1 shows up
    # as a failure and ordinary machine noise does not. It is not a service level.
    slowest = max(ms for rows in table.values() for _s, _q, ms in rows[-1:])
    assert slowest < 3000, (
        f"the slowest read at {SIZES[-1]} partners took {slowest:.0f} ms"
    )


def test_the_page_size_does_not_change_the_number_of_queries(world, staff_client):
    client, _user = staff_client(VIEW)
    _grow_to(120, world)
    counts = {}
    for path in ("resellers", "white-label", "customers"):
        seen = set()
        for limit in (1, 10, 25, 100):
            count, response = count_queries(
                lambda: client.get(f"/api/v1/admin/partners/{path}?limit={limit}")
            )
            assert response.status_code == 200
            seen.add(count)
        counts[path] = seen
    assert all(len(seen) == 1 for seen in counts.values()), counts


def test_a_keyset_walk_of_500_partners_reads_every_row_once(world, staff_client):
    client, _user = staff_client(VIEW)
    _grow_to(500, world)
    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 100, "scope": "all"}
        if cursor:
            params["cursor"] = cursor
        page = client.get("/api/v1/admin/partners/resellers", params).json()
        seen += [r["id"] for r in page["results"]]
        pages += 1
        cursor = page["next_cursor"]
        if not page["has_more"]:
            break
        assert pages < 20, "the walk did not terminate"
    assert len(seen) == len(set(seen)), "a row appeared twice"
    from workeasy360.commercial.partners.models import PartnerAccount

    assert len(seen) == PartnerAccount.objects.count()


def test_partner_activity_from_the_one_audit_log_at_50000_entries(world, staff_client):
    """The activity tab filters the platform's audit log by ``changes.partner_id``
    (there is no second audit store). Measured where it is worst: a partner with
    only a couple of old entries among many, so the newest-first walk must go
    deep to find them. Printed, and bounded loosely; if it ever needs an index
    it is a P3 with this number attached."""
    from workeasy360.commercial.partners.models import PartnerAccount

    client, _user = staff_client(VIEW, "platform.staff_audit.view")
    _grow_to(60, world)
    ids = [
        p.id
        for p in PartnerAccount.objects.filter(code__startswith="perf-").order_by("code")
    ]
    hot, rare = ids[:50], ids[-1]
    rows = []
    for total in (1_000, 10_000, 50_000):
        _grow_audit_to(total, hot)  # every entry names one of the first 50 partners
        url = f"/api/v1/admin/partners/{rare}/activity"
        count, response = count_queries(lambda: client.get(url))
        assert response.status_code == 200
        rows.append((total, count, _median_ms(lambda: client.get(url), runs=3)))
    print(
        "\naudit entries | queries | activity p50 (a partner with no entries: the full walk)"
    )
    for total, count, ms in rows:
        print(f"{total:>13} | {count:>7} | {ms:>8.1f} ms")
    assert len({count for _t, count, _ms in rows}) == 1
    assert rows[-1][2] < 5000

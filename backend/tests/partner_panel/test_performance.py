"""No N+1 on the panel's reads: the number of queries is the same at 10, 100 and
500 rows, and every list is paged. Timings are printed for the status record."""

from __future__ import annotations

import time
import uuid

import pytest
from django.utils import timezone

from tests.partner_panel.conftest import api, count_queries_of

pytestmark = pytest.mark.django_db


def _sub_partners(parent, user, count):
    from workeasy360.commercial.partners.models import (
        PartnerAccount,
        PartnerHierarchyLink,
        PartnerMember,
        PartnerProductGrant,
    )

    now = timezone.now()
    accounts = PartnerAccount.objects.bulk_create(
        [
            PartnerAccount(
                code=f"perf-{uuid.uuid4().hex[:12]}",
                display_name=f"Perf {n}",
                payout_currency="GBP",
                status="active",
                created_by_id=user.id,
            )
            for n in range(count)
        ]
    )
    PartnerHierarchyLink.objects.bulk_create(
        [
            PartnerHierarchyLink(
                parent=parent, child=a, created_by_id=user.id, created_at=now
            )
            for a in accounts
        ]
    )
    PartnerMember.objects.bulk_create(
        [
            PartnerMember(
                partner=a, user=user, role="owner", added_by_id=user.id, added_at=now
            )
            for a in accounts
        ]
    )
    grant = PartnerProductGrant.objects.filter(partner=parent, status="active").first()
    PartnerProductGrant.objects.bulk_create(
        [
            PartnerProductGrant(
                partner=a,
                product_id=grant.product_id,
                granted_by_id=user.id,
                granted_at=now,
            )
            for a in accounts
        ]
    )


def _ledger(partner, count):
    from workeasy360.commercial.commissions.models import CommissionAccrual

    now = timezone.now()
    CommissionAccrual.objects.bulk_create(
        [
            CommissionAccrual(
                partner=partner,
                kind="accrual",
                amount_minor=100 + n,
                currency="GBP",
                source_ref=f"perf:{uuid.uuid4().hex}",
                event_at=now,
                clears_at=now,
                basis_minor=1000,
                basis_currency="GBP",
                idempotency_key=f"perf:{uuid.uuid4().hex}",
            )
            for n in range(count)
        ]
    )


@pytest.mark.parametrize(
    "path",
    [
        "dashboard",
        "balance",
        "sub-partners",
        "members",
        "products",
        "plans",
        "orders",
        "affiliates",
        "activity",
    ],
)
def test_every_read_makes_the_same_number_of_queries_at_10_100_and_500_rows(
    seller, owner_client, path, capsys
):
    pid = seller["partner"].id
    if path == "affiliates":
        from workeasy360.commercial.partners import services

        services.grant_capability(
            seller["staff_ctx"],
            pid,
            capability="referral.view",
            reason="Granted for the test",
        )
    url = api(pid, path)
    owner_client.get(url)  # warm the platform organization and settings
    counts = {}
    timings = {}
    made = 0
    for target in (10, 100, 500):
        _sub_partners(seller["partner"], seller["user"], target - made)
        _ledger(seller["partner"], target - made)
        made = target
        started = time.perf_counter()
        counts[target], response = count_queries_of(lambda: owner_client.get(url))
        timings[target] = round((time.perf_counter() - started) * 1000)
        assert response.status_code == 200, (path, response.status_code)
    with capsys.disabled():
        print(f"PERF {path}: queries {counts} ms {timings}")
    assert counts[10] == counts[100] == counts[500], (path, counts)


def test_the_lists_are_paged_whatever_the_size(seller, owner_client):
    pid = seller["partner"].id
    _sub_partners(seller["partner"], seller["user"], 130)
    body = owner_client.get(api(pid, "sub-partners")).json()
    assert len(body["results"]) <= 100 and body["has_more"] is True
    assert (
        owner_client.get(api(pid, "sub-partners"), {"limit": 100000})
        .json()["results"]
        .__len__()
        <= 100
    )
    follow = owner_client.get(
        api(pid, "sub-partners"), {"cursor": body["next_cursor"]}
    ).json()
    assert follow["results"] and {r["id"] for r in follow["results"]}.isdisjoint(
        {r["id"] for r in body["results"]}
    )

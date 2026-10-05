"""Customer-directory text search at 100 / 500 / 5,000 organizations.

Phase 7 final audit, item 70. The search is an unindexed ``icontains`` over
name and slug (recorded as P3 debt, ``pg_trgm`` past ~50k organizations). This
measures it where it is worst - a term that matches nothing, so the keyset
walk must visit every row - and pins that it stays two queries and fast
enough for a primary admin flow at 5,000. ``pytest -s`` prints the table.
"""

from __future__ import annotations

import statistics
import time
import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

pytestmark = [pytest.mark.django_db, pytest.mark.slow]

SIZES = (100, 500, 5000)


def _grow_to(total: int, creator) -> None:
    from workeasy360.platform.organizations.models import Organization
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with unscoped_access(reason="Scale test seeds organizations", module="tests.phase7"):
        have = Organization.objects.count()
        Organization.objects.bulk_create(
            [
                Organization(
                    slug=f"scale-{uuid.uuid4().hex[:12]}",
                    legal_name=f"Scale Customer {i}",
                    display_name=f"Scale Customer {i}",
                    status="active",
                    created_by=creator,
                )
                for i in range(have, total)
            ],
            batch_size=1000,
        )


def _p50_ms(fn, runs: int = 7) -> float:
    fn()  # warm
    samples = []
    for _ in range(runs):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples)


def test_directory_search_stays_constant_query_and_fast_to_5000(user_factory):
    from workeasy360.platform.organizations.selectors import organizations_directory_page

    creator = user_factory()
    rows = []
    for size in SIZES:
        _grow_to(size, creator)
        miss = _p50_ms(lambda: organizations_directory_page(search="zz-no-such-customer"))
        hit = _p50_ms(lambda: organizations_directory_page(search="Customer 4"))
        browse = _p50_ms(lambda: organizations_directory_page())
        with CaptureQueriesContext(connection) as queries:
            organizations_directory_page(search="zz-no-such-customer")
        rows.append((size, len(queries), miss, hit, browse))

    print("\norganizations | queries | search miss p50 | search hit p50 | browse p50")
    for size, count, miss, hit, browse in rows:
        print(
            f"{size:>13} | {count:>7} | {miss:>12.1f} ms | {hit:>11.1f} ms | {browse:>7.1f} ms"
        )

    assert {count for _s, count, *_ in rows} == {rows[0][1]}  # no growth in queries
    # Generous ceiling for a shared developer machine: the worst case (a full
    # walk at 5,000 rows) must stay interactive. Past this, pg_trgm (D-debt).
    assert rows[-1][2] < 250

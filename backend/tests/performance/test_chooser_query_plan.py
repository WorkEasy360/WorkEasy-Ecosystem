"""The chooser's real query plan and latency at 10/100/500/1000 memberships.

A plan measured against a near-empty table says nothing - PostgreSQL
sequentially scans anything small, correctly. So the projection is first
padded with other users' rows (a platform, not a fixture), ``ANALYZE``d as
the table owner (the app role may not), and only then is the chooser's own
SQL - taken from the production selector, not hand-written - run under
``EXPLAIN (ANALYZE, BUFFERS)``.

Asserts what matters (index-served, never a sequential scan of the table,
one query) and prints the measurements recorded in
docs/phase-5/phase-5-status.md; run with ``-s`` to see them.
"""

from __future__ import annotations

import statistics
import time
import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.slow]

SIZES = (10, 100, 500, 1000)
BACKGROUND_USERS = 400
BACKGROUND_ROWS_PER_USER = 50


def _analyze_as_owner() -> None:
    import os

    import psycopg

    with psycopg.connect(
        host=os.environ.get("W360_DB_DIRECT_HOST", "127.0.0.1"),
        port=os.environ.get("W360_DB_DIRECT_PORT", "5442"),
        dbname=connection.settings_dict["NAME"],
        user=os.environ.get("W360_DB_MIGRATOR_USER", "w360_migrator"),
        password=os.environ.get("W360_DB_MIGRATOR_PASSWORD", "w360_migrator_pw"),
        autocommit=True,
    ) as owner:
        owner.execute("ANALYZE organizations_user_access")


def test_chooser_is_one_index_served_query_at_every_size(user_factory):
    from tests.performance.test_chooser_query_count import _create_memberships_for
    from workeasy360.platform.organizations.models import (
        Organization,
        UserOrganizationAccess,
    )
    from workeasy360.platform.organizations.selectors import organizations_for_user

    users = {size: user_factory() for size in SIZES}
    for size, user in users.items():
        _create_memberships_for(user, size)

    # Pad the table with 20,000 other users' rows over the same organizations.
    orgs = list(Organization.objects.values_list("id", "slug")[:BACKGROUND_ROWS_PER_USER])
    background = [user_factory() for _ in range(BACKGROUND_USERS)]
    UserOrganizationAccess.objects.bulk_create(
        [
            UserOrganizationAccess(
                user_id=other.id,
                organization_id=org_id,
                organization_slug=slug,
                organization_display_name=slug,
                organization_status="active",
                membership_id=uuid.uuid4(),
            )
            for other in background
            for org_id, slug in orgs
        ],
        batch_size=5000,
    )
    _analyze_as_owner()
    total_rows = UserOrganizationAccess.objects.count()

    print(f"\n  organizations_user_access rows: {total_rows}")
    for size, user in users.items():
        with CaptureQueriesContext(connection) as queries:
            page = organizations_for_user(user.id, limit=25)
        assert len(queries.captured_queries) == 1
        assert len(page.items) == min(size, 25)

        timings = []
        for _ in range(7):
            start = time.perf_counter()
            organizations_for_user(user.id, limit=25)
            timings.append((time.perf_counter() - start) * 1000)

        sql = queries.captured_queries[0]["sql"]
        with connection.cursor() as cur:
            cur.execute(f"EXPLAIN (ANALYZE, BUFFERS) {sql}")
            plan = "\n".join(row[0] for row in cur.fetchall())

        assert "Seq Scan on organizations_user_access" not in plan, plan
        assert "organizations_user_access" in plan and "Index" in plan, plan
        execution = next(line for line in plan.splitlines() if "Execution Time" in line)
        scan = next(line.strip() for line in plan.splitlines() if "Index" in line)
        print(
            f"  {size:>5} memberships: 1 query, median {statistics.median(timings):.2f} ms "
            f"(selector, wall) | {execution.strip()} | {scan}"
        )

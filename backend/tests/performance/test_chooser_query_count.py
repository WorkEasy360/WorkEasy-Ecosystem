"""Chooser query count is bounded, not O(memberships) or O(platform) (Phase 5,
Task 25/67). Replaces the Phase 4 measurement in
docs/phase-4/05-organization-selection.md (10/100/500 organizations on the
*platform* -> 56/506/2506 queries) with the projection-backed number, which
does not grow with either variable.
"""

from __future__ import annotations

import time
import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

pytestmark = pytest.mark.django_db


def _create_memberships_for(user, count: int) -> None:
    """Bypass the HTTP/service layer - hundreds of organizations, seeded
    directly and via the same projection sync production code uses, exactly
    like a `rebuild_access_projection` backfill would populate it."""
    from workeasy360.contracts.enums import MembershipStatus
    from workeasy360.platform.organizations import projection
    from workeasy360.platform.organizations.models import Membership, Organization
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with unscoped_access(reason="Perf fixture seeds many memberships", module="tests"):
        for i in range(count):
            org = Organization.objects.create(
                slug=f"perf-org-{uuid.uuid4().hex[:10]}",
                legal_name=f"Perf Co {i}",
                display_name=f"Perf Co {i}",
                status="active",
                created_by_id=user.id,
            )
            with organization_db_scope(org.id):
                membership = Membership(
                    organization_id=org.id,
                    user_id=user.id,
                    status=MembershipStatus.ACTIVE.value,
                    is_owner=True,
                    member_type="internal",
                )
                membership.save()
            projection.sync_access_for_membership(
                user_id=user.id, membership=membership, organization=org
            )


@pytest.mark.parametrize("count", [10, 100, 500])
def test_chooser_query_count_is_bounded_regardless_of_membership_count(
    user_factory, count
):
    from workeasy360.platform.organizations.selectors import organizations_for_user

    user = user_factory()
    _create_memberships_for(user, count)

    with CaptureQueriesContext(connection) as ctx:
        start = time.perf_counter()
        page = organizations_for_user(user.id, limit=50)
        elapsed_ms = (time.perf_counter() - start) * 1000

    # Task 25's actual requirement: query count must not grow linearly with
    # membership count. A handful of fixed queries (session/permission
    # lookups aside, this selector itself issues exactly one) - asserted
    # with headroom rather than pinned to "1" so an unrelated framework
    # query does not make this test flaky.
    assert len(ctx.captured_queries) <= 5, (
        f"{len(ctx.captured_queries)} queries for {count} memberships - "
        f"query count must not scale with membership count (Task 25)."
    )
    assert len(page.items) == min(count, 50)
    assert page.has_more == (count > 50)
    print(
        f"\n  {count} memberships: {len(ctx.captured_queries)} queries, {elapsed_ms:.1f} ms"
    )


def test_large_membership_user_is_never_silently_truncated(user_factory):
    """Task 67: at least 500 memberships, paginated, nothing silently
    dropped - the old scan's 2,000-organization cap and its silent omission
    no longer apply because this is no longer a scan."""
    from workeasy360.platform.organizations.selectors import organizations_for_user

    user = user_factory()
    _create_memberships_for(user, 500)

    seen = set()
    cursor = None
    pages = 0
    while True:
        page = organizations_for_user(user.id, limit=100, cursor=cursor)
        seen.update(o["organization_id"] for o in page.items)
        pages += 1
        if not page.has_more:
            break
        cursor = page.next_cursor
        assert pages < 20  # sanity bound on the loop itself

    assert len(seen) == 500

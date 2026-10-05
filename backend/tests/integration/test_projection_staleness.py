"""The projection is discovery, never authority (ADR-022) - proven over HTTP.

Each test makes ``UserOrganizationAccess`` say something the authoritative
tables (``Membership``, ``Organization``) do not, then selects the
organization the way the browser does - a tenant-scoped request - and shows
the authoritative answer wins. Then reconciliation converges the projection.

Includes the 500-membership acceptance (Task 67): every organization is
reachable through the paginated chooser, the per-request query count does
not grow with the page being read, and selecting one still re-resolves the
real membership.
"""

from __future__ import annotations

import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

pytestmark = [pytest.mark.django_db, pytest.mark.isolation]

ORGANIZATIONS = "/api/v1/organizations"


def _context(client, slug):
    return client.get(f"/api/v1/orgs/{slug}/context")


def _listed_slugs(client):
    return [o["slug"] for o in client.get(ORGANIZATIONS).json()["organizations"]]


def _set_membership_status(org_id, membership_id, status):
    """Change the authoritative row *without* the projection sync - the
    staleness the tests need, which no production path produces."""
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="test staleness", module="tests"),
        organization_db_scope(org_id),
    ):
        Membership.unscoped.filter(id=membership_id).update(status=status)


def _set_organization_status(org_id, status):
    from workeasy360.platform.organizations.models import Organization

    Organization.objects.filter(id=org_id).update(status=status)


def test_a_stale_row_for_a_removed_membership_cannot_select_the_organization(
    client, organization_factory
):
    from workeasy360.platform.organizations.projection import (
        check_access_projection_drift,
        rebuild_access_projection,
    )

    org, membership, user = organization_factory()
    client.force_login(user)
    assert _context(client, org.slug).status_code == 200

    _set_membership_status(org.id, membership.id, "removed")

    assert _listed_slugs(client) == [org.slug]  # the projection is stale...
    denied = _context(client, org.slug)  # ...and selection ignores it
    assert (denied.status_code, denied.json()["code"]) == (404, "not_found")

    report = check_access_projection_drift()
    assert {"organization_id": str(org.id), "user_id": str(user.id)} in report.stale_rows
    stats = rebuild_access_projection(prune_stale=True)
    assert stats.rows_pruned == 1
    assert _listed_slugs(client) == []
    assert check_access_projection_drift().is_clean


def test_a_stale_active_status_cannot_unblock_a_suspended_organization(
    client, organization_factory
):
    from workeasy360.platform.organizations.models import UserOrganizationAccess
    from workeasy360.platform.organizations.projection import rebuild_access_projection

    org, _membership, user = organization_factory()
    client.force_login(user)

    _set_organization_status(org.id, "suspended")

    row = UserOrganizationAccess.objects.get(user_id=user.id, organization_id=org.id)
    assert row.organization_status == "active"  # the projection is stale

    context = _context(client, org.slug).json()
    assert (context["organization_status"], context["write_allowed"]) == (
        "suspended",
        False,
    )
    blocked = client.post(f"/api/v1/orgs/{org.slug}/context")
    assert blocked.status_code == 402  # the write block uses the real status

    rebuild_access_projection()
    row.refresh_from_db()
    assert row.organization_status == "suspended"


def test_a_stale_active_status_cannot_open_a_locked_organization(
    client, organization_factory
):
    org, _membership, user = organization_factory()
    client.force_login(user)

    _set_organization_status(org.id, "locked")

    assert _listed_slugs(client) == [org.slug]
    assert _context(client, org.slug).status_code == 404


def test_a_poisoned_row_naming_a_foreign_organization_grants_nothing(
    client, organization_factory, user_factory
):
    """Projection poisoning: a row for an organization the user never
    belonged to (the worst a bug in the sync path could produce)."""
    from workeasy360.platform.organizations.models import UserOrganizationAccess

    victim_org, _m, _victim = organization_factory()
    intruder = user_factory()
    UserOrganizationAccess.objects.create(
        user_id=intruder.id,
        organization_id=victim_org.id,
        organization_slug=victim_org.slug,
        organization_display_name=victim_org.display_name,
        organization_status="active",
        membership_id=uuid.uuid4(),
        is_owner=True,
    )
    client.force_login(intruder)

    assert _listed_slugs(client) == [victim_org.slug]  # it can *appear*...
    for response in (
        _context(client, victim_org.slug),
        client.get(f"/api/v1/orgs/{victim_org.slug}/memberships"),
        client.get(f"/api/v1/orgs/{victim_org.slug}/organization"),
    ):
        assert response.status_code == 404  # ...it can never be *acted on*


# --- 500 memberships over HTTP (Task 67) ------------------------------------


def test_a_500_membership_user_can_page_through_and_select_every_organization(
    client, user_factory
):
    from tests.performance.test_chooser_query_count import _create_memberships_for
    from workeasy360.platform.organizations.models import UserOrganizationAccess

    user = user_factory()
    _create_memberships_for(user, 500)
    client.force_login(user)

    seen: list[str] = []
    query_counts: list[int] = []
    cursor = None
    while True:
        url = f"{ORGANIZATIONS}?limit=100" + (f"&cursor={cursor}" if cursor else "")
        with CaptureQueriesContext(connection) as queries:
            page = client.get(url).json()
        query_counts.append(len(queries.captured_queries))
        seen.extend(o["slug"] for o in page["organizations"])
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]

    assert len(seen) == len(set(seen)) == 500  # nothing lost, nothing repeated
    assert len(query_counts) == 5
    # The fifth page costs what the first did: bounded by page, not position.
    assert len(set(query_counts)) == 1, query_counts

    # Search is bounded and still exact.
    found = client.get(f"{ORGANIZATIONS}?q=Perf Co 499").json()["organizations"]
    assert [o["display_name"] for o in found] == ["Perf Co 499"]

    # Selecting the last organization re-resolves the real membership.
    last = seen[-1]
    assert _context(client, last).status_code == 200
    row = UserOrganizationAccess.objects.get(user_id=user.id, organization_slug=last)
    _set_membership_status(row.organization_id, row.membership_id, "suspended")
    assert _context(client, last).status_code == 404


@pytest.mark.parametrize("limit", ["abc", "-5", "0", "100000"])
def test_chooser_page_size_is_bounded_for_any_input(client, user_factory, limit):
    from tests.performance.test_chooser_query_count import _create_memberships_for

    user = user_factory()
    _create_memberships_for(user, 3)
    client.force_login(user)

    response = client.get(f"{ORGANIZATIONS}?limit={limit}")

    assert response.status_code == 200
    assert 1 <= len(response.json()["organizations"]) <= 100

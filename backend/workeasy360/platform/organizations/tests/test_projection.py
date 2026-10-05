"""Access-directory projection: sync, rebuild, drift (Phase 5, Tasks 20-25).

Plain ``django_db`` throughout - see test_onboarding.py's module docstring
for why (commit timing is covered separately).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


def test_rebuild_reconstructs_the_projection_from_membership(organization_factory):
    """Task 23: backfill for a database that had memberships before the
    projection existed - delete the row, rebuild, it comes back."""
    from workeasy360.platform.organizations.models import UserOrganizationAccess
    from workeasy360.platform.organizations.projection import rebuild_access_projection

    org, membership, user = organization_factory()
    UserOrganizationAccess.objects.filter(
        user_id=user.id, organization_id=org.id
    ).delete()
    assert not UserOrganizationAccess.objects.filter(
        user_id=user.id, organization_id=org.id
    ).exists()

    stats = rebuild_access_projection()

    assert stats.organizations_failed == 0
    row = UserOrganizationAccess.objects.get(user_id=user.id, organization_id=org.id)
    assert row.is_owner is True
    assert row.membership_id == membership.id


def test_rebuild_is_idempotent(organization_factory):
    """Two runs, compared row for row - including ``updated_at``, the
    chooser's sort key: a rebuild over a converged projection writes
    nothing, duplicates nothing and loses nothing."""
    from workeasy360.platform.organizations.models import UserOrganizationAccess
    from workeasy360.platform.organizations.projection import rebuild_access_projection

    for _ in range(3):
        organization_factory()

    def snapshot():
        return sorted(UserOrganizationAccess.objects.values_list())

    before = snapshot()
    first = rebuild_access_projection()
    after_first = snapshot()
    second = rebuild_access_projection()
    after_second = snapshot()

    assert first.organizations_failed == second.organizations_failed == 0
    assert first.rows_written == second.rows_written == 0
    assert before == after_first == after_second
    assert len(after_second) == 3


def test_rebuild_prunes_a_stale_row_only_when_asked(organization_factory):
    """By default rebuild leaves a row it would not have written (here: for
    a membership that does not exist) for check_access_projection_drift to
    report; ``prune_stale`` removes it, and only it."""
    from workeasy360.platform.organizations.models import UserOrganizationAccess
    from workeasy360.platform.organizations.projection import rebuild_access_projection

    _org, _membership, user = organization_factory()
    from workeasy360.platform.organizations.models import Organization

    other_org = Organization.objects.create(
        slug="orphan-org",
        legal_name="Orphan",
        display_name="Orphan",
        status="active",
        created_by_id=user.id,
    )
    UserOrganizationAccess.objects.create(
        user_id=user.id,
        organization_id=other_org.id,
        organization_slug=other_org.slug,
        organization_display_name=other_org.display_name,
        organization_status="active",
        membership_id=user.id,
        is_owner=False,
    )

    rebuild_access_projection()

    assert UserOrganizationAccess.objects.filter(
        user_id=user.id, organization_id=other_org.id
    ).exists()

    stats = rebuild_access_projection(prune_stale=True)

    assert stats.rows_pruned == 1
    assert not UserOrganizationAccess.objects.filter(
        user_id=user.id, organization_id=other_org.id
    ).exists()
    # The legitimate row survives the prune.
    assert UserOrganizationAccess.objects.filter(user_id=user.id).count() == 1


def test_drift_check_reports_missing_and_stale_rows_without_repairing(
    organization_factory,
):
    from workeasy360.platform.organizations.models import (
        Organization,
        UserOrganizationAccess,
    )
    from workeasy360.platform.organizations.projection import (
        check_access_projection_drift,
    )

    org, _membership, user = organization_factory()
    UserOrganizationAccess.objects.filter(
        user_id=user.id, organization_id=org.id
    ).delete()

    stale_org = Organization.objects.create(
        slug="stale-org",
        legal_name="Stale",
        display_name="Stale",
        status="active",
        created_by_id=user.id,
    )
    UserOrganizationAccess.objects.create(
        user_id=user.id,
        organization_id=stale_org.id,
        organization_slug=stale_org.slug,
        organization_display_name=stale_org.display_name,
        organization_status="active",
        membership_id=user.id,
        is_owner=False,
    )

    report = check_access_projection_drift()

    assert not report.is_clean
    assert {
        "organization_id": str(org.id),
        "user_id": str(user.id),
    } in report.missing_rows
    assert {
        "organization_id": str(stale_org.id),
        "user_id": str(user.id),
    } in report.stale_rows
    # Nothing was repaired - the missing row is still missing, the stale
    # row is still there.
    assert not UserOrganizationAccess.objects.filter(
        user_id=user.id, organization_id=org.id
    ).exists()
    assert UserOrganizationAccess.objects.filter(
        user_id=user.id, organization_id=stale_org.id
    ).exists()


def test_drift_check_detects_wrong_metadata(organization_factory):
    from workeasy360.platform.organizations.models import UserOrganizationAccess
    from workeasy360.platform.organizations.projection import (
        check_access_projection_drift,
    )

    org, _membership, user = organization_factory()
    UserOrganizationAccess.objects.filter(user_id=user.id, organization_id=org.id).update(
        organization_display_name="Stale Cached Name"
    )

    report = check_access_projection_drift()
    assert {
        "organization_id": str(org.id),
        "user_id": str(user.id),
    } in report.wrong_metadata_rows


def test_a_clean_projection_reports_no_drift(organization_factory):
    from workeasy360.platform.organizations.projection import (
        check_access_projection_drift,
    )

    organization_factory()
    report = check_access_projection_drift()
    assert report.is_clean


def test_organization_status_change_is_reflected_in_the_projection(organization_factory):
    """Task 20: organization suspended/reactivated syncs denormalised
    metadata - ``set_lifecycle_status`` calls
    projection.sync_access_metadata_for_organization."""
    from workeasy360.contracts.enums import OrganizationStatus
    from workeasy360.platform.organizations.models import UserOrganizationAccess
    from workeasy360.platform.organizations.services import set_lifecycle_status

    org, _membership, user = organization_factory()

    set_lifecycle_status(
        org.id, target=OrganizationStatus.SUSPENDED, reason="non-payment"
    )
    row = UserOrganizationAccess.objects.get(user_id=user.id, organization_id=org.id)
    assert row.organization_status == "suspended"

    set_lifecycle_status(org.id, target=OrganizationStatus.ACTIVE, reason="")
    row.refresh_from_db()
    assert row.organization_status == "active"


# --- IDOR / trust model (Task 18) -------------------------------------------


def test_a_users_chooser_never_names_another_users_organization(organization_factory):
    from workeasy360.platform.organizations.selectors import organizations_for_user

    org_a, _m_a, user_a = organization_factory()
    org_b, _m_b, user_b = organization_factory()

    page_a = organizations_for_user(user_a.id)
    page_b = organizations_for_user(user_b.id)

    assert [o["organization_id"] for o in page_a.items] == [str(org_a.id)]
    assert [o["organization_id"] for o in page_b.items] == [str(org_b.id)]

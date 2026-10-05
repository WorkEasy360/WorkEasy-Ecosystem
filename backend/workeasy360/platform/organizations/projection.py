"""The access-directory projection: sync, rebuild, drift (Phase 5; ADR-022).

Three operations, three different trust levels:

- :func:`sync_access_for_membership` / :func:`remove_access_for_membership`
  run **inside** the same transaction as the membership write they mirror
  (Task 21: this projection is transactionally synchronous, not eventually
  consistent - there is no replication lag to document because there is no
  replication). Called only by ``platform.organizations.services``, the
  one writer of memberships, after every change it makes.
- :func:`sync_access_metadata_for_organization` mirrors organization-level
  display metadata (name, status) into every row that denormalises it.
  Called by the same module's suspension, reactivation and rename.
- :func:`rebuild_access_projection` and :func:`check_access_projection_drift`
  are **operational** tools (Tasks 23-24), reconstructing or auditing the
  projection from ``Membership``/``Organization`` - the only source of truth.
  Rebuild upserts, and deletes only when asked to (``prune_stale``) and only
  rows whose authoritative membership is not active; safe to re-run.
  Drift-checking is read-only.
"""

from __future__ import annotations

import dataclasses
from typing import Any
from uuid import UUID

from workeasy360.contracts.enums import MembershipStatus
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

#: Rebuild/drift-check batch size - bounded so a large platform never loads
#: its whole organization table into memory at once (Task 23/62).
DEFAULT_BATCH_SIZE = 500


def _access_fields(membership: Any, organization: Any) -> dict[str, Any]:
    return {
        "organization_slug": organization.slug,
        "organization_display_name": organization.display_name,
        "organization_status": organization.status,
        "membership_id": membership.id,
        "is_owner": membership.is_owner,
    }


def sync_access_for_membership(
    *, user_id: UUID, membership: Any, organization: Any
) -> bool:
    """Upsert or remove one user's chooser row for one organization.
    Returns whether a row was written or deleted.

    Called transactionally, immediately after the ``Membership`` write it
    mirrors. Idempotent by construction: the unique ``(user, organization)``
    pair converges regardless of how many times a duplicate event or retry
    calls it (Task 22) - and a call that would change nothing writes
    nothing, so a rebuild over a converged projection is a genuine no-op
    that leaves ``updated_at`` (the chooser's sort key) untouched.
    """
    from django.db import IntegrityError, transaction

    from workeasy360.platform.organizations.models import UserOrganizationAccess

    if not membership.status_enum.grants_access:
        deleted, _ = UserOrganizationAccess.objects.filter(
            user_id=user_id, organization_id=organization.id
        ).delete()
        return deleted > 0

    fields = _access_fields(membership, organization)
    rows = UserOrganizationAccess.objects.select_for_update().filter(
        user_id=user_id, organization_id=organization.id
    )
    # Its own atomic block (a savepoint inside the caller's transaction), so
    # the row lock has a transaction to live in whoever the caller is.
    with transaction.atomic():
        row = rows.first()
        if row is None:
            try:
                with transaction.atomic():
                    UserOrganizationAccess.objects.create(
                        user_id=user_id, organization_id=organization.id, **fields
                    )
                return True
            except IntegrityError:
                # A concurrent sync inserted the same pair first; converge on it.
                row = rows.get()

        if all(getattr(row, name) == value for name, value in fields.items()):
            return False
        for name, value in fields.items():
            setattr(row, name, value)
        row.save(update_fields=[*fields, "updated_at"])
        return True


def remove_access_for_membership(*, user_id: UUID, organization_id: UUID) -> None:
    """Remove one user's chooser row - membership removed/expired (Task 20)."""
    from workeasy360.platform.organizations.models import UserOrganizationAccess

    UserOrganizationAccess.objects.filter(
        user_id=user_id, organization_id=organization_id
    ).delete()


def sync_access_metadata_for_organization(organization: Any) -> int:
    """Refresh denormalised display metadata on every row for one organization.

    A bulk ``update()``, not a per-row read/write loop: the number of members
    of one organization is bounded and small compared to the platform, unlike
    the read side this table exists to speed up.
    """
    from workeasy360.platform.organizations.models import UserOrganizationAccess

    return UserOrganizationAccess.objects.filter(organization_id=organization.id).update(
        organization_slug=organization.slug,
        organization_display_name=organization.display_name,
        organization_status=organization.status,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class RebuildStats:
    organizations_scanned: int
    organizations_failed: int
    rows_written: int
    rows_pruned: int


def rebuild_access_projection(
    *, batch_size: int = DEFAULT_BATCH_SIZE, prune_stale: bool = False
) -> RebuildStats:
    """Reconstruct the projection from ``Membership``/``Organization``.

    Idempotent (every write is the same :func:`sync_access_for_membership`
    the transactional path uses, and a converged row is not rewritten),
    bounded (organizations are visited in batches, memberships within each
    organization via its own tenant-scoped query - never a single unbounded
    read), and each organization's failure is caught and counted rather than
    aborting the whole run (Task 23: "observable failures", not "one bad row
    stops the operator's tool"). Each organization is its own transaction,
    so a retry after a partial failure redoes only what is still wrong.

    **Safe against concurrent membership writes.** The organization and its
    active memberships are read ``FOR NO KEY UPDATE``: a removal or
    suspension committing mid-rebuild either finishes first (and this read
    no longer sees it as active) or waits for this organization's
    transaction (and then deletes/updates the row this wrote). Without the
    lock, a rebuild could re-insert a row for a membership removed a moment
    earlier.

    Deletes only with ``prune_stale=True``, and then only rows whose
    authoritative membership is not active - the one divergence this
    function can decide from source-of-truth state alone. Without it, stale
    rows are left for :func:`check_access_projection_drift` to report.
    Neither choice affects authorization: nothing grants access from this
    table (ADR-022).
    """
    from workeasy360.platform.organizations.models import (
        Membership,
        Organization,
        UserOrganizationAccess,
    )
    from workeasy360.platform.organizations.selectors import all_organization_ids
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    scanned = 0
    failed = 0
    written = 0
    pruned = 0

    org_ids = all_organization_ids(batch_size=batch_size)
    with unscoped_access(
        reason="Projection rebuild reads every organization's memberships in turn",
        module="platform.organizations.projection",
    ):
        for start in range(0, len(org_ids), batch_size):
            for org_id in org_ids[start : start + batch_size]:
                scanned += 1
                try:
                    with organization_db_scope(org_id):
                        organization = (
                            Organization.objects.select_for_update(no_key=True)
                            .filter(id=org_id)
                            .first()
                        )
                        if organization is None:
                            continue
                        memberships = list(
                            Membership.unscoped.select_for_update(no_key=True).filter(
                                organization_id=org_id,
                                status=MembershipStatus.ACTIVE.value,
                            )
                        )
                        for membership in memberships:
                            written += sync_access_for_membership(
                                user_id=membership.user_id,
                                membership=membership,
                                organization=organization,
                            )
                        if prune_stale:
                            pruned += (
                                UserOrganizationAccess.objects.filter(
                                    organization_id=org_id
                                )
                                .exclude(user_id__in=[m.user_id for m in memberships])
                                .delete()[0]
                            )
                except Exception:
                    failed += 1
                    logger.exception(
                        "projection.rebuild.organization_failed",
                        organization_id=str(org_id),
                    )
                    metrics.increment("access_projection_rebuild_failures_total")

    metrics.increment("access_projection_rebuild_runs_total")
    logger.info(
        "projection.rebuild.complete",
        organizations_scanned=scanned,
        organizations_failed=failed,
        rows_written=written,
        rows_pruned=pruned,
    )
    return RebuildStats(
        organizations_scanned=scanned,
        organizations_failed=failed,
        rows_written=written,
        rows_pruned=pruned,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class DriftReport:
    """Read-only comparison. Nothing here is repaired automatically."""

    organizations_scanned: int
    missing_rows: list[dict[str, str]]  # active membership, no projection row
    stale_rows: list[dict[str, str]]  # projection row, no active membership
    wrong_metadata_rows: list[dict[str, str]]  # projection row, mismatched display data

    @property
    def is_clean(self) -> bool:
        return not (self.missing_rows or self.stale_rows or self.wrong_metadata_rows)


def check_access_projection_drift(*, batch_size: int = DEFAULT_BATCH_SIZE) -> DriftReport:
    """Compare the projection against source-of-truth state (Task 24).

    Three divergence classes, each individually actionable:

    - **missing**: an active membership with no corresponding row - a user
      who would silently not see an organization they belong to.
    - **stale**: a row whose membership is gone or no longer active - a user
      who would see an organization they no longer belong to (harmless by
      construction, since selection revalidates membership - Task 19 - but
      still drift worth fixing).
    - **wrong_metadata**: a row whose denormalised slug/name/status disagrees
      with the organization's actual current value.

    Read-only throughout. Repair is a separate, explicit operator decision
    (``rebuild_access_projection`` for missing/wrong_metadata, with
    ``prune_stale=True`` for stale), never performed here.
    """
    from workeasy360.platform.organizations.models import (
        Membership,
        Organization,
        UserOrganizationAccess,
    )
    from workeasy360.platform.organizations.selectors import all_organization_ids
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    missing: list[dict[str, str]] = []
    stale: list[dict[str, str]] = []
    wrong_metadata: list[dict[str, str]] = []
    scanned = 0

    org_ids = all_organization_ids(batch_size=batch_size)
    with unscoped_access(
        reason="Drift check compares every organization's memberships to its projection rows",
        module="platform.organizations.projection",
    ):
        for start in range(0, len(org_ids), batch_size):
            for org_id in org_ids[start : start + batch_size]:
                scanned += 1
                try:
                    with organization_db_scope(org_id):
                        organization = Organization.objects.filter(id=org_id).first()
                        if organization is None:
                            continue
                        active_by_user = {
                            m.user_id: m
                            for m in Membership.unscoped.filter(
                                organization_id=org_id,
                                status=MembershipStatus.ACTIVE.value,
                            )
                        }
                        rows_by_user = {
                            row.user_id: row
                            for row in UserOrganizationAccess.objects.filter(
                                organization_id=org_id
                            )
                        }

                        for user_id, membership in active_by_user.items():
                            row = rows_by_user.get(user_id)
                            if row is None:
                                missing.append(
                                    {
                                        "organization_id": str(org_id),
                                        "user_id": str(user_id),
                                    }
                                )
                            elif (
                                row.organization_slug != organization.slug
                                or row.organization_display_name
                                != organization.display_name
                                or row.organization_status != organization.status
                                or row.is_owner != membership.is_owner
                                or row.membership_id != membership.id
                            ):
                                wrong_metadata.append(
                                    {
                                        "organization_id": str(org_id),
                                        "user_id": str(user_id),
                                    }
                                )

                        for user_id, _row in rows_by_user.items():
                            if user_id not in active_by_user:
                                stale.append(
                                    {
                                        "organization_id": str(org_id),
                                        "user_id": str(user_id),
                                    }
                                )
                except Exception:
                    logger.exception(
                        "projection.drift_check.organization_failed",
                        organization_id=str(org_id),
                    )
                    metrics.increment("access_projection_drift_check_failures_total")

    metrics.increment("access_projection_drift_checks_total")
    if missing or stale or wrong_metadata:
        metrics.increment("access_projection_drift_detected_total")
    return DriftReport(
        organizations_scanned=scanned,
        missing_rows=missing,
        stale_rows=stale,
        wrong_metadata_rows=wrong_metadata,
    )

"""The organizations write boundary: the one place ``Organization``,
``Membership``, the creation ledger and member sign-in activity are written.

**Single writer (D-032, closed in the Phase 9 closure).** Onboarding, the
invitation flow, the member console, the organization's security settings,
the staff lifecycle actions and the control-plane anchor all *decide* in their
own modules - authorization, audit, roles, seats, events - and call these
functions to *do*. Each function:

- locks the row it changes and enforces the row's own invariants (one owner,
  the owner is never suspended or removed, the lifecycle moves only between
  the states it may);
- keeps the chooser projection (``projection``) in step in the same
  transaction, so no caller can forget it;
- returns a frozen record or the values a caller needs for its audit entry -
  never a model instance a caller could change and save.

Enforced by ``test_only_the_organizations_boundary_writes_its_tables`` (a
static scan) and by the suite-wide write-ownership guard in
``tests/write_ownership.py``, which fails the run if any executed write to
these tables comes from another module.

**Why the orchestration is not here.** Creating an organization also assigns
the owner role (``platform/authz``, P2), writes an audit entry and validates
the acting identity (``platform/identity``, a P1 *sibling* - F-5 forbids
same-layer imports inside ``platform/``). Those use cases live one layer up,
in ``platform/onboarding`` (P3) and ``platform/administration`` (P4)
(ADR-023).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from django.utils.text import slugify

from workeasy360.contracts.enums import MembershipStatus, MemberType, OrganizationStatus
from workeasy360.contracts.errors import (
    IdempotencyConflict,
    InvalidStateTransition,
    MemberNotFound,
    MembershipConflict,
    OrganizationValidationError,
    PermissionDenied,
    SecurityPolicyConflict,
)
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

#: Mirrors Organization.display_name's max_length. Enforced here too so a
#: too-long name is a 400 from validation, not a 500 from the database.
MAX_NAME_LENGTH = 255

#: Inserts attempted before a slug race is reported as a failure. Each retry
#: after the first draws a random suffix, so a second collision is already
#: astronomically unlikely.
_SLUG_ATTEMPTS = 3

#: A slug candidate this short or generic collides constantly and reads as a
#: platform path segment (``/o/app/...``) rather than an organization.
_RESERVED_SLUGS = frozenset(
    {
        "api",
        "app",
        "apps",
        "admin",
        "auth",
        "static",
        "healthz",
        "onboarding",
        "organizations",
        "login",
        "logout",
        "settings",
        "account",
        "www",
    }
)


@dataclass(frozen=True, slots=True)
class CreateOrganizationResult:
    organization_id: UUID
    slug: str
    display_name: str
    membership_id: UUID
    created: bool  # False when this was an idempotent replay


def validate_name(raw: Any) -> str:
    """The one required field (Task 3). Minimal on purpose.

    A string or nothing: ``str()`` of a JSON object or list would otherwise
    become the organization's name."""
    if raw is not None and not isinstance(raw, str):
        raise OrganizationValidationError("Organization name must be text.", field="name")
    name = (raw or "").strip()
    if not name:
        raise OrganizationValidationError("Organization name is required.", field="name")
    if len(name) > MAX_NAME_LENGTH:
        raise OrganizationValidationError(
            f"Organization name must be at most {MAX_NAME_LENGTH} characters.",
            field="name",
        )
    return name


def fingerprint(arguments: dict[str, Any]) -> str:
    encoded = json.dumps(arguments, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def generate_unique_slug(name: str) -> str:
    """Server-generated, collision-checked (Task 7).

    Slug is routing metadata, not security identity - security continues to
    resolve through membership (``platform.tenancy.resolution``), so a
    collision is resolved by appending a short random suffix rather than by
    anything that needs to be race-free at the SQL level: the ``Organization``
    table's own unique constraint on ``slug`` is what actually prevents a
    collision surviving a race between this check and the insert, and a
    ``slug`` clash there raises ``IntegrityError``, surfaced by the caller as
    a validation error rather than a 500 (Task 36).
    """
    from workeasy360.platform.organizations.models import Organization

    # Only [a-z0-9-], because that is all the tenancy middleware will route
    # (``platform.tenancy.resolution._ORG_PATH_RE``). ``slugify`` also keeps
    # underscores: an organization named "Acme_Corp" used to get the slug
    # "acme_corp", which no /orgs/<slug>/ request could ever resolve - the
    # owner was locked out of the organization they had just created (found
    # by the Phase 5 final audit). Runs of anything else become one hyphen.
    base = re.sub(r"[^a-z0-9]+", "-", slugify(name)).strip("-")[:50].strip("-")
    base = base or "organization"
    if base in _RESERVED_SLUGS:
        base = f"org-{base}"

    candidate = base
    attempts = 0
    while (
        candidate in _RESERVED_SLUGS
        or Organization.objects.filter(slug=candidate).exists()
    ):
        attempts += 1
        # Random rather than sequential: a sequential suffix under
        # concurrent creation of same-named organizations is itself a race
        # (two callers both see "-1" free). uuid4 hex is cheap and collision
        # odds are astronomically low even before the DB constraint backs it.
        import uuid as _uuid

        candidate = f"{base}-{_uuid.uuid4().hex[:6]}"
        if (
            attempts > 20
        ):  # pragma: no cover - defensive; the DB constraint is the real backstop
            raise OrganizationValidationError(
                "Could not generate a unique organization identifier. Try again.",
                field="name",
            )
    return candidate


@dataclass(frozen=True, slots=True)
class Claim:
    record_id: UUID
    is_replay: bool
    organization_id: UUID | None


def claim_creation(*, user_id: UUID, key: str, request_fingerprint: str) -> Claim:
    """Reserve an idempotency key for organization creation, or report reuse
    (Task 6). Mirrors ``integration.idempotency.services.claim`` exactly, but
    against ``OrganizationCreationRequest`` - see that model's docstring for
    why the tenant-scoped ledger cannot be reused here. Must run inside the
    caller's transaction: a savepoint catches the unique-constraint race so a
    genuine duplicate never poisons the enclosing transaction.
    """
    from workeasy360.platform.organizations.models import OrganizationCreationRequest

    if not connection.in_atomic_block:
        raise RuntimeError(
            "claim_creation called outside a transaction. The claim must "
            "roll back with the work it guards."
        )

    try:
        with transaction.atomic():
            record = OrganizationCreationRequest(
                user_id=user_id, key=key, fingerprint=request_fingerprint
            )
            record.save()
        return Claim(record_id=record.id, is_replay=False, organization_id=None)
    except IntegrityError:
        pass

    existing = OrganizationCreationRequest.objects.filter(
        user_id=user_id, key=key
    ).first()
    if existing is None:  # pragma: no cover - lost row between insert and read
        raise IdempotencyConflict(
            f"Idempotency key {key!r} could not be claimed or read for "
            f"organization creation."
        )
    if existing.fingerprint != request_fingerprint:
        logger.warning("organizations.creation.idempotency_fingerprint_mismatch")
        raise IdempotencyConflict(
            f"Idempotency key {key!r} was already used to create a "
            f"differently-named organization. Reusing a key for a different "
            f"request is refused; issue a new key.",
        )
    from workeasy360.platform.observability.metrics import metrics

    metrics.increment("organization_creation_idempotent_replays_total")
    return Claim(
        record_id=existing.id, is_replay=True, organization_id=existing.organization_id
    )


def complete_creation(claim: Claim, organization_id: UUID) -> None:
    from workeasy360.platform.organizations.models import OrganizationCreationRequest

    OrganizationCreationRequest.objects.filter(id=claim.record_id).update(
        organization_id=organization_id
    )


def replay_result(organization_id: UUID, user_id: UUID) -> CreateOrganizationResult:
    """Rebuild the response for a claimed-and-already-completed idempotent
    replay: read back the organization and the owner membership it produced.
    """
    from workeasy360.platform.organizations.models import Membership, Organization
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with unscoped_access(
        reason="Idempotent replay: reading back a prior creation's result, "
        "no tenant context available at this call site",
        module="platform.organizations.services",
    ):
        organization = Organization.objects.get(pk=organization_id)
        with organization_db_scope(organization.id):
            membership = Membership.unscoped.get(
                organization_id=organization.id, user_id=user_id, is_owner=True
            )
    return CreateOrganizationResult(
        organization_id=organization.id,
        slug=organization.slug,
        display_name=organization.display_name,
        membership_id=membership.id,
        created=False,
    )


@dataclass(frozen=True, slots=True)
class MemberRecord:
    """What a caller needs to know about a membership it just changed."""

    membership_id: UUID
    organization_id: UUID
    user_id: UUID
    status: MembershipStatus
    is_owner: bool
    member_type: str


def _record(membership: Any) -> MemberRecord:
    return MemberRecord(
        membership_id=membership.id,
        organization_id=membership.organization_id,
        user_id=membership.user_id,
        status=MembershipStatus(membership.status),
        is_owner=membership.is_owner,
        member_type=membership.member_type,
    )


def _mirror(membership: Any, organization: Any = None) -> None:
    """The member's chooser row, in the transaction that changed the member:
    written while the membership grants access, removed otherwise."""
    from workeasy360.platform.organizations import projection
    from workeasy360.platform.organizations.models import Organization

    if not membership.status_enum.grants_access:
        projection.remove_access_for_membership(
            user_id=membership.user_id, organization_id=membership.organization_id
        )
        return
    if organization is None:
        organization = Organization.objects.only(
            "id", "slug", "display_name", "status"
        ).get(id=membership.organization_id)
    projection.sync_access_for_membership(
        user_id=membership.user_id, membership=membership, organization=organization
    )


# --- organizations ------------------------------------------------------------------


def create_organization_and_owner_membership(
    *,
    user_id: UUID,
    name: str,
    owner_status: MembershipStatus = MembershipStatus.ACTIVE,
) -> CreateOrganizationResult:
    """The two rows organization creation genuinely owns: ``Organization``
    and its creator's owner ``Membership`` (and, for an owner who is already
    active, their chooser row). The caller (``platform.onboarding``) continues
    the same transaction with the role assignment and audit entry that make
    the pair meaningful (Task 5 - one transaction, no partial state).

    Runs with no tenant context, by construction: there is no organization
    to scope to until this creates one. Callers must have already validated
    ``name`` (``validate_name``) and resolved the acting identity - this
    function trusts both, exactly as ``platform.organizations.resolver``'s
    tenant-bootstrap query trusts a caller-verified ``user_id``.
    """
    from workeasy360.platform.organizations.models import Membership, Organization
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        transaction.atomic(),
        unscoped_access(
            reason="Organization creation: no tenant context can exist before "
            "this call creates the tenant",
            module="platform.organizations.services",
        ),
    ):
        # The slug check in generate_unique_slug races with a concurrent
        # creation of a same-named organization; the unique constraint is
        # what decides. The loser's insert waits for the winner's commit and
        # then fails, so a retry (in a savepoint, leaving the enclosing
        # transaction usable) sees the slug taken and picks a suffixed one.
        # Found by test_distinct_keys_from_many_threads_create_independent_
        # complete_organizations: without this, the customer got a 400.
        for attempt in range(_SLUG_ATTEMPTS):
            try:
                with transaction.atomic():
                    organization = Organization.objects.create(
                        slug=generate_unique_slug(name),
                        legal_name=name,
                        display_name=name,
                        # Task 8: ACTIVE, not the model's own PROVISIONING
                        # default, which permits neither read nor write - a
                        # fresh organization left there would be unusable by
                        # the owner it was just created for.
                        status=OrganizationStatus.ACTIVE.value,
                        created_by_id=user_id,
                    )
                break
            except IntegrityError as exc:
                if attempt == _SLUG_ATTEMPTS - 1:
                    raise OrganizationValidationError(
                        "Could not create the organization. Try again.", field="name"
                    ) from exc

        with organization_db_scope(organization.id):
            membership = Membership(
                organization_id=organization.id,
                user_id=user_id,
                status=owner_status.value,
                is_owner=True,
                member_type=MemberType.INTERNAL.value,
                # An invited owner has not joined yet; the acceptance stamps it.
                joined_at=timezone.now()
                if owner_status is MembershipStatus.ACTIVE
                else None,
            )
            membership.save()
        if owner_status is MembershipStatus.ACTIVE:
            _mirror(membership, organization)

    return CreateOrganizationResult(
        organization_id=organization.id,
        slug=organization.slug,
        display_name=organization.display_name,
        membership_id=membership.id,
        created=True,
    )


def ensure_organization(
    *, organization_id: UUID, slug: str, name: str, created_by_id: UUID
) -> bool:
    """Create an organization with a fixed identity unless it exists - the
    control-plane anchor, which has no members. Idempotent and race-safe;
    returns whether this call created it."""
    from workeasy360.platform.organizations.models import Organization

    if Organization.objects.filter(id=organization_id).exists():
        return False
    try:
        with transaction.atomic():
            Organization.objects.create(
                id=organization_id,
                slug=slug,
                legal_name=name,
                display_name=name,
                status=OrganizationStatus.ACTIVE.value,
                created_by_id=created_by_id,
            )
    except IntegrityError:
        # Lost the creation race: the row now exists, which is the goal.
        if not Organization.objects.filter(id=organization_id).exists():
            raise
        return False
    return True


def set_lifecycle_status(
    organization_id: UUID, *, target: OrganizationStatus, reason: str
) -> OrganizationStatus:
    """Suspend or reactivate one organization. Returns the previous status.

    The row is locked, so two staff members acting on one customer serialise
    and the second sees the first's result. Only ``active <-> suspended`` is
    offered: a locked, provisioning or pending-deletion organization has
    lifecycle rules of its own that this must not shortcut. A move to the state
    the organization is already in is a no-op. Nothing is deleted: suspension
    is read-only (a customer whose card expired must still see its invoices,
    export its data and pay - 03-multi-tenancy.md section 3.1), reactivation
    restores writes, and the data was never touched.

    Authorization and audit are the caller's (``controlplane``).
    """
    from workeasy360.platform.organizations import projection
    from workeasy360.platform.organizations.models import Organization

    with transaction.atomic():
        organization = (
            Organization.objects.select_for_update().filter(id=organization_id).first()
        )
        if organization is None:
            raise OrganizationValidationError(
                "No such organization.", field="organization_id"
            )
        previous = organization.status_enum
        if previous is target:
            return previous
        legal = {
            (OrganizationStatus.ACTIVE, OrganizationStatus.SUSPENDED),
            (OrganizationStatus.SUSPENDED, OrganizationStatus.ACTIVE),
        }
        if (previous, target) not in legal:
            raise InvalidStateTransition(
                entity="organizations.Organization",
                from_status=previous.value,
                to_status=target.value,
            )
        suspending = target is OrganizationStatus.SUSPENDED
        organization.status = target.value
        organization.suspended_at = timezone.now() if suspending else None
        organization.suspension_reason = reason if suspending else ""
        organization.save(
            update_fields=["status", "suspended_at", "suspension_reason", "updated_at"]
        )
        # Every member's chooser row carries the status (Task 20).
        projection.sync_access_metadata_for_organization(organization)
    return previous


#: The organization's own access policy (Phase 8 extension), applied by
#: ``contracts.values.access_policy`` at both doors - console and product sign-in.
ACCESS_POLICY_FIELDS = (
    "session_max_age_minutes",
    "password_min_length",
    "ip_allowlist_enabled",
    "ip_allowlist",
)


def update_access_policy(
    organization_id: UUID, changes: dict[str, Any], *, expected_version: int | None
) -> dict[str, Any]:
    """Apply already-validated access-policy values and move the policy's
    version. Returns the previous value of each changed field, for the
    caller's audit entry.

    ``expected_version`` is the version the editor loaded: two administrators
    saving at once get ``SecurityPolicyConflict``, not a silently merged
    allow-list. ``None`` only for staff break-glass, which overrides whatever
    the customer last saved.
    """
    from workeasy360.platform.organizations.models import Organization

    unknown = set(changes) - set(ACCESS_POLICY_FIELDS)
    if unknown:
        raise ValueError(f"Not access-policy fields: {sorted(unknown)}")
    with transaction.atomic():
        organization = Organization.objects.select_for_update().get(id=organization_id)
        if (
            expected_version is not None
            and organization.security_policy_version != expected_version
        ):
            raise SecurityPolicyConflict(
                "Someone else changed this policy. Reload it and try again.",
                field="version",
            )
        before = {name: getattr(organization, name) for name in changes}
        for name, value in changes.items():
            setattr(organization, name, value)
        organization.security_policy_version += 1
        organization.security_policy_updated_at = timezone.now()
        organization.save(
            update_fields=[
                *changes,
                "security_policy_version",
                "security_policy_updated_at",
                "updated_at",
            ]
        )
    return before


def rename_organization(
    organization_id: UUID, display_name: Any
) -> tuple[str, str] | None:
    """Change the display name (never the slug, which is URL identity and
    immutable). Returns ``(before, after)``, or ``None`` when the name is
    already that; every member's chooser row is refreshed in the same
    transaction."""
    from workeasy360.platform.organizations import projection
    from workeasy360.platform.organizations.models import Organization

    name = validate_name(display_name)
    with transaction.atomic():
        organization = Organization.objects.select_for_update().get(id=organization_id)
        before = organization.display_name
        if before == name:
            return None
        organization.display_name = name
        organization.save(update_fields=["display_name", "updated_at"])
        projection.sync_access_metadata_for_organization(organization)
    return before, name


# --- memberships --------------------------------------------------------------------


def _locked_member(organization_id: UUID, membership_id: UUID) -> Any:
    """A member of the organization in scope, locked; never a removed one.
    ``Membership.objects`` is the tenant-scoped manager, so another
    organization's member and no member at all are the same 404."""
    from workeasy360.platform.organizations.models import Membership

    membership = (
        Membership.objects.select_for_update()
        .filter(id=membership_id, organization_id=organization_id)
        .first()
    )
    if membership is None or membership.status == MembershipStatus.REMOVED.value:
        raise MemberNotFound("No such member.")
    return membership


def lock_member(organization_id: UUID, membership_id: UUID) -> MemberRecord:
    """Lock a member for the caller's transaction and say what it is - for a
    change that is recorded elsewhere (a role) but must serialise on the
    member."""
    return _record(_locked_member(organization_id, membership_id))


def set_member_status(
    organization_id: UUID,
    membership_id: UUID,
    *,
    target: MembershipStatus,
    actor_membership_id: UUID,
) -> MemberRecord:
    """Suspend (``SUSPENDED``) or reactivate (``ACTIVE``) a member. The owner
    is never suspended, nor is the actor themselves (the same mistake, made on
    oneself, has no one left to undo it)."""
    with transaction.atomic():
        membership = _locked_member(organization_id, membership_id)
        if target is MembershipStatus.SUSPENDED:
            if membership.is_owner:
                raise MembershipConflict("The organization owner cannot be suspended.")
            if membership.id == actor_membership_id:
                raise MembershipConflict("You cannot suspend yourself.")
            if membership.status != MembershipStatus.ACTIVE.value:
                raise MembershipConflict("Only an active member can be suspended.")
        elif target is MembershipStatus.ACTIVE:
            if membership.status != MembershipStatus.SUSPENDED.value:
                raise MembershipConflict("Only a suspended member can be reactivated.")
        else:
            raise ValueError(f"A member is not set to {target.value} this way.")
        membership.status = target.value
        membership.save(update_fields=["status", "updated_at"])
        _mirror(membership)
    return _record(membership)


def cut_off_member_sessions(
    organization_id: UUID,
    membership_id: UUID,
    *,
    actor_membership_id: UUID,
    at: datetime,
) -> MemberRecord:
    """A session signed in before ``at`` no longer reaches this organization.
    Only the owner may cut off the owner."""
    with transaction.atomic():
        membership = _locked_member(organization_id, membership_id)
        if membership.is_owner and membership.id != actor_membership_id:
            raise MembershipConflict("Only the owner can sign the owner out.")
        membership.sessions_valid_after = at
        membership.save(update_fields=["sessions_valid_after", "updated_at"])
    return _record(membership)


def join_member(organization_id: UUID, user_id: UUID) -> tuple[MemberRecord, bool]:
    """Make this person an active member (an invitation accepted). Returns
    ``(member, joined)``; ``joined`` is False when they already were one.

    A removed member comes back; a suspended one is not reinstated this way,
    and a pending owner (partner-provisioned) accepts through
    :func:`activate_invited_owner`. Runs in the organization's database scope
    the caller entered - no tenant context exists before someone joins.
    """
    from workeasy360.platform.organizations.models import Membership

    membership = (
        Membership.unscoped.select_for_update()
        .filter(organization_id=organization_id, user_id=user_id)
        .first()
    )
    if membership is not None and membership.status == MembershipStatus.SUSPENDED.value:
        raise MembershipConflict(
            "Your membership of this organization is suspended. Ask an "
            "administrator to reactivate it."
        )
    if membership is not None and membership.status == MembershipStatus.INVITED.value:
        raise MembershipConflict("Accept your ownership invitation instead.")
    if membership is not None and membership.status == MembershipStatus.ACTIVE.value:
        return _record(membership), False

    now = timezone.now()
    if membership is None:
        membership = Membership(
            organization_id=organization_id,
            user_id=user_id,
            status=MembershipStatus.ACTIVE.value,
            joined_at=now,
        )
        membership.save()
    else:
        membership.status = MembershipStatus.ACTIVE.value
        membership.joined_at = now
        membership.removed_at = None
        membership.save(update_fields=["status", "joined_at", "removed_at", "updated_at"])
    _mirror(membership)
    return _record(membership), True


def activate_invited_owner(
    organization_id: UUID, user_id: UUID
) -> tuple[MemberRecord, OrganizationStatus, bool]:
    """The invited owner of a partner-provisioned organization accepts.
    Returns ``(owner, organization status, activated)``; ``activated`` is
    False when they already had. Anything but a pending owner membership of
    this person is ``PermissionDenied``, uniformly.

    The caller has verified that ``user_id`` is the authenticated person the
    invitation was issued to.
    """
    from workeasy360.platform.organizations.models import Membership, Organization
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        transaction.atomic(),
        unscoped_access(
            reason="An invited owner accepts before any tenant context can exist",
            module="platform.organizations.services",
        ),
        organization_db_scope(organization_id),
    ):
        organization = Organization.objects.filter(id=organization_id).first()
        membership = (
            Membership.unscoped.select_for_update()
            .filter(organization_id=organization_id, user_id=user_id, is_owner=True)
            .first()
        )
        if organization is None or membership is None:
            raise PermissionDenied("No pending invitation.")
        status = organization.status_enum
        if membership.status == MembershipStatus.ACTIVE.value:
            return _record(membership), status, False
        if membership.status != MembershipStatus.INVITED.value:
            raise PermissionDenied("No pending invitation.")
        membership.status = MembershipStatus.ACTIVE.value
        membership.joined_at = timezone.now()
        membership.save(update_fields=["status", "joined_at", "updated_at"])
        _mirror(membership, organization)
    return _record(membership), status, True


def mark_membership_removed(organization_id: UUID, membership_id: UUID) -> MemberRecord:
    """Remove a member (and their chooser row). The owner cannot be removed
    this way (``uq_one_owner_per_org`` would refuse it at the schema level
    regardless; refusing here gives a controlled 400 instead of an
    ``IntegrityError``, Task 36). Authorization, seats and audit are the
    caller's (``platform.onboarding.services.remove_member``).

    Runs inside an already-open tenant context (a real ``/orgs/<slug>/...``
    request), so no ``unscoped_access`` is needed.
    """
    from workeasy360.platform.organizations.models import Membership

    # Locked, and not already removed: of two concurrent removals of one
    # member, the second waits, then finds nothing left to remove - one
    # state change, one audit entry - instead of both "succeeding".
    membership = (
        Membership.objects.select_for_update()
        .filter(id=membership_id, organization_id=organization_id)
        .exclude(status=MembershipStatus.REMOVED.value)
        .first()
    )
    if membership is None:
        raise OrganizationValidationError("No such membership.", field="membership_id")
    if membership.is_owner:
        raise OrganizationValidationError(
            "The organization owner cannot be removed.", field="membership_id"
        )

    membership.status = MembershipStatus.REMOVED.value
    membership.removed_at = timezone.now()
    membership.save(update_fields=["status", "removed_at", "updated_at"])
    _mirror(membership)
    return _record(membership)


# --- member sign-in activity ----------------------------------------------------------


def record_member_sign_in(
    *,
    organization_id: UUID,
    membership_id: UUID,
    user_id: UUID,
    session_ref: UUID,
    ip_prefix: str,
    device_label: str,
) -> None:
    """A member entered the organization in this browser session. Idempotent
    per (membership, session): ``ON CONFLICT DO NOTHING``."""
    from workeasy360.platform.organizations.models import MemberSignIn

    MemberSignIn.objects.bulk_create(
        [
            MemberSignIn(
                organization_id=organization_id,
                membership_id=membership_id,
                user_id=user_id,
                session_ref=session_ref,
                ip_prefix=ip_prefix[:64],
                device_label=device_label[:64],
            )
        ],
        ignore_conflicts=True,
    )


def prune_member_sign_ins(*, older_than_days: int) -> int:
    """Delete sign-in activity past its retention, one organization at a time.

    Cross-tenant by nature (a retention sweep visits every tenant), so each
    organization's own database scope is entered before its rows are touched -
    RLS still confines every delete to one tenant.
    """
    from datetime import timedelta

    from workeasy360.platform.organizations.models import MemberSignIn
    from workeasy360.platform.organizations.selectors import all_organization_ids
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    cutoff = timezone.now() - timedelta(days=max(1, int(older_than_days)))
    deleted = 0
    for organization_id in all_organization_ids():
        with (
            transaction.atomic(),
            unscoped_access(
                reason="Retention sweep of organization sign-in activity",
                module="platform.organizations.services",
            ),
            organization_db_scope(organization_id),
        ):
            count, _ = MemberSignIn.unscoped.filter(
                organization_id=organization_id, occurred_at__lt=cutoff
            ).delete()
            deleted += count
    return deleted

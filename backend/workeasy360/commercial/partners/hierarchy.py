"""Sub-partners: one partner delegating to another (ADR-045).

A sub-partner is an ordinary ``PartnerAccount`` that happens to have a live
``PartnerHierarchyLink`` row naming a parent. There is no agency model, no
sub-agency model and no second kind of account: everything a sub-partner can do
is decided by *its own* capability grants, member roles and product grants,
read live like any partner's.

What a parent holding ``subpartner.manage`` can do, and the ceiling on each:

- **Create** a sub-partner as a ``draft`` account with one named administrator.
  The parent chooses a name; the platform chooses the code.
- **Equip** it: capabilities only from ``DELEGABLE_CAPABILITIES`` *and* only ones
  the parent itself holds; products only ones the parent holds an active
  whole-product grant for. A parent cannot hand over what it does not have, so a
  delegation chain can never widen anyone's authority.
- **Activate**, **suspend** and **resume** it. Terminating a partner and
  anything involving money (commission, payouts) stay with Workeasy360 staff.

What a parent can never do: see or change the child's customers, billing,
profile, payout details, secrets or members' passwords; grant commission,
payout or custom-domain powers; or make the child a parent (one level deep).
The depth is the constant ``MAX_DEPTH`` - the schema allows deeper chains and
raising it is a policy decision, not a migration.

Every command locks the rows it decides on, validates before its first write,
audits through the one audit log and is idempotent on a retried call.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from workeasy360.commercial.partners import capabilities, selectors, services
from workeasy360.commercial.partners.access import require_transacting
from workeasy360.commercial.partners.domain import (
    assert_partner_transition,
    clean_reason,
    clean_text,
)
from workeasy360.contracts.enums import (
    GrantStatus,
    PartnerHierarchyStatus,
    PartnerStatus,
    PrincipalKind,
)
from workeasy360.contracts.errors import (
    PartnerNotActive,
    PartnerRecordNotFound,
    PartnerValidationError,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.observability.metrics import metrics

VIEW: Final = "platform.partner_subpartner.view"
MANAGE: Final = "platform.partner_subpartner.manage"

#: How many levels below a partner the hierarchy may go. One: a sub-partner
#: cannot itself have sub-partners. Extensible by changing this number and
#: reviewing the ceilings above - no schema change.
MAX_DEPTH: Final = 1

#: A parent may create at most this many live sub-partners. A guard against a
#: scripted flood of accounts, not a commercial limit (none exists).
MAX_LIVE_CHILDREN: Final = 200

HIERARCHY_OPERATIONS: Final = "partner_hierarchy_operations_total"

_SLUG_RE: Final = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class SubPartnerCreated:
    child_id: UUID
    link_id: UUID
    code: str
    administrator_invited: bool


# --- reading the tree ----------------------------------------------------------


def parent_link_for(child_id: UUID) -> Any | None:
    """The live link naming a parent for ``child_id``, or ``None``."""
    from workeasy360.commercial.partners.models import PartnerHierarchyLink

    return (
        PartnerHierarchyLink.objects.filter(child_id=child_id)
        .exclude(status=PartnerHierarchyStatus.ENDED.value)
        .select_related("parent")
        .first()
    )


def depth_below(partner_id: UUID) -> int:
    """How many live levels sit below ``partner_id`` (0 for a leaf)."""
    from workeasy360.commercial.partners.models import PartnerHierarchyLink

    levels = 0
    frontier = [partner_id]
    seen = {partner_id}
    while frontier:
        frontier = [
            c
            for c in PartnerHierarchyLink.objects.filter(parent_id__in=frontier)
            .exclude(status=PartnerHierarchyStatus.ENDED.value)
            .values_list("child_id", flat=True)
            if c not in seen
        ]
        if not frontier:
            break
        seen.update(frontier)
        levels += 1
    return levels


def depth_above(partner_id: UUID) -> int:
    """How many live ancestors ``partner_id`` has."""
    levels = 0
    seen = {partner_id}
    current = partner_id
    while True:
        link = parent_link_for(current)
        if link is None or link.parent_id in seen:
            return levels
        seen.add(link.parent_id)
        current = link.parent_id
        levels += 1


def is_descendant(ancestor_id: UUID, partner_id: UUID) -> bool:
    """Whether ``partner_id`` sits (live) somewhere below ``ancestor_id``."""
    seen = {partner_id}
    current = partner_id
    while True:
        link = parent_link_for(current)
        if link is None or link.parent_id in seen:
            return False
        if link.parent_id == ancestor_id:
            return True
        seen.add(link.parent_id)
        current = link.parent_id


def _own_child_link(ctx: TenantContext, child_id: UUID, *, lock: bool = False) -> Any:
    """The live link from *this caller's partner* to ``child_id``.

    Anything else - a stranger's child, an unrelated partner, the caller's own
    parent, the caller itself - is the same 404, so a guessed id reveals nothing.
    """
    from workeasy360.commercial.partners.models import PartnerHierarchyLink

    queryset = PartnerHierarchyLink.objects.filter(
        parent_id=ctx.partner_id, child_id=child_id
    ).exclude(status=PartnerHierarchyStatus.ENDED.value)
    if lock:
        queryset = queryset.select_for_update()
    link = queryset.first()
    if link is None:
        raise PartnerRecordNotFound("No such sub-partner.", partner_id=str(child_id))
    return link


# --- authorization -------------------------------------------------------------


def _require_parent(ctx: TenantContext, *, write: bool) -> None:
    """The caller is a partner user acting for their own partner, holding the
    sub-partner permission, and (for a write) that partner may transact."""
    if ctx.principal is not PrincipalKind.PARTNER or ctx.partner_id is None:
        raise PartnerRecordNotFound("No such partner.")
    authz.require(ctx, MANAGE if write else VIEW)
    if write:
        require_transacting(ctx)


# --- commands ------------------------------------------------------------------


def _new_code(parent_code: str, name: str) -> str:
    """A code the platform chooses: ``<parent>-<name>-<random>``, 3-48 characters,
    never client-supplied, so a code cannot be squatted or impersonate one."""
    stem = _SLUG_RE.sub("-", name.lower()).strip("-")[:16].strip("-") or "sub"
    base = f"{parent_code[:16].strip('-')}-{stem}"
    return f"{base}-{secrets.token_hex(3)}"[:48]


def _clean_capabilities(ctx: TenantContext, requested: Any) -> list[str]:
    """The requested capabilities, each delegable and each held by the parent."""
    if requested is None:
        return []
    if not isinstance(requested, list | tuple):
        raise PartnerValidationError("capabilities must be a list.", field="capabilities")
    held = selectors.active_capabilities(ctx.partner_id)
    clean: list[str] = []
    for item in requested:
        spec = capabilities.require_capability(str(item))
        if spec.key not in capabilities.DELEGABLE_CAPABILITIES:
            raise PartnerValidationError(
                f"{spec.label!r} cannot be given to a sub-partner. "
                "Ask Workeasy360 to grant it.",
                field="capabilities",
            )
        if spec.key not in held:
            raise PartnerValidationError(
                f"You do not hold {spec.label!r}, so you cannot give it away.",
                field="capabilities",
            )
        if spec.key not in clean:
            clean.append(spec.key)
    return clean


def _clean_products(ctx: TenantContext, requested: Any) -> list[str]:
    """The requested product codes, each one the parent holds a whole-product
    grant for (a plan-level grant narrows what may be handed on, so it is not
    enough to hand the product on)."""
    if requested is None:
        return []
    if not isinstance(requested, list | tuple):
        raise PartnerValidationError("products must be a list.", field="products")
    allowed = {
        g["product_code"]
        for g in selectors.granted_products(ctx.partner_id)
        if g["plan_code"] is None
    }
    clean: list[str] = []
    for item in requested:
        code = str(item or "").strip()
        if code not in allowed:
            raise PartnerValidationError(
                f"{code!r} is not a product you may give to a sub-partner.",
                field="products",
            )
        if code not in clean:
            clean.append(code)
    return clean


@in_scope
def create_sub_partner(
    ctx: TenantContext,
    *,
    display_name: Any,
    administrator_email: Any,
    administrator_name: Any = "",
    capability_keys: Any = None,
    product_codes: Any = None,
    payout_currency: Any = None,
) -> SubPartnerCreated:
    """Create a draft sub-partner under the caller's partner and invite its administrator.

    Nothing is active yet: the child can do nothing until :func:`activate_sub_partner`.
    The administrator is attached without being emailed (the child is a draft);
    activation sends the setup link, exactly as for a partner staff create.
    """
    from workeasy360.commercial.partners.models import (
        PartnerAccount,
        PartnerHierarchyLink,
    )

    _require_parent(ctx, write=True)
    name = clean_text(display_name, field="display_name", max_length=120)
    email = services._clean_email(administrator_email, required=True)
    person = clean_text(
        administrator_name, field="administrator_name", max_length=255, required=False
    )
    parent_row = selectors.partner_row(ctx.partner_id)
    currency = (
        services.parse_currency(payout_currency)
        if payout_currency
        else services.parse_currency(parent_row.payout_currency)
    )
    caps = _clean_capabilities(ctx, capability_keys)
    products = _clean_products(ctx, product_codes)

    with transaction.atomic():
        parent = services._lock_partner(ctx.partner_id)
        if depth_above(parent.id) + 1 > MAX_DEPTH:
            raise PartnerValidationError(
                "A sub-partner cannot have sub-partners of its own.", field="partner"
            )
        live = (
            PartnerHierarchyLink.objects.filter(parent_id=parent.id)
            .exclude(status=PartnerHierarchyStatus.ENDED.value)
            .count()
        )
        if live >= MAX_LIVE_CHILDREN:
            raise PartnerValidationError(
                f"You can have at most {MAX_LIVE_CHILDREN} sub-partners.", field="partner"
            )
        child = None
        for _attempt in range(5):
            try:
                with transaction.atomic():
                    child = PartnerAccount.objects.create(
                        code=_new_code(parent.code, name),
                        display_name=name,
                        payout_currency=currency.value,
                        created_by_id=ctx.user_id,
                    )
                break
            except IntegrityError:
                continue
        if child is None:  # pragma: no cover - 5 collisions of 24 random bits
            raise PartnerValidationError("Try again.", field="display_name")
        link = PartnerHierarchyLink.objects.create(
            parent_id=parent.id,
            child_id=child.id,
            created_by_id=ctx.user_id,
            created_at=timezone.now(),
        )
        for capability in caps:
            services._grant_capability(
                ctx,
                child.id,
                capability=capability,
                reason=f"Delegated by sub-partner parent {parent.code}",
            )
        for code in products:
            services._grant_product(ctx, child.id, product_code=code)
        invite = services._invite_member(
            ctx,
            child.id,
            email=email,
            role="owner",
            full_name=person,
            send_link=False,
        )
        audit.record(
            ctx,
            "partner.sub_partner_created",
            resource_type="partners.hierarchy_link",
            resource_id=link.id,
            changes={
                "partner_id": str(parent.id),
                "child_id": str(child.id),
                "capabilities": caps,
                "products": products,
            },
        )
    metrics.increment(HIERARCHY_OPERATIONS, operation="create", outcome="ok")
    return SubPartnerCreated(
        child_id=child.id,
        link_id=link.id,
        code=child.code,
        administrator_invited=invite.member is not None,
    )


@in_scope
def activate_sub_partner(ctx: TenantContext, child_id: UUID) -> Any:
    """Draft or suspended -> active, by the parent. Needs an administrator and at
    least one capability, so a sub-partner nobody can act for is never active."""
    from workeasy360.commercial.partners.models import PartnerCapabilityGrant

    _require_parent(ctx, write=True)
    with transaction.atomic():
        _own_child_link(ctx, child_id, lock=True)
        child = services._lock_partner(child_id)
        assert_partner_transition(child.status_enum, PartnerStatus.ACTIVE)
        if child.status_enum is PartnerStatus.ACTIVE:
            return child
        if child.status_enum is PartnerStatus.TERMINATED:
            raise PartnerNotActive("A terminated partner stays terminated.")
        if not services._has_admin_member(child.id):
            raise PartnerValidationError(
                "The sub-partner needs an administrator before it can be activated.",
                field="members",
            )
        if not PartnerCapabilityGrant.objects.filter(
            partner_id=child.id, status=GrantStatus.ACTIVE.value
        ).exists():
            raise PartnerValidationError(
                "Give the sub-partner at least one capability first.",
                field="capabilities",
            )
        first_time = child.status_enum is PartnerStatus.DRAFT
        previous = child.status_enum
        child.status = PartnerStatus.ACTIVE.value
        child.status_reason = "Activated by its parent partner"
        child.activated_at = timezone.now()
        services._bump(child, ["status", "status_reason", "activated_at"])
        sent = services._send_deferred_invitations(child) if first_time else 0
        audit.record(
            ctx,
            "partner.sub_partner_activated",
            resource_type="partners.partner",
            resource_id=child.id,
            changes={
                "partner_id": str(ctx.partner_id),
                "child_id": str(child.id),
                "from": previous.value,
                "invitations_sent": sent,
            },
        )
    metrics.increment(HIERARCHY_OPERATIONS, operation="activate", outcome="ok")
    return child


@in_scope
def suspend_sub_partner(ctx: TenantContext, child_id: UUID, *, reason: Any) -> Any:
    """Active -> suspended, by the parent. Reversible with :func:`activate_sub_partner`.
    Touches no customer: a suspended partner keeps its history and loses the
    ability to act, exactly as when staff suspend one."""
    _require_parent(ctx, write=True)
    why = clean_reason(reason)
    with transaction.atomic():
        link = _own_child_link(ctx, child_id, lock=True)
        child = services._lock_partner(child_id)
        assert_partner_transition(child.status_enum, PartnerStatus.SUSPENDED)
        if child.status_enum is PartnerStatus.SUSPENDED:
            return child
        child.status = PartnerStatus.SUSPENDED.value
        child.status_reason = why
        child.suspended_at = timezone.now()
        services._bump(child, ["status", "status_reason", "suspended_at"])
        services._refresh_public_hosts(child.id)
        link.status = PartnerHierarchyStatus.SUSPENDED.value
        link.version += 1
        link.save(update_fields=["status", "version"])
        audit.record(
            ctx,
            "partner.sub_partner_suspended",
            resource_type="partners.partner",
            resource_id=child.id,
            changes={"partner_id": str(ctx.partner_id), "child_id": str(child.id)},
            reason=why,
        )
    metrics.increment(HIERARCHY_OPERATIONS, operation="suspend", outcome="ok")
    return child


@in_scope
def resume_sub_partner(ctx: TenantContext, child_id: UUID) -> Any:
    """Suspended -> active, by the parent."""
    _require_parent(ctx, write=True)
    with transaction.atomic():
        link = _own_child_link(ctx, child_id, lock=True)
        if link.status != PartnerHierarchyStatus.SUSPENDED.value:
            raise PartnerValidationError(
                "That sub-partner is not suspended.", field="status"
            )
    child = activate_sub_partner(ctx, child_id)
    with transaction.atomic():
        link = _own_child_link(ctx, child_id, lock=True)
        link.status = PartnerHierarchyStatus.ACTIVE.value
        link.version += 1
        link.save(update_fields=["status", "version"])
    return child


@in_scope
def set_sub_partner_equipment(
    ctx: TenantContext,
    child_id: UUID,
    *,
    capability_keys: Any,
    product_codes: Any,
) -> dict[str, list[str]]:
    """Make the child hold exactly ``capability_keys`` (of the delegable ones) and
    ``product_codes``. Capabilities and products this command did not give are
    never touched: staff-granted powers (commission, payouts, domains) stay."""
    from workeasy360.commercial.partners.models import (
        PartnerCapabilityGrant,
        PartnerProductGrant,
    )

    _require_parent(ctx, write=True)
    wanted_caps = set(_clean_capabilities(ctx, capability_keys))
    wanted_products = set(_clean_products(ctx, product_codes))
    with transaction.atomic():
        _own_child_link(ctx, child_id, lock=True)
        child = services._lock_partner(child_id)
        if child.status_enum is PartnerStatus.TERMINATED:
            raise PartnerNotActive("A terminated partner cannot be changed.")
        have_caps = set(
            PartnerCapabilityGrant.objects.filter(
                partner_id=child.id, status=GrantStatus.ACTIVE.value
            ).values_list("capability", flat=True)
        )
        # Only delegable capabilities are this command's to add or remove.
        for capability in sorted(wanted_caps - have_caps):
            services._grant_capability(
                ctx,
                child.id,
                capability=capability,
                reason=f"Delegated by sub-partner parent {ctx.partner_id}",
            )
        for capability in sorted(
            (have_caps & capabilities.DELEGABLE_CAPABILITIES) - wanted_caps
        ):
            services._revoke_capability(
                ctx,
                child.id,
                capability=capability,
                reason="Withdrawn by its parent partner",
            )
        have_products = {
            g.product.code: g
            for g in PartnerProductGrant.objects.filter(
                partner_id=child.id, status=GrantStatus.ACTIVE.value, plan__isnull=True
            ).select_related("product")
        }
        for code in sorted(wanted_products - set(have_products)):
            services._grant_product(ctx, child.id, product_code=code)
        for code in sorted(set(have_products) - wanted_products):
            services._revoke_product_grant(ctx, child.id, grant_id=have_products[code].id)
        audit.record(
            ctx,
            "partner.sub_partner_equipped",
            resource_type="partners.partner",
            resource_id=child.id,
            changes={
                "partner_id": str(ctx.partner_id),
                "child_id": str(child.id),
                "capabilities": sorted(wanted_caps),
                "products": sorted(wanted_products),
            },
        )
    metrics.increment(HIERARCHY_OPERATIONS, operation="equip", outcome="ok")
    return {"capabilities": sorted(wanted_caps), "products": sorted(wanted_products)}

"""The Admin console's support desk (2026-11-09), on the one ticket table.

A third direction joins the two ``tickets.py`` describes: **direct**, a customer of
Workeasy360 with no partner asking Workeasy360. The desk shows those beside partners'
tickets to Workeasy360 (``platform``), filtered by who asked. A customer's ticket to its
own partner (``customer``) stays that partner's desk and is not shown here.

``platform.staff_support.view`` reads, ``platform.staff_support.manage`` answers, assigns
and resolves. The older partner-tickets screen keeps its own grants. Assignment and
resolution are audited; the audit names the ticket, never its words.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from workeasy360.commercial.partners import tickets
from workeasy360.commercial.partners.models import PartnerTicket, PartnerTicketMessage
from workeasy360.contracts.errors import PartnerRecordNotFound, PartnerValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.administration.notices import tell_staff
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.organizations.selectors import organization_ids_matching

VIEW = "platform.staff_support.view"
MANAGE = "platform.staff_support.manage"
DIRECT = "direct"
DESK_DIRECTIONS = (tickets.PLATFORM, DIRECT)
AUDIENCES = {
    "partner": (tickets.PLATFORM,),
    "customer": (DIRECT,),
    "all": DESK_DIRECTIONS,
}


def _user_names(ids: set[Any]) -> dict[Any, str]:
    from workeasy360.platform.identity.selectors import display_names

    return display_names(ids)


def _with_people(
    items: list[dict[str, Any]], rows: dict[str, Any]
) -> list[dict[str, Any]]:
    from workeasy360.platform.organizations.selectors import organization_names

    people = _user_names({r.assigned_to_id for r in rows.values()})
    customers = organization_names(
        {r.organization_id for r in rows.values() if r.organization_id}
    )
    for item in items:
        row = rows[item["id"]]
        item["audience"] = "partner" if row.direction == tickets.PLATFORM else "customer"
        item["partner_id"] = str(row.partner_id) if row.partner_id else None
        item["partner_name"] = row.partner.display_name if row.partner_id else ""
        item["organization_id"] = (
            str(row.organization_id) if row.organization_id else None
        )
        item["organization_name"] = customers.get(row.organization_id, "")
        item["assigned_to"] = str(row.assigned_to_id) if row.assigned_to_id else None
        item["assigned_to_name"] = people.get(row.assigned_to_id, "")
        item["resolution"] = row.resolution
    return items


def queue(
    ctx: TenantContext,
    *,
    audience: Any = None,
    status: Any = None,
    assigned: Any = None,
    search: Any = None,
    limit: Any = None,
    offset: Any = None,
) -> dict[str, Any]:
    """Tickets to Workeasy360, newest activity first."""
    authz.require(ctx, VIEW)
    directions = AUDIENCES.get(str(audience or "all"))
    if directions is None:
        raise PartnerValidationError("Unknown audience.", field="audience")
    rows = PartnerTicket.objects.filter(direction__in=directions).select_related(
        "partner"
    )
    if assigned == "me":
        rows = rows.filter(assigned_to_id=ctx.user_id)
    elif assigned == "none":
        rows = rows.filter(assigned_to__isnull=True)
    text = str(search or "").strip()[:80]
    if text:
        filters = (
            Q(subject__icontains=text)
            | Q(partner__display_name__icontains=text)
            | Q(organization_id__in=organization_ids_matching(text))
        )
        if text.lstrip("#").isdigit():
            filters |= Q(number=int(text.lstrip("#")))
        rows = rows.filter(filters)
    page = tickets._page(rows, status=status, limit=limit, offset=offset)
    found = {
        str(r.id): r
        for r in PartnerTicket.objects.filter(
            id__in=[i["id"] for i in page["items"]]
        ).select_related("partner")
    }
    page["items"] = _with_people(page["items"], found)
    return page


def _row(ticket_id: UUID, *, lock: bool = False) -> Any:
    rows = PartnerTicket.objects.filter(id=ticket_id, direction__in=DESK_DIRECTIONS)
    if lock:
        rows = rows.select_for_update(of=("self",))
    row = rows.select_related("partner").first()
    if row is None:
        raise PartnerRecordNotFound("No such ticket.")
    return row


@in_scope
def detail(ctx: TenantContext, ticket_id: UUID) -> dict[str, Any]:
    authz.require(ctx, VIEW)
    row = _row(ticket_id)
    out = _with_people([tickets.ticket_dict(row)], {str(row.id): row})[0]
    out["messages"] = tickets._conversation(row, show_internal=True, viewer=ctx.user_id)
    out["may_answer"] = authz.check(ctx, MANAGE).allowed
    return out


@in_scope
def reply(
    ctx: TenantContext,
    ticket_id: UUID,
    *,
    message: Any,
    status: Any = None,
    internal: Any = False,
) -> dict[str, Any]:
    authz.require(ctx, MANAGE)
    row = _row(ticket_id)
    tickets._append(
        ctx,
        row,
        side="platform",
        answering=True,
        message=message,
        internal=bool(internal),
        status=status,
    )
    return detail(ctx, ticket_id)


@in_scope
def assign(ctx: TenantContext, ticket_id: UUID, *, user_id: Any) -> dict[str, Any]:
    """Give the ticket to a staff member, or to nobody. Only staff can be assigned."""
    from workeasy360.platform.administration.staff_users import is_active_staff

    authz.require(ctx, MANAGE)
    assignee: UUID | None = None
    if user_id not in (None, ""):
        try:
            assignee = UUID(str(user_id))
        except (TypeError, ValueError) as exc:
            raise PartnerValidationError(
                "Choose a staff member.", field="user_id"
            ) from exc
        if not is_active_staff(assignee):
            raise PartnerValidationError("Choose a staff member.", field="user_id")
    with transaction.atomic():
        row = _row(ticket_id, lock=True)
        row.assigned_to_id = assignee
        row.updated_at = timezone.now()
        row.save(update_fields=["assigned_to", "updated_at"])
        audit.record(
            ctx,
            "support.ticket_assigned",
            resource_type="partners.ticket",
            resource_id=row.id,
            changes={"assigned": bool(assignee)},
        )
    return detail(ctx, ticket_id)


@in_scope
def resolve(ctx: TenantContext, ticket_id: UUID, *, resolution: Any) -> dict[str, Any]:
    authz.require(ctx, MANAGE)
    text = str(resolution or "").strip()
    if len(text) < 5 or len(text) > 500:
        raise PartnerValidationError(
            "Say how it was resolved, in 5 to 500 characters.", field="resolution"
        )
    now = timezone.now()
    with transaction.atomic():
        row = _row(ticket_id, lock=True)
        row.status = "resolved"
        row.resolution = text
        row.resolved_at = row.resolved_at or now
        row.updated_at = now
        row.save(update_fields=["status", "resolution", "resolved_at", "updated_at"])
        audit.record(
            ctx,
            "support.ticket_resolved",
            resource_type="partners.ticket",
            resource_id=row.id,
            changes={"status": "resolved"},
        )
    return detail(ctx, ticket_id)


def staff_people(ctx: TenantContext) -> list[dict[str, str]]:
    """Who a ticket can be assigned to."""
    from workeasy360.platform.administration.staff_users import active_staff

    authz.require(ctx, VIEW)
    return active_staff()


# --- a customer's side ---------------------------------------------------------------------


def customer_open(
    ctx: TenantContext,
    *,
    subject: Any,
    message: Any,
    category: Any = None,
    priority: Any = None,
) -> dict[str, Any]:
    """A member of a customer organization asks Workeasy360. The organization comes from
    the verified context; there is nothing to send that could name another."""
    title = tickets._subject(subject)
    body = tickets._body(message)
    kind = tickets._choice(
        category, tickets.CATEGORIES, field="category", default="general"
    )
    level = tickets._choice(
        priority, tickets.PRIORITIES, field="priority", default="normal"
    )
    now = timezone.now()
    first_due, resolve_due = tickets._deadlines(level, now)
    with transaction.atomic():
        number = (
            PartnerTicket.objects.filter(partner__isnull=True).aggregate(n=Max("number"))[
                "n"
            ]
            or 0
        ) + 1
        row = PartnerTicket.objects.create(
            partner_id=None,
            number=number,
            direction=DIRECT,
            organization_id=ctx.organization_id,
            subject=title,
            category=kind,
            priority=level,
            opened_by_id=ctx.user_id,
            opened_at=now,
            updated_at=now,
            first_response_due_at=first_due,
            resolve_due_at=resolve_due,
        )
        PartnerTicketMessage.objects.create(
            ticket=row, side="customer", author_id=ctx.user_id, body=body, created_at=now
        )
        audit.record(
            ctx,
            "support.ticket_opened",
            resource_type="partners.ticket",
            resource_id=row.id,
            changes={"category": kind, "priority": level},
        )
    tell_staff(
        "A customer opened a support ticket",
        f"Ticket #{number} is waiting for an answer.",
    )
    return tickets.ticket_dict(row)


def customer_list(
    ctx: TenantContext, *, status: Any = None, limit: Any = None, offset: Any = None
) -> dict[str, Any]:
    rows = PartnerTicket.objects.filter(
        direction=DIRECT, organization_id=ctx.organization_id
    )
    return tickets._page(rows, status=status or "all", limit=limit, offset=offset)


def _own(ctx: TenantContext, ticket_id: UUID) -> Any:
    row = PartnerTicket.objects.filter(
        id=ticket_id, direction=DIRECT, organization_id=ctx.organization_id
    ).first()
    if row is None:
        raise PartnerRecordNotFound("No such ticket.")
    return row


def customer_detail(ctx: TenantContext, ticket_id: UUID) -> dict[str, Any]:
    row = _own(ctx, ticket_id)
    out = tickets.ticket_dict(row)
    out["resolution"] = row.resolution
    out["messages"] = tickets._conversation(row, show_internal=False, viewer=ctx.user_id)
    return out


def customer_reply(
    ctx: TenantContext, ticket_id: UUID, *, message: Any
) -> dict[str, Any]:
    tickets._append(
        ctx, _own(ctx, ticket_id), side="customer", answering=False, message=message
    )
    return customer_detail(ctx, ticket_id)


def open_ticket_count() -> int:
    """Tickets to Workeasy360 still waiting for someone. For the bell."""
    return PartnerTicket.objects.filter(
        direction__in=DESK_DIRECTIONS, status__in=tickets.OPEN_STATUSES
    ).count()

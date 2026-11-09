"""The partner helpdesk: tickets, their conversation and the SLA (2026-11-03).

The unified partner dashboard specification asks for a two-way ticketing
system. Two directions, one table (``PartnerTicket``):

- **platform** ("upward"): a member of any partner - Affiliate, Reseller or
  White-label - asks Workeasy360. Every member may open one and follow its
  partner's (``platform.partner_dashboard.view``, the same permission the
  support form needed). Staff read them with ``platform.staff_partner.view``
  and answer with ``platform.staff_partner.manage``.
- **customer**: a member of one of the partner's *live* customer
  organizations asks the partner. The partner's support staff read them with
  ``platform.partner_desk.view`` and answer with ``platform.partner_desk.manage``
  (both unlocked by the ``support.access`` capability, which Resellers and
  White-label partners hold). The customer reaches only its own
  organization's tickets with its current partner, from the verified org
  context - there is no organization or partner id to send.

**Who is asking decides what is shown.** An answering side may write an
``internal`` note; the asking side never sees one. A message is appended and
never edited. A ticket is never deleted.

**The SLA** is two deadlines fixed when the ticket opens, from its priority
(``SLA_HOURS``): the first answer, and the resolution. Whether one is missed is
worked out on read (``sla_state``), so nothing has to run on a clock.

Audit entries name the ticket, its direction and its status - never its words.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from workeasy360.commercial.partners import capabilities, inputs
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.commercial.partners.services import _lock_partner
from workeasy360.contracts.errors import (
    PartnerRecordNotFound,
    PartnerRequestConflict,
    PartnerValidationError,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

ASK: Final = "platform.partner_dashboard.view"
DESK_VIEW: Final = "platform.partner_desk.view"
DESK_MANAGE: Final = "platform.partner_desk.manage"
STAFF_VIEW: Final = "platform.staff_partner.view"
STAFF_MANAGE: Final = "platform.staff_partner.manage"

PLATFORM: Final = "platform"
CUSTOMER: Final = "customer"
DIRECTIONS: Final = (PLATFORM, CUSTOMER)

PRIORITIES: Final = ("low", "normal", "high", "urgent")
CATEGORIES: Final = (
    "general",
    "technical",
    "billing",
    "commission",
    "payout",
    "account",
    "product",
)
STATUSES: Final = ("open", "in_progress", "waiting", "resolved", "closed")
OPEN_STATUSES: Final = ("open", "in_progress", "waiting")

#: (first answer, resolution) in hours, by priority.
SLA_HOURS: Final = {
    "urgent": (1, 8),
    "high": (4, 24),
    "normal": (8, 48),
    "low": (24, 96),
}
#: A deadline is "at risk" in the last quarter of the time it allows.
AT_RISK_SHARE: Final = 0.25

MAX_SUBJECT: Final = 200
MAX_BODY: Final = 5000
MAX_CANNED: Final = 100
MAX_OPEN_PER_ASKER: Final = 50
PAGE: Final = 50
SUPPORT_TEMPLATE: Final = "platform.partner.support_request"


def _model() -> Any:
    from workeasy360.commercial.partners.models import PartnerTicket

    return PartnerTicket


def _messages() -> Any:
    from workeasy360.commercial.partners.models import PartnerTicketMessage

    return PartnerTicketMessage


def _canned() -> Any:
    from workeasy360.commercial.partners.models import PartnerCannedResponse

    return PartnerCannedResponse


# --- reading ------------------------------------------------------------------------------


def sla_state(row: Any, now: datetime | None = None) -> dict[str, Any]:
    """Where the ticket stands against its two deadlines: ``met`` once
    answered (or resolved) in time, ``breached`` once a deadline passed
    without it, ``at_risk`` in the last quarter of the time a deadline allows,
    else ``on_track``."""
    now = now or timezone.now()

    def one(done: datetime | None, due: datetime) -> str:
        if done is not None:
            return "met" if done <= due else "breached"
        if row.status in ("resolved", "closed"):
            return "met"
        if now > due:
            return "breached"
        if due - now <= (due - row.opened_at) * AT_RISK_SHARE:
            return "at_risk"
        return "on_track"

    first = one(row.first_response_at, row.first_response_due_at)
    resolve = one(row.resolved_at or row.closed_at, row.resolve_due_at)
    worst = next(
        (s for s in ("breached", "at_risk", "on_track") if s in (first, resolve)), "met"
    )
    return {
        "first_response_due_at": row.first_response_due_at.isoformat(),
        "resolve_due_at": row.resolve_due_at.isoformat(),
        "first_response": first,
        "resolution": resolve,
        "state": worst,
    }


def ticket_dict(row: Any, *, now: datetime | None = None) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "number": row.number,
        "reference": f"#{row.number}",
        "direction": row.direction,
        "subject": row.subject,
        "category": row.category,
        "priority": row.priority,
        "status": row.status,
        "related_ref": row.related_ref,
        "opened_at": row.opened_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "first_response_at": row.first_response_at.isoformat()
        if row.first_response_at
        else None,
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
        "closed_at": row.closed_at.isoformat() if row.closed_at else None,
        "sla": sla_state(row, now),
    }


def _message_dict(m: Any, labels: dict[str, str], viewer: Any) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "side": m.side,
        # Who wrote it, by side - never a person's address across the desk.
        "author": labels.get(m.side, ""),
        "mine": m.author_id == viewer,
        "body": m.body,
        "internal": m.internal,
        "created_at": m.created_at.isoformat(),
    }


def _conversation(row: Any, *, show_internal: bool, viewer: Any) -> list[dict[str, Any]]:
    rows = list(_messages().objects.filter(ticket=row).order_by("created_at", "id"))
    if not show_internal:
        rows = [m for m in rows if not m.internal]
    labels = {
        "platform": "Workeasy360 Support",
        "partner": row.partner.display_name if row.partner_id else "",
        "customer": _customer_names(row.partner_id, [row.organization_id]).get(
            row.organization_id, ""
        )
        or "Customer",
    }
    return [_message_dict(m, labels, viewer) for m in rows]


def _customer_names(partner_id: UUID, organization_ids: list[Any]) -> dict[Any, str]:
    """The names of the partner's *live* customers among ``organization_ids`` -
    an ended relationship shows no name, as on the customer list."""
    from workeasy360.commercial.partners.models import PartnerOrganizationLink

    wanted = [o for o in organization_ids if o]
    if not wanted:
        return {}
    return dict(
        PartnerOrganizationLink.objects.filter(
            partner_id=partner_id, organization_id__in=wanted
        )
        .exclude(status="ended")
        .values_list("organization_id", "organization__display_name")
    )


def _page(queryset: Any, *, status: Any, limit: Any, offset: Any) -> dict[str, Any]:
    wanted = str(status or "open")
    if wanted not in (*STATUSES, "all", "open"):
        raise PartnerValidationError(
            f"status must be one of {[*STATUSES, 'all']}.", field="status"
        )
    try:
        size = max(1, min(int(limit or PAGE), 100))
        start = max(0, int(offset or 0))
    except (TypeError, ValueError):
        raise PartnerValidationError(
            "limit and offset must be numbers.", field="limit"
        ) from None
    counts = {s: 0 for s in STATUSES}
    for r in queryset.values("status").annotate(n=Count("id")):
        counts[r["status"]] = r["n"]
    if wanted == "open":
        queryset = queryset.filter(status__in=OPEN_STATUSES)
    elif wanted != "all":
        queryset = queryset.filter(status=wanted)
    total = queryset.count()
    now = timezone.now()
    rows = list(queryset.order_by("-updated_at", "-id")[start : start + size])
    return {
        "items": [ticket_dict(r, now=now) for r in rows],
        "counts": counts,
        "total": total,
        "limit": size,
        "offset": start,
    }


def open_counts(partner_id: UUID, *, desk: bool) -> dict[str, int]:
    """The dashboard's "open tickets": the partner's own open tickets to
    Workeasy360 and, for a member of its desk, its customers' open tickets;
    and how many of all of those have missed a deadline."""
    now = timezone.now()
    queryset = _model().objects.filter(partner_id=partner_id, status__in=OPEN_STATUSES)
    if not desk:
        queryset = queryset.filter(direction=PLATFORM)
    rows = list(
        queryset.only(
            "status",
            "opened_at",
            "first_response_at",
            "first_response_due_at",
            "resolved_at",
            "closed_at",
            "resolve_due_at",
        )[:1000]
    )
    return {
        "open": len(rows),
        "breached": sum(1 for r in rows if sla_state(r, now)["state"] == "breached"),
    }


# --- validation -----------------------------------------------------------------------------


def _choice(value: Any, allowed: tuple[str, ...], *, field: str, default: str) -> str:
    text = str(value or default)
    if text not in allowed:
        raise PartnerValidationError(f"Choose a {field}.", field=field)
    return text


def _subject(value: Any) -> str:
    text = inputs.clean_markup_free(value, field="subject", max_length=MAX_SUBJECT)
    if not text:
        raise PartnerValidationError("Enter a subject.", field="subject")
    return text


def _body(value: Any) -> str:
    text = inputs.clean_note(value, field="message", max_length=MAX_BODY)
    if not text.strip():
        raise PartnerValidationError("Enter a message.", field="message")
    return text


def _related(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    kind, _, ident = text.partition(":")
    if kind not in ("commission", "payout", "order", "withdrawal"):
        raise PartnerValidationError(
            "That is not something a ticket can be about.", field="related_ref"
        )
    try:
        UUID(ident)
    except ValueError:
        raise PartnerValidationError(
            "That is not something a ticket can be about.", field="related_ref"
        ) from None
    return f"{kind}:{ident}"


def _deadlines(priority: str, now: datetime) -> tuple[datetime, datetime]:
    first, resolve = SLA_HOURS[priority]
    return now + timedelta(hours=first), now + timedelta(hours=resolve)


# --- the commands ----------------------------------------------------------------------------


def _create(
    ctx: TenantContext,
    *,
    partner_id: UUID,
    direction: str,
    organization_id: UUID | None,
    side: str,
    subject: Any,
    message: Any,
    category: Any,
    priority: Any,
    related_ref: Any = "",
) -> Any:
    title = _subject(subject)
    body = _body(message)
    kind = _choice(category, CATEGORIES, field="category", default="general")
    level = _choice(priority, PRIORITIES, field="priority", default="normal")
    ref = _related(related_ref)
    now = timezone.now()
    with transaction.atomic():
        _lock_partner(partner_id)
        mine = _model().objects.filter(
            partner_id=partner_id, opened_by_id=ctx.user_id, status__in=OPEN_STATUSES
        )
        if mine.count() >= MAX_OPEN_PER_ASKER:
            raise PartnerRequestConflict(
                f"You already have {MAX_OPEN_PER_ASKER} open tickets. Close some first.",
                status="open",
            )
        number = (
            _model().objects.filter(partner_id=partner_id).aggregate(n=Max("number"))["n"]
            or 0
        ) + 1
        first_due, resolve_due = _deadlines(level, now)
        row = _model().objects.create(
            partner_id=partner_id,
            number=number,
            direction=direction,
            organization_id=organization_id,
            subject=title,
            category=kind,
            priority=level,
            related_ref=ref,
            opened_by_id=ctx.user_id,
            opened_at=now,
            updated_at=now,
            first_response_due_at=first_due,
            resolve_due_at=resolve_due,
        )
        _messages().objects.create(
            ticket=row, side=side, author_id=ctx.user_id, body=body, created_at=now
        )
        audit.record(
            ctx,
            "partner.ticket_opened",
            resource_type="partners.ticket",
            resource_id=row.id,
            changes={
                "partner_id": str(partner_id),
                "direction": direction,
                "category": kind,
                "priority": level,
            },
        )
    return row


def _append(
    ctx: TenantContext,
    row: Any,
    *,
    side: str,
    answering: bool,
    message: Any,
    internal: Any = False,
    status: Any = None,
) -> Any:
    """Add a message and/or move the status, under the ticket's row lock.

    The answering side may write an internal note and set any status; its
    first public answer stamps ``first_response_at`` and moves an ``open``
    ticket to ``in_progress``. The asking side may only close its ticket,
    and its message reopens a ``waiting`` or ``resolved`` one. A closed
    ticket takes no more messages."""
    note = bool(internal) and answering
    body = _body(message) if message not in (None, "") else ""
    wanted = (
        None
        if status in (None, "")
        else _choice(status, STATUSES, field="status", default="open")
    )
    if not body and wanted is None:
        raise PartnerValidationError("Enter a message.", field="message")
    if wanted is not None and not answering and wanted != "closed":
        raise PartnerValidationError(
            "You can only close your own ticket.", field="status"
        )
    now = timezone.now()
    with transaction.atomic():
        locked = _model().objects.select_for_update().get(id=row.id)
        if locked.status == "closed":
            raise PartnerRequestConflict(
                "This ticket is closed. Open a new one to continue.", status="closed"
            )
        fields = ["updated_at"]
        if body:
            _messages().objects.create(
                ticket=locked,
                side=side,
                author_id=ctx.user_id,
                body=body,
                internal=note,
                created_at=now,
            )
            if answering and not note:
                if locked.first_response_at is None:
                    locked.first_response_at = now
                    fields.append("first_response_at")
                if wanted is None and locked.status == "open":
                    wanted = "in_progress"
            if (
                not answering
                and wanted is None
                and locked.status in ("waiting", "resolved")
            ):
                wanted = "open"
        if wanted is not None and wanted != locked.status:
            locked.status = wanted
            fields.append("status")
            if wanted == "resolved":
                locked.resolved_at = now
                fields.append("resolved_at")
            elif wanted == "closed":
                locked.closed_at = now
                fields.append("closed_at")
            elif wanted == "open" and locked.resolved_at is not None:
                locked.resolved_at = None
                fields.append("resolved_at")
        locked.updated_at = now
        locked.save(update_fields=fields)
        audit.record(
            ctx,
            "partner.ticket_updated",
            resource_type="partners.ticket",
            resource_id=locked.id,
            changes={
                "direction": locked.direction,
                "side": side,
                "message": bool(body),
                "internal": note,
                "status": locked.status,
            },
        )
    return locked


# --- the partner's side --------------------------------------------------------------------


def _partner_id(ctx: TenantContext) -> UUID:
    if ctx.partner_id is None:
        raise PartnerRecordNotFound("No such partner.")
    return ctx.partner_id


def _direction(value: Any) -> str:
    return _choice(value, DIRECTIONS, field="direction", default=PLATFORM)


def _require_for(ctx: TenantContext, direction: str, *, manage: bool) -> None:
    if direction == PLATFORM:
        authz.require(ctx, ASK)
    else:
        authz.require(ctx, DESK_MANAGE if manage else DESK_VIEW)


@in_scope
def partner_list(
    ctx: TenantContext,
    *,
    direction: Any = PLATFORM,
    status: Any = None,
    limit: Any = None,
    offset: Any = None,
) -> dict[str, Any]:
    """The partner's tickets in one direction: its own to Workeasy360, or
    (the desk) its customers'. A customer ticket carries the customer's name."""
    wanted = _direction(direction)
    _require_for(ctx, wanted, manage=False)
    partner_id = _partner_id(ctx)
    page = _page(
        _model().objects.filter(partner_id=partner_id, direction=wanted),
        status=status,
        limit=limit,
        offset=offset,
    )
    if wanted == CUSTOMER:
        _name_customers(page["items"], partner_id)
    page["direction"] = wanted
    page["priorities"] = list(PRIORITIES)
    page["categories"] = list(CATEGORIES)
    return page


def _name_customers(items: list[dict[str, Any]], partner_id: UUID) -> None:
    from workeasy360.commercial.partners.models import PartnerTicket

    orgs = dict(
        PartnerTicket.objects.filter(id__in=[UUID(i["id"]) for i in items]).values_list(
            "id", "organization_id"
        )
    )
    names = _customer_names(partner_id, list(orgs.values()))
    for item in items:
        item["customer"] = names.get(orgs.get(UUID(item["id"])), "")


def _own(ctx: TenantContext, ticket_id: UUID) -> Any:
    row = _model().objects.filter(id=ticket_id, partner_id=_partner_id(ctx)).first()
    if row is None:
        raise PartnerRecordNotFound("No such ticket.")
    return row


@in_scope
def partner_detail(ctx: TenantContext, ticket_id: UUID) -> dict[str, Any]:
    row = _own(ctx, ticket_id)
    _require_for(ctx, row.direction, manage=False)
    out = ticket_dict(row)
    # The partner answers its customers' tickets (it sees its own notes) and
    # asks Workeasy360 (it never sees staff notes).
    out["messages"] = _conversation(
        row, show_internal=row.direction == CUSTOMER, viewer=ctx.user_id
    )
    if row.direction == CUSTOMER:
        _name_customers([out], row.partner_id)
        out["may_answer"] = DESK_MANAGE in authz.effective_permissions(ctx)
    return out


@in_scope
def open_platform_ticket(
    ctx: TenantContext,
    *,
    subject: Any,
    message: Any,
    category: Any = "general",
    priority: Any = "normal",
    related_ref: Any = "",
) -> dict[str, Any]:
    """A partner member asks Workeasy360. The partner-support address is told
    by email once the ticket is saved, when one is configured."""
    authz.require(ctx, ASK)
    partner_id = _partner_id(ctx)
    row = _create(
        ctx,
        partner_id=partner_id,
        direction=PLATFORM,
        organization_id=None,
        side="partner",
        subject=subject,
        message=message,
        category=category,
        priority=priority,
        related_ref=related_ref,
    )
    ticket_id = row.id
    transaction.on_commit(lambda: notify_staff(ticket_id))
    return partner_detail(ctx, ticket_id)


@in_scope
def partner_reply(
    ctx: TenantContext,
    ticket_id: UUID,
    *,
    message: Any = None,
    internal: Any = False,
    status: Any = None,
) -> dict[str, Any]:
    row = _own(ctx, ticket_id)
    answering = row.direction == CUSTOMER
    _require_for(ctx, row.direction, manage=answering)
    _append(
        ctx,
        row,
        side="partner",
        answering=answering,
        message=message,
        internal=internal,
        status=status,
    )
    return partner_detail(ctx, ticket_id)


def notify_staff(ticket_id: UUID) -> None:
    """Email the partner-support address about a new upward ticket. Best
    effort: a notice that cannot be queued is logged, the ticket stands."""
    from workeasy360.platform.identity import selectors as identity_selectors
    from workeasy360.platform.notifications.services import notify_account

    address = str(getattr(settings, "W360_PARTNER_SUPPORT_EMAIL", "") or "")
    row = _model().objects.filter(id=ticket_id).select_related("partner").first()
    if not address or row is None:
        return
    first = _messages().objects.filter(ticket=row).order_by("created_at").first()
    requester = identity_selectors.contact_address(row.opened_by_id)
    try:
        notify_account(
            template_key=SUPPORT_TEMPLATE,
            recipient_email=address,
            idempotency_key=f"partner-ticket:{row.id}",
            context={
                "partner_name": row.partner.display_name,
                "partner_code": row.partner.code,
                "requester_email": requester,
                "priority": row.priority,
                "subject": f"Ticket #{row.number}: {row.subject}",
                "message": first.body if first else "",
                "workspace": row.category,
            },
            envelope={"reply_to": requester} if requester else None,
        )
    except Exception:
        logger.warning("partner_ticket_notice_failed", ticket_id=str(ticket_id))


# --- canned responses (the desk) -----------------------------------------------------------------


def _canned_dict(c: Any) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "title": c.title,
        "body": c.body,
        "updated_at": c.updated_at.isoformat(),
    }


@in_scope
def canned_list(ctx: TenantContext) -> dict[str, Any]:
    authz.require(ctx, DESK_VIEW)
    rows = _canned().objects.filter(partner_id=_partner_id(ctx)).order_by("title")
    return {"items": [_canned_dict(c) for c in rows], "max": MAX_CANNED}


@in_scope
def canned_save(
    ctx: TenantContext, *, response_id: UUID | None = None, title: Any, body: Any
) -> dict[str, Any]:
    authz.require(ctx, DESK_MANAGE)
    partner_id = _partner_id(ctx)
    name = inputs.clean_markup_free(title, field="title", max_length=120)
    if not name:
        raise PartnerValidationError("Enter a title.", field="title")
    text = _body(body)
    now = timezone.now()
    with transaction.atomic():
        _lock_partner(partner_id)
        clash = _canned().objects.filter(partner_id=partner_id, title=name)
        if response_id is not None:
            row = _canned().objects.filter(id=response_id, partner_id=partner_id).first()
            if row is None:
                raise PartnerRecordNotFound("No such saved reply.")
            clash = clash.exclude(id=response_id)
        else:
            if _canned().objects.filter(partner_id=partner_id).count() >= MAX_CANNED:
                raise PartnerValidationError(
                    f"At most {MAX_CANNED} saved replies.", field="title"
                )
            row = _canned()(partner_id=partner_id)
        if clash.exists():
            raise PartnerValidationError(
                "A saved reply with that title exists.", field="title"
            )
        row.title, row.body, row.updated_by_id, row.updated_at = (
            name,
            text,
            ctx.user_id,
            now,
        )
        row.save()
    return _canned_dict(row)


@in_scope
def canned_delete(ctx: TenantContext, response_id: UUID) -> None:
    authz.require(ctx, DESK_MANAGE)
    deleted, _ = (
        _canned().objects.filter(id=response_id, partner_id=_partner_id(ctx)).delete()
    )
    if not deleted:
        raise PartnerRecordNotFound("No such saved reply.")


# --- the customer's side (the organization console) ----------------------------------------------


def _provider(organization_id: UUID) -> Any | None:
    """The partner this organization's support goes to: its live partner, if
    that partner runs a desk (holds ``support.access``)."""
    link = partner_selectors.live_link_for_organization(organization_id)
    if link is None:
        return None
    if capabilities.SUPPORT_ACCESS not in partner_selectors.active_capabilities(
        link.partner_id
    ):
        return None
    return link.partner


def _org(ctx: TenantContext) -> UUID:
    if ctx.organization_id is None:
        raise PartnerRecordNotFound("No such organization.")
    return ctx.organization_id


def customer_overview(ctx: TenantContext, *, status: Any = "all") -> dict[str, Any]:
    """The org console's view: who its support provider is, and its tickets
    to that provider. Any member of the organization (the route's tenant
    context verified the membership)."""
    organization_id = _org(ctx)
    partner = _provider(organization_id)
    if partner is None:
        return {
            "provider": None,
            "items": [],
            "counts": {},
            "total": 0,
            "priorities": list(PRIORITIES),
            "categories": list(CATEGORIES),
        }
    page = _page(
        _model().objects.filter(
            partner_id=partner.id, direction=CUSTOMER, organization_id=organization_id
        ),
        status=status,
        limit=None,
        offset=None,
    )
    page["provider"] = {"name": partner.display_name}
    page["priorities"] = list(PRIORITIES)
    page["categories"] = list(CATEGORIES)
    return page


def _customer_ticket(ctx: TenantContext, ticket_id: UUID) -> Any:
    organization_id = _org(ctx)
    partner = _provider(organization_id)
    row = (
        _model()
        .objects.filter(
            id=ticket_id,
            direction=CUSTOMER,
            organization_id=organization_id,
            partner_id=partner.id,
        )
        .first()
        if partner is not None
        else None
    )
    if row is None:
        raise PartnerRecordNotFound("No such ticket.")
    return row


def customer_detail(ctx: TenantContext, ticket_id: UUID) -> dict[str, Any]:
    row = _customer_ticket(ctx, ticket_id)
    out = ticket_dict(row)
    out["messages"] = _conversation(row, show_internal=False, viewer=ctx.user_id)
    return out


def customer_open(
    ctx: TenantContext,
    *,
    subject: Any,
    message: Any,
    category: Any = "general",
    priority: Any = "normal",
) -> dict[str, Any]:
    organization_id = _org(ctx)
    partner = _provider(organization_id)
    if partner is None:
        raise PartnerValidationError(
            "Your organization has no support provider to send a ticket to.",
            field="subject",
        )
    row = _create(
        ctx,
        partner_id=partner.id,
        direction=CUSTOMER,
        organization_id=organization_id,
        side="customer",
        subject=subject,
        message=message,
        category=category,
        priority=priority,
    )
    return customer_detail(ctx, row.id)


def customer_reply(
    ctx: TenantContext, ticket_id: UUID, *, message: Any = None, status: Any = None
) -> dict[str, Any]:
    row = _customer_ticket(ctx, ticket_id)
    _append(ctx, row, side="customer", answering=False, message=message, status=status)
    return customer_detail(ctx, ticket_id)


# --- Workeasy360 staff (the Super Admin desk) ----------------------------------------------------


@in_scope
def staff_queue(
    ctx: TenantContext,
    *,
    status: Any = None,
    search: Any = None,
    limit: Any = None,
    offset: Any = None,
) -> dict[str, Any]:
    """Every partner's tickets to Workeasy360, newest activity first."""
    authz.require(ctx, STAFF_VIEW)
    queryset = _model().objects.filter(direction=PLATFORM).select_related("partner")
    text = str(search or "").strip()[:80]
    if text:
        filters = (
            Q(subject__icontains=text)
            | Q(partner__display_name__icontains=text)
            | Q(partner__code__icontains=text)
        )
        if text.lstrip("#").isdigit():
            filters |= Q(number=int(text.lstrip("#")))
        queryset = queryset.filter(filters)
    page = _page(queryset, status=status, limit=limit, offset=offset)
    partners = dict(
        _model()
        .objects.filter(id__in=[UUID(i["id"]) for i in page["items"]])
        .values_list("id", "partner__display_name")
    )
    for item in page["items"]:
        item["partner_name"] = partners.get(UUID(item["id"]), "")
    return page


def _staff_row(ticket_id: UUID) -> Any:
    row = (
        _model()
        .objects.filter(id=ticket_id, direction=PLATFORM)
        .select_related("partner")
        .first()
    )
    if row is None:
        raise PartnerRecordNotFound("No such ticket.")
    return row


@in_scope
def staff_detail(ctx: TenantContext, ticket_id: UUID) -> dict[str, Any]:
    authz.require(ctx, STAFF_VIEW)
    row = _staff_row(ticket_id)
    out = ticket_dict(row)
    out["messages"] = _conversation(row, show_internal=True, viewer=ctx.user_id)
    out["partner"] = {
        "id": str(row.partner_id),
        "name": row.partner.display_name,
        "code": row.partner.code,
    }
    out["may_answer"] = STAFF_MANAGE in authz.effective_permissions(ctx)
    return out


@in_scope
def staff_reply(
    ctx: TenantContext,
    ticket_id: UUID,
    *,
    message: Any = None,
    internal: Any = False,
    status: Any = None,
) -> dict[str, Any]:
    authz.require(ctx, STAFF_MANAGE)
    row = _staff_row(ticket_id)
    _append(
        ctx,
        row,
        side="platform",
        answering=True,
        message=message,
        internal=internal,
        status=status,
    )
    return staff_detail(ctx, ticket_id)


def staff_open_count() -> int:
    return _model().objects.filter(direction=PLATFORM, status__in=OPEN_STATUSES).count()

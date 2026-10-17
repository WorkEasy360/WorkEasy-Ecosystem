"""The partner's Help screens: the Notification Center and Support (ADR-046).

**Notifications are not stored twice.** A partner's notifications are entries of
the one audit log that concern the partner, worded and classified here:
``KINDS`` maps an audit action to a title, a category, whether it is important
and the permission a person needs to be told about it. Nothing is copied into a
notification table. What *is* stored is only what a person alone owns - which
of those entries they have read (``PartnerNotificationRead`` and the "read
until" mark in ``PartnerNotificationState``).

Because the feed is the audit log, it is exactly as honest as the log: an event
the platform does not record against the partner (a customer's trial ending, a
card payment) is not invented here, and is listed under ``not_implemented``.

**Support is a pointer, not a help desk.** The platform has no ticket model and
this module builds none: it tells the partner where support is (a configured
contact address, and Easy Help Desk when that product is registered).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from django.conf import settings

from workeasy360.commercial.controlplane import partner_detail
from workeasy360.commercial.controlplane.partner_portal import NOT_IMPLEMENTED
from workeasy360.commercial.partners import console_reads, notification_state, selectors
from workeasy360.contracts.errors import PartnerValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import selectors as audit_selectors
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope

DASHBOARD_VIEW: Final = "platform.partner_dashboard.view"

#: The newest entries considered. A bounded window keeps the screen one query
#: (plus two small ones for read-state) whatever the size of the log.
WINDOW: Final = 100
MAX_MARK: Final = 50
VIEWS: Final = ("all", "unread", "important")


@dataclass(frozen=True, slots=True)
class Kind:
    title: str
    category: str
    important: bool
    #: The permission a member needs to be told about this kind of event.
    permission: str


def _k(title: str, category: str, permission: str, important: bool = False) -> Kind:
    return Kind(title, category, important, permission)


CUSTOMER = "platform.partner_customer.view"
COMMISSION = "platform.partner_commission.view"
PAYOUT = "platform.partner_payout.view"
DOMAIN = "platform.partner_domain.view"
PRODUCT = "platform.partner_product.view"
PRICING = "platform.partner_pricing.view"
SUBPARTNER = "platform.partner_subpartner.view"
MEMBER_MANAGE = "platform.partner_member.manage"
APIKEY = "platform.partner_apikey.view"

#: audit action -> how it reads as a notification. Every action here is one the
#: platform really records against a partner; the brief's other examples
#: (trial ending, subscription activated, card payment status) are not recorded
#: against the partner and are therefore listed as not implemented, not faked.
KINDS: Final[dict[str, Kind]] = {
    "partner.owner_activated": _k("Customer activated", "Customers", CUSTOMER),
    "partner.customer_provisioned": _k("Customer created", "Customers", CUSTOMER),
    "partner.provision_customer": _k("Customer created", "Customers", CUSTOMER),
    "partner.relationship_ended": _k(
        "Customer relationship ended", "Customers", CUSTOMER, True
    ),
    "commission.accrued": _k("Commission generated", "Commission", COMMISSION),
    "commission.reversed": _k("Commission reversed", "Commission", COMMISSION, True),
    "commission.reversal_recorded": _k(
        "Commission reversed", "Commission", COMMISSION, True
    ),
    "commission.adjustment_recorded": _k(
        "Commission adjusted", "Commission", COMMISSION, True
    ),
    "payout.prepared": _k("Payout updated", "Payouts", PAYOUT),
    "payout.ready": _k("Payout updated", "Payouts", PAYOUT),
    "payout.approved": _k("Payout updated", "Payouts", PAYOUT, True),
    "payout.paid": _k("Payout paid", "Payouts", PAYOUT, True),
    "payout.cancelled": _k("Payout cancelled", "Payouts", PAYOUT, True),
    "partner.domain_verified": _k("Domain verified", "Domains", DOMAIN),
    "partner.domain_activated": _k("Domain activated", "Domains", DOMAIN),
    "partner.domain_verification_failed": _k(
        "Domain verification failed", "Domains", DOMAIN, True
    ),
    "partner.domain_disabled": _k("Domain disabled", "Domains", DOMAIN, True),
    "partner.product_granted": _k("Product changed", "Products", PRODUCT, True),
    "partner.product_grant_updated": _k("Product changed", "Products", PRODUCT),
    "partner.product_grant_revoked": _k("Product removed", "Products", PRODUCT, True),
    "partner.price_set": _k("Pricing changed", "Products", PRICING, True),
    "partner.sub_partner_created": _k("Agency added", "Agency", SUBPARTNER),
    "partner.sub_partner_activated": _k("Agency activated", "Agency", SUBPARTNER),
    "partner.sub_partner_suspended": _k("Agency suspended", "Agency", SUBPARTNER, True),
    "partner.active": _k("Partner account activated", "Account", DASHBOARD_VIEW, True),
    "partner.suspended": _k("Partner account suspended", "Account", DASHBOARD_VIEW, True),
    "partner.terminated": _k("Partner account ended", "Account", DASHBOARD_VIEW, True),
    "partner.capability_granted": _k("Access changed", "Account", DASHBOARD_VIEW, True),
    "partner.capability_revoked": _k("Access changed", "Account", DASHBOARD_VIEW, True),
    "partner.member_invited": _k("Team member invited", "Security", MEMBER_MANAGE),
    "partner.member_added": _k("Team member added", "Security", MEMBER_MANAGE),
    "partner.member_role_changed": _k(
        "Team role changed", "Security", MEMBER_MANAGE, True
    ),
    "partner.member_suspended": _k(
        "Team member suspended", "Security", MEMBER_MANAGE, True
    ),
    "partner.member_resumed": _k("Team member resumed", "Security", MEMBER_MANAGE),
    "partner.member_removed": _k("Team member removed", "Security", MEMBER_MANAGE, True),
    "partner.api_key_created": _k("API key created", "Security", APIKEY, True),
    "partner.api_key_rotated": _k("API key rotated", "Security", APIKEY, True),
    "partner.api_key_revoked": _k("API key revoked", "Security", APIKEY, True),
}

NOT_RECORDED: Final = {
    "customer_trial_ending": "A customer's trial ending is not recorded against the partner.",
    "subscription_activated": "A subscription starting is not recorded against the partner.",
    "payment_status": (
        "A card or provider payment result belongs to the customer's order; the "
        "partner sees the order in Payments, not a notification."
    ),
}


def _window(ctx: TenantContext, permissions: frozenset[str]) -> list[dict[str, Any]]:
    """The newest allowed audit entries about this partner, shaped as
    notifications (without read-state)."""
    allowed = [a for a, k in KINDS.items() if k.permission in permissions]
    if not allowed:
        return []
    partner_id = ctx.partner_id
    assert partner_id is not None
    page = audit_selectors.audit_page(
        actions=allowed,
        resource_ids=[str(i) for i in console_reads.audit_resource_ids(partner_id)],
        changes_match=("partner_id", str(partner_id)),
        limit=WINDOW,
    )
    out = []
    for raw in page.items:
        kind = KINDS[raw["action"]]
        shaped = partner_detail._activity_item(raw, {})
        out.append(
            {
                "id": raw["id"],
                "occurred_at": raw["occurred_at"],
                "title": kind.title,
                "category": kind.category,
                "important": kind.important,
                # Only the closed "target" facts the Activity screen already
                # allows (a hostname, a product, a role...); never the changes.
                "detail": shaped["target"],
            }
        )
    return out


def _flag(events: list[dict[str, Any]], until: Any, seen: set[str]) -> None:
    for e in events:
        stamp = datetime.fromisoformat(e["occurred_at"])
        e["read"] = e["id"] in seen or (until is not None and stamp <= until)


@in_scope
def notifications(ctx: TenantContext, *, view: Any = "all") -> dict[str, Any]:
    """The caller's notifications: ``unread`` / ``all`` / ``important``."""
    authz.require(ctx, DASHBOARD_VIEW)
    chosen = view if isinstance(view, str) and view else "all"
    if chosen not in VIEWS:
        raise PartnerValidationError(f"view must be one of {list(VIEWS)}.", field="view")
    member = selectors.member_of(ctx.partner_id, ctx.user_id)
    permissions = authz.effective_permissions(ctx)
    events = _window(ctx, permissions)
    until, seen = notification_state.read_state(member, [UUID(e["id"]) for e in events])
    _flag(events, until, seen)
    unread = [e for e in events if not e["read"]]
    if chosen == "unread":
        shown = unread
    elif chosen == "important":
        shown = [e for e in events if e["important"]]
    else:
        shown = events
    return {
        "view": chosen,
        "items": shown,
        "counts": {
            "all": len(events),
            "unread": len(unread),
            "important": sum(1 for e in events if e["important"]),
            "important_unread": sum(1 for e in unread if e["important"]),
        },
        "window": WINDOW,
        "truncated": len(events) >= WINDOW,
        "not_implemented": NOT_RECORDED,
    }


@in_scope
def mark_read(
    ctx: TenantContext, *, ids: Any = None, everything: bool = False
) -> dict[str, Any]:
    """Mark some notifications - or, with ``everything``, all of them - read for
    the caller. Only entries the caller is allowed to see can be marked: any
    other id is ignored, so this is no way to probe the audit log."""
    authz.require(ctx, DASHBOARD_VIEW)
    member = selectors.member_of(ctx.partner_id, ctx.user_id)
    if everything:
        notification_state.mark_all_read(member)
        return notifications(ctx)
    if not isinstance(ids, list) or not ids or len(ids) > MAX_MARK:
        raise PartnerValidationError(
            f"ids must be a list of 1 to {MAX_MARK} notification ids.", field="ids"
        )
    wanted: set[str] = set()
    for value in ids:
        try:
            wanted.add(str(UUID(str(value))))
        except ValueError as exc:
            raise PartnerValidationError("Not a notification id.", field="ids") from exc
    permissions = authz.effective_permissions(ctx)
    visible = {e["id"] for e in _window(ctx, permissions)}
    notification_state.mark_read(member, [UUID(i) for i in sorted(wanted & visible)])
    return notifications(ctx)


# --- support -------------------------------------------------------------------------------


@in_scope
def support(ctx: TenantContext) -> dict[str, Any]:
    """Where a partner gets help. No request is stored here."""
    from workeasy360.platform.ecosystem import selectors as ecosystem

    authz.require(ctx, DASHBOARD_VIEW)
    partner = selectors.partner_row(ctx.partner_id)
    target = ecosystem.launch_target(
        str(getattr(settings, "W360_PARTNER_HELP_DESK_PRODUCT", "") or "")
    )
    desk = {
        "name": "Easy Help Desk",
        "available": bool(target and not target.is_terminal and target.launch_url),
        "url": target.launch_url if target and not target.is_terminal else "",
        "suspended": bool(target and target.is_suspended),
    }
    address = str(getattr(settings, "W360_PARTNER_SUPPORT_EMAIL", "") or "")
    return {
        "contact": {"email": address, "available": bool(address)},
        "help_desk": desk,
        "partner": {"name": partner.display_name if partner else ""},
        "priorities": ["low", "normal", "high", "urgent"],
        "requests": None,
        "not_implemented": {"partner_tickets": NOT_IMPLEMENTED["partner_tickets"]},
    }

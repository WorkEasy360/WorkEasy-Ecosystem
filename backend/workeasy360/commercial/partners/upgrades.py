"""Partner upgrades: asking to move to a larger experience (ADR-049).

An Affiliate that wants to resell, or a Reseller that wants its own brand, does
not register again. It keeps its sign-in, its partner account and everything it
has earned, and **asks**: a ``PartnerUpgradeRequest`` is opened on the same
account, the partner completes it, staff review it, and approving it grants the
approved bundle through the one existing capability-transition service
(``experience.transition_experience``). Nothing here grants anything.

What this module owns, and what it deliberately does not:

- it owns the request table - nothing else writes it - and its lifecycle:
  ``requested`` -> ``pending_approval`` -> ``approved`` | ``rejected``, with
  ``profile_required`` when staff ask for more and ``cancelled`` when the partner
  withdraws;
- it does not grant a capability, set a landing, price anything or read the
  landing preference directly: the partner's current experience is the
  workspace its grants open first (``selectors.landing_workspace``), and
  approval (``controlplane.partner_upgrades``, which sits above this layer and
  may check the whole profile) asks ``experience.transition_experience`` to
  change it;
- a request is a **request**. A partner cannot reach a Reseller or White-label
  power by creating, editing or submitting one: the partner-side commands below
  only ever write the request row, and the staff commands need
  ``platform.staff_partner.manage``.

Business details (legal name, phone, tax number, billing contact) are not
copied into the request: the wizard saves them to the partner's profile through
the ordinary profile command, and submitting checks the profile itself. The
request holds only what has no other home yet: the products asked for, the
pricing preference, commercial notes, and the brand and domain a White-label
partner proposes.
"""

from __future__ import annotations

from typing import Any, Final
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from workeasy360.commercial.catalog import selectors as catalog_selectors
from workeasy360.commercial.partners import (
    access,
    branding,
    capabilities,
    domains,
    inputs,
    selectors,
    services,
)
from workeasy360.commercial.partners.domain import clean_text
from workeasy360.commercial.payments import partner_fees
from workeasy360.contracts.errors import (
    PartnerRecordNotFound,
    PartnerUpgradeConflict,
    PartnerValidationError,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.notifications.templates import Template, register
from workeasy360.platform.observability.correlation import current_correlation_id
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

#: A partner's own administrators raise and edit a request: it is a change to
#: the partner's business profile, and the baseline already limits that to them.
OWN_VIEW: Final = "platform.partner_profile.view"
OWN_MANAGE: Final = "platform.partner_profile.manage"
STAFF_MANAGE: Final = services.MANAGE
STAFF_VIEW: Final = "platform.staff_partner.view"

REQUESTED: Final = "requested"
PROFILE_REQUIRED: Final = "profile_required"
PENDING_APPROVAL: Final = "pending_approval"
APPROVED: Final = "approved"
REJECTED: Final = "rejected"
CANCELLED: Final = "cancelled"

#: Open means a partner still has a live request (the partial unique index).
OPEN: Final = (REQUESTED, PROFILE_REQUIRED, PENDING_APPROVAL)
#: States in which the partner may still change what it proposed.
EDITABLE: Final = (REQUESTED, PROFILE_REQUIRED)
STATES: Final = (*OPEN, APPROVED, REJECTED, CANCELLED)

#: How a partner paid the set-up fee (2026-11-03).
OFFLINE: Final = "offline"
ONLINE: Final = "online"
EXPERIENCE_LABEL: Final = {
    capabilities.AFFILIATE: "Affiliate",
    capabilities.RESELLER: "Reseller",
    capabilities.WHITE_LABEL: "White-label",
}

TEMPLATE_KEY: Final = "platform.partner.upgrade_requested"

UPGRADE_REQUESTED = register(
    Template(
        key=TEMPLATE_KEY,
        subject="{partner_name} asks to upgrade to {to_label}",
        body_text=(
            "A partner asked to upgrade on its own account.\n\n"
            "Partner: {partner_name} ({partner_code})\n"
            "From: {from_label}\n"
            "To: {to_label}\n"
            "Set-up fee: {fee_line}\n\n"
            "Review it here: {review_url}\n\n"
            "Nothing changes until an administrator approves it."
        ),
        required=(
            "partner_name",
            "partner_code",
            "from_label",
            "to_label",
            "fee_line",
            "review_url",
        ),
        description=(
            "Sent to the staff who can approve partner upgrades when a partner "
            "submits an upgrade request, or pays its set-up fee online."
        ),
        samples=(
            ("partner_name", "Acme Media"),
            ("partner_code", "ACME"),
            ("from_label", "Affiliate"),
            ("to_label", "Reseller"),
            ("fee_line", "INR 25,000.00 - paid online"),
            ("review_url", "https://admin.example.com/admin/partners/1"),
        ),
    )
)

#: Where each experience may go. A direct Affiliate -> White-label is allowed:
#: nothing in the commercial policy requires Reseller first.
PATHS: Final[dict[str, tuple[str, ...]]] = {
    capabilities.AFFILIATE: (capabilities.RESELLER, capabilities.WHITE_LABEL),
    capabilities.RESELLER: (capabilities.WHITE_LABEL,),
}

#: The wizard's steps, in order, for each target. Presentation data the screen
#: reads from the server so there is one list. ``business`` is saved to the
#: profile; ``review`` saves nothing; the rest are sections of the request.
STEPS: Final[dict[str, tuple[tuple[str, str], ...]]] = {
    capabilities.RESELLER: (
        ("business", "Business"),
        ("products", "Products"),
        ("pricing", "Pricing"),
        ("commercial", "Commercial"),
        ("review", "Review"),
    ),
    capabilities.WHITE_LABEL: (
        ("business", "Business"),
        ("brand", "Brand"),
        ("products", "Products"),
        ("pricing", "Pricing"),
        ("domain", "Domain"),
        ("review", "Review"),
    ),
}

#: The sections of ``proposal`` a target accepts (a closed set per target).
SECTIONS: Final[dict[str, tuple[str, ...]]] = {
    capabilities.RESELLER: ("products", "pricing", "commercial"),
    capabilities.WHITE_LABEL: ("products", "pricing", "commercial", "brand", "domain"),
}

MAX_PRODUCTS: Final = 25
MAX_NOTE: Final = 500
MAX_COUNT: Final = 1_000_000
PRICING_PREFERENCES: Final = ("standard", "negotiated")


# --- who is asking ----------------------------------------------------------------


def _own(ctx: TenantContext, permission: str) -> UUID:
    if ctx.partner_id is None:
        raise PartnerRecordNotFound("No such partner.")
    authz.require(ctx, permission)
    return ctx.partner_id


def _model() -> Any:
    from workeasy360.commercial.partners.models import PartnerUpgradeRequest

    return PartnerUpgradeRequest


def _locked(request_id: UUID, partner_id: UUID | None = None) -> Any:
    """Row-lock one request. With ``partner_id`` it must be that partner's: a
    request of another partner is the same 404 as one that does not exist."""
    queryset = _model().objects.select_for_update().filter(id=request_id)
    if partner_id is not None:
        queryset = queryset.filter(partner_id=partner_id)
    row = queryset.first()
    if row is None:
        raise PartnerRecordNotFound("No such upgrade request.")
    return row


def open_request(partner_id: UUID) -> Any | None:
    return _model().objects.filter(partner_id=partner_id, status__in=OPEN).first()


def eligible_targets(partner_id: UUID) -> list[str]:
    """The experiences this partner may ask for now: the ones above the
    workspace it opens first that it does not already wear. A pure function of
    the grants and the landing; a stored label decides nothing."""
    landing = selectors.landing_workspace(partner_id)
    worn = {
        item["key"]
        for item in capabilities.experience_labels(
            selectors.active_capabilities(partner_id)
        )
    }
    return [t for t in PATHS.get(landing or "", ()) if t not in worn]


# --- reading ------------------------------------------------------------------------


def request_dict(row: Any, *, staff: bool = False) -> dict[str, Any]:
    """One request as a screen reads it. Staff additionally see who raised and
    decided it and the commercial reference; a partner sees the notes staff
    wrote *for it* and nothing about the people involved."""
    out: dict[str, Any] = {
        "id": str(row.id),
        "partner_id": str(row.partner_id),
        "from_experience": row.from_experience,
        "to_experience": row.to_experience,
        "status": row.status,
        "proposal": row.proposal,
        "requested_at": row.requested_at.isoformat(),
        "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "info_request_note": row.info_request_note,
        "decision_note": row.decision_note,
        "capabilities_added": row.capabilities_added,
        "capabilities_removed": row.capabilities_removed,
        "products_granted": row.products_granted,
        "version": row.version,
        "editable": row.status in EDITABLE,
        # ADR-053: the set-up fee fixed when the request opened, if any - and
        # (2026-11-03) how it was paid.
        "setup_fee": (
            {
                "amount_minor": row.fee_amount_minor,
                "currency": row.fee_currency,
                "settled": row.fee_settled_at is not None,
                "payment_method": row.payment_method or None,
                "payment_reference": row.payment_reference,
                "paid_online": fee_paid_online(row),
                "paid_at": row.fee_paid_at.isoformat() if row.fee_paid_at else None,
                "online_payment": partner_fees.get(row.fee_payment_id),
            }
            if row.fee_amount_minor
            else None
        ),
    }
    if staff:
        out["requested_by"] = row.requested_by.email
        out["decided_by"] = row.decided_by.email if row.decided_by_id else None
        out["commercial_reference"] = row.commercial_reference
        out["correlation_id"] = str(row.correlation_id) if row.correlation_id else None
    return out


def _rows(queryset: Any) -> Any:
    return queryset.select_related("requested_by", "decided_by")


def product_options() -> list[dict[str, str]]:
    """The products a partner may ask to sell: the ones on sale. Names only."""
    return [
        {"code": p["code"], "name": p["name"]}
        for p in catalog_selectors.visible_products()
        if p["status"] == "available"
    ]


@in_scope
def state(ctx: TenantContext) -> dict[str, Any]:
    """What the partner's own screens need: where it stands, what it may ask
    for, and its request. Readable by every member; ``can_request`` says whether
    this member may act (administrators only)."""
    partner_id = _own(ctx, OWN_VIEW)
    can_request = OWN_MANAGE in authz.effective_permissions(ctx)
    current = open_request(partner_id)
    recent = list(
        _rows(
            _model()
            .objects.filter(partner_id=partner_id)
            .exclude(status__in=OPEN)
            .order_by("-requested_at")[:5]
        )
    )
    out: dict[str, Any] = {
        "current": selectors.landing_workspace(partner_id),
        "eligible": eligible_targets(partner_id),
        "can_request": can_request,
        "open": request_dict(current) if current is not None else None,
        "recent": [request_dict(r) for r in recent],
        "steps": {
            k: [{"key": s, "label": label} for s, label in v] for k, v in STEPS.items()
        },
        "products": [],
        "checklist": [],
        # What each target would cost to start now (ADR-053); None is no fee.
        # Each says how to pay it offline and whether it can be paid online.
        "fees": {
            t: _payable(program_fee(t))
            for t in (capabilities.RESELLER, capabilities.WHITE_LABEL)
        },
    }
    if current is not None:
        out["products"] = product_options()
        out["checklist"] = checklist(partner_id, current)
        if current.fee_amount_minor:
            fee = program_fee(current.to_experience)
            out["payment"] = {
                "instructions": fee["instructions"] if fee else "",
                "online_available": partner_fees.online_available(current.fee_currency),
            }
    return out


def _payable(fee: dict[str, Any] | None) -> dict[str, Any] | None:
    if fee is None:
        return None
    return {**fee, "online_available": partner_fees.online_available(fee["currency"])}


def checklist(partner_id: UUID, row: Any) -> list[dict[str, Any]]:
    """What the request still needs, decided on the server from the stored
    facts: the same answer the submit command enforces, shown on the Review step."""
    from workeasy360.commercial.partners.models import PartnerProfile

    account = selectors.partner_row(partner_id)
    profile = PartnerProfile.objects.filter(partner_id=partner_id).first()
    business_gaps = [
        label
        for label, present in (
            ("primary contact name", account.contact_name if account else ""),
            ("primary contact email", account.contact_email if account else ""),
            ("business email", profile.business_email if profile else ""),
            ("phone", profile.phone if profile else ""),
            ("country", profile.country if profile else ""),
        )
        if not present
    ]
    proposal = row.proposal or {}
    items: list[dict[str, Any]] = [
        {
            "key": "business",
            "label": "Business details",
            "required": True,
            "done": not business_gaps,
            "detail": f"Add {', '.join(business_gaps)}." if business_gaps else "",
        },
        {
            "key": "products",
            "label": "Products to sell",
            "required": True,
            "done": bool(proposal.get("products")),
            "detail": "" if proposal.get("products") else "Choose at least one product.",
        },
    ]
    if row.to_experience == capabilities.WHITE_LABEL:
        named = bool((proposal.get("brand") or {}).get("brand_name"))
        items.append(
            {
                "key": "brand",
                "label": "Brand name",
                "required": True,
                "done": named,
                "detail": "" if named else "Give the brand a name.",
            }
        )
    for key, label in (
        ("pricing", "Pricing preference"),
        ("commercial", "Commercial notes"),
    ):
        items.append(
            {
                "key": key,
                "label": label,
                "required": False,
                "done": bool(proposal.get(key)),
                "detail": "",
            }
        )
    if row.to_experience == capabilities.WHITE_LABEL:
        items.append(
            {
                "key": "domain",
                "label": "Custom domain",
                "required": False,
                "done": bool((proposal.get("domain") or {}).get("hostname")),
                "detail": "Optional: the brand works on the Workeasy360 address.",
            }
        )
    return items


# --- the partner's commands --------------------------------------------------------------


@in_scope
def start(ctx: TenantContext, *, to: Any) -> dict[str, Any]:
    """Open an upgrade request on the caller's own partner account.

    The partner is always the caller's (never taken from the request). Asking
    again for what is already open returns it, so a double click or a second tab
    cannot raise two; asking for a different one while one is open is refused.
    """
    partner_id = _own(ctx, OWN_MANAGE)
    access.require_transacting(ctx)
    target = capabilities.require_experience(to)
    with transaction.atomic():
        # The same lock every other partner command takes: concurrent starts queue.
        services._lock_partner(partner_id)
        current = open_request(partner_id)
        if current is not None:
            if current.to_experience == target:
                return request_dict(current)
            raise PartnerUpgradeConflict(
                "This partner already has an open upgrade request. Finish or "
                "withdraw it before asking for another.",
                open_request_id=str(current.id),
            )
        if target not in eligible_targets(partner_id):
            raise PartnerValidationError(
                "This partner cannot ask for that upgrade from where it is.",
                field="experience",
            )
        fee = program_fee(target)
        row = _model().objects.create(
            partner_id=partner_id,
            from_experience=selectors.landing_workspace(partner_id) or "",
            to_experience=target,
            status=REQUESTED,
            requested_by_id=ctx.user_id,
            requested_at=timezone.now(),
            correlation_id=current_correlation_id(),
            fee_amount_minor=fee["amount_minor"] if fee else None,
            fee_currency=fee["currency"] if fee else "",
        )
        audit.record(
            ctx,
            "partner.upgrade_requested",
            resource_type="partners.upgrade_request",
            resource_id=row.id,
            changes={
                "partner_id": str(partner_id),
                "from": row.from_experience,
                "to": target,
            },
        )
    return request_dict(row)


def _check_version(row: Any, expected: Any) -> None:
    if isinstance(expected, bool) or not isinstance(expected, int):
        raise PartnerValidationError(
            "expected_version is required.", field="expected_version"
        )
    if row.version != expected:
        raise services.ConcurrentPartnerModification(
            "The request changed since you read it. Re-read it and retry.",
            expected_version=expected,
            actual_version=row.version,
        )


@in_scope
def save_section(
    ctx: TenantContext,
    request_id: UUID,
    *,
    section: Any,
    data: Any,
    expected_version: Any,
) -> dict[str, Any]:
    """Save one wizard step into the request. Validated in full before the write;
    a section the target does not take is refused, not ignored."""
    partner_id = _own(ctx, OWN_MANAGE)
    access.require_transacting(ctx)
    with transaction.atomic():
        row = _locked(request_id, partner_id)
        if row.status not in EDITABLE:
            raise PartnerUpgradeConflict(
                "This request can no longer be edited.", status=row.status
            )
        _check_version(row, expected_version)
        if section not in SECTIONS[row.to_experience]:
            raise PartnerValidationError(
                f"{section!r} is not a section of this upgrade. "
                f"Allowed: {list(SECTIONS[row.to_experience])}.",
                field="section",
            )
        cleaned = _CLEANERS[section](data)
        row.proposal = {**(row.proposal or {}), section: cleaned}
        row.version += 1
        row.save(update_fields=["proposal", "version", "updated_at"])
    return request_dict(row)


def _require_complete(partner_id: UUID, row: Any) -> None:
    missing = [c for c in checklist(partner_id, row) if c["required"] and not c["done"]]
    if missing:
        raise PartnerValidationError(
            "Complete these before submitting: "
            + ", ".join(c["label"].lower() for c in missing)
            + ".",
            field=missing[0]["key"],
            missing=[c["key"] for c in missing],
        )


@in_scope
def submit(
    ctx: TenantContext,
    request_id: UUID,
    *,
    expected_version: Any,
    payment_reference: Any = "",
) -> dict[str, Any]:
    """Send the request for review. Refused with the list of what is missing -
    the same list the Review step shows - until every required item is done.

    With a set-up fee not yet paid online, this is the **offline** way to pay:
    the partner gives the reference of its transfer, and staff verify it
    before approving (2026-11-03). Staff are told by email."""
    partner_id = _own(ctx, OWN_MANAGE)
    access.require_transacting(ctx)
    with transaction.atomic():
        row = _locked(request_id, partner_id)
        if row.status not in EDITABLE:
            raise PartnerUpgradeConflict(
                "This request was already submitted or closed.", status=row.status
            )
        _check_version(row, expected_version)
        _require_complete(partner_id, row)
        fields = ["status", "submitted_at", "info_request_note", "version", "updated_at"]
        if row.fee_amount_minor and not fee_paid_online(row):
            row.payment_reference = clean_text(
                payment_reference,
                field="payment_reference",
                max_length=120,
                required=True,
            )
            row.payment_method = OFFLINE
            fields += ["payment_reference", "payment_method"]
        row.status = PENDING_APPROVAL
        row.submitted_at = timezone.now()
        row.info_request_note = ""
        row.version += 1
        row.save(update_fields=fields)
        audit.record(
            ctx,
            "partner.upgrade_submitted",
            resource_type="partners.upgrade_request",
            resource_id=row.id,
            # Which sections were provided - never their contents.
            changes={
                "partner_id": str(partner_id),
                "from": row.from_experience,
                "to": row.to_experience,
                "sections": sorted(row.proposal or {}),
                "payment_method": row.payment_method or None,
            },
        )
        request_id = row.id
        transaction.on_commit(lambda: notify_staff(request_id))
    return request_dict(row)


# --- paying online (2026-11-03) -------------------------------------------------------


@in_scope
def online_payment_terms(
    ctx: TenantContext, request_id: UUID, *, expected_version: Any
) -> dict[str, Any]:
    """What an online payment of this request's set-up fee is for - checked
    exactly as submitting is (the partner's own, still editable, current
    version, every required item done) - for ``upgrade_payments`` to open the
    checkout. Writes nothing."""
    partner_id = _own(ctx, OWN_MANAGE)
    access.require_transacting(ctx)
    row = _model().objects.filter(id=request_id, partner_id=partner_id).first()
    if row is None:
        raise PartnerRecordNotFound("No such upgrade request.")
    if row.status not in EDITABLE:
        raise PartnerUpgradeConflict(
            "This request was already submitted or closed.", status=row.status
        )
    _check_version(row, expected_version)
    if not row.fee_amount_minor:
        raise PartnerValidationError(
            "This upgrade has no set-up fee.", field="payment_method"
        )
    if fee_paid_online(row):
        raise PartnerUpgradeConflict("The set-up fee is already paid.", status=row.status)
    _require_complete(partner_id, row)
    return {
        "partner_id": partner_id,
        "request_id": row.id,
        "amount_minor": row.fee_amount_minor,
        "currency": row.fee_currency,
        "from_experience": row.from_experience,
        "to_experience": row.to_experience,
    }


@in_scope
def attach_online_payment(
    ctx: TenantContext, request_id: UUID, payment_id: UUID
) -> dict[str, Any]:
    """Remember the online payment opened for this request. It pays nothing:
    only the provider's verified callback does (``fee_paid``)."""
    partner_id = _own(ctx, OWN_MANAGE)
    with transaction.atomic():
        row = _locked(request_id, partner_id)
        row.fee_payment_id = payment_id
        row.payment_method = ONLINE
        row.save(update_fields=["fee_payment_id", "payment_method", "updated_at"])
    return request_dict(row)


def fee_paid_online(row: Any) -> bool:
    """Whether the provider confirmed the set-up fee. Read from the payment
    itself as well, so a payment whose follow-up failed still counts."""
    if getattr(row, "fee_paid_at", None) is not None:
        return True
    payment = partner_fees.get(getattr(row, "fee_payment_id", None))
    return payment is not None and payment["status"] == "succeeded"


def fee_paid(request_id: UUID, payment_id: UUID) -> None:
    """The provider confirmed an online set-up fee (``partner_fees`` calls this
    after the payment commits). The request is stamped paid and, if the partner
    had not sent it yet, sent for review - and staff are told. Idempotent."""
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.platform.tenancy.db import organization_db_scope

    payment = partner_fees.get(payment_id)
    if payment is None or payment["status"] != "succeeded":
        return
    with transaction.atomic(), organization_db_scope(PLATFORM_ORGANIZATION_ID):
        row = _model().objects.select_for_update().filter(id=request_id).first()
        if row is None or row.fee_paid_at is not None:
            return
        if (row.fee_amount_minor, row.fee_currency) != (
            payment["amount_minor"],
            payment["currency"],
        ):
            logger.warning("partner.upgrade.fee_amount_differs", request_id=str(row.id))
            return
        row.fee_paid_at = timezone.now()
        row.fee_payment_id = payment_id
        row.payment_method = ONLINE
        fields = ["fee_paid_at", "fee_payment_id", "payment_method", "updated_at"]
        sent = row.status in EDITABLE
        if sent:
            row.status = PENDING_APPROVAL
            row.submitted_at = timezone.now()
            row.info_request_note = ""
            row.version += 1
            fields += ["status", "submitted_at", "info_request_note", "version"]
        row.save(update_fields=fields)
        audit.record_system(
            PLATFORM_ORGANIZATION_ID,
            "partner.upgrade_fee_paid",
            resource_type="partners.upgrade_request",
            resource_id=row.id,
            changes={
                "partner_id": str(row.partner_id),
                "from": row.from_experience,
                "to": row.to_experience,
                "payment_id": str(payment_id),
                "amount_minor": row.fee_amount_minor,
                "currency": row.fee_currency,
                "submitted": sent,
            },
            reason="The payment provider confirmed the upgrade's set-up fee.",
        )
        if row.status == PENDING_APPROVAL:
            request_id = row.id
            transaction.on_commit(lambda: notify_staff(request_id))


def notify_staff(request_id: UUID) -> int:
    """Email every active staff member who can approve upgrades about one
    request waiting for them. Best effort: a notice that cannot be queued is
    logged and skipped - it never undoes the request. Once per recipient per
    submission. Returns how many were queued."""
    from workeasy360.contracts.values import portals
    from workeasy360.platform.authz import staff
    from workeasy360.platform.identity import portals as portal_links
    from workeasy360.platform.notifications.services import notify_account

    sent = 0
    try:
        row = _model().objects.select_related("partner").filter(id=request_id).first()
        if row is None or row.status != PENDING_APPROVAL:
            return 0
        review = f"{portal_links.origin(portals.ADMIN)}/admin/partners/{row.partner_id}"
        for holder in staff.holders(STAFF_MANAGE):
            notify_account(
                template_key=TEMPLATE_KEY,
                recipient_email=holder["email"],
                idempotency_key=f"partner-upgrade:{row.id}:{row.version}:{holder['user_id']}",
                context={
                    "partner_name": row.partner.display_name,
                    "partner_code": row.partner.code,
                    "from_label": EXPERIENCE_LABEL.get(row.from_experience, "None"),
                    "to_label": EXPERIENCE_LABEL.get(
                        row.to_experience, row.to_experience
                    ),
                    "fee_line": _fee_line(row),
                    "review_url": review,
                },
            )
            sent += 1
    except Exception as exc:  # best effort: the request stands either way
        logger.warning(
            "partner.upgrade.notify_failed",
            request_id=str(request_id),
            error_type=type(exc).__name__,
        )
    return sent


def _fee_line(row: Any) -> str:
    if not row.fee_amount_minor:
        return "none"
    from workeasy360.contracts.values.money import CurrencyCode

    code = CurrencyCode(row.fee_currency)
    amount = f"{row.fee_currency} {row.fee_amount_minor / 10**code.exponent:,.{code.exponent}f}"
    if fee_paid_online(row):
        return f"{amount} - paid online (confirmed by the payment provider)"
    return f"{amount} - paid offline, reference {row.payment_reference or 'not given'} (verify before approving)"


@in_scope
def cancel(ctx: TenantContext, request_id: UUID) -> dict[str, Any]:
    """Withdraw an open request. Closed requests stay as history."""
    partner_id = _own(ctx, OWN_MANAGE)
    with transaction.atomic():
        row = _locked(request_id, partner_id)
        if row.status not in OPEN:
            raise PartnerUpgradeConflict(
                "This request is already closed.", status=row.status
            )
        row.status = CANCELLED
        row.version += 1
        row.save(update_fields=["status", "version", "updated_at"])
        audit.record(
            ctx,
            "partner.upgrade_cancelled",
            resource_type="partners.upgrade_request",
            resource_id=row.id,
            changes={
                "partner_id": str(partner_id),
                "from": row.from_experience,
                "to": row.to_experience,
            },
        )
    return request_dict(row)


# --- the cleaners: one per section, closed key sets --------------------------------------


def _count(value: Any, *, field: str) -> int | None:
    if value in (None, ""):
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= MAX_COUNT
    ):
        raise PartnerValidationError(
            f"{field} must be a whole number from 0 to {MAX_COUNT}.", field=field
        )
    return value


def _clean_products(data: Any) -> list[str]:
    raw = data.get("products") if isinstance(data, dict) else data
    if not isinstance(raw, list) or not all(isinstance(c, str) for c in raw):
        raise PartnerValidationError(
            "products must be a list of product codes.", field="products"
        )
    codes = list(dict.fromkeys(raw))
    if len(codes) > MAX_PRODUCTS:
        raise PartnerValidationError(
            f"Ask for at most {MAX_PRODUCTS} products.", field="products"
        )
    offered = {p["code"] for p in product_options()}
    unknown = [c for c in codes if c not in offered]
    if unknown:
        raise PartnerValidationError(
            "Some products are not available to ask for.", field="products"
        )
    return codes


def _clean_pricing(data: Any) -> dict[str, Any]:
    body = inputs.mapping(data)
    problems = inputs.Problems()
    inputs.reject_unknown(
        body, ("preference", "notes"), field="pricing", problems=problems
    )
    problems.first()
    preference = body.get("preference", "standard")
    if preference not in PRICING_PREFERENCES:
        raise PartnerValidationError(
            f"preference must be one of {list(PRICING_PREFERENCES)}.", field="preference"
        )
    return {
        "preference": preference,
        "notes": inputs.clean_note(body.get("notes"), field="notes", max_length=MAX_NOTE),
    }


def _clean_commercial(data: Any) -> dict[str, Any]:
    body = inputs.mapping(data)
    keys = ("expected_customers", "team_size", "customer_management", "notes")
    problems = inputs.Problems()
    inputs.reject_unknown(body, keys, field="commercial", problems=problems)
    problems.first()
    return {
        "expected_customers": _count(
            body.get("expected_customers"), field="expected_customers"
        ),
        "team_size": _count(body.get("team_size"), field="team_size"),
        "customer_management": inputs.clean_note(
            body.get("customer_management"),
            field="customer_management",
            max_length=MAX_NOTE,
        ),
        "notes": inputs.clean_note(body.get("notes"), field="notes", max_length=MAX_NOTE),
    }


def _clean_brand(data: Any) -> dict[str, str]:
    body = inputs.mapping(data)
    keys = ("brand_name", "primary_color", "secondary_color", "support_email")
    problems = inputs.Problems()
    inputs.reject_unknown(body, keys, field="brand", problems=problems)
    problems.first()
    out: dict[str, str] = {
        "brand_name": clean_text(
            body.get("brand_name"), field="brand_name", max_length=80
        ),
    }
    for key in ("primary_color", "secondary_color"):
        value = body.get(key)
        out[key] = branding.validate_color(value, field=key) if value else ""
    out["support_email"] = branding.validate_support_email(
        body.get("support_email") or ""
    )
    return out


def _clean_domain(data: Any) -> dict[str, str]:
    body = inputs.mapping(data)
    problems = inputs.Problems()
    inputs.reject_unknown(body, ("hostname",), field="domain", problems=problems)
    problems.first()
    hostname = body.get("hostname")
    return {"hostname": domains.normalize_hostname(hostname) if hostname else ""}


_CLEANERS: Final[dict[str, Any]] = {
    "products": _clean_products,
    "pricing": _clean_pricing,
    "commercial": _clean_commercial,
    "brand": _clean_brand,
    "domain": _clean_domain,
}


# --- staff: reading ------------------------------------------------------------------------


@in_scope
def staff_for_partner(ctx: TenantContext, partner_id: UUID) -> dict[str, Any]:
    """A partner's upgrade standing for the Partner Detail screen: what it is
    now, what it may ask for, its open request and its history."""
    authz.require(ctx, STAFF_VIEW)
    if selectors.partner_row(partner_id) is None:
        raise PartnerRecordNotFound("No such partner.", partner_id=str(partner_id))
    rows = list(
        _rows(
            _model().objects.filter(partner_id=partner_id).order_by("-requested_at")[:25]
        )
    )
    current = next((r for r in rows if r.status in OPEN), None)
    return {
        "current": selectors.landing_workspace(partner_id),
        "eligible": eligible_targets(partner_id),
        "status": current.status if current is not None else "none",
        "requested": current.to_experience if current is not None else None,
        "open": request_dict(current, staff=True) if current is not None else None,
        "checklist": checklist(partner_id, current) if current is not None else [],
        "history": [request_dict(r, staff=True) for r in rows if r.status not in OPEN],
        "products": product_options(),
    }


@in_scope
def staff_queue(
    ctx: TenantContext, *, status: str | None, limit: int = 50
) -> dict[str, Any]:
    """The review queue: requests across partners, newest first. ``status`` is
    one of the lifecycle states, or empty for the open ones."""
    authz.require(ctx, STAFF_VIEW)
    if status and status not in STATES:
        raise PartnerValidationError(
            f"status must be one of {list(STATES)}.", field="status"
        )
    queryset = _model().objects.select_related("partner", "requested_by", "decided_by")
    queryset = (
        queryset.filter(status=status) if status else queryset.filter(status__in=OPEN)
    )
    rows = list(queryset.order_by("-requested_at")[: max(1, min(int(limit), 100))])
    counts = {s: _model().objects.filter(status=s).count() for s in OPEN}
    return {
        "items": [
            {
                **request_dict(r, staff=True),
                "partner_name": r.partner.display_name,
                "partner_code": r.partner.code,
            }
            for r in rows
        ],
        "counts": counts,
    }


def pending_count() -> int:
    """Requests waiting for a staff decision - the partners dashboard's number."""
    return _model().objects.filter(status=PENDING_APPROVAL).count()


def open_for_partners(partner_ids: list[UUID]) -> dict[UUID, dict[str, str]]:
    """``{partner_id: {id, to, status}}`` for the partners with an open request,
    in one query - what the All Partners list draws a badge from."""
    if not partner_ids:
        return {}
    return {
        r["partner_id"]: {
            "id": str(r["id"]),
            "to": r["to_experience"],
            "status": r["status"],
        }
        for r in _model()
        .objects.filter(partner_id__in=partner_ids, status__in=OPEN)
        .values("id", "partner_id", "to_experience", "status")
    }


# --- staff: deciding --------------------------------------------------------------------------


def lock_for_decision(
    partner_id: UUID, request_id: UUID, *, allowed: tuple[str, ...]
) -> Any:
    """Row-lock a request for a decision: it must be this partner's and in one of
    ``allowed``. Call inside a transaction, after locking the partner."""
    row = _locked(request_id, partner_id)
    if row.status not in allowed:
        raise PartnerUpgradeConflict(
            "This request is not waiting for that decision.",
            status=row.status,
        )
    return row


def _note(value: Any, *, required: bool) -> str:
    text = inputs.clean_note(value, field="note", max_length=MAX_NOTE)
    if required and len(text) < 3:
        raise PartnerValidationError("Write a note: the partner reads it.", field="note")
    return text


@in_scope
def request_info(
    ctx: TenantContext, partner_id: UUID, request_id: UUID, *, note: Any
) -> dict[str, Any]:
    """Send a submitted request back to the partner with a note saying what is
    missing. The partner may then edit and submit again."""
    authz.require(ctx, STAFF_MANAGE)
    text = _note(note, required=True)
    with transaction.atomic():
        services._lock_partner(partner_id)
        row = lock_for_decision(partner_id, request_id, allowed=(PENDING_APPROVAL,))
        row.status = PROFILE_REQUIRED
        row.info_request_note = text
        row.version += 1
        row.save(update_fields=["status", "info_request_note", "version", "updated_at"])
        audit.record(
            ctx,
            "partner.upgrade_info_requested",
            resource_type="partners.upgrade_request",
            resource_id=row.id,
            changes={"partner_id": str(partner_id), "to": row.to_experience},
            reason=text,
        )
    return request_dict(row, staff=True)


@in_scope
def reject(
    ctx: TenantContext, partner_id: UUID, request_id: UUID, *, note: Any
) -> dict[str, Any]:
    """Decline a request. The partner keeps exactly what it had; nothing is
    granted and nothing is removed."""
    authz.require(ctx, STAFF_MANAGE)
    text = _note(note, required=True)
    with transaction.atomic():
        services._lock_partner(partner_id)
        # Staff may close a request at any open stage: one the partner started and
        # abandoned is as much theirs to decline as one it submitted.
        row = lock_for_decision(partner_id, request_id, allowed=OPEN)
        _close(ctx, row, REJECTED, text)
        audit.record(
            ctx,
            "partner.upgrade_rejected",
            resource_type="partners.upgrade_request",
            resource_id=row.id,
            changes={
                "partner_id": str(partner_id),
                "from": row.from_experience,
                "to": row.to_experience,
            },
            reason=text,
        )
    return request_dict(row, staff=True)


def _close(ctx: TenantContext, row: Any, status: str, note: str) -> None:
    row.status = status
    row.decided_by_id = ctx.user_id
    row.decided_at = timezone.now()
    row.decision_note = note
    row.version += 1
    row.save(
        update_fields=[
            "status",
            "decided_by",
            "decided_at",
            "decision_note",
            "version",
            "updated_at",
        ]
    )


# --- set-up fees (ADR-053) ---------------------------------------------------


def program_fee(experience: str) -> dict[str, Any] | None:
    """The set-up fee for becoming ``experience`` now, or ``None``."""
    from workeasy360.commercial.partners.models import PartnerProgramFee

    row = PartnerProgramFee.objects.filter(experience=experience).first()
    if row is None or row.amount_minor <= 0:
        return None
    return {
        "amount_minor": row.amount_minor,
        "currency": row.currency,
        "instructions": row.payment_instructions,
    }


@in_scope
def set_program_fee(
    ctx: TenantContext,
    *,
    experience: Any,
    currency: Any,
    amount_minor: Any = None,
    amount: Any = None,
    instructions: Any = None,
) -> dict[str, Any] | None:
    """Set (or, with zero, remove) the set-up fee for a target experience.
    Requests already open keep the fee they started with. The amount is either
    whole minor units or the decimal text a person typed (``"25000.00"``),
    converted here for the currency - never by a browser."""
    from workeasy360.commercial.partners.models import PartnerProgramFee
    from workeasy360.contracts.values.money import CurrencyCode

    authz.require(ctx, STAFF_MANAGE)
    target = str(experience or "")
    if target not in (capabilities.RESELLER, capabilities.WHITE_LABEL):
        raise PartnerValidationError(
            "A set-up fee is for becoming a reseller or a white-label partner.",
            field="experience",
        )
    try:
        code = CurrencyCode(str(currency))
    except ValueError as exc:
        raise PartnerValidationError("Unsupported currency.", field="currency") from exc
    if amount is not None:
        amount_minor = _minor_units(amount, code)
    if (
        isinstance(amount_minor, bool)
        or not isinstance(amount_minor, int)
        or not 0 <= amount_minor <= 10**12
    ):
        raise PartnerValidationError(
            "amount_minor must be a whole number of minor units, zero or more.",
            field="amount_minor",
        )
    text = (
        None
        if instructions is None
        else clean_text(
            instructions, field="instructions", max_length=1000, required=False
        )
    )
    with transaction.atomic():
        previous = (
            PartnerProgramFee.objects.select_for_update()
            .filter(experience=target)
            .first()
        )
        before = (
            {"amount_minor": previous.amount_minor, "currency": previous.currency}
            if previous is not None
            else None
        )
        defaults: dict[str, Any] = {
            "amount_minor": amount_minor,
            "currency": code.value,
            "updated_by_id": ctx.user_id,
        }
        if text is not None:
            defaults["payment_instructions"] = text
        PartnerProgramFee.objects.update_or_create(experience=target, defaults=defaults)
        audit.record(
            ctx,
            "partner.program_fee_set",
            resource_type="partners.program_fee",
            resource_id=target,
            changes={
                "experience": target,
                "before": before,
                "after": {"amount_minor": amount_minor, "currency": code.value},
                "instructions_changed": text is not None
                and text != (previous.payment_instructions if previous else ""),
            },
        )
    return program_fee(target)


def _minor_units(text: Any, currency: Any) -> int:
    """``"25000.00"`` in INR -> 2500000. Decimal, never float; at most the
    currency's own number of decimals."""
    from decimal import Decimal, InvalidOperation

    raw = str(text or "").strip()
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise PartnerValidationError("Enter an amount.", field="amount") from exc
    exponent = currency.exponent
    if (
        not value.is_finite() or value < 0 or value.as_tuple().exponent < -exponent  # type: ignore[operator]
    ):
        raise PartnerValidationError(
            f"Enter an amount with at most {exponent} decimals.", field="amount"
        )
    return int(value.scaleb(exponent))


def require_fee_settled(row: Any, reference: str) -> bool:
    """Approval's fee rule: a request that carries a set-up fee is approved
    only once it is paid - online, confirmed by the provider, or offline, with
    the reference of the payment staff verified. Returns whether a fee was
    settled."""
    if not row.fee_amount_minor:
        return False
    if fee_paid_online(row):
        return True
    if not reference:
        raise PartnerValidationError(
            "This upgrade has a set-up fee. Record the payment reference "
            "(the invoice or transfer) before approving.",
            field="commercial_reference",
        )
    return True


def record_approval(
    ctx: TenantContext,
    row: Any,
    *,
    note: str,
    commercial_reference: str,
    added: list[str],
    removed: list[str],
    products: list[str],
) -> dict[str, Any]:
    """Stamp a request approved, after the transition service has granted the
    bundle. Called by the staff approval command in its transaction, so the
    grants and this stamp commit or roll back together."""
    fee_settled = require_fee_settled(row, commercial_reference)
    _close(ctx, row, APPROVED, note)
    row.capabilities_added = added
    row.capabilities_removed = removed
    row.products_granted = products
    row.commercial_reference = commercial_reference
    if fee_settled:
        row.fee_settled_at = timezone.now()
    row.save(
        update_fields=[
            "capabilities_added",
            "capabilities_removed",
            "products_granted",
            "commercial_reference",
            "fee_settled_at",
        ]
    )
    audit.record(
        ctx,
        "partner.upgrade_approved",
        resource_type="partners.upgrade_request",
        resource_id=row.id,
        # The fields an auditor asks for: who, from what, to what, what changed,
        # which commercial terms - and never a profile value.
        changes={
            "partner_id": str(row.partner_id),
            "from": row.from_experience,
            "to": row.to_experience,
            "requested_by": str(row.requested_by_id),
            "requested_at": row.requested_at.isoformat(),
            "capabilities_added": added,
            "capabilities_removed": removed,
            "products_granted": products,
            "commercial_reference": commercial_reference,
            "setup_fee": {
                "amount_minor": row.fee_amount_minor,
                "currency": row.fee_currency,
                "paid_online": fee_paid_online(row),
            }
            if row.fee_amount_minor
            else None,
            "request_correlation_id": str(row.correlation_id)
            if row.correlation_id
            else None,
        },
        reason=note,
    )
    return request_dict(row, staff=True)

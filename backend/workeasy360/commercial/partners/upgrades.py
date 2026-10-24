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
from workeasy360.contracts.errors import (
    PartnerRecordNotFound,
    PartnerUpgradeConflict,
    PartnerValidationError,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.observability.correlation import current_correlation_id

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
    }
    if current is not None:
        out["products"] = product_options()
        out["checklist"] = checklist(partner_id, current)
    return out


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
        row = _model().objects.create(
            partner_id=partner_id,
            from_experience=selectors.landing_workspace(partner_id) or "",
            to_experience=target,
            status=REQUESTED,
            requested_by_id=ctx.user_id,
            requested_at=timezone.now(),
            correlation_id=current_correlation_id(),
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


@in_scope
def submit(
    ctx: TenantContext, request_id: UUID, *, expected_version: Any
) -> dict[str, Any]:
    """Send the request for review. Refused with the list of what is missing -
    the same list the Review step shows - until every required item is done."""
    partner_id = _own(ctx, OWN_MANAGE)
    access.require_transacting(ctx)
    with transaction.atomic():
        row = _locked(request_id, partner_id)
        if row.status not in EDITABLE:
            raise PartnerUpgradeConflict(
                "This request was already submitted or closed.", status=row.status
            )
        _check_version(row, expected_version)
        missing = [
            c for c in checklist(partner_id, row) if c["required"] and not c["done"]
        ]
        if missing:
            raise PartnerValidationError(
                "Complete these before submitting: "
                + ", ".join(c["label"].lower() for c in missing)
                + ".",
                field=missing[0]["key"],
                missing=[c["key"] for c in missing],
            )
        row.status = PENDING_APPROVAL
        row.submitted_at = timezone.now()
        row.info_request_note = ""
        row.version += 1
        row.save(
            update_fields=[
                "status",
                "submitted_at",
                "info_request_note",
                "version",
                "updated_at",
            ]
        )
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
            },
        )
    return request_dict(row)


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
    _close(ctx, row, APPROVED, note)
    row.capabilities_added = added
    row.capabilities_removed = removed
    row.products_granted = products
    row.commercial_reference = commercial_reference
    row.save(
        update_fields=[
            "capabilities_added",
            "capabilities_removed",
            "products_granted",
            "commercial_reference",
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
            "request_correlation_id": str(row.correlation_id)
            if row.correlation_id
            else None,
        },
        reason=note,
    )
    return request_dict(row, staff=True)

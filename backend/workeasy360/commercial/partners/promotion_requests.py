"""Asking to promote a product, and deciding (ADR-055).

An affiliate finds a Store product in its Store Console, asks to promote it,
and staff approve or refuse. **A request is a request**: it grants nothing.
Approving one makes the ordinary promotion grant through
``promotion.grant_locked`` - the same command staff use by hand on the
partner's page - so every link, campaign, capture and commission check keeps
reading the one grant table and nothing here can open a second way in.

What this module owns:

- the request table and its lifecycle: ``pending`` -> ``approved`` |
  ``rejected``; ``approved`` -> ``revoked`` when the grant it produced is
  withdrawn (``promotion.revoke_promotion`` marks it, whichever screen staff
  use). Rows are never deleted or reopened, so the history of one partner and
  product is its rows in order;
- the product state the partner sees (``not_requested``, ``pending``,
  ``approved``, ``rejected``, ``revoked``), derived from requests and grants,
  never stored;
- the notice to the staff who can decide, queued after the request commits.

Who may do what: a partner member holding ``platform.partner_referral.create``
asks for its own partner only (``ctx.partner_id``; there is no partner id to
send). Staff holding ``platform.staff_partner.view`` read the queue; only
``platform.staff_partner.manage`` approves, refuses or revokes. Every command
locks the partner row first and the request row second, so two members asking
at once, two administrators deciding at once, or an approval racing a
revocation resolve one after the other; the partial unique index on pending
requests is the last word if anything slips past.
"""

from __future__ import annotations

from typing import Any, Final
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from workeasy360.commercial.catalog import selectors as catalog_selectors
from workeasy360.commercial.partners import access, capabilities, inputs, promotion
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.commercial.partners.services import MANAGE, _lock_partner
from workeasy360.contracts.enums import GrantStatus
from workeasy360.contracts.errors import (
    PartnerRecordNotFound,
    PartnerRequestConflict,
    PartnerValidationError,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.notifications.templates import Template, register
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

OWN_CREATE: Final = "platform.partner_referral.create"
OWN_VIEW: Final = "platform.partner_referral.view"
STAFF_VIEW: Final = "platform.staff_partner.view"

PENDING: Final = "pending"
APPROVED: Final = "approved"
REJECTED: Final = "rejected"
REVOKED: Final = "revoked"
STATES: Final = (PENDING, APPROVED, REJECTED, REVOKED)
NOT_REQUESTED: Final = "not_requested"

#: How the partner means to reach people. A closed list, so the queue can be
#: filtered and nothing free-form hides in it.
CHANNELS: Final[dict[str, str]] = {
    "website": "Website or blog",
    "social_media": "Social media",
    "email": "Email newsletter",
    "video": "Video or podcast",
    "community": "Community or forum",
    "paid_ads": "Paid advertising",
    "events": "Events or webinars",
    "other": "Other",
}
FIELDS: Final = ("product_code", "channel", "website", "audience", "strategy", "notes")
MAX_AUDIENCE: Final = 300
MAX_TEXT: Final = 1000
MAX_REASON: Final = 500

TEMPLATE_KEY: Final = "platform.partner.promotion_requested"

PROMOTION_REQUESTED = register(
    Template(
        key=TEMPLATE_KEY,
        subject="{partner_name} asks to promote {product_name}",
        body_text=(
            "An affiliate asked to promote a product from its Store Console.\n\n"
            "Partner: {partner_name} ({partner_code})\n"
            "Product: {product_name}\n"
            "Channel: {channel}\n\n"
            "Review it here: {review_url}\n\n"
            "Nothing is granted until an administrator approves it."
        ),
        required=(
            "partner_name",
            "partner_code",
            "product_name",
            "channel",
            "review_url",
        ),
        description=(
            "Sent to the staff who can approve partner products when an "
            "affiliate requests permission to promote one."
        ),
        samples=(
            ("partner_name", "Acme Media"),
            ("partner_code", "ACME"),
            ("product_name", "Easy CRM"),
            ("channel", "Website or blog"),
            ("review_url", "https://admin.example.com/admin/partners/product-requests"),
        ),
    )
)


def _model() -> Any:
    from workeasy360.commercial.partners.models import PartnerPromotionRequest

    return PartnerPromotionRequest


def _grants() -> Any:
    from workeasy360.commercial.partners.models import PartnerPromotionGrant

    return PartnerPromotionGrant


def eligible_codes() -> frozenset[str]:
    """The products a request may name now: on the Store (active, published,
    listed) and open to the affiliate channel - the same list Explore shows."""
    return frozenset(
        p["code"] for p in catalog_selectors.store_products() if p["affiliate_enabled"]
    )


# --- read models ------------------------------------------------------------------------


def request_dict(row: Any, *, staff: bool = False) -> dict[str, Any]:
    """One request as the partner (or, with ``staff``, the console) reads it.
    The partner is told when and why, never which administrator decided."""
    out: dict[str, Any] = {
        "id": str(row.id),
        "product_code": row.product.code,
        "product_name": row.product.name,
        "status": row.status,
        "channel": row.channel,
        "channel_label": CHANNELS.get(row.channel, row.channel),
        "website": row.website,
        "audience": row.audience,
        "strategy": row.strategy,
        "notes": row.notes,
        "requested_at": row.requested_at.isoformat(),
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "decision_reason": row.decision_reason,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "revoke_reason": row.revoke_reason,
    }
    if staff:
        out.update(
            {
                "partner_id": str(row.partner_id),
                "requested_by": row.requested_by.email,
                "decided_by": row.decided_by.email if row.decided_by_id else None,
                "revoked_by": row.revoked_by.email if row.revoked_by_id else None,
                "grant_id": str(row.grant_id) if row.grant_id else None,
            }
        )
    return out


def _rows(queryset: Any, *, staff: bool = False) -> Any:
    related = ["product"]
    if staff:
        related += ["partner", "requested_by", "decided_by", "revoked_by"]
    return queryset.select_related(*related)


def partner_requests(partner_id: UUID, *, limit: int = 200) -> list[dict[str, Any]]:
    """The partner's requests, newest first - its My Requests tab."""
    rows = _rows(_model().objects.filter(partner_id=partner_id)).order_by("-requested_at")
    return [request_dict(r) for r in rows[: max(1, limit)]]


def product_states(partner_id: UUID) -> dict[str, dict[str, Any]]:
    """``{product_code: {status, request}}`` for every product the partner has
    asked for or been granted - two queries for the lot.

    ``approved`` means a live grant, however it was made. Otherwise the most
    recent event decides: the newest request's state, unless a grant was
    revoked after it (a product staff assigned by hand and later withdrew).
    """
    latest: dict[str, Any] = {}
    for row in _rows(_model().objects.filter(partner_id=partner_id)).order_by(
        "-requested_at"
    ):
        latest.setdefault(row.product.code, row)
    live: set[str] = set()
    last_revoked: dict[str, Any] = {}
    for code, status, revoked_at in (
        _grants()
        .objects.filter(partner_id=partner_id)
        .values_list("product__code", "status", "revoked_at")
    ):
        if status == GrantStatus.ACTIVE.value:
            live.add(code)
        elif revoked_at and (code not in last_revoked or revoked_at > last_revoked[code]):
            last_revoked[code] = revoked_at
    out: dict[str, dict[str, Any]] = {}
    for code in set(latest) | live | set(last_revoked):
        row = latest.get(code)
        if code in live:
            status = APPROVED
        elif row is None:
            status = REVOKED
        else:
            status = REVOKED if row.status == APPROVED else row.status
            settled = row.revoked_at or row.decided_at or row.requested_at
            if code in last_revoked and last_revoked[code] > settled:
                status = REVOKED
        out[code] = {"status": status, "request": request_dict(row) if row else None}
    return out


def may_request(partner_id: UUID, permissions: frozenset[str]) -> str | None:
    """Why this member cannot ask for a product now, or ``None`` if it can."""
    row = partner_selectors.partner_row(partner_id)
    if row is None or not row.status_enum.may_transact:
        return "This partner account is not active, so it cannot request products."
    if capabilities.REFERRAL_CREATE not in partner_selectors.active_capabilities(
        partner_id
    ):
        return "This partner account does not hold the referral capability."
    if OWN_CREATE not in permissions:
        return "Your role cannot request products. Ask the account owner."
    return None


def profile_website(partner_id: UUID) -> str:
    """The website on the partner's profile - the request form's starting value."""
    from workeasy360.commercial.partners.models import PartnerProfile

    return (
        PartnerProfile.objects.filter(partner_id=partner_id)
        .values_list("website", flat=True)
        .first()
        or ""
    )


# --- the partner asks --------------------------------------------------------------------


def _clean(body: Any) -> dict[str, str]:
    raw = inputs.mapping(body)
    unknown = sorted(set(raw) - set(FIELDS))
    if unknown:
        raise PartnerValidationError(
            f"Unknown fields {unknown}. Allowed: {list(FIELDS)}.", field=unknown[0]
        )
    channel = str(raw.get("channel") or "").strip()
    if channel not in CHANNELS:
        raise PartnerValidationError("Choose how you will promote it.", field="channel")
    audience = inputs.clean_markup_free(
        raw.get("audience"), field="audience", max_length=MAX_AUDIENCE
    )
    strategy = inputs.clean_note(
        raw.get("strategy"), field="strategy", max_length=MAX_TEXT
    )
    if len(audience) < 3:
        raise PartnerValidationError("Describe who you will reach.", field="audience")
    if len(strategy) < 10:
        raise PartnerValidationError(
            "Describe how you will promote it (at least 10 characters).",
            field="strategy",
        )
    return {
        "product_code": str(raw.get("product_code") or "").strip(),
        "channel": channel,
        "website": inputs.clean_https_url(raw.get("website"), field="website"),
        "audience": audience,
        "strategy": strategy,
        "notes": inputs.clean_note(raw.get("notes"), field="notes", max_length=MAX_TEXT),
    }


@in_scope
def submit(ctx: TenantContext, *, body: Any) -> dict[str, Any]:
    """Ask to promote one Store product. Refused for a product the partner may
    already promote or has already asked for, and for one not open to
    affiliates. Staff who can decide are notified once the request commits."""
    if ctx.partner_id is None:
        raise PartnerRecordNotFound("No such partner.")
    authz.require(ctx, OWN_CREATE)
    access.require_transacting(ctx)
    partner_id = ctx.partner_id
    data = _clean(body)
    code = data["product_code"]
    if code not in eligible_codes():
        raise PartnerValidationError(
            "That product is not open for promotion requests.", field="product_code"
        )
    product_id, _plan = catalog_selectors.resolve_product_and_plan(code, None)
    with transaction.atomic():
        _lock_partner(partner_id)
        reason = may_request(partner_id, authz.effective_permissions(ctx))
        if reason:
            raise PartnerValidationError(reason, field="product_code")
        if promotion.may_promote(partner_id, code):
            raise PartnerRequestConflict(
                "You may already promote this product.", status=APPROVED
            )
        if (
            _model()
            .objects.filter(partner_id=partner_id, product_id=product_id, status=PENDING)
            .exists()
        ):
            raise PartnerRequestConflict(
                "A request for this product is already waiting for approval.",
                status=PENDING,
            )
        try:
            with transaction.atomic():
                row = _model().objects.create(
                    partner_id=partner_id,
                    product_id=product_id,
                    channel=data["channel"],
                    website=data["website"],
                    audience=data["audience"],
                    strategy=data["strategy"],
                    notes=data["notes"],
                    requested_by_id=ctx.user_id,
                    requested_at=timezone.now(),
                )
        except IntegrityError:
            raise PartnerRequestConflict(
                "A request for this product is already waiting for approval.",
                status=PENDING,
            ) from None
        audit.record(
            ctx,
            "partner.promotion_requested",
            resource_type="partners.promotion_request",
            resource_id=row.id,
            # Which product and channel - never the partner's words.
            changes={
                "partner_id": str(partner_id),
                "product_code": code,
                "channel": row.channel,
            },
        )
        request_id = row.id
        transaction.on_commit(lambda: notify_staff(request_id))
    return request_dict(_rows(_model().objects.filter(id=request_id)).get())


def notify_staff(request_id: UUID) -> int:
    """Email every active staff member holding ``platform.staff_partner.manage``
    about one new request. Best effort: a notice that cannot be queued is
    logged and skipped - it never undoes the request. Idempotent per request
    and recipient. Returns how many were queued."""
    from workeasy360.contracts.values import portals
    from workeasy360.platform.authz import staff
    from workeasy360.platform.identity import portals as portal_links
    from workeasy360.platform.notifications.services import notify_account

    sent = 0
    try:
        row = _rows(_model().objects.filter(id=request_id), staff=True).first()
        if row is None or row.status != PENDING:
            return 0
        review = (
            f"{portal_links.origin(portals.ADMIN)}/admin/partners/product-requests"
            f"?request={row.id}"
        )
        for holder in staff.holders(MANAGE):
            notify_account(
                template_key=TEMPLATE_KEY,
                recipient_email=holder["email"],
                idempotency_key=f"promotion-request:{row.id}:{holder['user_id']}",
                context={
                    "partner_name": row.partner.display_name,
                    "partner_code": row.partner.code,
                    "product_name": row.product.name,
                    "channel": CHANNELS.get(row.channel, row.channel),
                    "review_url": review,
                },
            )
            sent += 1
    except Exception as exc:  # best effort: the request stands either way
        logger.warning(
            "partner.promotion_request.notify_failed",
            request_id=str(request_id),
            error_type=type(exc).__name__,
        )
    return sent


# --- staff decide -------------------------------------------------------------------------


def _locked_for_decision(request_id: UUID) -> tuple[Any, Any]:
    """Lock the request's partner, then the request - the order every command
    here and ``promotion`` uses, so no two of them deadlock."""
    partner_id = (
        _model()
        .objects.filter(id=request_id)
        .values_list("partner_id", flat=True)
        .first()
    )
    if partner_id is None:
        raise PartnerRecordNotFound("No such request.", request_id=str(request_id))
    partner = _lock_partner(partner_id)
    row = (
        _rows(_model().objects.select_for_update(of=("self",)), staff=True)
        .filter(id=request_id)
        .first()
    )
    if row is None:  # pragma: no cover - rows are never deleted
        raise PartnerRecordNotFound("No such request.", request_id=str(request_id))
    return partner, row


def _decided(row: Any, ctx: TenantContext, status: str, *, reason: str = "") -> None:
    row.status = status
    row.decided_by_id = ctx.user_id
    row.decided_at = timezone.now()
    row.decision_reason = reason
    row.save(
        update_fields=["status", "decided_by", "decided_at", "decision_reason", "grant"]
    )


@in_scope
def approve(ctx: TenantContext, request_id: UUID) -> dict[str, Any]:
    """Approve a pending request: the partner gets the promotion grant for the
    product (or keeps the one it already holds) and the product moves to its
    My Products. Refused if the product has left the Store or the affiliate
    channel since, or the partner can no longer hold the grant."""
    authz.require(ctx, MANAGE)
    with transaction.atomic():
        partner, row = _locked_for_decision(request_id)
        if row.status != PENDING:
            raise PartnerRequestConflict(
                "This request was already decided.", status=row.status
            )
        if row.product.code not in eligible_codes():
            raise PartnerValidationError(
                "This product is no longer on the Store for affiliates. Reject the "
                "request, or list the product and allow Affiliate first.",
                field="product_code",
            )
        row.grant = promotion.grant_locked(ctx, partner, product_code=row.product.code)
        _decided(row, ctx, APPROVED)
        audit.record(
            ctx,
            "partner.promotion_request_approved",
            resource_type="partners.promotion_request",
            resource_id=row.id,
            changes={
                "partner_id": str(row.partner_id),
                "product_code": row.product.code,
                "grant_id": str(row.grant_id),
            },
        )
    return request_dict(row, staff=True)


def _reason(value: Any, *, required: bool) -> str:
    text = inputs.clean_note(value, field="reason", max_length=MAX_REASON)
    if required and len(text) < 3:
        raise PartnerValidationError(
            "Write a reason: the partner reads it.", field="reason"
        )
    return text


@in_scope
def reject(ctx: TenantContext, request_id: UUID, *, reason: Any) -> dict[str, Any]:
    """Refuse a pending request with a reason the partner reads. Nothing is
    granted or removed; the partner may ask again."""
    authz.require(ctx, MANAGE)
    text = _reason(reason, required=True)
    with transaction.atomic():
        _partner, row = _locked_for_decision(request_id)
        if row.status != PENDING:
            raise PartnerRequestConflict(
                "This request was already decided.", status=row.status
            )
        _decided(row, ctx, REJECTED, reason=text)
        audit.record(
            ctx,
            "partner.promotion_request_rejected",
            resource_type="partners.promotion_request",
            resource_id=row.id,
            changes={"partner_id": str(row.partner_id), "product_code": row.product.code},
            reason=text,
        )
    return request_dict(row, staff=True)


@in_scope
def revoke(ctx: TenantContext, request_id: UUID, *, reason: Any = "") -> dict[str, Any]:
    """Withdraw what an approved request granted - the one revocation command
    (``promotion.revoke_promotion``), which also marks this request. New links
    stop at once; earned commission and recorded referrals are untouched."""
    authz.require(ctx, MANAGE)
    text = _reason(reason, required=False)
    row = _rows(_model().objects.filter(id=request_id)).first()
    if row is None:
        raise PartnerRecordNotFound("No such request.", request_id=str(request_id))
    if row.status != APPROVED or row.grant_id is None:
        raise PartnerRequestConflict(
            "Only an approved request can be revoked.", status=row.status
        )
    try:
        promotion.revoke_promotion(
            ctx, row.partner_id, grant_id=row.grant_id, reason=text
        )
    except PartnerRecordNotFound:
        # Revoked a moment ago on another screen: the request says so already.
        raise PartnerRequestConflict(
            "This permission was already revoked.", status=REVOKED
        ) from None
    return request_dict(
        _rows(_model().objects.filter(id=request_id), staff=True).get(), staff=True
    )


# --- staff read -----------------------------------------------------------------------------


def pending_count() -> int:
    return _model().objects.filter(status=PENDING).count()


@in_scope
def staff_queue(
    ctx: TenantContext,
    *,
    status: Any = None,
    search: Any = None,
    partner_id: Any = None,
    limit: Any = 50,
    offset: Any = 0,
) -> dict[str, Any]:
    """The review queue across partners, newest first: one status (default
    ``pending``) or ``all``, optionally one partner, optionally a search over
    the partner's name or code and the product's name or code. Bounded and
    paged; the counts per status are one aggregate query."""
    authz.require(ctx, STAFF_VIEW)
    wanted = str(status or PENDING)
    if wanted != "all" and wanted not in STATES:
        raise PartnerValidationError(
            f"status must be one of {[*STATES, 'all']}.", field="status"
        )
    try:
        size = max(1, min(int(limit), 100))
        start = max(0, int(offset))
    except (TypeError, ValueError):
        raise PartnerValidationError(
            "limit and offset must be numbers.", field="limit"
        ) from None
    queryset = _model().objects.all()
    if partner_id:
        try:
            queryset = queryset.filter(partner_id=UUID(str(partner_id)))
        except ValueError:
            raise PartnerValidationError("No such partner.", field="partner_id") from None
    text = str(search or "").strip()[:80]
    if text:
        queryset = queryset.filter(
            Q(partner__display_name__icontains=text)
            | Q(partner__code__icontains=text)
            | Q(product__name__icontains=text)
            | Q(product__code__icontains=text)
        )
    counts = {s: 0 for s in STATES}
    for row in queryset.values("status").annotate(n=Count("id")):
        counts[row["status"]] = row["n"]
    if wanted != "all":
        queryset = queryset.filter(status=wanted)
    total = queryset.count()
    rows = _rows(queryset, staff=True).order_by("-requested_at", "id")[
        start : start + size
    ]
    return {
        "items": [
            {
                **request_dict(r, staff=True),
                "partner_name": r.partner.display_name,
                "partner_code": r.partner.code,
                "partner_status": r.partner.status,
            }
            for r in rows
        ],
        "counts": counts,
        "total": total,
        "limit": size,
        "offset": start,
        "channels": CHANNELS,
    }


@in_scope
def staff_detail(ctx: TenantContext, request_id: UUID) -> dict[str, Any]:
    """One request with what a decision needs: the partner, the product as the
    catalog has it now, whether it is still eligible, and the history of this
    partner and product - every request and every grant, oldest last."""
    authz.require(ctx, STAFF_VIEW)
    row = _rows(_model().objects.filter(id=request_id), staff=True).first()
    if row is None:
        raise PartnerRecordNotFound("No such request.", request_id=str(request_id))
    partner = row.partner
    product = row.product
    history = _rows(
        _model().objects.filter(partner_id=partner.id, product_id=product.id), staff=True
    ).order_by("-requested_at")
    grants = (
        _grants()
        .objects.filter(partner_id=partner.id, product_id=product.id)
        .select_related("granted_by", "revoked_by")
        .order_by("-granted_at")
    )
    return {
        "request": request_dict(row, staff=True),
        "partner": {
            "id": str(partner.id),
            "name": partner.display_name,
            "code": partner.code,
            "status": partner.status,
            "website": profile_website(partner.id),
            "referral_capability": capabilities.REFERRAL_CREATE
            in partner_selectors.active_capabilities(partner.id),
            "promotes": sorted(promotion.promotable_codes(partner.id)),
        },
        "product": {
            "code": product.code,
            "name": product.name,
            "status": product.status,
            "eligible": product.code in eligible_codes(),
        },
        "history": [request_dict(r, staff=True) for r in history],
        "grants": [
            {
                "id": str(g.id),
                "status": g.status,
                "granted_at": g.granted_at.isoformat(),
                "granted_by": g.granted_by.email,
                "revoked_at": g.revoked_at.isoformat() if g.revoked_at else None,
                "revoked_by": g.revoked_by.email if g.revoked_by_id else None,
            }
            for g in grants
        ],
    }

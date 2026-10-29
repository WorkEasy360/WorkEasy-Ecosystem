"""Referrals: capture, conversion and attribution (Phase 7, Task 36-39, 88-89).

**Affiliate is referral-only.** Holding ``referral.create`` lets a partner mint
public codes and see how many converted; it grants no view of any customer and
no authority over one.

**Attribution rule: first touch, recorded at conversion.** A browser keeps one
referral token. While it is live (captured, unconverted, inside the programme's
attribution window) a later click - on another partner's link, another link of
the same partner or the same link again - is *counted* as a click on that link
but does not replace the token (ADR-054; before it, the last click won). Once
the token expires or converts, the next click starts a new one. The customer is
attributed to the partner of the token the browser presents at conversion; that
conversion is recorded once and never re-pointed, and ``organization_id`` is
unique so no customer is ever attributed twice.

**Product links need a promotion grant (ADR-054).** A link for a product - its
page, pricing, a feature or an update - is made only while the partner holds
the product's ``PartnerPromotionGrant``. After a revocation the programme's
``revoked_links`` policy decides whether existing links still take visitors.

**Nothing personal is stored.** Capture takes only a code. The visitor gets an
opaque bearer token; only its hash is kept. Conversion links the token to the
customer organization - and only the organization's own active owner can do
that, so a token cannot be redeemed by a third party.

**The public capture endpoint is intentionally unauthenticated** and is bounded
three ways: a strict 8-character code, an IP rate limit at the view, and a
retention prune of unconverted rows (``prune_expired_referrals``).
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import timedelta
from typing import Any, Final
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from workeasy360.commercial.commissions import domain, policy, selectors
from workeasy360.commercial.partners import access, capabilities, promotion
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.contracts.enums import (
    GrantStatus,
    PartnerStatus,
    ReferralLinkKind,
    ReferralStatus,
    RevokedLinkPolicy,
)
from workeasy360.contracts.errors import PartnerRecordNotFound, PartnerValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.observability.metrics import metrics

CREATE = "platform.partner_referral.create"

#: No I, L, O, 0 or 1: a code is read aloud and typed from print.
_ALPHABET: Final = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
#: Any code that can exist: a minted one (8 of the alphabet) or one the partner
#: chose (ADR-053: ``AMAN360``) - 4 to 16 letters and digits.
_CODE_RE: Final = re.compile(r"^[A-Z0-9]{4,16}$")
#: A chosen code must contain a letter, so it never reads as an order or id number.
_CHOSEN_RE: Final = re.compile(r"^(?=.*[A-Z])[A-Z0-9]{4,16}$")
#: ADR-054: several links per product (page, pricing, features, updates, campaigns).
MAX_CODES_PER_PARTNER: Final = 100
MAX_CAMPAIGNS_PER_PARTNER: Final = 50
NEW_CUSTOMER_WINDOW: Final = timedelta(days=30)

REFERRAL_EVENTS = "referral_events_total"


def capture_window() -> timedelta:
    """How long a new capture may convert: the programme policy (ADR-054)."""
    return timedelta(days=policy.current().attribution_days)


def _new_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(8))


@in_scope
def create_referral_code(
    ctx: TenantContext,
    *,
    label: Any = "",
    code: Any = None,
    product_code: Any = "",
    plan_code: Any = "",
    campaign_id: Any = None,
    link_kind: Any = None,
    update_id: Any = None,
) -> Any:
    """Mint a public referral code (a link) for the acting partner.

    ADR-053: the partner may choose the code (``AMAN360``), point the link at
    one product (and plan) of the Store, and put it in one of its campaigns.
    ADR-054: the link may open the product's page, its pricing, or one of its
    published feature or release updates (``link_kind`` + ``update_id``), and
    any product link needs the partner's promotion grant for the product. The
    campaign must be the partner's own and active. The destination is derived,
    never typed.
    """
    authz.require(ctx, CREATE)
    access.require_transacting(ctx)
    assert ctx.partner_id is not None  # require_transacting established it
    return _mint(
        ctx,
        ctx.partner_id,
        label,
        chosen=code,
        target=_target(
            ctx.partner_id,
            link_kind=link_kind,
            product_code=product_code,
            plan_code=plan_code,
            update_id=update_id,
        ),
        campaign_id=campaign_id,
    )


_GENERAL: Final = (ReferralLinkKind.GENERAL.value, "", "", None)


def _target(
    partner_id: UUID,
    *,
    link_kind: Any,
    product_code: Any,
    plan_code: Any,
    update_id: Any,
) -> tuple[str, str, str, Any]:
    """A link's ``(kind, product, plan, update)``: a general link, or a link to
    a Store product open to affiliates that the partner may promote - its page
    (optionally one plan), its pricing, or one of its published updates."""
    from workeasy360.commercial.catalog import selectors as catalog_selectors

    product = str(product_code or "").strip()
    plan = str(plan_code or "").strip()
    try:
        kind = ReferralLinkKind(
            str(link_kind or "").strip()
            or (ReferralLinkKind.PRODUCT if product else ReferralLinkKind.GENERAL)
        )
    except ValueError:
        raise PartnerValidationError("Unknown kind of link.", field="link_kind") from None
    if kind is ReferralLinkKind.GENERAL:
        if product or plan or update_id not in (None, ""):
            raise PartnerValidationError(
                "A general link names no product.", field="product_code"
            )
        return _GENERAL
    if not product:
        raise PartnerValidationError("Choose the product first.", field="product_code")
    if product not in {p["code"] for p in catalog_selectors.store_products()} or (
        "affiliate" not in catalog_selectors.channels_open(product)
    ):
        raise PartnerValidationError(
            "That product is not available for referral links.", field="product_code"
        )
    if not promotion.may_promote(partner_id, product):
        raise PartnerValidationError(
            "You are not authorized to promote that product.", field="product_code"
        )
    update = None
    if kind in (ReferralLinkKind.FEATURE, ReferralLinkKind.UPDATE):
        update = _update(update_id, product, kind)
    if plan and kind is not ReferralLinkKind.PRODUCT:
        raise PartnerValidationError(
            "Only a product page link names a plan.", field="plan_code"
        )
    if plan:
        _check_plan(product, plan)
    return kind.value, product, plan, update


def _update(update_id: Any, product: str, kind: ReferralLinkKind) -> Any:
    from workeasy360.commercial.catalog import selectors as catalog_selectors

    try:
        uid = UUID(str(update_id))
    except ValueError:
        raise PartnerValidationError(
            "Choose the update to promote.", field="update_id"
        ) from None
    row = catalog_selectors.linkable_update(uid, product_code=product)
    wanted = "feature" if kind is ReferralLinkKind.FEATURE else "release"
    if row is None or row.kind != wanted:
        raise PartnerValidationError(
            "That update is not a published one of this product.", field="update_id"
        )
    return row


def _check_plan(product: str, plan: str) -> None:
    from workeasy360.commercial.catalog import selectors as catalog_selectors
    from workeasy360.contracts.errors import CatalogValidationError

    try:
        catalog_selectors.resolve_product_and_plan(product, plan)
    except CatalogValidationError as exc:
        raise PartnerValidationError(
            "That plan is not one of the product's plans.", field="plan_code"
        ) from exc


def _campaign(partner_id: UUID, campaign_id: Any) -> Any:
    from workeasy360.commercial.commissions.models import ReferralCampaign

    if campaign_id in (None, ""):
        return None
    try:
        cid = UUID(str(campaign_id))
    except ValueError as exc:
        raise PartnerValidationError("No such campaign.", field="campaign_id") from exc
    row = ReferralCampaign.objects.filter(
        id=cid, partner_id=partner_id, status=GrantStatus.ACTIVE.value
    ).first()
    if row is None:
        raise PartnerValidationError("No such campaign.", field="campaign_id")
    return row


def _chosen_code(raw: Any) -> str | None:
    if raw in (None, ""):
        return None
    code = str(raw).strip().upper()
    if not _CHOSEN_RE.match(code):
        raise PartnerValidationError(
            "A code is 4 to 16 letters and digits, with at least one letter.",
            field="code",
        )
    return code


@in_scope
def create_campaign(ctx: TenantContext, *, name: Any) -> Any:
    """Start a campaign: a named group of the partner's own links."""
    from workeasy360.commercial.commissions.models import ReferralCampaign
    from workeasy360.commercial.partners.domain import clean_text

    authz.require(ctx, CREATE)
    access.require_transacting(ctx)
    text = clean_text(name, field="name", max_length=64, required=True)
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                [f"campaigns:{ctx.partner_id}"],
            )
        if (
            ReferralCampaign.objects.filter(partner_id=ctx.partner_id).count()
            >= MAX_CAMPAIGNS_PER_PARTNER
        ):
            raise PartnerValidationError(
                f"A partner may hold at most {MAX_CAMPAIGNS_PER_PARTNER} campaigns.",
                field="name",
            )
        if ReferralCampaign.objects.filter(
            partner_id=ctx.partner_id, name__iexact=text
        ).exists():
            raise PartnerValidationError(
                "You already have a campaign with that name.", field="name"
            )
        campaign = ReferralCampaign.objects.create(
            partner_id=ctx.partner_id, name=text, created_by_id=ctx.user_id
        )
        audit.record(
            ctx,
            "partner.campaign_created",
            resource_type="commissions.referral_campaign",
            resource_id=campaign.id,
            changes={"name": text, "partner_id": str(ctx.partner_id)},
        )
    return campaign


@in_scope
def set_campaign_status(ctx: TenantContext, campaign_id: UUID, *, active: Any) -> Any:
    """Archive a campaign (its links keep working and keep their history) or
    bring it back."""
    from workeasy360.commercial.commissions.models import ReferralCampaign

    authz.require(ctx, CREATE)
    if not isinstance(active, bool):
        raise PartnerValidationError("active must be true or false.", field="active")
    with transaction.atomic():
        campaign = (
            ReferralCampaign.objects.select_for_update()
            .filter(id=campaign_id, partner_id=ctx.partner_id)
            .first()
        )
        if campaign is None:
            raise PartnerRecordNotFound("No such campaign.", campaign_id=str(campaign_id))
        status = GrantStatus.ACTIVE.value if active else GrantStatus.REVOKED.value
        if campaign.status != status:
            campaign.status = status
            campaign.save(update_fields=["status"])
            audit.record(
                ctx,
                "partner.campaign_status_changed",
                resource_type="commissions.referral_campaign",
                resource_id=campaign.id,
                changes={"status": status},
            )
    return campaign


#: Staff set a partner up (Complete Your Profile, ADR-048): the same mint, on
#: behalf of a partner that holds the referral capability.
STAFF_MANAGE: Final = "platform.staff_partner.manage"


@in_scope
def create_referral_code_for_partner(
    ctx: TenantContext, partner_id: UUID, *, label: Any = ""
) -> Any:
    """Mint a referral code **for** a partner, as staff setting it up.

    The code is the partner's exactly as if it had made it: same alphabet, same
    limit, same audit action (the entry names the partner). Staff may do this
    only for a partner that holds ``referral.create`` - the capability, not the
    screen, decides who has codes - and never for a terminated one. A draft
    partner may be given its code before it is activated; the code captures
    nothing until the partner is active (``capture_referral``).
    """
    authz.require(ctx, STAFF_MANAGE)
    row = partner_selectors.partner_row(partner_id)
    if row is None:
        raise PartnerRecordNotFound("No such partner.", partner_id=str(partner_id))
    if row.status == PartnerStatus.TERMINATED.value:
        raise PartnerValidationError(
            "A terminated partner cannot be given a referral code.", field="partner"
        )
    if capabilities.REFERRAL_CREATE not in partner_selectors.active_capabilities(
        partner_id
    ):
        raise PartnerValidationError(
            "This partner does not hold the referral capability, so it cannot "
            "have a referral code.",
            field="partner",
        )
    return _mint(ctx, partner_id, label)


def _mint(
    ctx: TenantContext,
    partner_id: UUID,
    label: Any,
    *,
    chosen: Any = None,
    target: tuple[str, str, str, Any] = _GENERAL,
    campaign_id: Any = None,
) -> Any:
    from workeasy360.commercial.commissions.models import ReferralCode
    from workeasy360.commercial.partners.domain import clean_text

    text = clean_text(label, field="label", max_length=64, required=False)
    wanted = _chosen_code(chosen)
    kind, product, plan, update = target
    campaign = _campaign(partner_id, campaign_id)
    if campaign is not None and not text:
        text = campaign.name

    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                [f"refcodes:{partner_id}"],
            )
        live = ReferralCode.objects.filter(
            partner_id=partner_id, status=GrantStatus.ACTIVE.value
        ).count()
        if live >= MAX_CODES_PER_PARTNER:
            raise PartnerValidationError(
                f"A partner may hold at most {MAX_CODES_PER_PARTNER} active codes.",
                field="label",
            )
        for _ in range(1 if wanted else 5):
            try:
                with transaction.atomic():
                    code = ReferralCode.objects.create(
                        partner_id=partner_id,
                        code=wanted or _new_code(),
                        label=text,
                        link_kind=kind,
                        product_code=product,
                        plan_code=plan,
                        update=update,
                        campaign=campaign,
                        created_by_id=ctx.user_id,
                    )
                break
            except IntegrityError:
                if wanted:
                    raise PartnerValidationError(
                        "That code is taken. Choose another.", field="code"
                    ) from None
                continue
        else:  # pragma: no cover - 31^8 space
            raise PartnerValidationError("Could not allocate a code; try again.")
        audit.record(
            ctx,
            "partner.referral_code_created",
            resource_type="commissions.referral_code",
            resource_id=code.id,
            changes={
                "label": text,
                "partner_id": str(partner_id),
                "chosen": bool(wanted),
                "link_kind": kind,
                "product_code": product,
                "plan_code": plan,
                "update_id": str(update.id) if update is not None else None,
                "campaign_id": str(campaign.id) if campaign else None,
            },
        )
    return code


def destination(code: Any) -> str:
    """Where a referral link sends the visitor: always a path on the
    platform's own origin, derived when the link is followed from its kind and
    its product or update - never a stored URL, so a link cannot be turned into
    an open redirect. A product that has left the Store sends the visitor to
    sign-up; an archived update, to its product's page. Attribution never
    depends on which page opens."""
    from workeasy360.commercial.catalog import selectors as catalog_selectors

    product = getattr(code, "product_code", "") or ""
    if not product:
        return "/signup"
    slugs = {p["code"]: p["slug"] for p in catalog_selectors.store_products()}
    slug = slugs.get(product)
    if not slug or not _SLUG_RE.fullmatch(slug):
        return "/signup"
    kind = getattr(code, "link_kind", ReferralLinkKind.PRODUCT.value)
    if kind in (ReferralLinkKind.FEATURE.value, ReferralLinkKind.UPDATE.value):
        path = catalog_selectors.update_page_path(code.update_id)
        if path and _UPDATE_PATH_RE.fullmatch(path):
            return path
    if kind == ReferralLinkKind.PRICING.value:
        return f"/products/{slug}?view=pricing"
    suffix = f"?plan={code.plan_code}" if code.plan_code else ""
    return f"/products/{slug}{suffix}"


_SLUG_RE: Final = re.compile(r"[a-z0-9-]{1,64}")
_UPDATE_PATH_RE: Final = re.compile(r"/products/[a-z0-9-]{1,64}/updates/[a-z0-9-]{1,64}")


@in_scope
def update_referral_code(
    ctx: TenantContext, code_id: UUID, *, label: Any = None, active: Any = None
) -> Any:
    """Rename a link, or switch it off or back on (ADR-054). Switching back on
    is refused for a product the partner may no longer promote, and counts
    against the active-link limit. Its referrals and earnings are untouched."""
    from workeasy360.commercial.commissions.models import ReferralCode
    from workeasy360.commercial.partners.domain import clean_text

    authz.require(ctx, CREATE)
    if active is not None and not isinstance(active, bool):
        raise PartnerValidationError("active must be true or false.", field="active")
    if label is None and active is None:
        raise PartnerValidationError("Nothing to change.", field="body")
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                [f"refcodes:{ctx.partner_id}"],
            )
        code = (
            ReferralCode.objects.select_for_update()
            .filter(id=code_id, partner_id=ctx.partner_id)
            .first()
        )
        if code is None:
            raise PartnerRecordNotFound("No such referral code.", code_id=str(code_id))
        changed: dict[str, Any] = {}
        if label is not None:
            text = clean_text(label, field="label", max_length=64, required=False)
            if text != code.label:
                code.label = changed["label"] = text
        if active is not None:
            status = GrantStatus.ACTIVE.value if active else GrantStatus.REVOKED.value
            if status != code.status:
                if active:
                    _may_reactivate(ctx, code)
                code.status = changed["status"] = status
        if changed:
            code.save(update_fields=list(changed))
            audit.record(
                ctx,
                "partner.referral_code_changed",
                resource_type="commissions.referral_code",
                resource_id=code.id,
                changes=changed,
            )
    return code


def _may_reactivate(ctx: TenantContext, code: Any) -> None:
    from workeasy360.commercial.commissions.models import ReferralCode

    access.require_transacting(ctx)
    if code.product_code and not promotion.may_promote(
        code.partner_id, code.product_code
    ):
        raise PartnerValidationError(
            "You are no longer authorized to promote this product.", field="active"
        )
    live = ReferralCode.objects.filter(
        partner_id=code.partner_id, status=GrantStatus.ACTIVE.value
    ).count()
    if live >= MAX_CODES_PER_PARTNER:
        raise PartnerValidationError(
            f"A partner may hold at most {MAX_CODES_PER_PARTNER} active codes.",
            field="active",
        )


@in_scope
def disable_referral_code(ctx: TenantContext, code_id: UUID) -> Any:
    """Stop a code accepting new captures. Existing referrals are unaffected."""
    from workeasy360.commercial.commissions.models import ReferralCode

    authz.require(ctx, CREATE)
    with transaction.atomic():
        code = (
            ReferralCode.objects.select_for_update()
            .filter(id=code_id, partner_id=ctx.partner_id)
            .first()
        )
        if code is None:
            raise PartnerRecordNotFound("No such referral code.", code_id=str(code_id))
        if code.status == GrantStatus.ACTIVE.value:
            code.status = GrantStatus.REVOKED.value
            code.save(update_fields=["status"])
            audit.record(
                ctx,
                "partner.referral_code_disabled",
                resource_type="commissions.referral_code",
                resource_id=code.id,
            )
    return code


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def capture_referral(
    raw_code: Any, *, visitor_id: Any = None, held_token: Any = None
) -> str | None:
    """Record that a visitor followed a partner's code. **Public.**

    Returns the opaque token for the visitor's browser to keep, or ``None`` when
    the code is not usable - unknown, disabled, or its partner may not take new
    referrals. The refusal is uniform, so the endpoint does not reveal which
    codes exist or why one does not work.

    ``visitor_id`` is the visitor's first-party cookie id (ADR-053); only its
    hash is stored, to count unique visitors.
    """
    capture = capture_visit(raw_code, visitor_id=visitor_id, held_token=held_token)
    return capture[0] if capture is not None else None


def capture_visit(
    raw_code: Any, *, visitor_id: Any = None, held_token: Any = None
) -> tuple[str, str] | None:
    """:func:`capture_referral` plus the link's destination path.

    ``held_token`` is the referral token the browser already keeps. While it
    is live, it is what comes back - first touch - and this click is recorded
    for the link's figures only, under a token nobody is given."""
    from workeasy360.commercial.commissions.models import Referral, ReferralCode

    code = str(raw_code or "").strip().upper()
    if not _CODE_RE.match(code):
        return None
    row = (
        ReferralCode.objects.select_related("partner")
        .filter(code=code, status=GrantStatus.ACTIVE.value)
        .first()
    )
    if (
        row is None
        or row.partner.status != PartnerStatus.ACTIVE.value
        or capabilities.REFERRAL_CREATE
        not in partner_selectors.active_capabilities(row.partner_id)
        or (row.product_code and not _promotion_allows(row))
    ):
        metrics.increment(REFERRAL_EVENTS, outcome="rejected")
        return None

    token = secrets.token_urlsafe(24)
    now = timezone.now()
    held = _live_token(held_token, now)
    visitor = str(visitor_id or "")
    Referral.objects.create(
        code=row,
        partner_id=row.partner_id,
        token_hash=_hash(token),
        captured_at=now,
        expires_at=now + capture_window(),
        visitor_hash=_hash(visitor) if 16 <= len(visitor) <= 64 else "",
    )
    metrics.increment(REFERRAL_EVENTS, outcome="kept" if held else "captured")
    return held or token, destination(row)


def _promotion_allows(code: Any) -> bool:
    """A product link takes visitors while its partner may promote the
    product, or - after a revocation - while the programme says ``continue``."""
    return (
        promotion.may_promote(code.partner_id, code.product_code)
        or policy.current().revoked_links is RevokedLinkPolicy.CONTINUE
    )


def _live_token(raw: Any, now: Any) -> str | None:
    """``raw`` if it is a live referral token: captured, unconverted, unexpired."""
    from workeasy360.commercial.commissions.models import Referral

    text = str(raw or "").strip()
    if not text or len(text) > 128:
        return None
    live = Referral.objects.filter(
        token_hash=_hash(text),
        status=ReferralStatus.CAPTURED.value,
        expires_at__gt=now,
    ).exists()
    return text if live else None


def record_registration(token: Any) -> bool:
    """The visitor holding ``token`` created a Workeasy360 account (ADR-053's
    Registrations step). Best-effort and idempotent: only a live, unconverted
    capture is stamped, once. Never raises - sign-up must not depend on it."""
    from workeasy360.commercial.commissions.models import Referral

    text = str(token or "").strip()
    if not text or len(text) > 128:
        return False
    try:
        updated = Referral.objects.filter(
            token_hash=_hash(text),
            status=ReferralStatus.CAPTURED.value,
            registered_at__isnull=True,
            expires_at__gt=timezone.now(),
        ).update(registered_at=timezone.now())
    except Exception:  # pragma: no cover - a referral outage never blocks sign-up
        return False
    if updated:
        metrics.increment(REFERRAL_EVENTS, outcome="registered")
    return bool(updated)


def record_trial(organization_id: UUID) -> bool:
    """Stamp the Trial step (ADR-054) on the referral that converted this
    customer, from its first product trial. Idempotent: stamped once. Never
    raises - it runs beside commission accrual after a commercial change."""
    from workeasy360.commercial.commissions.models import Referral
    from workeasy360.commercial.subscriptions.selectors import first_trial_started_at

    try:
        if not Referral.objects.filter(
            organization_id=organization_id,
            status=ReferralStatus.CONVERTED.value,
            trial_started_at__isnull=True,
        ).exists():
            return False
        started = first_trial_started_at(organization_id)
        if started is None:
            return False
        updated = Referral.objects.filter(
            organization_id=organization_id, trial_started_at__isnull=True
        ).update(trial_started_at=started)
    except Exception:  # pragma: no cover - a funnel figure never breaks a sale
        return False
    return bool(updated)


def convert_referral(*, user_id: UUID, token: Any, organization_id: UUID) -> bool:
    """Attribute a customer to the referral a browser presents. Returns whether
    a conversion was recorded.

    Every condition is checked server-side, and a failure of any is the same
    ``False`` - the caller (a signup flow) never learns which:

    - the token is a live, unexpired, unconverted capture;
    - the caller is the **active owner** of the organization (verified from
      the membership, not from anything the client says);
    - the organization is new (created within 30 days), so an established
      customer cannot be retro-attributed;
    - the organization has no partner relationship (a managed customer earns
      for its managing partner, never for a referrer);
    - the caller is not, and never was, one of the referring partner's own
      people (a self-referral would pay the partner for its own purchases).

    A converted referral is never re-pointed, and an organization converts at
    most once (a unique constraint, so the second of two concurrent
    conversions loses).
    """
    from workeasy360.commercial.commissions.models import Referral
    from workeasy360.platform.organizations.resolver import is_active_owner
    from workeasy360.platform.organizations.selectors import organization_created_at

    text = str(token or "").strip()
    if not text or len(text) > 128:
        return False
    if not is_active_owner(user_id=user_id, organization_id=organization_id):
        return False
    created = organization_created_at(organization_id)
    now = timezone.now()
    if created is None or now - created > NEW_CUSTOMER_WINDOW:
        return False
    if partner_selectors.live_link_for_organization(organization_id) is not None:
        return False

    with transaction.atomic():
        referral = (
            Referral.objects.select_for_update()
            .filter(token_hash=_hash(text))
            .select_related("partner")
            .first()
        )
        if (
            referral is None
            or referral.status != ReferralStatus.CAPTURED.value
            or referral.expires_at <= now
            or referral.partner.status != PartnerStatus.ACTIVE.value
            or partner_selectors.has_been_member(referral.partner_id, user_id)
        ):
            return False
        try:
            with transaction.atomic():
                referral.status = ReferralStatus.CONVERTED.value
                referral.converted_at = now
                referral.organization_id = organization_id
                referral.attribution_rule = domain.ATTRIBUTION_RULE
                referral.save(
                    update_fields=[
                        "status",
                        "converted_at",
                        "organization",
                        "attribution_rule",
                    ]
                )
        except IntegrityError:
            return False  # this customer was already attributed
    metrics.increment(REFERRAL_EVENTS, outcome="converted")
    return True


def prune_expired_referrals(*, batch: int = 1000) -> int:
    """Retention: delete unconverted referrals long past their expiry.

    A captured, never-converted referral is an operational scrap, not a
    commercial fact; it is kept for a grace period (for support) and then
    removed. **Converted referrals are never touched** - they are the
    attribution record behind a commission.
    """
    from workeasy360.commercial.commissions.models import Referral

    cutoff = timezone.now() - timedelta(
        days=int(getattr(settings, "W360_REFERRAL_RETENTION_DAYS", 180))
    )
    ids = list(
        Referral.objects.filter(
            status=ReferralStatus.CAPTURED.value, expires_at__lt=cutoff
        ).values_list("id", flat=True)[:batch]
    )
    if not ids:
        return 0
    deleted, _ = Referral.objects.filter(
        id__in=ids, status=ReferralStatus.CAPTURED.value
    ).delete()
    return deleted


def summary(ctx: TenantContext) -> dict[str, Any]:
    authz.require(ctx, "platform.partner_referral.view")
    return {
        "counts": selectors.referral_summary(ctx.partner_id),
        "codes": selectors.referral_codes(ctx.partner_id),
    }

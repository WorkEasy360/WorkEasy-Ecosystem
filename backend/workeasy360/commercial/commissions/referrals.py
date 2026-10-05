"""Referrals: capture, conversion and attribution (Phase 7, Task 36-39, 88-89).

**Affiliate is referral-only.** Holding ``referral.create`` lets a partner mint
public codes and see how many converted; it grants no view of any customer and
no authority over one.

**Attribution rule: first touch, recorded at conversion.** A visitor who
followed several partners' links holds several captured tokens; the customer is
attributed to the partner of the token the *browser presents at conversion*
(the first it kept), that conversion is recorded once and is never re-pointed,
and ``organization_id`` is unique so no customer is ever attributed twice.

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

from workeasy360.commercial.commissions import domain, selectors
from workeasy360.commercial.partners import access, capabilities
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.contracts.enums import GrantStatus, PartnerStatus, ReferralStatus
from workeasy360.contracts.errors import PartnerRecordNotFound, PartnerValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.observability.metrics import metrics

CREATE = "platform.partner_referral.create"

#: No I, L, O, 0 or 1: a code is read aloud and typed from print.
_ALPHABET: Final = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_RE: Final = re.compile(r"^[A-HJKMNP-Z2-9]{8}$")
MAX_CODES_PER_PARTNER: Final = 20
NEW_CUSTOMER_WINDOW: Final = timedelta(days=30)

REFERRAL_EVENTS = "referral_events_total"


def capture_window() -> timedelta:
    return timedelta(days=int(getattr(settings, "W360_REFERRAL_CAPTURE_DAYS", 90)))


def _new_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(8))


@in_scope
def create_referral_code(ctx: TenantContext, *, label: Any = "") -> Any:
    """Mint a public referral code for the acting partner."""
    from workeasy360.commercial.commissions.models import ReferralCode
    from workeasy360.commercial.partners.domain import clean_text

    authz.require(ctx, CREATE)
    access.require_transacting(ctx)
    text = clean_text(label, field="label", max_length=64, required=False)

    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                [f"refcodes:{ctx.partner_id}"],
            )
        live = ReferralCode.objects.filter(
            partner_id=ctx.partner_id, status=GrantStatus.ACTIVE.value
        ).count()
        if live >= MAX_CODES_PER_PARTNER:
            raise PartnerValidationError(
                f"A partner may hold at most {MAX_CODES_PER_PARTNER} active codes.",
                field="label",
            )
        for _ in range(5):
            try:
                with transaction.atomic():
                    code = ReferralCode.objects.create(
                        partner_id=ctx.partner_id,
                        code=_new_code(),
                        label=text,
                        created_by_id=ctx.user_id,
                    )
                break
            except IntegrityError:
                continue
        else:  # pragma: no cover - 31^8 space
            raise PartnerValidationError("Could not allocate a code; try again.")
        audit.record(
            ctx,
            "partner.referral_code_created",
            resource_type="commissions.referral_code",
            resource_id=code.id,
            changes={"label": text},
        )
    return code


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


def capture_referral(raw_code: Any) -> str | None:
    """Record that a visitor followed a partner's code. **Public.**

    Returns the opaque token for the visitor's browser to keep, or ``None`` when
    the code is not usable - unknown, disabled, or its partner may not take new
    referrals. The refusal is uniform, so the endpoint does not reveal which
    codes exist or why one does not work.
    """
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
    ):
        metrics.increment(REFERRAL_EVENTS, outcome="rejected")
        return None

    token = secrets.token_urlsafe(24)
    now = timezone.now()
    Referral.objects.create(
        code=row,
        partner_id=row.partner_id,
        token_hash=_hash(token),
        captured_at=now,
        expires_at=now + capture_window(),
    )
    metrics.increment(REFERRAL_EVENTS, outcome="captured")
    return token


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

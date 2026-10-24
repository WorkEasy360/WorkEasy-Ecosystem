"""Scoped partner API credentials (ADR-046).

A partner's own systems may read a small, fixed slice of the partner's data
over HTTPS with a bearer key. This module is the key's whole lifecycle -
create, rotate, revoke, list, and the check that a presented key is good - and
it is deliberately narrow:

- **Only partner keys.** A key reaches exactly the partner that owns it, through
  the read-only ``/partner-api/`` endpoints. It is never a session, never
  accepted by the partner portal's own API, and never a way to reach anything
  of Workeasy360's: no cloud credential, no master secret, no signing key and
  no database credential is stored, shown or accepted anywhere here.
- **Scopes are a closed list** (:data:`SCOPES`), each tied to the permission it
  stands for. A person can only mint a key for scopes their own role already
  holds (no escalation), and a scope is checked again on every use - revoke the
  capability, or demote the creator, and the key narrows or stops at once.
- **The secret is shown once** and only its SHA-256 is kept. It is 256 random
  bits, so a fast hash is the right tool (as for every other bearer secret on
  the platform); comparison is constant-time.
- **Creating, rotating and revoking are dangerous actions**: they need the
  ``platform.partner_apikey.manage`` permission, which the permission registry
  marks ``dangerous`` - a second-factor (AAL 2) session is required by the one
  decision function, not by this module - and they are rate-limited by the view
  and audited here (the audit entry names the key, never the secret).
- **A key always expires** (30 to 365 days) and dies with its creator's
  membership.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from workeasy360.commercial.partners import access, inputs, selectors
from workeasy360.contracts.errors import PartnerRecordNotFound, PartnerValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope

VIEW: Final = "platform.partner_apikey.view"
MANAGE: Final = "platform.partner_apikey.manage"

PREFIX: Final = "w360pk_"
KEY_ID_LENGTH: Final = 12
EXPIRY_CHOICES_DAYS: Final = (30, 90, 180, 365)
DEFAULT_EXPIRY_DAYS: Final = 90
MAX_ACTIVE_KEYS: Final = 20
#: ``last_used_at`` is written at most this often per key: a busy integration
#: must not turn every read into a write.
LAST_USED_RESOLUTION: Final = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class ScopeSpec:
    key: str
    label: str
    description: str
    #: The permission the scope stands for. A key may carry the scope only while
    #: its creator's role holds this, so a key can never do more than a person.
    permission: str


SCOPES: Final[dict[str, ScopeSpec]] = {
    s.key: s
    for s in (
        ScopeSpec(
            "account.read",
            "Read the account",
            "The partner's name, status and payout currency.",
            "platform.partner_profile.view",
        ),
        ScopeSpec(
            "customers.read",
            "Read customers",
            "The partner's customer list: the same fields the portal shows, no owner or member details.",
            "platform.partner_customer.view",
        ),
        ScopeSpec(
            "commissions.read",
            "Read commission",
            "The partner's commission ledger entries.",
            "platform.partner_commission.view",
        ),
    )
}


# --- the credential --------------------------------------------------------------------


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _mint() -> tuple[str, str, str]:
    """``(token, key_id, secret_hash)`` for a fresh credential."""
    key_id = secrets.token_hex(KEY_ID_LENGTH // 2)
    secret = secrets.token_urlsafe(32)
    return f"{PREFIX}{key_id}_{secret}", key_id, hash_secret(secret)


def parse_token(token: Any) -> tuple[str, str] | None:
    """``(key_id, secret)`` from a presented credential, or ``None`` if it is not
    shaped like one. Pure parsing: nothing is looked up."""
    if not isinstance(token, str) or len(token) > 128 or not token.startswith(PREFIX):
        return None
    body = token[len(PREFIX) :]
    key_id, sep, secret = (
        body[:KEY_ID_LENGTH],
        body[KEY_ID_LENGTH : KEY_ID_LENGTH + 1],
        body[KEY_ID_LENGTH + 1 :],
    )
    if sep != "_" or len(key_id) != KEY_ID_LENGTH or not secret:
        return None
    return key_id, secret


def _state(row: Any, now: datetime) -> str:
    if row.status == "revoked":
        return "revoked"
    return "expired" if row.expires_at <= now else "active"


def _row(row: Any, now: datetime, emails: dict[Any, str]) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "key_id": f"{PREFIX}{row.key_id}",
        "scopes": [
            {"key": k, "label": SCOPES[k].label} for k in row.scopes if k in SCOPES
        ],
        "status": _state(row, now),
        "created_at": row.created_at.isoformat(),
        "created_by": emails.get(row.created_by.user_id, ""),
        "expires_at": row.expires_at.isoformat(),
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "rotated_from": bool(row.replaces_id),
    }


# --- reads --------------------------------------------------------------------------------


@in_scope
def overview(ctx: TenantContext) -> dict[str, Any]:
    """The partner's keys (never a secret or a hash) and what a new key may carry."""
    from workeasy360.commercial.partners.models import PartnerApiKey
    from workeasy360.platform.identity import selectors as identity

    authz.require(ctx, VIEW)
    permissions = authz.effective_permissions(ctx)
    now = timezone.now()
    rows = list(
        PartnerApiKey.objects.filter(partner_id=ctx.partner_id)
        .select_related("created_by")
        .order_by("-created_at", "-id")[:100]
    )
    emails = identity.emails_for_users({r.created_by.user_id for r in rows})
    return {
        "keys": [_row(r, now, emails) for r in rows],
        "scopes": [
            {"key": s.key, "label": s.label, "description": s.description}
            for s in SCOPES.values()
            if s.permission in permissions
        ],
        "expiry_days": list(EXPIRY_CHOICES_DAYS),
        "default_expiry_days": DEFAULT_EXPIRY_DAYS,
        "can_manage": MANAGE in permissions,
        "limit": MAX_ACTIVE_KEYS,
        "never_issued": [
            "Cloud or hosting credentials",
            "Workeasy360 master secrets or signing keys",
            "Database credentials",
        ],
    }


# --- commands ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Issued:
    """What a create or a rotate returns: the key's row, and the secret that is
    shown to the person once and exists nowhere else."""

    key: dict[str, Any]
    secret: str


def _clean_scopes(ctx: TenantContext, value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise PartnerValidationError("Choose at least one scope.", field="scopes")
    if len(value) > len(SCOPES) or not all(isinstance(v, str) for v in value):
        raise PartnerValidationError(
            "scopes must be a list of scope names.", field="scopes"
        )
    unknown = sorted(set(value) - set(SCOPES))
    if unknown:
        raise PartnerValidationError(
            f"Unknown scopes {unknown}. Allowed: {sorted(SCOPES)}.", field="scopes"
        )
    permissions = authz.effective_permissions(ctx)
    # A person cannot mint a key that does more than their own role can.
    beyond = sorted(s for s in set(value) if SCOPES[s].permission not in permissions)
    if beyond:
        raise PartnerValidationError(
            f"Your role does not allow the scopes {beyond}.", field="scopes"
        )
    return sorted(set(value), key=list(SCOPES).index)


def _clean_expiry(value: Any) -> int:
    if value is None:
        return DEFAULT_EXPIRY_DAYS
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value not in EXPIRY_CHOICES_DAYS
    ):
        raise PartnerValidationError(
            f"expires_in_days must be one of {list(EXPIRY_CHOICES_DAYS)}.",
            field="expires_in_days",
        )
    return value


def _issue(
    ctx: TenantContext,
    *,
    name: str,
    scopes: list[str],
    days: int,
    replaces: Any = None,
) -> Issued:
    from workeasy360.commercial.partners.models import PartnerApiKey
    from workeasy360.platform.identity import selectors as identity

    member = selectors.member_of(ctx.partner_id, ctx.user_id)
    if member is None:  # pragma: no cover - partner_actor already proved it
        raise PartnerRecordNotFound("No such partner.", partner_id=str(ctx.partner_id))
    now = timezone.now()
    token, key_id, digest = _mint()
    row = PartnerApiKey.objects.create(
        partner_id=ctx.partner_id,
        name=name,
        key_id=key_id,
        secret_hash=digest,
        scopes=scopes,
        created_by=member,
        created_at=now,
        expires_at=now + timedelta(days=days),
        replaces=replaces,
    )
    emails = identity.emails_for_users({ctx.user_id})
    return Issued(key=_row(row, now, emails), secret=token)


@in_scope
def create(
    ctx: TenantContext, *, name: Any, scopes: Any, expires_in_days: Any = None
) -> Issued:
    """Mint a key. The secret is in the result and nowhere else."""
    from workeasy360.commercial.partners.models import PartnerApiKey
    from workeasy360.commercial.partners.services import _lock_partner

    access.require_transacting(ctx)
    authz.require(ctx, MANAGE)
    label = inputs.clean_markup_free(name, field="name", max_length=80)
    if not label:
        raise PartnerValidationError("Give the key a name.", field="name")
    wanted = _clean_scopes(ctx, scopes)
    days = _clean_expiry(expires_in_days)
    with transaction.atomic():
        _lock_partner(ctx.partner_id)
        live = PartnerApiKey.objects.filter(
            partner_id=ctx.partner_id, status="active", expires_at__gt=timezone.now()
        ).count()
        if live >= MAX_ACTIVE_KEYS:
            raise PartnerValidationError(
                f"At most {MAX_ACTIVE_KEYS} keys can be active at once. Revoke one first.",
                field="name",
            )
        issued = _issue(ctx, name=label, scopes=wanted, days=days)
        _audit(ctx, "partner.api_key_created", issued.key, scopes=wanted, days=days)
    return issued


@in_scope
def rotate(ctx: TenantContext, *, key_id: UUID, expires_in_days: Any = None) -> Issued:
    """Replace a key with a new secret. The old secret stops working at once; the
    new key keeps the old one's name and scopes (re-checked against the person
    rotating - rotation is no way around the no-escalation rule)."""
    from workeasy360.commercial.partners.models import PartnerApiKey
    from workeasy360.commercial.partners.services import _lock_partner

    access.require_transacting(ctx)
    authz.require(ctx, MANAGE)
    days = _clean_expiry(expires_in_days)
    with transaction.atomic():
        _lock_partner(ctx.partner_id)
        old = (
            PartnerApiKey.objects.select_for_update()
            .filter(id=key_id, partner_id=ctx.partner_id, status="active")
            .first()
        )
        if old is None:
            raise PartnerRecordNotFound("No such key.", key_id=str(key_id))
        scopes = _clean_scopes(ctx, list(old.scopes))
        now = timezone.now()
        old.status = "revoked"
        old.revoked_at = now
        old.revoked_by_id = ctx.user_id
        old.revoke_reason = "rotated"
        old.save(update_fields=["status", "revoked_at", "revoked_by", "revoke_reason"])
        issued = _issue(ctx, name=old.name, scopes=scopes, days=days, replaces=old)
        _audit(ctx, "partner.api_key_rotated", issued.key, scopes=scopes, days=days)
    return issued


@in_scope
def revoke(ctx: TenantContext, *, key_id: UUID) -> dict[str, Any]:
    """Stop a key now. Revoking an already-revoked key is a no-op 404, like any
    other id that is not an active key of this partner."""
    from workeasy360.commercial.partners.models import PartnerApiKey
    from workeasy360.platform.identity import selectors as identity

    authz.require(ctx, MANAGE)
    with transaction.atomic():
        row = (
            PartnerApiKey.objects.select_for_update()
            .select_related("created_by")
            .filter(id=key_id, partner_id=ctx.partner_id, status="active")
            .first()
        )
        if row is None:
            raise PartnerRecordNotFound("No such key.", key_id=str(key_id))
        now = timezone.now()
        row.status = "revoked"
        row.revoked_at = now
        row.revoked_by_id = ctx.user_id
        row.revoke_reason = "revoked"
        row.save(update_fields=["status", "revoked_at", "revoked_by", "revoke_reason"])
        emails = identity.emails_for_users({row.created_by.user_id})
        shaped = _row(row, now, emails)
        _audit(ctx, "partner.api_key_revoked", shaped)
    return shaped


def _audit(
    ctx: TenantContext,
    action: str,
    key: dict[str, Any],
    *,
    scopes: list[str] | None = None,
    days: int | None = None,
) -> None:
    """The trail names the key (its public id) and what it may do. It never
    carries the secret, the hash or the full credential."""
    changes: dict[str, Any] = {
        "partner_id": str(ctx.partner_id),
        "key_id": key["key_id"],
        "name": key["name"],
    }
    if scopes is not None:
        changes["scopes"] = scopes
    if days is not None:
        changes["expires_in_days"] = days
    audit.record(
        ctx,
        action,
        resource_type="partners.api_key",
        resource_id=key["id"],
        changes=changes,
    )


# --- using a key ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Principal:
    """Who a good key is: a partner and what the key may still do. Carries no
    user, no session and no authority beyond ``scopes``."""

    partner_id: UUID
    key_pk: UUID
    key_id: str
    scopes: frozenset[str]


def authenticate(token: Any, *, now: datetime | None = None) -> Principal | None:
    """The principal a presented credential stands for, or ``None``.

    Every failure is the same ``None`` - wrong shape, unknown id, wrong secret,
    revoked, expired, partner not active, creator no longer a member - so the
    endpoint cannot be used to learn which of those it was. A key is good only
    while *all* of these hold, checked on every request:

    - the secret matches (constant-time);
    - it is active and not past its expiry;
    - its partner may still transact;
    - the member who created it is still an active member of that partner;
    - each scope is still permitted to that member (the intersection is returned).
    """
    from workeasy360.commercial.partners.models import PartnerApiKey

    parsed = parse_token(token)
    if parsed is None:
        return None
    key_id, secret = parsed
    now = now or timezone.now()
    row = (
        PartnerApiKey.objects.select_related("partner", "created_by", "created_by__user")
        .filter(key_id=key_id)
        .first()
    )
    # Hash even when no row matched, so timing does not reveal which ids exist.
    presented = hash_secret(secret)
    if row is None:
        hmac.compare_digest(presented, "0" * 64)
        return None
    if not hmac.compare_digest(presented, row.secret_hash):
        return None
    if row.status != "active" or row.expires_at <= now:
        return None
    if not row.partner.status_enum.may_transact:
        return None
    member = row.created_by
    if member.status != "active" or member.partner_id != row.partner_id:
        return None
    if member.user.status != "active":
        return None
    permitted = selectors._permissions_of(member)
    scopes = frozenset(
        s for s in row.scopes if s in SCOPES and SCOPES[s].permission in permitted
    )
    if not scopes:
        return None
    if row.last_used_at is None or now - row.last_used_at >= LAST_USED_RESOLUTION:
        PartnerApiKey.objects.filter(pk=row.pk).update(last_used_at=now)
    return Principal(
        partner_id=row.partner_id, key_pk=row.pk, key_id=row.key_id, scopes=scopes
    )

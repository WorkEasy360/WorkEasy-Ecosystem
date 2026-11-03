"""Payout details and withdrawal requests: the records (2026-11-03, ADR-057).

This module owns ``PartnerPayoutMethod`` and ``PartnerWithdrawalRequest``: how
they are validated, sealed, written and read. It moves no money and reads no
ledger. The orchestration that needs the commission ledger and the payout
engine - the available balance, TDS, turning a request into a payout - is
``controlplane.partner_withdrawals``, which calls the functions here.

**Sealing.** The account number and the PAN are Fernet-sealed under
``PARTNER_PAYOUT_SEAL_KEYS``, bound to the partner and the field, so a
ciphertext copied to another row does not open. Only the last four characters
are stored in the clear. With no key configured, saving is refused
(``SealUnavailable``) - a key is never derived in production.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone

from workeasy360.commercial.partners import inputs
from workeasy360.commercial.partners.services import _lock_partner
from workeasy360.contracts.errors import (
    PartnerRecordNotFound,
    PartnerRequestConflict,
    PartnerValidationError,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit

IFSC: Final = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
ACCOUNT: Final = re.compile(r"^[0-9]{6,18}$")
VPA: Final = re.compile(r"^[A-Za-z0-9.\-_]{2,64}@[A-Za-z][A-Za-z0-9]{1,63}$")
PAN: Final = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")

OPEN: Final = "requested"
QUEUE_STATES: Final = ("requested", "processing", "rejected", "cancelled")


class SealUnavailable(PartnerValidationError):
    """No payout seal key is configured, so payout details cannot be stored."""


def _method_model() -> Any:
    from workeasy360.commercial.partners.models import PartnerPayoutMethod

    return PartnerPayoutMethod


def _request_model() -> Any:
    from workeasy360.commercial.partners.models import PartnerWithdrawalRequest

    return PartnerWithdrawalRequest


# --- sealing --------------------------------------------------------------------------------


def _box() -> MultiFernet:
    keys = list(getattr(settings, "PARTNER_PAYOUT_SEAL_KEYS", []) or [])
    if not keys:
        raise SealUnavailable(
            "Payout details cannot be saved yet: the platform has no payout "
            "encryption key configured. Ask Workeasy360 support.",
            field="method",
        )
    return MultiFernet([Fernet(k) for k in keys])


def _seal(value: str, *, partner_id: UUID, field: str) -> str:
    payload = json.dumps(
        {"p": str(partner_id), "f": field, "v": value}, separators=(",", ":")
    )
    return _box().encrypt(payload.encode("utf-8")).decode("ascii")


def _open(token: str, *, partner_id: UUID, field: str) -> str:
    if not token:
        return ""
    try:
        data = json.loads(_box().decrypt(token.encode("ascii")))
    except (InvalidToken, ValueError):
        return ""
    if data.get("p") != str(partner_id) or data.get("f") != field:
        return ""
    return str(data.get("v", ""))


# --- payout details ---------------------------------------------------------------------------


def method_row(partner_id: UUID) -> Any | None:
    return _method_model().objects.filter(partner_id=partner_id).first()


def destination(row: Any) -> str:
    if row.method == "upi":
        return f"UPI {row.upi_vpa}"
    return f"Bank ****{row.account_hint} ({row.ifsc})"


def method_dict(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "method": row.method,
        "account_holder": row.account_holder,
        "bank_name": row.bank_name,
        "ifsc": row.ifsc,
        "account_hint": row.account_hint,
        "upi_vpa": row.upi_vpa,
        "pan_on_file": bool(row.pan_sealed),
        "pan_hint": row.pan_hint,
        "verification": row.verification,
        "verification_note": row.verification_note,
        "verified_at": row.verified_at.isoformat() if row.verified_at else None,
        "updated_at": row.updated_at.isoformat(),
        "destination": destination(row),
    }


def clean_method(body: Any, existing: Any | None, currency: str) -> dict[str, str]:
    """Validate a payout-details form. A blank account number or PAN keeps the
    one on file; an INR partner must have a PAN (TDS is filed against it)."""
    data = body if isinstance(body, dict) else {}
    method = str(data.get("method") or "")
    if method not in ("bank", "upi"):
        raise PartnerValidationError("Choose bank transfer or UPI.", field="method")
    holder = inputs.clean_markup_free(
        data.get("account_holder"), field="account_holder", max_length=120
    )
    if not holder:
        raise PartnerValidationError(
            "Enter the account holder's name.", field="account_holder"
        )
    out = {
        "method": method,
        "account_holder": holder,
        "bank_name": "",
        "ifsc": "",
        "account": "",
        "upi_vpa": "",
    }
    if method == "bank":
        out["bank_name"] = inputs.clean_markup_free(
            data.get("bank_name"), field="bank_name", max_length=120
        )
        out["ifsc"] = str(data.get("ifsc") or "").strip().upper()
        if not IFSC.match(out["ifsc"]):
            raise PartnerValidationError(
                "Enter an 11-character IFSC (for example HDFC0001234).", field="ifsc"
            )
        account = re.sub(r"\s", "", str(data.get("account_number") or ""))
        if account:
            if not ACCOUNT.match(account):
                raise PartnerValidationError(
                    "Enter the account number: 6 to 18 digits.", field="account_number"
                )
            if account != re.sub(
                r"\s", "", str(data.get("account_number_confirm") or "")
            ):
                raise PartnerValidationError(
                    "The two account numbers do not match.",
                    field="account_number_confirm",
                )
        elif not (
            existing is not None and existing.method == "bank" and existing.account_sealed
        ):
            raise PartnerValidationError(
                "Enter the account number.", field="account_number"
            )
        out["account"] = account
    else:
        out["upi_vpa"] = str(data.get("upi_vpa") or "").strip()
        if not VPA.match(out["upi_vpa"]):
            raise PartnerValidationError(
                "Enter a UPI address such as name@bank.", field="upi_vpa"
            )
    pan = str(data.get("pan") or "").strip().upper()
    if pan and not PAN.match(pan):
        raise PartnerValidationError(
            "Enter a 10-character PAN (for example ABCDE1234F).", field="pan"
        )
    if (
        not pan
        and currency == "INR"
        and not (existing is not None and existing.pan_sealed)
    ):
        raise PartnerValidationError(
            "Enter your PAN: TDS cannot be filed without it.", field="pan"
        )
    out["pan"] = pan
    return out


def save_method(ctx: TenantContext, partner_id: UUID, body: Any, currency: str) -> Any:
    """Validate, seal and save the partner's payout details; any change sends
    them back to pending verification. Audited without a number."""
    data = clean_method(body, method_row(partner_id), currency)
    _box()  # refuse before anything is written when no key is configured
    with transaction.atomic():
        _lock_partner(partner_id)
        row = method_row(partner_id) or _method_model()(partner_id=partner_id)
        row.method = data["method"]
        row.account_holder = data["account_holder"]
        row.bank_name = data["bank_name"]
        row.ifsc = data["ifsc"]
        row.upi_vpa = data["upi_vpa"]
        if data["method"] == "upi":
            row.account_sealed, row.account_hint = "", ""
        elif data["account"]:
            row.account_sealed = _seal(
                data["account"], partner_id=partner_id, field="account"
            )
            row.account_hint = data["account"][-4:]
        if data["pan"]:
            row.pan_sealed = _seal(data["pan"], partner_id=partner_id, field="pan")
            row.pan_hint = data["pan"][-4:]
        row.verification = "pending"
        row.verification_note = ""
        row.verified_by_id = None
        row.verified_at = None
        row.updated_by_id = ctx.user_id
        row.updated_at = timezone.now()
        row.save()
        audit.record(
            ctx,
            "partner.payout_method_saved",
            resource_type="partners.payout_method",
            resource_id=partner_id,
            # Which kind - never a number.
            changes={"method": row.method, "pan_changed": bool(data["pan"])},
        )
    return row


def set_verification(
    ctx: TenantContext, partner_id: UUID, verdict: str, note: str
) -> Any:
    with transaction.atomic():
        _lock_partner(partner_id)
        row = method_row(partner_id)
        if row is None:
            raise PartnerRecordNotFound("This partner has no payout details.")
        row.verification = verdict
        row.verification_note = note
        row.verified_by_id = ctx.user_id
        row.verified_at = timezone.now()
        row.save(
            update_fields=[
                "verification",
                "verification_note",
                "verified_by",
                "verified_at",
            ]
        )
        audit.record(
            ctx,
            "partner.payout_method_verified",
            resource_type="partners.payout_method",
            resource_id=partner_id,
            changes={"decision": verdict},
        )
    return row


def revealed(ctx: TenantContext, partner_id: UUID, row: Any) -> dict[str, str]:
    """The full account number and PAN, for the person making the transfer.
    Audited every time."""
    audit.record(
        ctx,
        "partner.payout_method_revealed",
        resource_type="partners.payout_method",
        resource_id=partner_id,
    )
    return {
        "account_number": _open(
            row.account_sealed, partner_id=partner_id, field="account"
        ),
        "pan": _open(row.pan_sealed, partner_id=partner_id, field="pan"),
    }


def pending_methods(limit: int = 100) -> list[Any]:
    return list(
        _method_model()
        .objects.filter(verification="pending")
        .select_related("partner")
        .order_by("updated_at")[:limit]
    )


# --- withdrawal requests ----------------------------------------------------------------------


def recent_requests(partner_id: UUID, limit: int = 50) -> list[Any]:
    return list(
        _request_model()
        .objects.filter(partner_id=partner_id)
        .order_by("-requested_at")[:limit]
    )


def has_open_request(partner_id: UUID) -> bool:
    return _request_model().objects.filter(partner_id=partner_id, status=OPEN).exists()


def create_request(
    ctx: TenantContext,
    *,
    partner_id: UUID,
    currency: str,
    amount_minor: int,
    tds_rate_bp: int,
    tds_minor: int,
    net_minor: int,
    to: str,
) -> Any:
    """A new open request (inside the caller's transaction, which holds the
    partner lock). The partial unique index is the last word on "one open"."""
    try:
        with transaction.atomic():
            row = _request_model().objects.create(
                partner_id=partner_id,
                currency=currency,
                amount_minor=amount_minor,
                tds_rate_bp=tds_rate_bp,
                tds_minor=tds_minor,
                net_minor=net_minor,
                destination=to,
                requested_by_id=ctx.user_id,
                requested_at=timezone.now(),
            )
    except IntegrityError:
        raise PartnerRequestConflict(
            "A withdrawal request is already waiting for Workeasy360.", status=OPEN
        ) from None
    audit.record(
        ctx,
        "partner.withdrawal_requested",
        resource_type="partners.withdrawal",
        resource_id=row.id,
        changes={
            "currency": currency,
            "amount_minor": amount_minor,
            "tds_minor": tds_minor,
        },
    )
    return row


def lock_open_request(withdrawal_id: UUID, *, partner_id: UUID | None = None) -> Any:
    """The request, row-locked, when it is still waiting (inside a transaction)."""
    queryset = _request_model().objects.select_for_update().filter(id=withdrawal_id)
    if partner_id is not None:
        queryset = queryset.filter(partner_id=partner_id)
    row = queryset.first()
    if row is None:
        raise PartnerRecordNotFound("No such withdrawal request.")
    if row.status != OPEN:
        raise PartnerRequestConflict("This request is not waiting.", status=row.status)
    return row


def decide(
    ctx: TenantContext,
    row: Any,
    *,
    status: str,
    note: str = "",
    payout_id: UUID | None = None,
) -> Any:
    """Close a locked open request: ``processing`` (a payout prepared),
    ``rejected`` (staff, with a note) or ``cancelled`` (the partner)."""
    row.status = status
    row.decision_note = note
    row.decided_by_id = ctx.user_id
    row.decided_at = timezone.now()
    fields = ["status", "decision_note", "decided_by", "decided_at"]
    if payout_id is not None:
        row.payout_id = payout_id
        fields.append("payout_id")
    row.save(update_fields=fields)
    audit.record(
        ctx,
        f"partner.withdrawal_{status}",
        resource_type="partners.withdrawal",
        resource_id=row.id,
        changes={"payout_id": str(payout_id)} if payout_id else None,
    )
    return row


def request_row(withdrawal_id: UUID) -> Any:
    return _request_model().objects.get(id=withdrawal_id)


def queue(status: str, limit: int = 100) -> tuple[list[Any], dict[str, int]]:
    """Requests across partners in one status (or ``all``) and the counts."""
    queryset = _request_model().objects.select_related("partner")
    counts = {s: 0 for s in QUEUE_STATES}
    for r in queryset.values("status").annotate(n=Count("id")):
        counts[r["status"]] = r["n"]
    if status != "all":
        queryset = queryset.filter(status=status)
    return list(queryset.order_by("-requested_at")[:limit]), counts


def pending_count() -> int:
    return _request_model().objects.filter(status=OPEN).count()

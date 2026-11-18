"""Wallet & Payouts: payout details, withdrawal requests and TDS (2026-11-03, ADR-057).

The unified partner dashboard specification asks for a withdrawal queue with a
minimum, TDS (Section 194H) and verified PAN and bank details. This module is
the orchestration; the records are ``partners.withdrawals``:

- **Payout details**: a bank account (holder, bank, IFSC, account number) or a
  UPI address, and the PAN. The account number and the PAN are sealed and shown
  back only as their last four characters. The platform has no penny-drop or
  PAN verification provider, so **verification is a staff decision**; changing
  a detail sends it back to pending. Staff may reveal the full details to make
  a transfer - audited.
- **A withdrawal request** asks for the whole available balance (cleared
  commission in no payout, in the payout currency - the payout engine pays every
  cleared row, never a chosen part). It needs a verified payout method, a PAN
  for INR, at least ``W360_PARTNER_MIN_WITHDRAWAL_MINOR`` and no other open
  request. TDS at ``W360_PARTNER_TDS_RATE_BP`` is worked out on an INR amount.
- **Staff turn a request into a payout** with the existing
  ``payouts.prepare_payout`` (idempotency key ``withdrawal:<id>``). Its approval
  by a second person and its "paid" confirmation are unchanged; the request then
  reads its state from that payout. Or staff reject it with a note.

Nothing here writes the commission ledger or moves money. The browser sends no
amount: every figure is computed from the ledger here.
"""

from __future__ import annotations

from typing import Any, Final
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from workeasy360.commercial.commissions import payouts
from workeasy360.commercial.commissions import selectors as commission_selectors
from workeasy360.commercial.partners import inputs, withdrawals
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.commercial.partners.services import _lock_partner
from workeasy360.contracts.enums import PartnerStatus
from workeasy360.contracts.errors import (
    PartnerNotActive,
    PartnerRecordNotFound,
    PartnerRequestConflict,
    PartnerValidationError,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope

VIEW: Final = "platform.partner_payout.view"
REQUEST: Final = "platform.partner_payout.request"
STAFF: Final = "platform.staff_payout.manage"

OPEN: Final = withdrawals.OPEN
SealUnavailable = withdrawals.SealUnavailable


def _tds_rate(currency: str) -> int:
    # Section 194H is Indian tax: withheld on INR amounts only.
    return (
        int(getattr(settings, "W360_PARTNER_TDS_RATE_BP", 0) or 0)
        if currency == "INR"
        else 0
    )


def _split(amount: int, rate_bp: int) -> tuple[int, int]:
    tds = amount * rate_bp // 10_000
    return tds, amount - tds


def _minimum() -> int:
    return int(getattr(settings, "W360_PARTNER_MIN_WITHDRAWAL_MINOR", 0) or 0)


def _withdrawal_dict(row: Any, payout: dict[str, Any] | None) -> dict[str, Any]:
    status = row.status
    amount, tds, net = row.amount_minor, row.tds_minor, row.net_minor
    if payout is not None:
        # Linked: the payout is the truth - its amount (more may have cleared
        # since the request) and its state.
        status = {"paid": "paid", "cancelled": "cancelled"}.get(
            payout["status"], "processing"
        )
        amount = payout["payable_minor"]
        tds, net = _split(amount, row.tds_rate_bp)
    return {
        "id": str(row.id),
        "currency": row.currency,
        "amount_minor": amount,
        "tds_rate_bp": row.tds_rate_bp,
        "tds_minor": tds,
        "net_minor": net,
        "status": status,
        "destination": row.destination,
        "requested_at": row.requested_at.isoformat(),
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "decision_note": row.decision_note,
        "paid_at": payout["paid_at"] if payout else None,
    }


def _withdrawals(rows: list[Any]) -> list[dict[str, Any]]:
    states = commission_selectors.payout_states([r.payout_id for r in rows])
    return [
        _withdrawal_dict(r, states.get(str(r.payout_id)) if r.payout_id else None)
        for r in rows
    ]


def _available(partner: Any) -> int:
    money = commission_selectors.partner_money(partner.id, timezone.now())
    return next(
        (m["available_minor"] for m in money if m["currency"] == partner.payout_currency),
        0,
    )


def _blocker(partner: Any, method: Any | None, available: int, has_open: bool) -> str:
    """Why a withdrawal cannot be requested now, or "" when it can."""
    if partner.status_enum not in (PartnerStatus.ACTIVE, PartnerStatus.TERMINATED):
        return "Withdrawals are on hold while this partner account is not active."
    if has_open:
        return "A withdrawal request is already waiting for Workeasy360."
    if method is None:
        return "Add your payout details first."
    if method.verification != "verified":
        return (
            "Your payout details were not accepted - update them."
            if method.verification == "rejected"
            else "Your payout details are waiting for verification by Workeasy360."
        )
    if partner.payout_currency == "INR" and not method.pan_sealed:
        return "Add your PAN to your payout details first."
    if available < _minimum():
        return "Your available balance is below the minimum withdrawal."
    return ""


@in_scope
def wallet(ctx: TenantContext) -> dict[str, Any]:
    """The Wallet & Payouts page: balances, payout details, the withdrawal
    rules and preview, and the partner's requests."""
    authz.require(ctx, VIEW)
    partner_id = ctx.partner_id
    assert partner_id is not None
    partner = partner_selectors.partner_row(partner_id)
    method = withdrawals.method_row(partner_id)
    available = _available(partner)
    items = _withdrawals(withdrawals.recent_requests(partner_id))
    has_open = any(i["status"] == OPEN for i in items)
    rate = _tds_rate(partner.payout_currency)
    tds, net = _split(available, rate)
    may = REQUEST in authz.effective_permissions(ctx)
    blocker = _blocker(partner, method, available, has_open)
    shown = withdrawals.method_dict(method)
    if ctx.is_break_glass and shown is not None:
        # A staff guest view (Login as Guest, ADR-062): that payout details are on
        # file and where they stand, never the account, IFSC, UPI id or holder.
        shown = {
            "method": shown["method"],
            "account_holder": "",
            "bank_name": "",
            "ifsc": "",
            "account_hint": "",
            "upi_vpa": "",
            "pan_on_file": shown["pan_on_file"],
            "pan_hint": "",
            "verification": shown["verification"],
            "verification_note": "",
            "verified_at": shown["verified_at"],
            "updated_at": shown["updated_at"],
            "destination": "On file (hidden in Guest Mode)",
            "redacted": True,
        }
    return {
        "currency": partner.payout_currency,
        "balances": commission_selectors.partner_money(partner_id, timezone.now()),
        "method": shown,
        "rules": {
            "minimum_minor": _minimum(),
            "tds_rate_bp": rate,
            "tds_section": "194H" if rate else "",
            "pan_required": partner.payout_currency == "INR",
            "verification": "manual",
        },
        "preview": {"amount_minor": available, "tds_minor": tds, "net_minor": net},
        "can_manage": may,
        "can_request": may and not blocker,
        "blocker": blocker,
        "withdrawals": items,
    }


# --- the partner's commands -------------------------------------------------------------------


@in_scope
def save_payout_method(ctx: TenantContext, *, body: Any) -> dict[str, Any]:
    """Save where payouts go. Any change sends the details back to pending
    verification. A blank account number or PAN keeps the one on file."""
    authz.require(ctx, REQUEST)
    partner_id = ctx.partner_id
    assert partner_id is not None
    partner = partner_selectors.partner_row(partner_id)
    withdrawals.save_method(ctx, partner_id, body, partner.payout_currency)
    return wallet(ctx)


@in_scope
def request_withdrawal(ctx: TenantContext) -> dict[str, Any]:
    """Ask to be paid the whole available balance. Staff see it in the Super
    Admin withdrawal queue."""
    authz.require(ctx, REQUEST)
    partner_id = ctx.partner_id
    assert partner_id is not None
    with transaction.atomic():
        partner = _lock_partner(partner_id)
        method = withdrawals.method_row(partner_id)
        available = _available(partner)
        has_open = withdrawals.has_open_request(partner_id)
        blocker = _blocker(partner, method, available, has_open)
        if blocker:
            if partner.status_enum not in (
                PartnerStatus.ACTIVE,
                PartnerStatus.TERMINATED,
            ):
                raise PartnerNotActive(blocker, partner_status=partner.status)
            if has_open:
                raise PartnerRequestConflict(blocker, status=OPEN)
            raise PartnerValidationError(blocker, field="amount")
        rate = _tds_rate(partner.payout_currency)
        tds, net = _split(available, rate)
        withdrawals.create_request(
            ctx,
            partner_id=partner_id,
            currency=partner.payout_currency,
            amount_minor=available,
            tds_rate_bp=rate,
            tds_minor=tds,
            net_minor=net,
            to=withdrawals.destination(method),
        )
    return wallet(ctx)


@in_scope
def cancel_withdrawal(ctx: TenantContext, withdrawal_id: UUID) -> dict[str, Any]:
    authz.require(ctx, REQUEST)
    with transaction.atomic():
        row = withdrawals.lock_open_request(withdrawal_id, partner_id=ctx.partner_id)
        withdrawals.decide(ctx, row, status="cancelled")
    return wallet(ctx)


# --- Workeasy360 staff -------------------------------------------------------------------------


@in_scope
def staff_queue(ctx: TenantContext, *, status: Any = OPEN) -> dict[str, Any]:
    """Withdrawal requests across partners, and payout details awaiting
    verification."""
    authz.require(ctx, STAFF)
    wanted = str(status or OPEN)
    if wanted not in (*withdrawals.QUEUE_STATES, "all"):
        raise PartnerValidationError("Choose a status.", field="status")
    rows, counts = withdrawals.queue(wanted)
    items = _withdrawals(rows)
    for item, row in zip(items, rows, strict=True):
        item["partner"] = {
            "id": str(row.partner_id),
            "name": row.partner.display_name,
            "code": row.partner.code,
        }
    return {
        "items": items,
        "counts": counts,
        "pending_methods": [
            {
                **withdrawals.method_dict(m),
                "partner": {
                    "id": str(m.partner_id),
                    "name": m.partner.display_name,
                    "code": m.partner.code,
                },
            }
            for m in withdrawals.pending_methods()
        ],
    }


@in_scope
def staff_payout_method(
    ctx: TenantContext, partner_id: UUID, *, reveal: bool = False
) -> dict[str, Any]:
    """One partner's payout details. ``reveal`` opens the account number and
    the PAN, for the person making the transfer - audited every time."""
    authz.require(ctx, STAFF)
    row = withdrawals.method_row(partner_id)
    if row is None:
        raise PartnerRecordNotFound("This partner has no payout details.")
    out = withdrawals.method_dict(row)
    if reveal:
        out.update(withdrawals.revealed(ctx, partner_id, row))
    return out


@in_scope
def staff_verify_method(
    ctx: TenantContext, partner_id: UUID, *, decision: Any, note: Any = ""
) -> dict[str, Any]:
    authz.require(ctx, STAFF)
    verdict = str(decision or "")
    if verdict not in ("verified", "rejected"):
        raise PartnerValidationError("Choose verify or reject.", field="decision")
    text = inputs.clean_markup_free(note, field="note", max_length=500)
    if verdict == "rejected" and not text:
        raise PartnerValidationError(
            "Say what is wrong, so the partner can fix it.", field="note"
        )
    return withdrawals.method_dict(
        withdrawals.set_verification(ctx, partner_id, verdict, text)
    )


@in_scope
def staff_start_payout(ctx: TenantContext, withdrawal_id: UUID) -> dict[str, Any]:
    """Turn a request into an ordinary payout (prepared now, up to today). The
    payout's approval by another person and its paid confirmation follow on
    the Payouts screen, unchanged."""
    authz.require(ctx, STAFF)
    with transaction.atomic():
        row = withdrawals.lock_open_request(withdrawal_id)
        payout = payouts.prepare_payout(
            ctx,
            partner_id=row.partner_id,
            period_end=timezone.now().date(),
            idempotency_key=f"withdrawal:{row.id}",
        )
        withdrawals.decide(ctx, row, status="processing", payout_id=payout.id)
    return _withdrawals([withdrawals.request_row(withdrawal_id)])[0]


@in_scope
def staff_reject(ctx: TenantContext, withdrawal_id: UUID, *, note: Any) -> dict[str, Any]:
    authz.require(ctx, STAFF)
    text = inputs.clean_markup_free(note, field="note", max_length=500)
    if not text:
        raise PartnerValidationError(
            "Say why, so the partner knows what to do.", field="note"
        )
    with transaction.atomic():
        row = withdrawals.lock_open_request(withdrawal_id)
        withdrawals.decide(ctx, row, status="rejected", note=text)
    return _withdrawals([withdrawals.request_row(withdrawal_id)])[0]


def pending_count() -> int:
    return withdrawals.pending_count()

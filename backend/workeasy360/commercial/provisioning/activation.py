"""Automatic product access: what a customer can do with each product, and the
one command that starts a free trial and makes the product ready to open
(2026-11-28 brief "Automatic Free Trial Activation & Product Access").

**One mechanism for every product.** Nothing here names a product: the trial
terms come from the product's own configuration (``ProductTrialConfig`` and the
plan it is started on), the entitlement from ``provisioning.reconcile``, the
seat from the trial's own seat pool and the "can this person open it?" answer
from ``platform.launcher.access.can_launch_product`` - the same decision the
launch endpoint re-runs before a browser leaves for the product. A new product
gets all of it by being registered, priced and given a trial.

**Nothing here grants anything new.** It composes services that already
authorise themselves: ``start_product_trial`` (the owner's
``commercial.subscription.manage``, one trial per organization and product,
idempotent), ``reconcile`` (derives entitlements from durable state) and seat
assignment (only the trial's own starter, or someone allowed to assign seats,
is seated - never another member). Platform staff rights are never touched.

**The customer is told the truth.** "Open Product" is offered only when the
launch decision is ``active``: entitled, seated, and an integration accepting
sign-in. Anything less is "Setting Up" (a change still in flight), "Retry
Setup" (something this command can repair), or a plain reason.

The product itself creates the person and the organization on first sign-in
through the shared Workeasy360 SSO connector (``DefaultUserAdapter`` /
``DefaultOrganizationAdapter``), from the verified ID token; the platform does
not call products (the ecosystem module makes no outbound calls).
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Any

from django.utils import timezone

from workeasy360.commercial.subscriptions import my_products, trials
from workeasy360.contracts.enums import StrEnum
from workeasy360.contracts.errors import SeatLimitReached, TrialNotAllowed
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.entitlements.services.read import entitlement_request_scope
from workeasy360.platform.launcher.access import AppAccessState, can_launch_product
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

VIEW = "platform.organization.view"
ASSIGN_SEATS = "platform.seat.assign"

#: How long after a trial starts a missing entitlement still reads "Setting
#: Up" rather than "Retry Setup": provisioning normally runs in the same
#: request, so anything longer means a pass failed and needs repeating.
SETUP_GRACE = timedelta(minutes=2)

ACTIVATIONS = "commercial_product_activations_total"


def _decide(ctx: TenantContext, code: str) -> Any:
    """The launch decision, read afresh. Entitlement reads are memoised for the
    whole request, so a decision taken after this request started a trial,
    provisioned or seated someone must not reuse an answer from before it."""
    with entitlement_request_scope():
        return can_launch_product(ctx, code)


class Action(StrEnum):
    """The one primary action a product card shows. Closed: the page maps each
    to a button or a sentence and never infers one from other fields."""

    OPEN = "open"
    START_TRIAL = "start_trial"
    BUY = "buy"
    UPGRADE = "upgrade"
    SETTING_UP = "setting_up"
    RETRY_SETUP = "retry_setup"
    #: Only someone else in the organization can do the next step (start a
    #: trial for it, or give this person a seat).
    ASK_OWNER = "ask_owner"
    UNAVAILABLE = "unavailable"
    CONTACT_SALES = "contact_sales"


# --- reading ----------------------------------------------------------------------------


def _buyable(product: dict[str, Any]) -> bool:
    """Whether the product has a standard plan with a price the customer can
    check out (free plans included)."""
    return any(
        plan.get("kind") == "standard"
        and plan.get("billing_type") != "free_trial"
        and any(price.get("amount_minor") is not None for price in plan.get("prices", []))
        for plan in product.get("plans", [])
    )


def _trial_plan(product: dict[str, Any]) -> str | None:
    codes = (product.get("trial") or {}).get("plan_codes") or []
    return codes[0] if codes else None


def _result(action: Action, reason: str = "", **extra: Any) -> dict[str, Any]:
    return {"action": action.value, "reason": reason or None, **extra}


def _may_seat_self(ctx: TenantContext, trial: Any) -> bool:
    """Whether a repair may give this person a seat: they started the running
    trial, or they may assign seats in this organization anyway."""
    if ctx.membership_id is None:
        return False
    if trial is not None and trial.started_by_user_id == ctx.user_id:
        return True
    return bool(authz.check(ctx, ASSIGN_SEATS))


def _held_result(
    ctx: TenantContext,
    decision: Any,
    *,
    holding: Any,
    trial: Any,
    product: dict[str, Any] | None,
    now: Any,
) -> dict[str, Any]:
    """The action for a product the organization holds (trial or paid), from
    the authoritative launch decision."""
    facts: dict[str, Any] = {
        "holding": holding.source,
        "launch_state": decision.state.value,
        "upgrade_available": bool(
            holding.source == "trial" and product is not None and _buyable(product)
        ),
        "trial_ends_at": None,
        "trial_days_left": None,
    }
    if holding.trial_ends_at is not None:
        facts["trial_ends_at"] = holding.trial_ends_at.isoformat()
        facts["trial_days_left"] = max(
            0, math.ceil((holding.trial_ends_at - now).total_seconds() / 86400)
        )
    state = decision.state
    if state is AppAccessState.ACTIVE:
        return _result(Action.OPEN, **facts)
    if state is AppAccessState.NO_SEAT:
        if _may_seat_self(ctx, trial):
            return _result(Action.RETRY_SETUP, "seat_missing", **facts)
        return _result(Action.ASK_OWNER, "no_seat", **facts)
    if state is AppAccessState.NO_ENTITLEMENT:
        # The record says the organization holds it, the entitlement does not
        # (yet): provisioning is still in flight, or a pass failed.
        recent = trial is not None and now - trial.started_at < SETUP_GRACE
        if recent:
            return _result(Action.SETTING_UP, "provisioning", **facts)
        return _result(Action.RETRY_SETUP, "provisioning", **facts)
    if state is AppAccessState.INTEGRATION_SUSPENDED:
        return _result(Action.UNAVAILABLE, "temporarily_unavailable", **facts)
    return _result(Action.UNAVAILABLE, "not_ready", **facts)


def _open_result(
    ctx: TenantContext, product: dict[str, Any], *, may_start: bool
) -> dict[str, Any]:
    """The action for a product the organization does not hold."""
    facts: dict[str, Any] = {
        "holding": None,
        "launch_state": None,
        "upgrade_available": False,
        "trial_ends_at": product.get("trial_ends_at"),
        "trial_days_left": None,
    }
    if product.get("availability", "available") != "available":
        return _result(Action.UNAVAILABLE, "coming_soon", **facts)
    buyable = _buyable(product)
    state = product.get("trial_state")
    if state == "available" and _trial_plan(product):
        decision = can_launch_product(ctx, product["code"])
        if decision.state is AppAccessState.PRODUCT_UNAVAILABLE:
            # Nothing to open yet: a trial now would be a promise the product
            # cannot keep. Buying stays possible where a plan is on sale.
            if buyable:
                return _result(Action.BUY, **facts)
            return _result(Action.UNAVAILABLE, "not_ready", **facts)
        if not may_start:
            return _result(Action.ASK_OWNER, "owner_starts_trials", **facts)
        return _result(
            Action.START_TRIAL,
            trial_plan_code=_trial_plan(product),
            trial_days=(product.get("trial") or {}).get("duration_days"),
            **facts,
        )
    if state == "used":
        facts["trial_outcome"] = product.get("trial_outcome")
        if buyable:
            return _result(Action.UPGRADE, "trial_ended", **facts)
        return _result(Action.CONTACT_SALES, "trial_ended", **facts)
    if buyable:
        return _result(Action.BUY, **facts)
    return _result(Action.CONTACT_SALES, **facts)


def product_access(ctx: TenantContext, listing: dict[str, Any]) -> dict[str, Any]:
    """``{"products": {code: access}}`` for every product of ``listing`` (the
    organization's Explore listing, already narrowed by its partner).

    Read-only: it never starts, provisions or seats anything. Every member may
    read it, like the listing itself.
    """
    authz.require(ctx, VIEW)
    now = timezone.now()
    may_start = bool(authz.check(ctx, trials.MANAGE))
    held = my_products.holdings(at=now)
    out: dict[str, Any] = {}
    # One fresh memo for the whole page: it may follow a command in the same
    # request (the activation's own answer), whose writes an earlier memo
    # would not show.
    with entitlement_request_scope():
        _fill(ctx, listing, held=held, may_start=may_start, now=now, out=out)
    return {"products": out}


def _fill(
    ctx: TenantContext,
    listing: dict[str, Any],
    *,
    held: dict[str, Any],
    may_start: bool,
    now: Any,
    out: dict[str, Any],
) -> None:
    for product in listing["products"]:
        code = product["code"]
        holding = held.get(code)
        if holding is None:
            out[code] = _open_result(ctx, product, may_start=may_start)
            continue
        out[code] = _held_result(
            ctx,
            can_launch_product(ctx, code),
            holding=holding,
            trial=trials.active_trial(code) if holding.source == "trial" else None,
            product=product,
            now=now,
        )


# --- the commands --------------------------------------------------------------------------


def repair_access(ctx: TenantContext, product_code: str) -> str:
    """Make a product the organization already holds ready for this person
    ("Retry Setup"). Idempotent: run it as often as wanted.

    1. If the entitlement is missing although a trial or paid line holds the
       product, provision again: the commercial-change notification, whose
       provisioning listener reconciles from durable state - the one path every
       commercial change takes (the entitlement writer stays behind the A10
       single-writer contract). A pass that fails is logged by the hook; it is
       detected here by the entitlement still being missing afterwards.
    2. If the product is licensed per seat and this person has none, seat them -
       only when they started the running trial or may assign seats.

    Returns what is still in the way (``""`` when nothing is):
    ``provisioning_failed`` or ``seats_full``.
    """
    from workeasy360.commercial.subscriptions.hooks import notify_commercial_change

    authz.require(ctx, VIEW)
    code = str(product_code)
    decision = _decide(ctx, code)
    if decision.state is AppAccessState.NO_ENTITLEMENT:
        if my_products.holdings().get(code) is None:
            return ""
        notify_commercial_change(ctx.organization_id)
        decision = _decide(ctx, code)
        if decision.state is AppAccessState.NO_ENTITLEMENT:
            metrics.increment(ACTIVATIONS, outcome="provisioning_failed")
            logger.error(
                "activation.provisioning_failed",
                organization_id=str(ctx.organization_id),
                product_code=code,
            )
            return "provisioning_failed"
    if decision.state is AppAccessState.NO_SEAT:
        outcome = trials.seat_trial_starter(ctx, code)
        if outcome == trials.NOT_THE_STARTER and _may_seat_self(ctx, None):
            from workeasy360.platform.launcher import services as launcher

            try:
                launcher.assign_seat(
                    ctx, product_code=code, membership_id=ctx.membership_id
                )
            except SeatLimitReached:
                outcome = trials.SEATS_FULL
        if outcome == trials.SEATS_FULL:
            return "seats_full"
    return ""


def activate_trial(
    ctx: TenantContext,
    *,
    product: dict[str, Any] | None,
    plan_code: str | None,
    idempotency_key: str,
) -> dict[str, Any]:
    """Start the organization's free trial of ``product`` (its Explore listing
    entry) and make it ready to open. Returns ``{"trial": dto, "failure": str}``.

    Safe to repeat: the same idempotency key replays the first start; a second
    click or tab with another key finds the trial already running and only
    repairs access. A request for a product that has nowhere to open yet, or
    is not in this organization's listing, is refused before anything is
    written.
    """
    if product is None:
        raise TrialNotAllowed(reason="not_offered")
    code = str(product["code"])
    if _decide(ctx, code).state is AppAccessState.PRODUCT_UNAVAILABLE:
        raise TrialNotAllowed(reason="product_not_ready", product_code=code)
    plan = str(plan_code or "") or _trial_plan(product)
    if not plan:
        raise TrialNotAllowed(reason="not_offered", product_code=code)

    try:
        started = trials.start_product_trial(
            ctx, product_code=code, plan_code=plan, idempotency_key=idempotency_key
        )
        replay = False
    except TrialNotAllowed as exc:
        # Already started (another key, another tab) or already bought: the
        # customer wanted access, and has it - not an error to show them.
        running = trials.active_trial(code)
        if exc.context.get("reason") == "already_used" and running is not None:
            started, replay = running, True
        elif exc.context.get("reason") == "already_owned":
            started, replay = None, True
        else:
            raise

    failure = repair_access(ctx, code)
    metrics.increment(
        ACTIVATIONS, outcome=failure or ("repeated" if replay else "activated")
    )
    if started is not None:
        audit.record(
            ctx,
            "commercial.product_trial.activation",
            resource_type="subscriptions.product_trial",
            resource_id=started.id,
            changes={
                "product_code": code,
                "repeated": replay,
                "failure": failure or None,
            },
        )
    return {"trial": started, "failure": failure}


def apply_failure(access: dict[str, Any], failure: str) -> dict[str, Any]:
    """What the card says after a command that could not finish: a failed
    provisioning pass is "Retry Setup", a full seat pool needs someone with
    seat rights."""
    if failure == "provisioning_failed":
        return {**access, "action": Action.RETRY_SETUP.value, "reason": failure}
    if failure == "seats_full" and access["action"] != Action.OPEN.value:
        return {**access, "action": Action.ASK_OWNER.value, "reason": failure}
    return access

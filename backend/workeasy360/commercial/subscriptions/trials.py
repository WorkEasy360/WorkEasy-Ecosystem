"""Product trials - starting and ending one (Phase 11).

A product trial is a durable commercial fact, **not** a subscription: see
``models.ProductTrial`` for why. It reaches entitlements only through
``provisioning`` (an input to ``compute_entitlements``), so a trial can never
grant access by any other path, and nothing is invoiced.

Every command follows the shape ``services.py`` documents: authorise in the
service, claim an idempotency key inside the transaction, write, audit in the
same transaction, then notify provisioning after it. No external service is
called, so no transaction is held across a network round trip.

**Rules a start must satisfy** (all server-side; the button the storefront
shows is only a hint):

1. the caller holds ``commercial.subscription.manage`` - the same authority
   that lets an owner buy;
2. staff configured a trial for the product and switched it on;
3. the product is on sale (available and listed);
4. the plan is one of the configured eligible plans **and** a standard,
   public, available plan of the product - the storefront's own test;
5. no paid line already holds the product;
6. this organization has never trialled this product (subscription history
   *and* the trial table) - and the database backs that with a unique key.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from workeasy360.commercial.catalog import selectors as catalog
from workeasy360.commercial.subscriptions import selectors
from workeasy360.commercial.subscriptions.hooks import notify_commercial_change
from workeasy360.contracts.enums import ProductTrialStatus, TrialSource
from workeasy360.contracts.errors import (
    CatalogValidationError,
    CommercialRecordNotFound,
    SeatLimitReached,
    TrialNotAllowed,
)
from workeasy360.contracts.values.identifiers import ProductCode
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.integration.idempotency import services as idempotency
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

MANAGE = "commercial.subscription.manage"
VIEW = "platform.organization.view"

PRODUCT_TRIALS_STARTED = "commercial_product_trials_started_total"

#: Longest idempotency key accepted (the same bound orders use).
MAX_KEY_LENGTH = 128


def list_trials(ctx: TenantContext) -> tuple[selectors.ProductTrialDTO, ...]:
    """Every product trial this organization has had, newest first. Readable by
    every member, like the marketplace: what an organization is trialling is
    not a secret from its own members."""
    authz.require(ctx, VIEW)
    return tuple(reversed(selectors.product_trials()))


def start_product_trial(
    ctx: TenantContext,
    *,
    product_code: str,
    plan_code: str,
    idempotency_key: str,
    source: TrialSource = TrialSource.SELF_SERVE,
) -> selectors.ProductTrialDTO:
    """Start this organization's one trial of ``product_code`` on ``plan_code``.

    Idempotent on ``idempotency_key``: a retried request, a refresh or a double
    click returns the trial the first request created.
    """
    from workeasy360.commercial.subscriptions.models import ProductTrial

    authz.require(ctx, MANAGE)
    key = str(idempotency_key or "").strip()
    if not key or len(key) > MAX_KEY_LENGTH:
        raise CatalogValidationError(
            f"An idempotency key of 1 to {MAX_KEY_LENGTH} characters is required.",
            field="idempotency_key",
        )
    try:
        code = str(ProductCode(str(product_code or "")))
    except ValueError as exc:
        raise CatalogValidationError("No such product.", field="product_code") from exc
    plan_code = str(plan_code or "")

    now = timezone.now()
    offer = catalog.trial_offer(code)
    if offer is None or not offer.enabled:
        raise TrialNotAllowed(reason="not_offered", product_code=code)
    if not offer.on_sale:
        raise TrialNotAllowed(reason="product_unavailable", product_code=code)
    if plan_code not in offer.eligible_plan_codes:
        raise TrialNotAllowed(reason="plan_not_eligible", product_code=code)
    plan = catalog.public_plan_version(code, plan_code, at=now)
    if plan is None:
        raise TrialNotAllowed(reason="plan_not_eligible", product_code=code)

    seats = plan.included_seats
    if offer.seat_limit is not None:
        seats = min(seats, offer.seat_limit)
    seats = max(seats, 1)

    with transaction.atomic():
        claim = idempotency.claim(
            organization_id=ctx.organization_id,
            scope="commercial.product_trial.start",
            key=key,
            # Every argument that changes the outcome: a retry naming another
            # plan is a different command, not a replay.
            arguments={"product_code": code, "plan_code": plan_code},
        )
        if claim.is_replay:
            return _resolve_trial_ref(claim.result_ref)

        if code in selectors.paid_product_codes(
            selectors.subscriptions_for_organization(), now
        ):
            raise TrialNotAllowed(reason="already_owned", product_code=code)
        if selectors.has_used_a_trial(ctx.organization_id, product_code=code):
            raise TrialNotAllowed(reason="already_used", product_code=code)

        try:
            with transaction.atomic():
                trial = ProductTrial.objects.create(
                    product_code=code,
                    plan_version_id=plan.id,
                    plan_code=plan.plan_code,
                    seats=seats,
                    duration_days=offer.duration_days,
                    source=source.value,
                    status=ProductTrialStatus.ACTIVE.value,
                    started_at=now,
                    ends_at=now + timedelta(days=offer.duration_days),
                    started_by_user_id=ctx.user_id,
                )
        except IntegrityError:
            # The unique key (organization, product): a concurrent start with
            # another idempotency key won the race. The database decides.
            raise TrialNotAllowed(reason="already_used", product_code=code) from None

        audit.record(
            ctx,
            "commercial.product_trial.started",
            resource_type="subscriptions.product_trial",
            resource_id=trial.id,
            changes={
                "product_code": code,
                "plan": plan.plan_code,
                "plan_version": plan.version,
                "seats": seats,
                "duration_days": offer.duration_days,
                "ends_at": trial.ends_at.isoformat(),
                "source": source.value,
            },
        )
        idempotency.complete(claim, result_ref=f"subscriptions.trial:{trial.id}")

    metrics.increment(PRODUCT_TRIALS_STARTED, source=source.value)
    notify_commercial_change(ctx.organization_id)
    _seat_the_starter(ctx, code, trial.id)
    return _resolve_trial_ref(f"subscriptions.trial:{trial.id}")


def _seat_the_starter(ctx: TenantContext, code: str, trial_id: UUID) -> None:
    """Give the person who started the trial one of its seats, so "Open" works
    the moment the trial starts instead of first asking an administrator.

    Runs after provisioning has granted the trial's seats (the listener is
    synchronous). Best effort: a product licensed organization-wide has no
    seat pool, and a full pool or a failed provisioning pass leaves the trial
    started - seats can still be assigned by hand.
    """
    from workeasy360.platform.entitlements.services import seats

    if (
        ctx.membership_id is None
        or seats.capacity(ctx.organization_id, pool_key=code) == 0
    ):
        return
    try:
        assignment = seats.assign_seat(
            ctx.organization_id,
            pool_key=code,
            membership_id=ctx.membership_id,
            assigned_by_membership_id=ctx.membership_id,
        )
    except SeatLimitReached:
        return
    audit.record(
        ctx,
        "commercial.product_trial.seat_assigned",
        resource_type="SeatAssignment",
        resource_id=str(assignment.id),
        changes={
            "product_code": code,
            "membership_id": str(ctx.membership_id),
            "trial_id": str(trial_id),
        },
    )


def cancel_product_trial(
    ctx: TenantContext, *, trial_id: UUID
) -> selectors.ProductTrialDTO:
    """End an active trial now, at the customer's request.

    The trial stays on record as ``cancelled`` (the once-per-product rule still
    holds); its entitlement is removed by the recomputation that follows.
    Cancelling one already over is a no-op that reports its state.
    """
    from workeasy360.commercial.subscriptions.models import ProductTrial

    authz.require(ctx, MANAGE)
    with transaction.atomic():
        trial = ProductTrial.objects.select_for_update().filter(id=trial_id).first()
        if trial is None:
            # The scoped manager already excluded other tenants' rows: a bad id
            # and another tenant's id are indistinguishable.
            raise CommercialRecordNotFound("No such trial.", trial_id=str(trial_id))
        if trial.status_enum is not ProductTrialStatus.ACTIVE:
            return _resolve_trial_ref(f"subscriptions.trial:{trial.id}")
        trial.status = ProductTrialStatus.CANCELLED.value
        trial.ended_at = timezone.now()
        trial.save(update_fields=["status", "ended_at", "updated_at"])
        audit.record(
            ctx,
            "commercial.product_trial.cancelled",
            resource_type="subscriptions.product_trial",
            resource_id=trial.id,
            changes={"product_code": trial.product_code},
        )
    notify_commercial_change(ctx.organization_id)
    return _resolve_trial_ref(f"subscriptions.trial:{trial.id}")


def _resolve_trial_ref(ref: str) -> selectors.ProductTrialDTO:
    """The trial an idempotency record points at, read through the tenant scope
    (another organization's id resolves to nothing)."""
    prefix, _, raw = ref.partition(":")
    if prefix != "subscriptions.trial":
        raise CommercialRecordNotFound("No such trial.")
    found = selectors.trial(UUID(raw))
    if found is None:
        raise CommercialRecordNotFound("No such trial.", trial_id=raw)
    return found

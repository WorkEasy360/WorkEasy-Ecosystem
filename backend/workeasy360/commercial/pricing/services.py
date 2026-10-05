"""Pricing application services.

The rule this module exists to enforce: **a published price is never edited**.
An existing invoice must remain reproducible from the catalog, and a customer
must not discover a price change through their invoice.

So a price rise is two writes, not one: close the current row with
``valid_to``, publish a new row starting where the old one ended. Both facts
survive, `resolve_price(at=...)` returns the historical one for a historical
date, and the change is auditable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from workeasy360.commercial.catalog import selectors as catalog
from workeasy360.commercial.pricing.domain import TIERED_MODELS
from workeasy360.contracts.enums import (
    BillingInterval,
    PriceBookChannel,
    PriceModel,
    TaxBehaviour,
)
from workeasy360.contracts.errors import (
    CatalogValidationError,
    CommercialRecordNotFound,
    ImmutableRecord,
    UnsupportedCurrencyOperation,
)
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

MANAGE = "commercial.pricing.manage"


def _authorise(ctx: TenantContext, operation: str) -> None:
    """Price books are global control-plane data, like the catalog."""
    authz.require(ctx, MANAGE)
    authz.require_platform_staff(ctx, operation=operation, permission=MANAGE)


def _one(queryset: Any, **lookup: Any) -> Any:
    """The single row ``lookup`` names, or a 404.

    Every id reaching these services comes from a request body or path, so a
    miss is the caller's mistake - a ``CommercialRecordNotFound`` (404) - and
    never an unhandled ``DoesNotExist`` that surfaces as a 500 (Phase 7 final
    audit: the admin catalog routes reached these with arbitrary ids).
    """
    row = queryset.filter(**lookup).first()
    if row is None:
        raise CommercialRecordNotFound(
            f"No such {queryset.model._meta.verbose_name}.",
            **{key: str(value) for key, value in lookup.items()},
        )
    return row


# --- price books ----------------------------------------------------------


def create_price_book(
    ctx: TenantContext,
    *,
    code: str,
    name: str,
    currency: CurrencyCode,
    channel: PriceBookChannel = PriceBookChannel.DIRECT,
    region: str = "",
    priority: int = 100,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    partner_id: UUID | None = None,
) -> Any:
    """Create a segment.

    ``region=""`` is the default book for a currency. A regional book uses an
    ISO 3166-1 alpha-2 code and a lower ``priority`` so it wins.

    A ``partner`` book must name the partner it prices for, and no other
    channel may. The caller that supplies ``partner_id`` (the partner
    administration service) is responsible for having verified the partner
    exists - pricing sits below partners and cannot look.
    """
    from workeasy360.commercial.pricing.models import PriceBook

    _authorise(ctx, "pricing.price_book.create")
    if (channel is PriceBookChannel.PARTNER) != (partner_id is not None):
        raise CatalogValidationError(
            "A partner price book must name its partner, and only a partner "
            "price book may.",
            field="partner_id",
        )

    with transaction.atomic():
        if PriceBook.objects.filter(code=code).exists():
            raise CatalogValidationError(
                f"Price book code {code!r} is already in use.", field="code"
            )
        book = PriceBook.objects.create(
            code=code,
            name=name,
            channel=channel.value,
            partner_id=partner_id,
            region=region.upper() if region else "",
            currency=currency.value,
            priority=priority,
            valid_from=valid_from or timezone.now(),
            valid_to=valid_to,
        )
        audit.record(
            ctx,
            "commercial.price_book.created",
            resource_type="pricing.price_book",
            resource_id=book.id,
            changes={
                "code": book.code,
                "channel": book.channel,
                "currency": book.currency,
                "region": book.region,
                "partner_id": str(partner_id) if partner_id else None,
            },
        )
    return book


# --- list prices ----------------------------------------------------------


def publish_price(
    ctx: TenantContext,
    *,
    price_book_id: UUID,
    interval: BillingInterval,
    plan_version_id: UUID | None = None,
    bundle_version_id: UUID | None = None,
    amount_minor: int | None = None,
    price_model: PriceModel = PriceModel.FLAT,
    tiers: list[dict[str, Any]] | None = None,
    min_quantity: int = 1,
    max_quantity: int | None = None,
    tax_behaviour: TaxBehaviour = TaxBehaviour.EXCLUSIVE,
    valid_from: datetime | None = None,
) -> Any:
    """Publish a price, superseding any current one for the same target.

    Create-and-publish in one step is deliberate. A draft price serves no
    review purpose that a draft *plan version* does not already serve, and an
    unpublishable half-price row in the table is a trap for the resolver.

    Supersession closes the previous row rather than editing it, so the
    historical amount survives and a past invoice stays reproducible.
    """
    from workeasy360.commercial.pricing.models import PlanPrice, PriceBook, PriceTier

    _authorise(ctx, "pricing.price.publish")

    if (plan_version_id is None) == (bundle_version_id is None):
        raise CatalogValidationError(
            "A price targets exactly one of plan_version_id or bundle_version_id.",
            field="target",
        )
    if price_model is PriceModel.USAGE:
        raise CatalogValidationError(
            "Usage-based pricing requires metered consumption, which is not "
            "implemented. A usage price would resolve to an amount nobody can "
            "compute, so it cannot be published.",
            field="price_model",
        )

    tiered = price_model in TIERED_MODELS
    if tiered and not tiers:
        raise CatalogValidationError(
            f"A {price_model.value} price must declare tiers.", field="tiers"
        )
    if not tiered:
        if amount_minor is None:
            raise CatalogValidationError(
                f"A {price_model.value} price must carry amount_minor.",
                field="amount_minor",
            )
        if isinstance(amount_minor, bool) or not isinstance(amount_minor, int):
            # bool is a subclass of int, and a float here is the defect this
            # whole representation exists to make impossible (R-08).
            raise CatalogValidationError(
                "amount_minor must be an integer number of minor units. "
                "Floats are forbidden in money arithmetic.",
                field="amount_minor",
            )
    if tiers and not tiered:
        raise CatalogValidationError(
            f"Tiers were supplied for a {price_model.value} price, which does "
            f"not use them.",
            field="tiers",
        )

    # One clock reading for the whole decision. Two calls to now() would make
    # an un-backdated publish appear microseconds in the past and fail its own
    # backdating guard.
    now = timezone.now()
    starts_at = valid_from or now

    # The price's target must exist before it is written. Every FK here is
    # DEFERRABLE INITIALLY DEFERRED, so an unknown id would otherwise pass the
    # INSERT, the view would build its 201, and the request would die with an
    # IntegrityError at COMMIT (Phase 5 deferred-FK audit).
    if plan_version_id is not None:
        catalog.require_plan_version(plan_version_id)
    if bundle_version_id is not None:
        catalog.require_bundle_version(bundle_version_id)

    with transaction.atomic():
        book = _one(PriceBook.objects.select_for_update(), id=price_book_id)

        superseded = list(
            PlanPrice.objects.select_for_update().filter(
                price_book=book,
                plan_version_id=plan_version_id,
                bundle_version_id=bundle_version_id,
                interval=interval.value,
                published_at__isnull=False,
                valid_to__isnull=True,
            )
        )
        if superseded and starts_at < now:
            # Backdating a supersession rewrites what a *past* date resolves
            # to, which is the one thing versioned pricing exists to prevent:
            # an invoice raised last week would no longer be reproducible
            # from the catalog. Establishing a first price with a historical
            # start is fine - there is no prior fact to contradict.
            raise CatalogValidationError(
                f"Backdating a price change to {starts_at.isoformat()} would "
                f"retroactively change what a past date resolves to. A price "
                f"change takes effect now or in the future.",
                field="valid_from",
            )
        for previous in superseded:
            if previous.valid_from >= starts_at:
                raise CatalogValidationError(
                    f"A price for this target already starts at "
                    f"{previous.valid_from.isoformat()}, which is not before "
                    f"{starts_at.isoformat()}. Two prices effective at once "
                    f"makes resolution a coin toss.",
                    field="valid_from",
                )
            # ``valid_to`` is the one column a published price may still
            # change: it records supersession. The immutability trigger
            # permits exactly this and refuses every other edit.
            previous.valid_to = starts_at
            previous.save(update_fields=["valid_to", "updated_at"])

        price = PlanPrice.objects.create(
            price_book=book,
            plan_version_id=plan_version_id,
            bundle_version_id=bundle_version_id,
            interval=interval.value,
            model=price_model.value,
            amount_minor=None if tiered else amount_minor,
            min_quantity=min_quantity,
            max_quantity=max_quantity,
            tax_behaviour=tax_behaviour.value,
            valid_from=starts_at,
        )
        if tiers:
            PriceTier.objects.bulk_create(
                [
                    PriceTier(
                        plan_price=price,
                        up_to=tier.get("up_to"),
                        unit_amount_minor=tier.get("unit_amount_minor"),
                        flat_amount_minor=tier.get("flat_amount_minor"),
                    )
                    for tier in tiers
                ]
            )
        # Published last, so the immutability trigger does not fire on the
        # tier inserts that belong to this same act of publishing.
        price.published_at = now
        price.save(update_fields=["published_at", "updated_at"])

        audit.record(
            ctx,
            "commercial.price.published",
            resource_type="pricing.plan_price",
            resource_id=price.id,
            changes={
                "price_book": book.code,
                "currency": book.currency,
                "interval": interval.value,
                "model": price_model.value,
                "amount_minor": price.amount_minor,
                "superseded": [str(p.id) for p in superseded],
            },
        )
    logger.info(
        "pricing.price.published",
        price_book=book.code,
        currency=book.currency,
        superseded=len(superseded),
    )
    return price


def close_price(ctx: TenantContext, *, price_id: UUID, at: datetime | None = None) -> Any:
    """Withdraw a price without replacing it.

    Closing rather than deleting: an invoice raised last month referenced
    this row, and deleting it would make that invoice unexplainable.
    """
    from workeasy360.commercial.pricing.models import PlanPrice

    _authorise(ctx, "pricing.price.close")

    with transaction.atomic():
        price = _one(PlanPrice.objects.select_for_update(), id=price_id)
        if price.valid_to is not None:
            raise ImmutableRecord(
                "This price is already closed.", resource="pricing.plan_price"
            )
        price.valid_to = at or timezone.now()
        price.save(update_fields=["valid_to", "updated_at"])
        audit.record(
            ctx,
            "commercial.price.closed",
            resource_type="pricing.plan_price",
            resource_id=price.id,
            changes={"valid_to": price.valid_to.isoformat()},
        )
    return price


# --- negotiated prices ----------------------------------------------------


def grant_contract_price(
    ctx: TenantContext,
    *,
    organization_id: UUID,
    interval: BillingInterval,
    amount_minor: int,
    currency: CurrencyCode,
    plan_version_id: UUID | None = None,
    bundle_version_id: UUID | None = None,
    price_model: PriceModel = PriceModel.FLAT,
    tax_behaviour: TaxBehaviour = TaxBehaviour.EXCLUSIVE,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    contract_ref: str = "",
) -> Any:
    """Record a negotiated rate for one organization.

    Tenant-scoped data, so it is written under the caller's tenant context and
    RLS applies. ``approved_by`` is the acting user: a negotiated price with
    no named approver is a discount nobody owns.
    """
    from workeasy360.commercial.pricing.models import ContractPrice

    _authorise(ctx, "pricing.contract_price.grant")

    if (plan_version_id is None) == (bundle_version_id is None):
        raise CatalogValidationError(
            "A contract price targets exactly one of plan_version_id or "
            "bundle_version_id.",
            field="target",
        )
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int):
        raise CatalogValidationError(
            "amount_minor must be an integer number of minor units.",
            field="amount_minor",
        )
    if organization_id != ctx.organization_id:
        # The tenancy layer would refuse the write anyway; refusing here
        # gives the caller the real reason instead of a mismatch deep in the
        # ORM, and keeps the check in the service where it is testable.
        raise UnsupportedCurrencyOperation(
            "A contract price must be granted inside the target "
            "organization's own tenant context.",
            organization_id=str(organization_id),
        )

    starts_at = valid_from or timezone.now()

    with transaction.atomic():
        price = ContractPrice(
            organization_id=organization_id,
            plan_version_id=plan_version_id,
            bundle_version_id=bundle_version_id,
            interval=interval.value,
            model=price_model.value,
            amount_minor=amount_minor,
            currency=currency.value,
            tax_behaviour=tax_behaviour.value,
            valid_from=starts_at,
            valid_to=valid_to,
            contract_ref=contract_ref,
            approved_by_id=ctx.user_id,
        )
        price.save()
        audit.record(
            ctx,
            "commercial.contract_price.granted",
            resource_type="pricing.contract_price",
            resource_id=price.id,
            changes={
                "currency": currency.value,
                "interval": interval.value,
                "contract_ref": contract_ref,
            },
        )
    return price

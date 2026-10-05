"""Pricing: segmented price books, and negotiated org-scoped contract prices.

``commercial/pricing`` is the only module that computes a price. No product
and no other commercial module contains a price rule - a product that knows
what it costs cannot be repackaged (05-commercial-model.md section 2).

**Two planes, deliberately separate.** ``PriceBook`` and ``PlanPrice`` are
global reference data with no ``organization_id`` and no RLS.
``ContractPrice`` is a negotiated, org-scoped rate with both. Making them one
table with a nullable organization would mean a bug in price-book querying
could leak one customer's negotiated rate to another; as two tables, it
cannot.

**Money is (integer minor units, currency code).** Never a float, never a bare
``Decimal``. Every arithmetic operation goes through ``contracts.values.Money``
(R-08, F1).
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db import models

from workeasy360.contracts.enums import (
    BillingInterval,
    PriceBookChannel,
    PriceModel,
    TaxBehaviour,
)
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.platform.tenancy.models import TenantScopedModel


def _choices(enum_cls: Any) -> list[tuple[str, str]]:
    return [(member.value, member.name.replace("_", " ").title()) for member in enum_cls]


class PriceBook(models.Model):
    """A named set of prices for one segment.

    The segmentation axes are ``channel x region x currency x time``. Adding a
    market, a channel or a partner tier is **new rows**, never a new column
    and never a code change - which is what makes "future pricing strategies"
    cheap rather than a migration each time.

    ``RESELLER_GOLD`` is a price book code with ``channel=partner``.

    **A partner book names its partner.** ``partner_id`` is a soft reference
    (no foreign key): ``commercial/partners`` sits *above* pricing in the
    commercial layering, so pricing cannot import or point at it. The
    invariant the foreign key would have given is enforced twice: a check
    constraint ties ``channel=partner`` to a non-null ``partner_id``, and the
    only writer that sets one is the partner-administration service, which
    verifies the partner exists.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=128)

    channel = models.CharField(
        max_length=16,
        choices=_choices(PriceBookChannel),
        default=PriceBookChannel.DIRECT.value,
    )
    #: The partner this book prices for. Set exactly when ``channel=partner``.
    partner_id = models.UUIDField(null=True, blank=True)
    #: ISO 3166-1 alpha-2 or a region group. Empty means "any region", which
    #: is how the default book is expressed.
    region = models.CharField(max_length=16, blank=True, default="")
    currency = models.CharField(max_length=3, choices=_choices(CurrencyCode))

    #: Lower wins. Explicit rather than "most recently created", because
    #: creation order is not a pricing decision anybody made.
    priority = models.IntegerField(default=100)

    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pricing_price_book"
        ordering = ["priority", "code"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(channel="partner", partner_id__isnull=False)
                | (~models.Q(channel="partner") & models.Q(partner_id__isnull=True)),
                name="ck_pricebook_partner_iff_partner_channel",
            ),
        ]
        indexes = [
            models.Index(
                fields=["channel", "currency", "region", "priority"],
                name="ix_pricebook_segment",
            ),
            models.Index(
                fields=["partner_id", "currency"],
                condition=models.Q(partner_id__isnull=False),
                name="ix_pricebook_partner",
            ),
            models.Index(fields=["is_active", "valid_from"], name="ix_pricebook_active"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.code} ({self.currency})"

    @property
    def currency_enum(self) -> CurrencyCode:
        return CurrencyCode(self.currency)


class PlanPrice(models.Model):
    """A list price for one plan version or bundle version, in one book.

    **No ``currency`` column.** The price book has exactly one currency, so a
    currency here would be a second copy that can disagree with it - and a
    price whose currency disagrees with its book is a billing incident
    waiting for a customer to notice. It is read through
    ``price_book.currency``, which is the single source.

    **Immutable once published.** Changing a published amount rewrites
    financial history: an existing invoice would no longer be reproducible
    from the catalog. A price change publishes a *new* row and closes the old
    one with ``valid_to``. Enforced by a database trigger as well as by the
    service (F5).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    price_book = models.ForeignKey(
        PriceBook, on_delete=models.CASCADE, related_name="prices"
    )
    plan_version = models.ForeignKey(
        "catalog.PlanVersion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="prices",
    )
    bundle_version = models.ForeignKey(
        "catalog.BundleVersion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="prices",
    )

    interval = models.CharField(max_length=16, choices=_choices(BillingInterval))
    model = models.CharField(
        max_length=16, choices=_choices(PriceModel), default=PriceModel.FLAT.value
    )
    #: Integer minor units. NULL only when the model is tiered or volume, where
    #: the amount lives on the tiers.
    amount_minor = models.BigIntegerField(null=True, blank=True)

    min_quantity = models.IntegerField(default=1)
    max_quantity = models.IntegerField(null=True, blank=True)
    tax_behaviour = models.CharField(
        max_length=16,
        choices=_choices(TaxBehaviour),
        default=TaxBehaviour.EXCLUSIVE.value,
    )

    #: Effective window *within* the book. A price rise is a new row starting
    #: where the old one ends, so both the old and the new fact survive and a
    #: historical invoice remains reproducible.
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True)

    published_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pricing_plan_price"
        constraints = [
            # Exactly one target. A price for both a plan and a bundle is
            # meaningless, and a price for neither is unreachable.
            models.CheckConstraint(
                condition=models.Q(
                    plan_version__isnull=False, bundle_version__isnull=True
                )
                | models.Q(plan_version__isnull=True, bundle_version__isnull=False),
                name="ck_price_targets_exactly_one",
            ),
            models.CheckConstraint(
                condition=models.Q(amount_minor__isnull=True)
                | models.Q(amount_minor__gte=0),
                name="ck_price_amount_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(max_quantity__isnull=True)
                | models.Q(max_quantity__gte=models.F("min_quantity")),
                name="ck_price_quantity_range",
            ),
            models.CheckConstraint(
                condition=models.Q(valid_to__isnull=True)
                | models.Q(valid_to__gt=models.F("valid_from")),
                name="ck_price_valid_window",
            ),
        ]
        indexes = [
            models.Index(
                fields=["price_book", "plan_version", "interval", "valid_from"],
                name="ix_price_book_plan",
            ),
            models.Index(
                fields=["price_book", "bundle_version", "interval", "valid_from"],
                name="ix_price_book_bundle",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.price_book_id}:{self.amount_minor} {self.interval}"


class PriceTier(models.Model):
    """One band of a graduated or volume price.

    ``tiered`` charges each band at its own rate; ``volume`` charges *all*
    units at the rate of the band reached. Both are needed and conflating
    them produces invoices customers dispute, so the distinction lives on the
    parent's ``model`` and is applied in one function.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    plan_price = models.ForeignKey(
        PlanPrice, on_delete=models.CASCADE, related_name="tiers"
    )
    #: NULL means infinity - the final, unbounded band.
    up_to = models.IntegerField(null=True, blank=True)
    unit_amount_minor = models.BigIntegerField(null=True, blank=True)
    flat_amount_minor = models.BigIntegerField(null=True, blank=True)

    class Meta:
        db_table = "pricing_price_tier"
        ordering = [models.F("up_to").asc(nulls_last=True)]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(unit_amount_minor__isnull=False)
                | models.Q(flat_amount_minor__isnull=False),
                name="ck_tier_has_an_amount",
            ),
            models.CheckConstraint(
                condition=models.Q(up_to__isnull=True) | models.Q(up_to__gte=1),
                name="ck_tier_up_to_positive",
            ),
        ]
        indexes = [models.Index(fields=["plan_price"], name="ix_tier_price")]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"up_to={self.up_to} unit={self.unit_amount_minor}"


class ContractPrice(TenantScopedModel):
    """A negotiated rate for one organization. Data plane: RLS applies.

    Deliberately **not** a row in the global ``PriceBook`` with a nullable
    organization. ``PriceBook`` has no ``organization_id`` and never needs
    RLS; this has one and always does. A bug in price-book querying can
    produce a wrong price; it cannot leak one customer's negotiated rate to
    another.

    ``approved_by`` is mandatory: a negotiated price with no named approver is
    a discount nobody owns.
    """

    plan_version = models.ForeignKey(
        "catalog.PlanVersion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="contract_prices",
    )
    bundle_version = models.ForeignKey(
        "catalog.BundleVersion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="contract_prices",
    )

    interval = models.CharField(max_length=16, choices=_choices(BillingInterval))
    model = models.CharField(
        max_length=16, choices=_choices(PriceModel), default=PriceModel.FLAT.value
    )
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3, choices=_choices(CurrencyCode))
    tax_behaviour = models.CharField(
        max_length=16,
        choices=_choices(TaxBehaviour),
        default=TaxBehaviour.EXCLUSIVE.value,
    )

    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True)
    contract_ref = models.CharField(max_length=128, blank=True, default="")

    approved_by = models.ForeignKey(
        "identity.User", on_delete=models.PROTECT, related_name="approved_prices"
    )

    class Meta:
        db_table = "pricing_contract_price"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    plan_version__isnull=False, bundle_version__isnull=True
                )
                | models.Q(plan_version__isnull=True, bundle_version__isnull=False),
                name="ck_contract_price_targets_one",
            ),
            models.CheckConstraint(
                condition=models.Q(amount_minor__gte=0),
                name="ck_contract_price_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(valid_to__isnull=True)
                | models.Q(valid_to__gt=models.F("valid_from")),
                name="ck_contract_price_window",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "plan_version", "valid_from"],
                name="ix_contract_org_plan",
            ),
            models.Index(
                fields=["organization", "bundle_version", "valid_from"],
                name="ix_contract_org_bundle",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"contract {self.amount_minor} {self.currency}"

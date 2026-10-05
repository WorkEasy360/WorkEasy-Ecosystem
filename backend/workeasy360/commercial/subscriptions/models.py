"""The commercial relationship: ``Subscription`` and ``SubscriptionItem``.

Tenant-owned data. RLS, the scoped manager, the suspension write block and
the composite foreign keys all apply exactly as they do to any other tenant
table - a subscription is a customer's record, not control-plane
configuration.

Three design points carry most of the weight.

**One subscription, many items, one renewal.** Modelling one subscription per
product would make a suite purchase four subscriptions with four renewal
dates and four dunning cycles - a support nightmare
(06-entitlements.md section 3).

**Items are effective-dated, never deleted.** ``added_at`` and ``removed_at``
bound the window in which an item grants anything. That single mechanism
covers plan changes, period-end downgrades, scheduled cancellation and
proration history without a separate "scheduled change" table, and it means
the question "what did this customer have on 3 March?" is answerable by a
query rather than by an audit-log reconstruction.

**Items carry a commercial snapshot.** The plan version they pin is immutable
and the price row they pin is immutable, *and* the resolved money is
denormalised onto the item. Belt and braces deliberately: historical
commercial facts must not be derivable only from catalog tables, because a
catalog row can be superseded, closed, or restored from a different backup
than the subscription.
"""

from __future__ import annotations

from typing import Any

from django.db import models

from workeasy360.contracts.enums import (
    BillingInterval,
    CollectionMode,
    ProductTrialStatus,
    RenewalPolicy,
    SubscriptionStatus,
    TrialSource,
)
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.platform.tenancy.models import TenantScopedModel


def _choices(enum_cls: Any) -> list[tuple[str, str]]:
    return [(member.value, member.name.replace("_", " ").title()) for member in enum_cls]


class Subscription(TenantScopedModel):
    """One organization's commercial relationship with Workeasy360."""

    status = models.CharField(
        max_length=16,
        choices=_choices(SubscriptionStatus),
        default=SubscriptionStatus.TRIALING.value,
        db_index=True,
    )

    #: Fixed at creation from the organization's billing account. Mid-life
    #: currency switching is a cancel-and-resubscribe, not an update:
    #: anything else makes proration and revenue reporting incoherent.
    currency = models.CharField(max_length=3, choices=_choices(CurrencyCode))
    billing_interval = models.CharField(
        max_length=16,
        choices=_choices(BillingInterval),
        default=BillingInterval.MONTHLY.value,
    )
    collection_mode = models.CharField(
        max_length=16,
        choices=_choices(CollectionMode),
        default=CollectionMode.AUTOMATIC.value,
    )
    renewal_policy = models.CharField(
        max_length=16,
        choices=_choices(RenewalPolicy),
        default=RenewalPolicy.AUTO_RENEW.value,
    )

    current_period_start = models.DateTimeField()
    current_period_end = models.DateTimeField()

    trial_ends_at = models.DateTimeField(null=True, blank=True)
    trial_source = models.CharField(
        max_length=16, choices=_choices(TrialSource), blank=True, default=""
    )

    activated_at = models.DateTimeField(null=True, blank=True)
    #: When the customer asked to cancel.
    cancelled_at = models.DateTimeField(null=True, blank=True)
    #: When access actually stops. Period end for a normal cancellation, now
    #: for an immediate one. Access is computed against this, not against
    #: ``cancelled_at``: revoking on request instead of at period end is
    #: theft, and generates chargebacks.
    cancel_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    #: Optimistic concurrency token. A caller that read the subscription and
    #: is about to change it passes the version it saw; a mismatch means
    #: something else changed it first and the caller must re-read rather
    #: than overwrite. Two admins racing on "upgrade" is the case this
    #: prevents from producing two upgrades.
    version = models.IntegerField(default=0)

    class Meta:
        db_table = "subscriptions_subscription"
        constraints = [
            models.UniqueConstraint(
                fields=["id", "organization"], name="uq_subscription_id_org"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    current_period_end__gt=models.F("current_period_start")
                ),
                name="ck_subscription_period_ordered",
            ),
            # **One open subscription per organization**, enforced by the
            # database rather than by a lookup.
            #
            # ``SELECT ... FOR UPDATE`` cannot lock a row that does not exist
            # yet, so two simultaneous first purchases would both find none
            # and both insert - leaving the organization with two renewal
            # cycles, two dunning cycles, and the one-line-per-product
            # constraint (which is per subscription) unable to stop the same
            # product appearing twice.
            models.UniqueConstraint(
                fields=["organization"],
                condition=~models.Q(status=SubscriptionStatus.EXPIRED.value),
                name="uq_one_open_subscription_per_org",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "status"], name="ix_subscription_org_status"
            ),
            models.Index(
                fields=["organization", "current_period_end"],
                name="ix_subscription_org_period",
            ),
            models.Index(
                fields=["organization", "trial_ends_at"], name="ix_subscription_org_trial"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"subscription {self.id} [{self.status}]"

    @property
    def status_enum(self) -> SubscriptionStatus:
        return SubscriptionStatus(self.status)

    @property
    def currency_enum(self) -> CurrencyCode:
        return CurrencyCode(self.currency)

    @property
    def interval_enum(self) -> BillingInterval:
        return BillingInterval(self.billing_interval)


class SubscriptionItem(TenantScopedModel):
    """One commercialised line.

    A line is either **priced** (a plan or a bundle the customer bought, with
    a pinned price) or **derived** (a plan granted by a bundle line, carrying
    no price of its own). A bundle is priced as one thing, so its components
    must not each carry an amount - summing them would invent revenue.

    Derived lines are ordinary items in every other respect: entitlement
    computation reads plan-version items uniformly and never asks how they
    came to exist. That is what stops a bundle needing a special entitlement
    path (Task 7).
    """

    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="items"
    )
    #: Set on a bundle-derived line, pointing at the priced bundle line.
    parent_item = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="derived_items",
    )

    plan_version = models.ForeignKey(
        "catalog.PlanVersion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="subscription_items",
    )
    bundle_version = models.ForeignKey(
        "catalog.BundleVersion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="subscription_items",
    )

    # --- commercial snapshot, denormalised on purpose ---------------------
    #
    # PROTECT on the two foreign keys already stops a referenced version being
    # deleted. These columns answer the harder question: what was agreed, in
    # terms that do not require joining to a catalog that has moved on.

    #: Empty on a bundle line, which spans products.
    product_code = models.CharField(max_length=32, blank=True, default="", db_index=True)
    plan_code = models.CharField(max_length=64, blank=True, default="")
    plan_version_number = models.IntegerField(null=True, blank=True)
    bundle_code = models.CharField(max_length=64, blank=True, default="")

    #: Which seat pool this line contributes to. Empty means the product's own
    #: pool, which is the common case.
    seat_pool = models.CharField(max_length=64, blank=True, default="")
    quantity = models.IntegerField(default=1)

    #: A soft reference to the pinned ``PlanPrice`` **or** ``ContractPrice``.
    #: Not a foreign key because it is polymorphic across two tables; the
    #: source is recorded alongside so it is never ambiguous which.
    price_id = models.UUIDField(null=True, blank=True)
    price_source = models.CharField(max_length=24, blank=True, default="")
    #: Which rule matched, in words. Makes "why is this customer paying this?"
    #: answerable in production rather than by re-deriving resolution.
    price_reason = models.CharField(max_length=255, blank=True, default="")

    unit_amount_minor = models.BigIntegerField(null=True, blank=True)
    total_amount_minor = models.BigIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True, default="")
    interval = models.CharField(max_length=16, blank=True, default="")
    price_model = models.CharField(max_length=16, blank=True, default="")
    tax_behaviour = models.CharField(max_length=16, blank=True, default="")

    # --- effective dating -------------------------------------------------

    added_at = models.DateTimeField()
    #: Soft removal, and frequently **in the future**: a period-end downgrade
    #: closes the outgoing line at the period boundary and opens the incoming
    #: one there. Never deleted - proration, revenue reporting and disputes
    #: all need the history.
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "subscriptions_item"
        constraints = [
            models.UniqueConstraint(
                fields=["id", "organization"], name="uq_subscription_item_id_org"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    plan_version__isnull=False, bundle_version__isnull=True
                )
                | models.Q(plan_version__isnull=True, bundle_version__isnull=False),
                name="ck_item_targets_exactly_one",
            ),
            # A derived line is always a plan line: a bundle cannot contain a
            # bundle, so a bundle line never has a parent.
            models.CheckConstraint(
                condition=models.Q(parent_item__isnull=True)
                | models.Q(plan_version__isnull=False),
                name="ck_derived_item_is_a_plan",
            ),
            # A derived line carries no money. Summing bundle components
            # against the bundle's own price would double-count revenue.
            models.CheckConstraint(
                condition=models.Q(parent_item__isnull=True)
                | models.Q(price_id__isnull=True, total_amount_minor__isnull=True),
                name="ck_derived_item_has_no_price",
            ),
            # A priced line is fully snapshotted or not priced at all. A price
            # id with no amount is a line nobody can invoice.
            models.CheckConstraint(
                condition=models.Q(price_id__isnull=True)
                | models.Q(total_amount_minor__isnull=False)
                & ~models.Q(currency="")
                & ~models.Q(interval=""),
                name="ck_priced_item_is_snapshotted",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1), name="ck_item_quantity_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(removed_at__isnull=True)
                | models.Q(removed_at__gte=models.F("added_at")),
                name="ck_item_removal_after_addition",
            ),
            # **The invariant that stops a bundle duplicating a product.** At
            # most one open line per product per subscription, enforced by the
            # database rather than by expansion logic remembering to check.
            # A future-dated change sets the outgoing line's removed_at, which
            # takes it out of this index, so scheduled changes still work.
            models.UniqueConstraint(
                fields=["subscription", "product_code"],
                condition=models.Q(removed_at__isnull=True) & ~models.Q(product_code=""),
                name="uq_one_open_item_per_product",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "subscription"], name="ix_item_org_sub"),
            models.Index(
                fields=["organization", "product_code"], name="ix_item_org_product"
            ),
            models.Index(fields=["organization", "removed_at"], name="ix_item_org_open"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.plan_code or self.bundle_code} x{self.quantity}"

    @property
    def is_derived(self) -> bool:
        return self.parent_item_id is not None


class ProductTrial(TenantScopedModel):
    """One organization's free trial of one product (Phase 11).

    **Why this is not a subscription.** An organization may hold only one open
    subscription, and a subscription's trial covers everything on it. A
    customer who has bought two products and wants to *try* a third cannot be
    expressed that way. A product trial is instead a durable commercial fact
    that ``provisioning`` reads as an **input** to entitlement computation
    (source kind ``trial``): no subscription is created or changed, nothing is
    invoiced, and the entitlement disappears by itself at ``ends_at``.

    **Once, by the database.** ``uq_product_trial_once`` makes a second trial
    of the same product by the same organization unrepresentable, whatever a
    retry, a double click or a replay does.

    The terms (``seats``, ``duration_days``, ``ends_at``) are copied from the
    product's configuration when the trial starts: changing the configuration
    later never shortens or extends a trial in progress.
    """

    product_code = models.CharField(max_length=32, db_index=True)
    plan_version = models.ForeignKey(
        "catalog.PlanVersion", on_delete=models.PROTECT, related_name="product_trials"
    )
    plan_code = models.CharField(max_length=64)
    seats = models.PositiveIntegerField()
    duration_days = models.PositiveSmallIntegerField()
    source = models.CharField(
        max_length=16,
        choices=_choices(TrialSource),
        default=TrialSource.SELF_SERVE.value,
    )
    status = models.CharField(
        max_length=16,
        choices=_choices(ProductTrialStatus),
        default=ProductTrialStatus.ACTIVE.value,
        db_index=True,
    )
    started_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    #: When it stopped being ``active`` (converted, expired or cancelled).
    ended_at = models.DateTimeField(null=True, blank=True)
    started_by_user_id = models.UUIDField()

    class Meta:
        db_table = "subscriptions_product_trial"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "product_code"], name="uq_product_trial_once"
            ),
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("started_at")),
                name="ck_product_trial_window",
            ),
            models.CheckConstraint(
                condition=models.Q(seats__gte=1), name="ck_product_trial_seats"
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "status", "ends_at"], name="ix_product_trial_due"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"trial of {self.product_code} [{self.status}]"

    @property
    def status_enum(self) -> ProductTrialStatus:
        return ProductTrialStatus(self.status)

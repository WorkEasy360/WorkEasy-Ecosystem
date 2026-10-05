"""Subscription read queries, returning DTOs.

``commercial/provisioning`` computes entitlements from what this module
returns and never queries subscription tables itself (F-6). Selectors are the
contract; models are an implementation detail.

Two of these functions run **outside any request tenant context**, because
provisioning and reconciliation are cross-tenant by nature. They therefore
use the audited ``unscoped`` manager inside ``unscoped_access(reason=...)``,
exactly as ``platform/entitlements/selectors.py`` already does for the same
reason. Application scoping is relaxed; PostgreSQL RLS is not, so a caller
that has not entered an organization's database scope still sees nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db.models import Prefetch

from workeasy360.contracts.enums import (
    BillingInterval,
    ProductTrialStatus,
    RenewalPolicy,
    SubscriptionStatus,
)

UNSCOPED_REASON = (
    "Entitlement computation and reconciliation read commercial state "
    "outside a request tenant context"
)


@dataclass(frozen=True, slots=True)
class SubscriptionItemDTO:
    """One commercialised line, as provisioning sees it."""

    id: UUID
    subscription_id: UUID
    plan_version_id: UUID | None
    bundle_version_id: UUID | None
    product_code: str
    plan_code: str
    bundle_code: str
    seat_pool: str
    quantity: int
    added_at: datetime
    removed_at: datetime | None
    is_derived: bool

    def is_effective_at(self, moment: datetime) -> bool:
        if self.added_at > moment:
            return False
        return self.removed_at is None or self.removed_at > moment

    @property
    def source_ref(self) -> str:
        """The soft reference recorded on every entitlement this line causes.

        Per **item**, not per subscription: an organization holding CRM from
        both a bundle and a standalone plan has two sources, and collapsing
        them would make "most generous wins" unable to see that there are two.
        """
        return f"subscriptions.item:{self.id}"


@dataclass(frozen=True, slots=True)
class SubscriptionDTO:
    """One subscription and its lines."""

    id: UUID
    organization_id: UUID
    status: SubscriptionStatus
    currency: str
    billing_interval: BillingInterval
    renewal_policy: RenewalPolicy
    current_period_start: datetime
    current_period_end: datetime
    trial_ends_at: datetime | None
    cancel_at: datetime | None
    ended_at: datetime | None
    version: int
    items: tuple[SubscriptionItemDTO, ...]

    def access_ends_at(self) -> datetime | None:
        candidates = [value for value in (self.cancel_at, self.ended_at) if value]
        if self.status is SubscriptionStatus.TRIALING and self.trial_ends_at:
            candidates.append(self.trial_ends_at)
        return min(candidates) if candidates else None

    def grants_access_at(self, moment: datetime) -> bool:
        """Whether this subscription grants anything at ``moment``.

        Expiry is a positive, durable decision - a date that has arrived -
        not an absence of information, so computing it here is consistent
        with "absence of information never revokes".
        """
        if self.status is SubscriptionStatus.EXPIRED:
            return False
        ends = self.access_ends_at()
        return ends is None or ends > moment

    def effective_items(self, moment: datetime) -> tuple[SubscriptionItemDTO, ...]:
        """Plan lines in force at ``moment``.

        Bundle lines are excluded: they are the priced commercial line, and
        what the customer may *use* comes from the plan lines the bundle
        expanded into. Including both would grant the same product twice.
        """
        return tuple(
            item
            for item in self.items
            if item.plan_version_id is not None and item.is_effective_at(moment)
        )


@dataclass(frozen=True, slots=True)
class ProductTrialDTO:
    """One product trial, as provisioning and the console see it (Phase 11)."""

    id: UUID
    organization_id: UUID
    product_code: str
    plan_version_id: UUID
    plan_code: str
    seats: int
    duration_days: int
    source: str
    status: ProductTrialStatus
    started_at: datetime
    ends_at: datetime
    ended_at: datetime | None

    def is_effective_at(self, moment: datetime) -> bool:
        """Whether it grants a trial at ``moment``: still ``active`` and inside
        its window. Time, not the sweeper, ends access - so a trial whose
        ``ends_at`` has passed grants nothing even before its row is marked."""
        return (
            self.status is ProductTrialStatus.ACTIVE
            and self.started_at <= moment < self.ends_at
        )

    @property
    def source_ref(self) -> str:
        """The soft reference recorded on the entitlement this trial causes."""
        return f"subscriptions.trial:{self.id}"


def _trial_dto(row) -> ProductTrialDTO:
    return ProductTrialDTO(
        id=row.id,
        organization_id=row.organization_id,
        product_code=row.product_code,
        plan_version_id=row.plan_version_id,
        plan_code=row.plan_code,
        seats=row.seats,
        duration_days=row.duration_days,
        source=row.source,
        status=ProductTrialStatus(row.status),
        started_at=row.started_at,
        ends_at=row.ends_at,
        ended_at=row.ended_at,
    )


def _item_dto(row) -> SubscriptionItemDTO:
    return SubscriptionItemDTO(
        id=row.id,
        subscription_id=row.subscription_id,
        plan_version_id=row.plan_version_id,
        bundle_version_id=row.bundle_version_id,
        product_code=row.product_code,
        plan_code=row.plan_code,
        bundle_code=row.bundle_code,
        seat_pool=row.seat_pool,
        quantity=row.quantity,
        added_at=row.added_at,
        removed_at=row.removed_at,
        is_derived=row.parent_item_id is not None,
    )


def _subscription_dto(row) -> SubscriptionDTO:
    return SubscriptionDTO(
        id=row.id,
        organization_id=row.organization_id,
        status=SubscriptionStatus(row.status),
        currency=row.currency,
        billing_interval=BillingInterval(row.billing_interval),
        renewal_policy=RenewalPolicy(row.renewal_policy),
        current_period_start=row.current_period_start,
        current_period_end=row.current_period_end,
        trial_ends_at=row.trial_ends_at,
        cancel_at=row.cancel_at,
        ended_at=row.ended_at,
        version=row.version,
        items=tuple(_item_dto(item) for item in row.items.all()),
    )


def _with_items(queryset):
    """Two queries for any number of subscriptions and lines.

    Without the prefetch this is one query per subscription inside the
    provisioning loop - the classic N+1, and in the one place where it is
    least visible because nobody watches a reconciliation job's query count.
    Pinned by ``test_entitlement_provisioning_query_count_is_bounded``.
    """
    from workeasy360.commercial.subscriptions.models import SubscriptionItem

    return queryset.prefetch_related(
        Prefetch(
            "items",
            queryset=SubscriptionItem.objects.order_by("added_at", "id"),
        )
    )


def commercial_state(organization_id: UUID) -> tuple[SubscriptionDTO, ...]:
    """Every subscription an organization holds, with its lines.

    The complete input to ``compute_entitlements``. Returns *all*
    subscriptions rather than only live ones, because whether a subscription
    still grants anything is a function of time that the computation applies
    at its own ``at`` - filtering here would bake "now" into the read and
    break recomputation for a historical instant.
    """
    from workeasy360.commercial.subscriptions.models import Subscription, SubscriptionItem
    from workeasy360.platform.tenancy.db import assert_tenant_scope, organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    # Both isolation layers. ``unscoped_access`` relaxes application scoping;
    # RLS is not relaxed by it, and with no tenant variable set the policy
    # matches zero rows - which would make this return an empty commercial
    # state and provisioning expire everything the customer holds.
    with (
        unscoped_access(
            reason=UNSCOPED_REASON, module="commercial.subscriptions.selectors"
        ),
        organization_db_scope(organization_id),
    ):
        # Expected scope verified, not assumed. This read is the input whose
        # emptiness makes provisioning revoke everything, so the one thing it
        # must never do is return an empty result because its own scope did
        # not take effect. Free: the scope records what it bound, so this is
        # a ContextVar comparison rather than a round trip.
        assert_tenant_scope(organization_id, what="commercial_state")
        rows = (
            Subscription.unscoped.filter(organization_id=organization_id)
            .prefetch_related(
                Prefetch(
                    "items",
                    queryset=SubscriptionItem.unscoped.order_by("added_at", "id"),
                )
            )
            .order_by("created_at")
        )
        return tuple(_subscription_dto(row) for row in rows)


def commercial_trials(organization_id: UUID) -> tuple[ProductTrialDTO, ...]:
    """Every product trial an organization has had, oldest first.

    The second input to ``compute_entitlements``, read exactly the way
    ``commercial_state`` is: outside a request, inside one organization's
    database scope, with the scope asserted rather than assumed - an empty
    answer caused by a scope that did not take effect would expire every trial.
    """
    from workeasy360.commercial.subscriptions.models import ProductTrial
    from workeasy360.platform.tenancy.db import assert_tenant_scope, organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(
            reason=UNSCOPED_REASON, module="commercial.subscriptions.selectors"
        ),
        organization_db_scope(organization_id),
    ):
        assert_tenant_scope(organization_id, what="commercial_trials")
        rows = ProductTrial.unscoped.filter(organization_id=organization_id).order_by(
            "started_at", "id"
        )
        return tuple(_trial_dto(row) for row in rows)


def product_trials() -> tuple[ProductTrialDTO, ...]:
    """Every product trial visible in the current tenant context."""
    from workeasy360.commercial.subscriptions.models import ProductTrial

    return tuple(
        _trial_dto(row) for row in ProductTrial.objects.order_by("started_at", "id")
    )


def trial(trial_id: UUID) -> ProductTrialDTO | None:
    """One product trial of the current tenant, or ``None`` - another
    organization's id resolves to nothing, exactly like an unknown one."""
    from workeasy360.commercial.subscriptions.models import ProductTrial

    row = ProductTrial.objects.filter(id=trial_id).first()
    return _trial_dto(row) if row is not None else None


def paid_product_codes(
    subscriptions: tuple[SubscriptionDTO, ...], moment: datetime
) -> frozenset[str]:
    """Products a *paid* line grants at ``moment``.

    A line on a subscription that is still ``trialing`` is not paid, and an
    expired subscription grants nothing. What is left is what makes a product
    trial redundant: the customer owns the product.
    """
    return frozenset(
        item.product_code
        for subscription in subscriptions
        if subscription.status is not SubscriptionStatus.TRIALING
        and subscription.grants_access_at(moment)
        for item in subscription.effective_items(moment)
        if item.product_code
    )


@dataclass(frozen=True, slots=True)
class SaleFactDTO:
    """One priced line, with the commercial snapshot it was sold under.

    The input to commission accrual (``commercial/commissions``). Everything
    in it is read from **immutable** columns of the item and its subscription
    - the price snapshot is frozen by trigger, ``added_at`` never moves - so a
    commission computed from it today equals one computed next year.

    ``predecessor_total_minor`` is set when this line *continues* another of
    the same product on the same subscription (the outgoing line was closed at
    the instant this one opened): a plan or quantity change, which earns on
    the increase only, never on the whole new price again.

    ``subscription_activated_at`` is when the subscription first became a paid
    relationship (``activate`` converts a trial; a purchase without a trial is
    active at once). It stays ``None`` for a trial that never converts - the
    one sale the platform itself knows was never paid for.
    """

    item_id: UUID
    organization_id: UUID
    subscription_id: UUID
    product_code: str
    plan_code: str
    bundle_code: str
    quantity: int
    added_at: datetime
    removed_at: datetime | None
    total_amount_minor: int
    currency: str
    interval: str
    price_source: str
    predecessor_total_minor: int | None
    predecessor_currency: str
    subscription_status: SubscriptionStatus
    subscription_cancelled_at: datetime | None
    subscription_cancel_at: datetime | None
    subscription_activated_at: datetime | None = None

    @property
    def source_ref(self) -> str:
        return f"subscriptions.item:{self.item_id}"


def sale_facts(organization_id: UUID) -> tuple[SaleFactDTO, ...]:
    """Every priced, directly-sold line of one organization, oldest first.

    Bundle lines are included (they are the priced line); their derived
    components are not (they carry no price). Two queries regardless of size:
    the lines, and the closed lines they may continue.
    """
    from workeasy360.commercial.subscriptions.models import SubscriptionItem
    from workeasy360.platform.tenancy.db import assert_tenant_scope, organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(
            reason="Commission accrual reads priced sale facts outside a request",
            module="commercial.subscriptions.selectors",
        ),
        organization_db_scope(organization_id),
    ):
        assert_tenant_scope(organization_id, what="sale_facts")
        rows = list(
            SubscriptionItem.unscoped.filter(
                organization_id=organization_id,
                parent_item__isnull=True,
                total_amount_minor__isnull=False,
            )
            .select_related("subscription")
            .order_by("added_at", "id")
        )
        # A continuation: another line of the same subscription and product
        # that was closed exactly when this one opened.
        closed_at = {}
        for row in rows:
            if row.removed_at is not None and row.product_code:
                closed_at[(row.subscription_id, row.product_code, row.removed_at)] = row

        facts = []
        for row in rows:
            predecessor = (
                closed_at.get((row.subscription_id, row.product_code, row.added_at))
                if row.product_code
                else None
            )
            if predecessor is row:
                predecessor = None
            sub = row.subscription
            facts.append(
                SaleFactDTO(
                    item_id=row.id,
                    organization_id=row.organization_id,
                    subscription_id=row.subscription_id,
                    product_code=row.product_code,
                    plan_code=row.plan_code,
                    bundle_code=row.bundle_code,
                    quantity=row.quantity,
                    added_at=row.added_at,
                    removed_at=row.removed_at,
                    total_amount_minor=row.total_amount_minor,
                    currency=row.currency,
                    interval=row.interval,
                    price_source=row.price_source,
                    predecessor_total_minor=(
                        predecessor.total_amount_minor if predecessor else None
                    ),
                    predecessor_currency=predecessor.currency if predecessor else "",
                    subscription_status=SubscriptionStatus(sub.status),
                    subscription_cancelled_at=sub.cancelled_at,
                    subscription_cancel_at=sub.cancel_at,
                    subscription_activated_at=sub.activated_at,
                )
            )
        return tuple(facts)


def subscriptions_due_for_attention(organization_id: UUID, *, at: datetime) -> list[UUID]:
    """Subscriptions of **one** organization whose timestamps have passed.

    Per organization rather than one cross-tenant scan, because a scan is not
    available: every tenant table matches zero rows without a tenant
    variable. The caller enumerates organizations from the control plane and
    enters each one's scope, which is the shape 03-multi-tenancy.md requires
    of any job that spans tenants.

    One query rather than three, because "trial ended", "cancellation due"
    and "period elapsed" are all the same condition - a durable timestamp
    that is now in the past - and three scans of one table for one reason is
    three times the load for no extra information.
    """
    from django.db.models import Q

    from workeasy360.commercial.subscriptions.models import Subscription
    from workeasy360.platform.tenancy.db import assert_tenant_scope, organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(
            reason=UNSCOPED_REASON, module="commercial.subscriptions.selectors"
        ),
        organization_db_scope(organization_id),
    ):
        assert_tenant_scope(organization_id, what="subscriptions_due_for_attention")
        return list(
            Subscription.unscoped.filter(organization_id=organization_id)
            .filter(
                Q(trial_ends_at__lte=at, status=SubscriptionStatus.TRIALING.value)
                | Q(cancel_at__lte=at, status=SubscriptionStatus.CANCELLED.value)
                | Q(current_period_end__lte=at)
            )
            .exclude(status=SubscriptionStatus.EXPIRED.value)
            .values_list("id", flat=True)
        )


# --- request-context reads ------------------------------------------------


def subscription_detail(subscription_id: UUID) -> SubscriptionDTO | None:
    """One subscription, read inside the caller's tenant context.

    Uses the scoped manager, so a subscription id belonging to another tenant
    simply does not resolve - the IDOR case is closed by the manager rather
    than by an ownership check this function could forget.
    """
    from workeasy360.commercial.subscriptions.models import Subscription

    row = _with_items(Subscription.objects.filter(id=subscription_id)).first()
    return _subscription_dto(row) if row is not None else None


def item_codes(subscription_item_id: UUID) -> tuple[str, str] | None:
    """``(product_code, plan_code)`` of one line of the current tenant, or None.

    For a partner's product-scope check before it changes a line: scoped by
    the manager, so another tenant's line id resolves to nothing.
    """
    from workeasy360.commercial.subscriptions.models import SubscriptionItem

    row = (
        SubscriptionItem.objects.filter(id=subscription_item_id)
        .values_list("product_code", "plan_code")
        .first()
    )
    return (row[0], row[1]) if row is not None else None


def subscriptions_for_organization() -> tuple[SubscriptionDTO, ...]:
    """Every subscription visible in the current tenant context."""
    from workeasy360.commercial.subscriptions.models import Subscription

    rows = _with_items(Subscription.objects.all()).order_by("created_at")
    return tuple(_subscription_dto(row) for row in rows)


def trialled_product_codes(organization_id: UUID) -> frozenset[str]:
    """Every product this organization has ever trialled - product trials and
    the older plan-level (subscription) trials - in two queries. The batch form
    of ``has_used_a_trial``, for a page that draws a card per product."""
    from workeasy360.commercial.subscriptions.models import (
        ProductTrial,
        SubscriptionItem,
    )

    product_trials = set(
        ProductTrial.objects.filter(organization_id=organization_id).values_list(
            "product_code", flat=True
        )
    )
    plan_trials = set(
        SubscriptionItem.objects.filter(
            organization_id=organization_id, subscription__trial_ends_at__isnull=False
        )
        .exclude(product_code="")
        .values_list("product_code", flat=True)
    )
    return frozenset(product_trials | plan_trials)


def has_used_a_trial(organization_id: UUID, *, product_code: str) -> bool:
    """Whether this organization has ever trialled this product.

    Derived from durable history rather than a "has_trialled" flag: a flag is
    one data-fix away from being reset, and items are never deleted, so the
    history is the stronger source. This is the enforcement point for trial
    abuse that the architecture places at the platform level - per-domain
    limits and signup fingerprinting are separate and live in identity.
    """
    from workeasy360.commercial.subscriptions.models import (
        ProductTrial,
        SubscriptionItem,
    )

    if ProductTrial.objects.filter(
        organization_id=organization_id, product_code=product_code
    ).exists():
        return True
    return SubscriptionItem.objects.filter(
        organization_id=organization_id,
        product_code=product_code,
        subscription__trial_ends_at__isnull=False,
    ).exists()

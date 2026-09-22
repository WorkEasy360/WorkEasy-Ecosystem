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


def subscriptions_for_organization() -> tuple[SubscriptionDTO, ...]:
    """Every subscription visible in the current tenant context."""
    from workeasy360.commercial.subscriptions.models import Subscription

    rows = _with_items(Subscription.objects.all()).order_by("created_at")
    return tuple(_subscription_dto(row) for row in rows)


def has_used_a_trial(organization_id: UUID, *, product_code: str) -> bool:
    """Whether this organization has ever trialled this product.

    Derived from durable history rather than a "has_trialled" flag: a flag is
    one data-fix away from being reset, and items are never deleted, so the
    history is the stronger source. This is the enforcement point for trial
    abuse that the architecture places at the platform level - per-domain
    limits and signup fingerprinting are separate and live in identity.
    """
    from workeasy360.commercial.subscriptions.models import SubscriptionItem

    return SubscriptionItem.objects.filter(
        organization_id=organization_id,
        product_code=product_code,
        subscription__trial_ends_at__isnull=False,
    ).exists()

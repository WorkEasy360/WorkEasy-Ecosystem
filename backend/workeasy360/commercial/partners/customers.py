"""The partner's commercial use cases: provisioning and selling (Phase 7).

**One commercial engine.** Nothing here writes a subscription, an entitlement
or a price. Selling is ``subscriptions.purchase_plan`` with a *partner*
context; changing a plan is ``subscriptions.change_plan``; entitlements follow
from ``provisioning.reconcile`` exactly as for a direct sale (Task 56). What
this module adds is the partner's **gates**, in a fixed order:

1. the specific permission (the capability's action permission);
2. the partner may transact (active, not suspended or terminated);
3. an **active relationship** with exactly this customer, else a 404;
4. for anything that sells - a new line, a plan change, more seats, turning a
   trial into a paid subscription - an active **product grant** covering
   every plan involved. Holding ``subscription.manage`` is not a licence to
   sell a product the partner was never approved for.

and then it re-binds the actor to the customer's organization and calls the
ordinary service inside that organization's scope. Every failure raises before
the first write or from inside the scope's transaction, so a refused command
leaves nothing behind (D-016).

Partner-conducted actions are audited **in the customer's own audit log**
(``actor_kind = partner``), which is how a customer sees who acted on its
account.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from workeasy360.commercial.billing import services as billing
from workeasy360.commercial.catalog import selectors as catalog
from workeasy360.commercial.partners import access, activation, selectors
from workeasy360.commercial.partners.domain import clean_text
from workeasy360.commercial.pricing.resolution import resolve_price
from workeasy360.commercial.subscriptions import selectors as subscription_selectors
from workeasy360.commercial.subscriptions import services as subscription_services
from workeasy360.contracts.enums import BillingInterval, ChangeTiming, PartnerStatus
from workeasy360.contracts.errors import (
    PartnerRecordNotFound,
    PartnerValidationError,
    PermissionDenied,
    PriceNotAvailable,
)
from workeasy360.contracts.events import partner_events as schemas
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.integration.events import services as events
from workeasy360.integration.idempotency import services as idempotency
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import (
    acting_scope,
    context_for_organization,
    in_scope,
)
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

PROVISION = "platform.partner_customer.provision"
CUSTOMER_MANAGE = "platform.partner_customer.manage"
SELL = "platform.partner_subscription.sell"
SUBSCRIPTION_MANAGE = "platform.partner_subscription.manage"
PRICING_VIEW = "platform.partner_pricing.view"

PARTNER_PROVISIONING = "partner_provisioning_total"
MAX_CLIENT_KEY = 90


@dataclass(frozen=True, slots=True)
class ProvisionResult:
    organization_id: UUID
    slug: str
    link_id: UUID
    replayed: bool


def _scoped_key(ctx: TenantContext, client_key: Any) -> str:
    """Namespace a caller's idempotency key by partner.

    The idempotency ledger is keyed on (organization, scope, key), and every
    partner command runs in the one platform organization - so two partners
    choosing the same key would collide. Prefixing with the partner id makes
    the key unique per partner **without** putting the partner in the scope
    string, which the ledger also uses as a metric label (unbounded ids are
    forbidden there).
    """
    key = str(client_key or "").strip()
    if not key or len(key) > MAX_CLIENT_KEY:
        raise PartnerValidationError(
            f"An Idempotency-Key of 1-{MAX_CLIENT_KEY} characters is required: partner "
            f"commands may be retried, and a command without a key could run twice.",
            field="idempotency_key",
        )
    return f"{ctx.partner_id}:{key}"


# --- provisioning ---------------------------------------------------------


@in_scope
def provision_customer(
    ctx: TenantContext,
    *,
    organization_name: Any,
    owner_email: Any,
    idempotency_key: Any,
    currency: Any = None,
    owner_name: Any = "",
) -> ProvisionResult:
    """Create a customer organization, relate this partner to it and invite its owner.

    **All-or-nothing.** The organization, the invited owner membership, the
    partner link, the billing account, the invitation and both audit entries
    are written in one transaction; any failure rolls the whole thing back, so
    there is no state with an organization but no link, or a link with no
    billing account. **Idempotent**: a retry with the same key returns the
    original organization instead of creating a second.

    The customer is *its own tenant*. The partner does not become an owner,
    does not appear as a member and is not the organization's ``created_by``
    in any authority-bearing sense - its reach is the link row, which ends
    without touching the customer (Task 26-28).

    **No product-domain record is created.** Selling the customer a product is
    a separate, explicit command (``sell_plan``).
    """
    from workeasy360.commercial.partners.models import (
        PartnerOrganizationLink,
        PartnerOwnerInvite,
    )
    from workeasy360.platform.identity.services import ensure_identity_for_email
    from workeasy360.platform.onboarding.services import (
        create_organization_for_pending_owner,
    )

    authz.require(ctx, PROVISION)
    access.require_transacting(ctx)
    key = _scoped_key(ctx, idempotency_key)

    name = clean_text(organization_name, field="organization_name", max_length=120)
    address = clean_text(owner_email, field="owner_email", max_length=254).lower()
    # Names an owner whose account this call creates; never renames one that
    # already exists (``ensure_identity_for_email``).
    person = clean_text(owner_name, field="owner_name", max_length=255, required=False)
    partner = selectors.partner_row(ctx.partner_id)
    try:
        billing_currency = (
            CurrencyCode(str(currency))
            if currency
            else CurrencyCode(partner.payout_currency)
        )
    except ValueError as exc:
        raise PartnerValidationError(
            f"currency must be one of {sorted(c.value for c in CurrencyCode)}.",
            field="currency",
        ) from exc

    with transaction.atomic():
        claim = idempotency.claim(
            organization_id=ctx.organization_id,
            scope="partner.provision_customer",
            key=key,
            arguments={
                "name": name,
                # The address is fingerprinted, never stored in the ledger.
                "owner": hashlib.sha256(address.encode()).hexdigest(),
                "currency": billing_currency.value,
            },
        )
        if claim.is_replay:
            return _replayed(ctx, claim.result_ref)

        owner_id, _created = ensure_identity_for_email(address, full_name=person)
        if owner_id is None:
            # One message for a malformed address and an account that may not
            # be assigned, so this is not a way to probe which exist.
            raise PartnerValidationError(
                "That address cannot be assigned as an owner.", field="owner_email"
            )
        if selectors.has_been_member(ctx.partner_id, owner_id):
            # Self-dealing by the direct route: a customer owned by the
            # partner's own people would earn the partner commission on sales
            # it makes to itself. (The partner knows its members; this names
            # nothing it did not already know.)
            raise PartnerValidationError(
                "A partner's own people cannot own a customer it provisions.",
                field="owner_email",
            )

        created = create_organization_for_pending_owner(owner_user_id=owner_id, name=name)

        customer = context_for_organization(ctx, created.organization_id)
        with acting_scope(customer):
            link = PartnerOrganizationLink.objects.create(
                partner_id=ctx.partner_id,
                organization_id=created.organization_id,
                started_at=timezone.now(),
                created_by_id=ctx.user_id,
            )
            billing.open_billing_account(customer, currency=billing_currency)
            invite = PartnerOwnerInvite.objects.create(
                organization_id=created.organization_id,
                user_id=owner_id,
                partner_id=ctx.partner_id,
                expires_at=timezone.now() + activation.invite_ttl(),
            )
            audit.record(
                customer,
                "partner.customer_provisioned",
                resource_type="partners.link",
                resource_id=link.id,
                changes={
                    "partner_code": partner.code,
                    "billing_currency": billing_currency.value,
                },
            )

        # The owner's activation link (Phase 10e, D-020). Sent in this same
        # transaction - after the customer's own scope has closed, so the email
        # is queued under the platform's routing context - so the invitation
        # row, its hashed link and the queued email commit together or not at all.
        activation.issue_and_send(invite)

        events.publish(
            events.build_event(
                ctx,
                schemas.PARTNER_CUSTOMER_LINKED_V1.event_type,
                1,
                aggregate_type=schemas.LINK_AGGREGATE,
                aggregate_id=link.id,
                payload={
                    "partner_id": str(ctx.partner_id),
                    "organization_id": str(created.organization_id),
                    "link_id": str(link.id),
                },
            )
        )
        audit.record(
            ctx,
            "partner.customer_provisioned",
            resource_type="partners.link",
            resource_id=link.id,
            changes={
                "organization_id": str(created.organization_id),
                "partner_id": str(ctx.partner_id),
            },
        )
        idempotency.complete(claim, result_ref=f"partners.link:{link.id}")

    metrics.increment(PARTNER_PROVISIONING, outcome="created")
    return ProvisionResult(
        organization_id=created.organization_id,
        slug=created.slug,
        link_id=link.id,
        replayed=False,
    )


def _replayed(ctx: TenantContext, result_ref: str) -> ProvisionResult:
    from workeasy360.commercial.partners.models import PartnerOrganizationLink

    _kind, _, raw = result_ref.partition(":")
    link = (
        PartnerOrganizationLink.objects.select_related("organization")
        .filter(id=raw, partner_id=ctx.partner_id)
        .first()
    )
    if link is None:
        raise PartnerRecordNotFound(
            "This command was already performed, but its result can no longer be read."
        )
    metrics.increment(PARTNER_PROVISIONING, outcome="replayed")
    return ProvisionResult(
        organization_id=link.organization_id,
        slug=link.organization.slug,
        link_id=link.id,
        replayed=True,
    )


def accept_owner_invite(*, user_id: UUID, organization_id: UUID) -> UUID:
    """The invited person accepts, becoming the organization's owner.

    Only the exact user the invitation was issued to can accept it, only while
    it is pending and unexpired, and only once: the invitation row is locked,
    so two simultaneous acceptances resolve to one activation. Returns the
    organization id.
    """
    from workeasy360.commercial.partners.models import PartnerOwnerInvite
    from workeasy360.platform.onboarding.services import activate_pending_owner

    with transaction.atomic():
        invite = (
            PartnerOwnerInvite.objects.select_for_update()
            .filter(organization_id=organization_id, user_id=user_id)
            .order_by("-created_at")
            .first()
        )
        # One 404 for "no invitation", "not yours" and "no longer valid".
        if invite is None:
            raise PartnerRecordNotFound("No such invitation.")
        if invite.status == "accepted":
            return organization_id
        if invite.status != "pending":
            raise PartnerRecordNotFound("No such invitation.")
        if invite.expires_at <= timezone.now():
            invite.status = "expired"
            invite.resolved_at = timezone.now()
            invite.save(update_fields=["status", "resolved_at"])
            raise PartnerRecordNotFound("No such invitation.")

        activate_pending_owner(user_id=user_id, organization_id=organization_id)
        invite.status = "accepted"
        invite.resolved_at = timezone.now()
        invite.token_hash = ""  # spent: an emailed link can no longer match
        invite.save(update_fields=["status", "resolved_at", "token_hash"])
    return organization_id


def pending_invites_for_user(user_id: UUID) -> list[dict[str, Any]]:
    """The caller's own pending owner invitations. ``user_id`` is the
    authenticated session's, never a request value."""
    from workeasy360.commercial.partners.models import PartnerOwnerInvite

    return [
        {
            "organization_id": str(i.organization_id),
            "organization_name": i.organization.display_name,
            "expires_at": i.expires_at.isoformat(),
        }
        for i in PartnerOwnerInvite.objects.filter(
            user_id=user_id, status="pending", expires_at__gt=timezone.now()
        )
        .select_related("organization")
        .order_by("-created_at")[:20]
    ]


# --- what a partner may see -----------------------------------------------


def approved_catalog(
    ctx: TenantContext, *, interval: BillingInterval = BillingInterval.MONTHLY
) -> list[dict[str, Any]]:
    """The plans this partner may sell, with the price it sells them at.

    Only granted products appear, and each price is what
    ``resolve_price`` returns for **this partner**: its own price book if it
    has one for the plan, otherwise the public list price. Nothing else about
    pricing is visible - no other partner's book, no wholesale margin, no
    commission rate (Task 66).
    """
    authz.require(ctx, PRICING_VIEW)
    partner = selectors.partner_row(ctx.partner_id)
    grants = selectors.granted_products(ctx.partner_id)
    now = timezone.now()
    plans = catalog.sellable_plans(sorted({g["product_code"] for g in grants}), at=now)
    currency = CurrencyCode(partner.payout_currency)

    result = []
    for plan in plans:
        if not selectors.may_sell(
            ctx.partner_id, product_code=plan["product_code"], plan_code=plan["plan_code"]
        ):
            continue
        try:
            quote = resolve_price(
                plan_version_id=plan["plan_version_id"],
                organization_id=None,
                currency=currency,
                interval=interval,
                quantity=1,
                at=now,
                partner_id=ctx.partner_id,
            )
            price: dict[str, Any] | None = {
                "unit_amount_minor": quote.unit_amount_minor,
                "total_amount_minor": quote.total.minor_units,
                "currency": quote.currency.value,
                "interval": quote.interval.value,
                "source": quote.price_source.value,
            }
        except PriceNotAvailable:
            price = None
        result.append(
            {
                "plan_version_id": str(plan["plan_version_id"]),
                "plan_code": plan["plan_code"],
                "plan_name": plan["plan_name"],
                "product_code": plan["product_code"],
                "product_name": plan["product_name"],
                "included_seats": plan["included_seats"],
                "trial_days": plan["trial_days"],
                "price": price,
            }
        )
    return result


def customer_subscription(
    ctx: TenantContext, organization_id: UUID
) -> list[dict[str, Any]]:
    """A related customer's subscription lines - enough to act on, no more."""
    if not (authz.check(ctx, SELL) or authz.check(ctx, SUBSCRIPTION_MANAGE)):
        raise PermissionDenied(permission=SUBSCRIPTION_MANAGE)
    customer = access.customer_context(ctx, organization_id, write=False)
    with acting_scope(customer):
        subscriptions = subscription_selectors.subscriptions_for_organization()
    return [
        {
            "subscription_id": str(sub.id),
            "status": sub.status.value,
            "version": sub.version,
            "currency": sub.currency,
            "billing_interval": sub.billing_interval.value,
            "current_period_end": sub.current_period_end.isoformat(),
            "cancel_at": sub.cancel_at.isoformat() if sub.cancel_at else None,
            "items": [
                {
                    "item_id": str(item.id),
                    "product_code": item.product_code,
                    "plan_code": item.plan_code,
                    "bundle_code": item.bundle_code,
                    "quantity": item.quantity,
                    "is_derived": item.is_derived,
                    "removed_at": item.removed_at.isoformat()
                    if item.removed_at
                    else None,
                }
                for item in sub.items
            ],
        }
        for sub in subscriptions
    ]


# --- selling and managing -------------------------------------------------


def _assert_codes_sellable(ctx: TenantContext, product_code: str, plan_code: str) -> None:
    if not (
        product_code
        and plan_code
        and selectors.may_sell(
            ctx.partner_id, product_code=product_code, plan_code=plan_code
        )
    ):
        raise PermissionDenied(
            "That plan is not approved for this partner.",
            permission=SELL,
            reason="product_not_granted",
        )


def _assert_sellable_by(ctx: TenantContext, plan_version_id: UUID) -> Any:
    plan = catalog.require_plan_version(plan_version_id)
    _assert_codes_sellable(ctx, plan.product_code, plan.plan_code)
    return plan


@in_scope
def open_billing(ctx: TenantContext, organization_id: UUID, *, currency: Any) -> Any:
    authz.require(ctx, CUSTOMER_MANAGE)
    customer = access.customer_context(ctx, organization_id, write=True)
    try:
        code = CurrencyCode(str(currency))
    except ValueError as exc:
        raise PartnerValidationError("Unsupported currency.", field="currency") from exc
    with acting_scope(customer):
        access.lock_active_link(ctx, organization_id)
        return billing.open_billing_account(customer, currency=code)


@in_scope
def sell_plan(
    ctx: TenantContext,
    organization_id: UUID,
    *,
    plan_version_id: UUID,
    quantity: int,
    idempotency_key: Any,
    billing_interval: BillingInterval = BillingInterval.MONTHLY,
    with_trial: bool = False,
) -> Any:
    """Sell an approved plan to a related customer - the ordinary purchase path."""
    authz.require(ctx, SELL)
    customer = access.customer_context(ctx, organization_id, write=True)
    _assert_sellable_by(ctx, plan_version_id)
    key = _scoped_key(ctx, idempotency_key)
    with acting_scope(customer):
        access.lock_active_link(ctx, organization_id)
        item = subscription_services.purchase_plan(
            customer,
            plan_version_id=plan_version_id,
            idempotency_key=key,
            quantity=quantity,
            with_trial=with_trial,
            billing_interval=billing_interval,
        )
    metrics.increment("partner_subscription_operations_total", operation="sell")
    return item


@in_scope
def change_customer_plan(
    ctx: TenantContext,
    organization_id: UUID,
    *,
    subscription_item_id: UUID,
    to_plan_version_id: UUID,
    idempotency_key: Any,
    expected_version: int | None = None,
    timing: ChangeTiming | None = None,
) -> Any:
    authz.require(ctx, SUBSCRIPTION_MANAGE)
    customer = access.customer_context(ctx, organization_id, write=True)
    _assert_sellable_by(ctx, to_plan_version_id)
    with acting_scope(customer):
        access.lock_active_link(ctx, organization_id)
        item = subscription_services.change_plan(
            customer,
            subscription_item_id=subscription_item_id,
            to_plan_version_id=to_plan_version_id,
            idempotency_key=_scoped_key(ctx, idempotency_key),
            timing=timing,
            expected_version=expected_version,
        )
    metrics.increment("partner_subscription_operations_total", operation="change_plan")
    return item


@in_scope
def change_customer_quantity(
    ctx: TenantContext,
    organization_id: UUID,
    *,
    subscription_item_id: UUID,
    quantity: int,
    idempotency_key: Any,
    expected_version: int | None = None,
) -> Any:
    authz.require(ctx, SUBSCRIPTION_MANAGE)
    customer = access.customer_context(ctx, organization_id, write=True)
    with acting_scope(customer):
        access.lock_active_link(ctx, organization_id)
        # More seats is more sale: the line's product must be one this partner
        # may sell *now*, not merely one it once sold (or never did).
        codes = subscription_selectors.item_codes(subscription_item_id)
        if codes is not None:
            _assert_codes_sellable(ctx, *codes)
        item = subscription_services.change_quantity(
            customer,
            subscription_item_id=subscription_item_id,
            quantity=quantity,
            idempotency_key=_scoped_key(ctx, idempotency_key),
            expected_version=expected_version,
        )
    metrics.increment(
        "partner_subscription_operations_total", operation="change_quantity"
    )
    return item


@in_scope
def cancel_customer_subscription(
    ctx: TenantContext,
    organization_id: UUID,
    *,
    subscription_id: UUID,
    idempotency_key: Any,
    expected_version: int | None = None,
) -> Any:
    """Schedule a related customer's cancellation at period end.

    Never immediate: cutting a customer off mid-term is a decision for staff
    (with a reason), not a routine partner action.
    """
    authz.require(ctx, SUBSCRIPTION_MANAGE)
    customer = access.customer_context(ctx, organization_id, write=True)
    with acting_scope(customer):
        access.lock_active_link(ctx, organization_id)
        subscription = subscription_services.cancel_subscription(
            customer,
            subscription_id=subscription_id,
            idempotency_key=_scoped_key(ctx, idempotency_key),
            immediate=False,
            reason_code="partner_request",
            expected_version=expected_version,
        )
    metrics.increment("partner_subscription_operations_total", operation="cancel")
    return subscription


@in_scope
def activate_customer_subscription(
    ctx: TenantContext,
    organization_id: UUID,
    *,
    subscription_id: UUID,
    idempotency_key: Any,
    expected_version: int | None = None,
) -> Any:
    authz.require(ctx, SUBSCRIPTION_MANAGE)
    customer = access.customer_context(ctx, organization_id, write=True)
    with acting_scope(customer):
        access.lock_active_link(ctx, organization_id)
        # Converting a trial is when it becomes a paid sale (and earns): every
        # open, priced line on it must be within the partner's product scope.
        detail = subscription_selectors.subscription_detail(subscription_id)
        for line in detail.items if detail is not None else ():
            if line.removed_at is None and not line.is_derived:
                _assert_codes_sellable(ctx, line.product_code, line.plan_code)
        subscription = subscription_services.activate_subscription(
            customer,
            subscription_id=subscription_id,
            idempotency_key=_scoped_key(ctx, idempotency_key),
            expected_version=expected_version,
        )
    metrics.increment("partner_subscription_operations_total", operation="activate")
    return subscription


def partner_status(ctx: TenantContext) -> PartnerStatus:
    return selectors.partner_row(ctx.partner_id).status_enum


# --- a central order for a customer (ADR-053) ------------------------------


def _seller(ctx: TenantContext) -> Any:
    """This partner as the seller of an order: it may sell what its grants and
    switches allow (``may_sell``), priced from its own book."""
    from workeasy360.commercial.payments.orders import Seller

    partner_id = ctx.partner_id
    assert partner_id is not None
    return Seller(
        channel="partner",
        partner_id=partner_id,
        may_sell=lambda product_code, plan_code: selectors.may_sell(
            partner_id, product_code=product_code, plan_code=plan_code
        ),
    )


@in_scope
def quote_customer_order(
    ctx: TenantContext, organization_id: UUID, **request: Any
) -> dict[str, Any]:
    """What an order for this customer would cost - priced by the server from
    the partner's book (else list), nothing taken from the browser."""
    from workeasy360.commercial.payments import orders

    authz.require(ctx, SELL)
    customer = access.customer_context(ctx, organization_id, write=False)
    with acting_scope(customer):
        return orders.quote(customer, seller=_seller(ctx), **request)


@in_scope
def place_customer_order(
    ctx: TenantContext,
    organization_id: UUID,
    *,
    idempotency_key: Any,
    **request: Any,
) -> dict[str, Any]:
    """Place a central Order for a related customer (Store Working Flow, page
    5): the same order, payment, subscription and entitlement path as a direct
    purchase. A customer on automatic collection pays the order's invoice
    before anything is granted; a partner-billed or invoiced account is an
    approved sale, fulfilled at once with nothing charged now. The partner's
    margin or commission accrues only once the subscription is a paid sale."""
    from workeasy360.commercial.payments import orders

    authz.require(ctx, SELL)
    customer = access.customer_context(ctx, organization_id, write=True)
    key = _scoped_key(ctx, idempotency_key)
    with acting_scope(customer):
        access.lock_active_link(ctx, organization_id)
        order = orders.place(
            customer, idempotency_key=key, seller=_seller(ctx), **request
        )
    metrics.increment("partner_subscription_operations_total", operation="order")
    return order

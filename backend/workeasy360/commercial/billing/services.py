"""Billing application services.

Deliberately small. There is no payment provider, so there is nothing here
that charges, refunds, or records money. What exists is the establishment of
an organization's billing identity, which subscription creation depends on.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import transaction

from workeasy360.contracts.enums import CollectionMode
from workeasy360.contracts.errors import CatalogValidationError
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz

MANAGE = "commercial.subscription.manage"


def open_billing_account(
    ctx: TenantContext,
    *,
    currency: CurrencyCode,
    collection_mode: CollectionMode = CollectionMode.AUTOMATIC,
    billing_email: str = "",
    tax_reference: str = "",
    billing_region: str = "",
) -> Any:
    """Establish the organization's billing identity. Idempotent by design.

    Returns the existing account unchanged if one exists, rather than raising
    or - much worse - overwriting its currency. A currency change is a
    cancel-and-resubscribe decision, not a side effect of calling a setup
    endpoint twice.
    """
    from workeasy360.commercial.billing.models import BillingAccount

    authz.require(ctx, MANAGE)

    with transaction.atomic():
        existing = BillingAccount.objects.filter(
            organization_id=ctx.organization_id
        ).first()
        if existing is not None:
            return existing

        account = BillingAccount(
            organization_id=ctx.organization_id,
            currency=currency.value,
            collection_mode=collection_mode.value,
            billing_email=billing_email,
            tax_reference=tax_reference,
            billing_region=billing_region.upper() if billing_region else "",
        )
        account.save()
        audit.record(
            ctx,
            "commercial.billing_account.opened",
            resource_type="billing.account",
            resource_id=account.id,
            # billing_email is personal data and is not copied into the audit
            # trail; the account id is enough to find it.
            changes={
                "currency": account.currency,
                "collection_mode": account.collection_mode,
                "billing_region": account.billing_region,
            },
        )
    return account


def billing_account_for(organization_id: UUID) -> Any:
    """The organization's billing account, or raise.

    Raises rather than creating one on demand: an implicit account would
    silently pick a currency, and picking a customer's billing currency by
    accident is not recoverable once an invoice exists.
    """
    from workeasy360.commercial.billing.models import BillingAccount

    account = BillingAccount.objects.filter(organization_id=organization_id).first()
    if account is None:
        raise CatalogValidationError(
            "This organization has no billing account. Open one - which fixes "
            "its billing currency - before creating a subscription.",
            field="billing_account",
        )
    return account

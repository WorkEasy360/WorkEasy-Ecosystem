"""Billing foundation - the minimum a later payment integration needs.

**One model, and no more.** The Phase 2 brief allows ``BillingEvent``,
``BillingAttempt`` and a payment-provider customer reference as *possible*
concepts. None is here, because nothing writes them. A payment-attempt table
with no payment provider is a schema that will be wrong by the time it has a
writer, and an empty table that looks like it records payments is worse than
no table: it invites someone to read it and conclude money moved.

The trigger for all three is the same and is recorded in
docs/phase-2/cleanup-review.md: the first real payment-provider
integration, which will define their shape from a live API rather than from
a guess.

What genuinely exists now is the need for an organization's **billing
identity**: the currency every one of its subscriptions is denominated in,
how it is collected, and where to reach it. Without that anchor, currency
would be chosen per subscription and an organization could end up with a GBP
subscription and an INR one, which makes revenue reporting and any future
consolidated invoice incoherent.

Nothing here simulates a payment, and no field records money received.
"""

from __future__ import annotations

from typing import Any

from django.db import models

from workeasy360.contracts.enums import CollectionMode
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.platform.tenancy.models import TenantScopedModel


def _choices(enum_cls: Any) -> list[tuple[str, str]]:
    return [(member.value, member.name.replace("_", " ").title()) for member in enum_cls]


class BillingAccount(TenantScopedModel):
    """One organization's billing identity. Exactly one per organization.

    ``currency`` is the source of truth for every subscription the
    organization holds. Fixing it here rather than per subscription is what
    makes "no accidental arithmetic between different currencies" structural:
    two subscriptions of one organization cannot disagree, because there is
    one place the value comes from.

    Changing it is deliberately not a service in this phase. A currency switch
    is a cancel-and-resubscribe, not an update: anything else makes proration
    and revenue reporting incoherent (05-commercial-model.md section 4.4).
    """

    currency = models.CharField(max_length=3, choices=_choices(CurrencyCode))
    collection_mode = models.CharField(
        max_length=16,
        choices=_choices(CollectionMode),
        default=CollectionMode.AUTOMATIC.value,
    )

    #: Where invoices and dunning go. Distinct from any member's login email:
    #: the person holding the card is frequently not a user of the product.
    billing_email = models.EmailField(blank=True, default="")
    #: VAT / GSTIN / ABN. A reference, not a validated tax registration -
    #: validation belongs to the tax service at invoicing time.
    tax_reference = models.CharField(max_length=64, blank=True, default="")

    #: Derived from the verified billing address, never from IP or locale, and
    #: used to select a regional price book.
    billing_region = models.CharField(max_length=16, blank=True, default="")

    class Meta:
        db_table = "billing_account"
        constraints = [
            models.UniqueConstraint(
                fields=["organization"], name="uq_billing_account_org"
            ),
        ]
        indexes = [
            models.Index(fields=["organization"], name="ix_billing_account_org"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"billing account for {self.organization_id} ({self.currency})"

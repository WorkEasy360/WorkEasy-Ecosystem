"""Billing application services.

Deliberately small: the establishment of an organization's billing identity,
which subscription creation depends on. Charging, refunds and settlement live
in ``commercial/payments``.

**Billing terms are the platform's, not the customer's** (Phase 9). How an
account is collected (``collection_mode``) and which regional price book it
buys from (``billing_region``) decide what a customer pays and whether they
are invoiced at all - so a customer opening their own account gets automatic
collection and the default region, and only Workeasy360 staff
(``set_billing_terms``) or a partner's own provisioning set anything else.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import transaction

from workeasy360.contracts.enums import CollectionMode, PrincipalKind
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
    if ctx.principal is PrincipalKind.MEMBER and (
        collection_mode is not CollectionMode.AUTOMATIC or billing_region
    ):
        raise CatalogValidationError(
            "Collection mode and billing region are set by Workeasy360, not by "
            "the customer.",
            field="collection_mode",
        )

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


def billing_account_or_none(organization_id: UUID) -> Any:
    """The organization's billing account, or ``None`` - for pages that show
    "open a billing account first" rather than fail (Phase 8 extension)."""
    from workeasy360.commercial.billing.models import BillingAccount

    return BillingAccount.objects.filter(organization_id=organization_id).first()


# --- the billing profile (User Admin Panel, 2026-11-11) ------------------------------

PROFILE_MANAGE = "platform.billing.manage"

#: Field -> (most characters, what the field is called in an error).
PROFILE_FIELDS: dict[str, tuple[int, str]] = {
    "legal_name": (200, "Business name"),
    "address_line1": (200, "Address line 1"),
    "address_line2": (200, "Address line 2"),
    "city": (100, "City"),
    "state": (100, "State or region"),
    "postal_code": (20, "Postal code"),
    "country": (2, "Country"),
    "tax_reference": (64, "Tax ID"),
    "billing_email": (254, "Billing email"),
}


def _clean_profile_value(field: str, raw: Any) -> str:
    if raw is None:
        raw = ""
    if not isinstance(raw, str):
        raise CatalogValidationError(
            f"{PROFILE_FIELDS[field][1]} must be text.", field=field
        )
    value = " ".join(raw.split())
    most, label = PROFILE_FIELDS[field]
    if len(value) > most:
        raise CatalogValidationError(
            f"{label} can be at most {most} characters.", field=field
        )
    if any(ord(ch) < 32 for ch in value):
        raise CatalogValidationError(
            f"{label} contains a control character.", field=field
        )
    if field == "country":
        value = value.upper()
        if value and not (len(value) == 2 and value.isascii() and value.isalpha()):
            raise CatalogValidationError(
                "Choose a country (a two-letter code such as IN).", field=field
            )
    elif field == "postal_code":
        if value and not all(ch.isalnum() or ch in " -" for ch in value):
            raise CatalogValidationError(
                "A postal code has letters, digits, spaces and dashes only.", field=field
            )
    elif field == "tax_reference":
        value = value.upper()
        if value and not all(ch.isalnum() or ch in " -/." for ch in value):
            raise CatalogValidationError(
                "A tax ID has letters, digits and - / . only.", field=field
            )
    elif field == "billing_email" and value:
        from django.core.exceptions import ValidationError
        from django.core.validators import validate_email

        try:
            validate_email(value)
        except ValidationError as exc:
            raise CatalogValidationError(
                "Enter a valid billing email.", field=field
            ) from exc
        value = value.lower()
    return value


def update_billing_profile(ctx: TenantContext, changes: dict[str, Any]) -> Any:
    """Change who the organization's invoices are made out to, and where. Only the
    fields named in ``changes`` (the profile fields, nothing else: never the currency,
    the collection mode or the region). Audited by field name - the values are the
    customer's own business details and stay on the account."""
    from workeasy360.commercial.billing.models import BillingAccount

    authz.require(ctx, PROFILE_MANAGE)
    unknown = sorted(set(changes) - set(PROFILE_FIELDS))
    if unknown:
        raise CatalogValidationError(
            f"These cannot be changed here: {', '.join(unknown)}.", field=unknown[0]
        )
    cleaned = {field: _clean_profile_value(field, raw) for field, raw in changes.items()}
    with transaction.atomic():
        account = (
            BillingAccount.objects.select_for_update()
            .filter(organization_id=ctx.organization_id)
            .first()
        )
        if account is None:
            raise CatalogValidationError(
                "This organization has no billing account yet. Open one first.",
                field="billing_account",
            )
        changed = [f for f, v in cleaned.items() if getattr(account, f) != v]
        if not changed:
            return account
        for field in changed:
            setattr(account, field, cleaned[field])
        account.save(update_fields=[*changed, "updated_at"])
        audit.record(
            ctx,
            "commercial.billing_account.profile_changed",
            resource_type="billing.account",
            resource_id=account.id,
            changes={"fields": sorted(changed)},
        )
    return account


def billing_profile_json(account: Any) -> dict[str, Any]:
    """The account as the customer's billing pages show it."""
    return {
        "id": str(account.id),
        "currency": account.currency,
        "collection_mode": account.collection_mode,
        "billing_email": account.billing_email,
        "billing_region": account.billing_region,
        "tax_reference": account.tax_reference,
        "legal_name": account.legal_name,
        "address_line1": account.address_line1,
        "address_line2": account.address_line2,
        "city": account.city,
        "state": account.state,
        "postal_code": account.postal_code,
        "country": account.country,
        "updated_at": account.updated_at.isoformat() if account.updated_at else None,
    }


def adopt_partner_wallet(ctx: TenantContext) -> Any:
    """ADR-063: the customer in ``ctx`` (a partner acting on it) is from now on
    paid for by that partner from its Platform Wallet, and never invoiced.

    Only from automatic collection, the default a reseller's new customer gets;
    an account staff set to be invoiced or billed through its partner offline
    keeps those terms (changing them is ``set_billing_terms``). The caller has
    checked the customer holds no subscription it pays for itself. Idempotent."""
    from workeasy360.commercial.billing.models import BillingAccount

    authz.require(ctx, MANAGE)
    if ctx.principal is not PrincipalKind.PARTNER:
        raise CatalogValidationError(
            "Only the customer's partner pays from its wallet.", field="collection_mode"
        )
    with transaction.atomic():
        account = (
            BillingAccount.objects.select_for_update()
            .filter(organization_id=ctx.organization_id)
            .first()
        )
        if account is None:
            raise CatalogValidationError(
                "This customer has no billing account yet.", field="billing_account"
            )
        if account.collection_mode == CollectionMode.PARTNER_WALLET.value:
            return account
        if account.collection_mode != CollectionMode.AUTOMATIC.value:
            raise CatalogValidationError(
                "Workeasy360 bills this customer on agreed terms. Ask support to "
                "move it to your Platform Wallet.",
                field="collection_mode",
            )
        account.collection_mode = CollectionMode.PARTNER_WALLET.value
        account.save(update_fields=["collection_mode", "updated_at"])
        audit.record(
            ctx,
            "commercial.billing_account.terms_changed",
            resource_type="billing.account",
            resource_id=account.id,
            changes={
                "before": {"collection_mode": CollectionMode.AUTOMATIC.value},
                "after": {"collection_mode": account.collection_mode},
            },
            reason="The partner pays for this customer from its Platform Wallet.",
        )
    return account


STAFF_TERMS = "platform.staff_payment.manage"


def set_billing_terms(
    actor: TenantContext,
    organization_id: UUID,
    *,
    collection_mode: CollectionMode,
    billing_region: str,
    reason: str,
) -> Any:
    """A Super Admin sets how an account is collected and which regional price
    book it buys from - a sales-led customer paying by invoice, a regional
    agreement (Phase 9). Dangerous (``platform.staff_payment.manage``),
    with a reason, audited in the platform log and the customer's own. Future
    invoices and prices follow; nothing already issued changes."""
    from workeasy360.commercial.billing.models import BillingAccount
    from workeasy360.platform.authz.control_plane import (
        context_for_organization,
        organization_scope,
    )

    authz.require(actor, STAFF_TERMS)
    why = (reason or "").strip()
    if len(why) < 10:
        raise CatalogValidationError(
            "Give a reason of at least 10 characters.", field="reason"
        )
    region = (billing_region or "").strip().upper()
    if len(region) > 8 or (not region.replace("-", "").isalnum() and region):
        raise CatalogValidationError("Enter a short region code.", field="billing_region")
    customer = context_for_organization(actor, organization_id)
    with organization_scope(actor, organization_id), transaction.atomic():
        account = (
            BillingAccount.objects.select_for_update()
            .filter(organization_id=organization_id)
            .first()
        )
        if account is None:
            raise CatalogValidationError(
                "This customer has no billing account yet.", field="billing_account"
            )
        before = {
            "collection_mode": account.collection_mode,
            "billing_region": account.billing_region,
        }
        account.collection_mode = collection_mode.value
        account.billing_region = region
        account.save(update_fields=["collection_mode", "billing_region", "updated_at"])
        after = {"collection_mode": account.collection_mode, "billing_region": region}
        audit.record(
            customer,
            "commercial.billing_account.terms_changed",
            resource_type="billing.account",
            resource_id=account.id,
            changes={"before": before, "after": after},
            reason=why,
        )
    from workeasy360.platform.authz.control_plane import ensure_scope

    with ensure_scope(actor):
        audit.record(
            actor,
            "staff.billing_terms_changed",
            resource_type="organizations.organization",
            resource_id=organization_id,
            changes={"before": before, "after": after},
            reason=why,
        )
    return account

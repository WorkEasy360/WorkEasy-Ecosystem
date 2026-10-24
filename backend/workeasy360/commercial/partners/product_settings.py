"""A partner's own switches over the products it was granted (ADR-045).

Presentation and a sale switch, nothing more. The partner can

- turn a granted product **off for new sales** and back on;
- choose whether it is shown on its customers' **Explore** and **My Products**
  pages;
- set the **order** its products appear in.

What none of this can do: grant a product (``PartnerProductGrant`` is staff's -
a partner cannot enable a product it holds no grant for, and the check below is
against the grant, not the setting), change a price, end a subscription or touch
an entitlement. A product turned off here keeps selling nothing new and leaves
every existing customer exactly as they were.
"""

from __future__ import annotations

from typing import Any, Final
from uuid import UUID

from django.db import transaction

from workeasy360.commercial.partners import services
from workeasy360.commercial.partners.access import require_transacting
from workeasy360.contracts.enums import GrantStatus
from workeasy360.contracts.errors import PartnerRecordNotFound, PartnerValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope

MANAGE: Final = "platform.partner_product.manage"
MAX_ORDER_ITEMS: Final = 100


def _flag(value: Any, field: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise PartnerValidationError(f"{field} must be true or false.", field=field)
    return value


def _granted_product_id(partner_id: UUID, product_code: str) -> UUID:
    """The id of a product this partner holds an *active grant* for, else a 404.

    The grant is the authority; this is where a partner that tries to switch on
    a product it was never granted is stopped."""
    from workeasy360.commercial.partners.models import PartnerProductGrant

    row = (
        PartnerProductGrant.objects.filter(
            partner_id=partner_id,
            status=GrantStatus.ACTIVE.value,
            product__code=product_code,
        )
        .values_list("product_id", flat=True)
        .first()
    )
    if row is None:
        raise PartnerRecordNotFound(
            "No such product.", product_code=str(product_code)[:48]
        )
    return row


@in_scope
def update_product_setting(
    ctx: TenantContext,
    *,
    product_code: Any,
    enabled_for_sale: Any = None,
    show_in_explore: Any = None,
    show_in_my_products: Any = None,
) -> dict[str, Any]:
    """Change the switches for one granted product. Only what is sent changes."""
    from workeasy360.commercial.partners.models import PartnerProductSetting

    authz.require(ctx, MANAGE)
    require_transacting(ctx)
    changes = {
        key: value
        for key, value in (
            ("enabled_for_sale", _flag(enabled_for_sale, "enabled_for_sale")),
            ("show_in_explore", _flag(show_in_explore, "show_in_explore")),
            ("show_in_my_products", _flag(show_in_my_products, "show_in_my_products")),
        )
        if value is not None
    }
    if not changes:
        raise PartnerValidationError("Nothing to change.", field="body")
    code = str(product_code or "").strip()
    with transaction.atomic():
        services._lock_partner(ctx.partner_id)
        product_id = _granted_product_id(ctx.partner_id, code)
        row, _created = PartnerProductSetting.objects.select_for_update().get_or_create(
            partner_id=ctx.partner_id,
            product_id=product_id,
            defaults={"updated_by_id": ctx.user_id},
        )
        before = {key: getattr(row, key) for key in changes}
        if before == changes:
            return setting_dict(row)
        for key, value in changes.items():
            setattr(row, key, value)
        row.updated_by_id = ctx.user_id
        row.save()
        audit.record(
            ctx,
            "partner.product_setting_changed",
            resource_type="partners.product_setting",
            resource_id=row.id,
            changes={
                "partner_id": str(ctx.partner_id),
                "product_code": code,
                **{f"{key}": value for key, value in changes.items()},
            },
        )
    return setting_dict(row)


@in_scope
def set_display_order(ctx: TenantContext, *, product_codes: Any) -> list[str]:
    """Put the named granted products in this order (1, 2, 3, ...). Products not
    named keep their place after the named ones. Presentation only."""
    from workeasy360.commercial.partners.models import PartnerProductSetting

    authz.require(ctx, MANAGE)
    require_transacting(ctx)
    if (
        not isinstance(product_codes, list | tuple)
        or len(product_codes) > MAX_ORDER_ITEMS
    ):
        raise PartnerValidationError(
            "order must be a list of product codes.", field="order"
        )
    codes: list[str] = []
    for item in product_codes:
        code = str(item or "").strip()
        if code in codes:
            raise PartnerValidationError("A product can appear only once.", field="order")
        codes.append(code)
    with transaction.atomic():
        services._lock_partner(ctx.partner_id)
        for position, code in enumerate(codes, start=1):
            product_id = _granted_product_id(ctx.partner_id, code)
            row, _created = (
                PartnerProductSetting.objects.select_for_update().get_or_create(
                    partner_id=ctx.partner_id,
                    product_id=product_id,
                    defaults={"updated_by_id": ctx.user_id},
                )
            )
            if row.display_order != position:
                row.display_order = position
                row.updated_by_id = ctx.user_id
                row.save()
        audit.record(
            ctx,
            "partner.product_order_changed",
            resource_type="partners.partner",
            resource_id=ctx.partner_id,
            changes={"partner_id": str(ctx.partner_id), "order": codes},
        )
    return codes


def setting_dict(row: Any) -> dict[str, Any]:
    return {
        "enabled_for_sale": row.enabled_for_sale,
        "show_in_explore": row.show_in_explore,
        "show_in_my_products": row.show_in_my_products,
        "display_order": row.display_order,
    }


DEFAULTS: Final = {
    "enabled_for_sale": True,
    "show_in_explore": True,
    "show_in_my_products": True,
    "display_order": 0,
}


def settings_by_product_code(partner_id: UUID) -> dict[str, dict[str, Any]]:
    """``{product_code: switches}`` for the products this partner changed
    anything on. One query."""
    from workeasy360.commercial.partners.models import PartnerProductSetting

    return {
        row.product.code: setting_dict(row)
        for row in PartnerProductSetting.objects.filter(
            partner_id=partner_id
        ).select_related("product")
    }

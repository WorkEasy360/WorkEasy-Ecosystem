"""Customer setup: what a customer must tell us before a product is fully set up.

**A fourth state of its own.** Payment, subscription, setup and member access are
separate facts, so setup never changes a subscription's status and a subscription never
waits on it: "Pending Setup" in the Admin console is this table, nothing else. What a
product requires is data an Admin entered (``SetupRequirement``); with none, the product
needs no setup and no record appears.

``pending`` -> ``submitted`` -> ``approved``, or ``correction_required`` and back to
``submitted``. A customer reaches only its own organization's record, from the verified
context. Staff read behind ``platform.staff_customer.view`` and decide behind
``platform.staff_subscription.manage``. Answers are bounded text, never markup.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from workeasy360.commercial.provisioning.models import CustomerSetup, SetupRequirement
from workeasy360.contracts.errors import CatalogValidationError, CommercialRecordNotFound
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.administration.notices import tell_staff
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope

VIEW = "platform.staff_customer.view"
MANAGE = "platform.staff_subscription.manage"
CATALOG_VIEW = "platform.staff_catalog.view"
CATALOG_MANAGE = "platform.staff_catalog.manage"
MAX_ANSWER = 2000


def requirement_json(row: SetupRequirement) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "product_code": row.product_code,
        "key": row.key,
        "label": row.label,
        "help_text": row.help_text,
        "is_required": row.is_required,
        "is_active": row.is_active,
        "display_order": row.display_order,
    }


def requirements(ctx: TenantContext, *, product_code: Any = None) -> list[dict[str, Any]]:
    authz.require(ctx, CATALOG_VIEW)
    rows = SetupRequirement.objects.order_by("product_code", "display_order", "label")
    if product_code:
        rows = rows.filter(product_code=str(product_code))
    return [requirement_json(row) for row in rows]


def _clean_requirement(body: dict[str, Any]) -> dict[str, Any]:
    label = " ".join(str(body.get("label") or "").split())[:120]
    if not label:
        raise CatalogValidationError(
            "Say what you need from the customer.", field="label"
        )
    code = str(body.get("product_code") or "").strip()[:64]
    if not code:
        raise CatalogValidationError("Choose a product.", field="product_code")
    order = body.get("display_order", 0)
    if isinstance(order, bool) or not isinstance(order, int) or not 0 <= order <= 999:
        raise CatalogValidationError("Order must be 0 to 999.", field="display_order")
    return {
        "product_code": code,
        "label": label,
        "help_text": " ".join(str(body.get("help_text") or "").split())[:300],
        "is_required": bool(body.get("is_required", True)),
        "is_active": bool(body.get("is_active", True)),
        "display_order": order,
    }


@in_scope
def save_requirement(
    ctx: TenantContext, body: dict[str, Any], *, requirement_id: UUID | None = None
) -> dict[str, Any]:
    authz.require(ctx, CATALOG_MANAGE)
    fields = _clean_requirement(body)
    with transaction.atomic():
        if requirement_id is None:
            row = SetupRequirement.objects.create(key=uuid4().hex[:12], **fields)
        else:
            row = (
                SetupRequirement.objects.select_for_update()
                .filter(id=requirement_id)
                .first()
            )
            if row is None:
                raise CommercialRecordNotFound("No such requirement.")
            for name, value in fields.items():
                setattr(row, name, value)
            row.save()
        audit.record(
            ctx,
            "setup.requirement_saved",
            resource_type="provisioning.setup_requirement",
            resource_id=row.id,
            changes={"product": row.product_code, "is_active": row.is_active},
        )
    return requirement_json(row)


def _sync_pending() -> None:
    """A record for every customer holding a product that now needs setup. Cheap and
    repeatable: a record that exists is left alone."""
    from workeasy360.commercial.subscriptions.admin_reads import holders_of_products

    products = set(
        SetupRequirement.objects.filter(is_active=True).values_list(
            "product_code", flat=True
        )
    )
    if not products:
        return
    CustomerSetup.objects.bulk_create(
        [
            CustomerSetup(organization_id=organization_id, product_code=product)
            for organization_id, product in holders_of_products(products)
        ],
        ignore_conflicts=True,
    )


def record_json(
    row: CustomerSetup, names: dict[Any, str], people: dict[Any, str]
) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "organization_id": str(row.organization_id),
        "organization_name": names.get(row.organization_id, ""),
        "product_code": row.product_code,
        "status": row.status,
        "answers": dict(row.answers or {}),
        "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "decided_by_name": people.get(row.decided_by_id, ""),
        "decision_reason": row.decision_reason,
        "version": row.version,
    }


def queue(
    ctx: TenantContext,
    *,
    status: Any = None,
    product: Any = None,
    organization_id: UUID | None = None,
    limit: Any = None,
    offset: Any = None,
) -> dict[str, Any]:
    from workeasy360.platform.identity.selectors import display_names
    from workeasy360.platform.organizations.selectors import organization_names

    authz.require(ctx, VIEW)
    _sync_pending()
    rows = CustomerSetup.objects.all()
    if status:
        if status not in CustomerSetup.Status.values:
            raise CatalogValidationError("Unknown status.", field="status")
        rows = rows.filter(status=status)
    if product:
        rows = rows.filter(product_code=str(product))
    if organization_id is not None:
        rows = rows.filter(organization_id=organization_id)
    try:
        size = max(1, min(int(limit or 25), 100))
        start = max(0, int(offset or 0))
    except (TypeError, ValueError):
        size, start = 25, 0
    total = rows.count()
    page = list(rows.order_by("-submitted_at", "-created_at")[start : start + size])
    names = organization_names({r.organization_id for r in page})
    people = display_names({r.decided_by_id for r in page})
    from django.db.models import Count

    counts = {
        row["status"]: row["n"]
        for row in CustomerSetup.objects.values("status").annotate(n=Count("id"))
    }
    return {
        "results": [record_json(r, names, people) for r in page],
        "total": total,
        "limit": size,
        "offset": start,
        "counts": counts,
        "requirements": [
            requirement_json(r)
            for r in SetupRequirement.objects.filter(
                is_active=True,
                product_code__in={p.product_code for p in page},
            ).order_by("display_order")
        ],
    }


@in_scope
def decide(
    ctx: TenantContext,
    record_id: UUID,
    *,
    decision: Any,
    reason: Any,
    expected_version: Any = None,
) -> dict[str, Any]:
    """Approve what a customer sent, or ask them to correct it. Touches nothing of the
    subscription: setup is its own state."""
    from workeasy360.platform.identity.selectors import display_names
    from workeasy360.platform.organizations.selectors import organization_names

    authz.require(ctx, MANAGE)
    target = {"approve": "approved", "request_correction": "correction_required"}.get(
        str(decision or "")
    )
    if target is None:
        raise CatalogValidationError("Choose a decision.", field="decision")
    why = str(reason or "").strip()[:500]
    if target == "correction_required" and len(why) < 10:
        raise CatalogValidationError(
            "Say what to correct, in at least 10 characters: the customer reads this.",
            field="reason",
        )
    with transaction.atomic():
        row = CustomerSetup.objects.select_for_update().filter(id=record_id).first()
        if row is None:
            raise CommercialRecordNotFound("No such setup record.")
        if expected_version is not None and expected_version != row.version:
            raise CatalogValidationError(
                "This changed in the meantime. Reload.", field="expected_version"
            )
        if row.status != "submitted":
            raise CatalogValidationError(
                "Only something submitted can be decided.", field="status"
            )
        row.status = target
        row.decision_reason = why
        row.decided_at = timezone.now()
        row.decided_by_id = ctx.user_id
        row.version += 1
        row.save(
            update_fields=[
                "status",
                "decision_reason",
                "decided_at",
                "decided_by_id",
                "version",
                "updated_at",
            ]
        )
        audit.record(
            ctx,
            f"setup.{target}",
            resource_type="provisioning.customer_setup",
            resource_id=row.id,
            changes={
                "product": row.product_code,
                "organization": str(row.organization_id),
            },
            reason=why or "Setup approved.",
        )
    return record_json(
        row, organization_names({row.organization_id}), display_names({row.decided_by_id})
    )


# --- a customer's side ---------------------------------------------------------------------


def customer_view(ctx: TenantContext) -> dict[str, Any]:
    """What this organization still owes, per product it holds."""
    _sync_pending()
    rows = CustomerSetup.objects.filter(organization_id=ctx.organization_id).order_by(
        "product_code"
    )
    requirements_by_product: dict[str, list[dict[str, Any]]] = {}
    for req in SetupRequirement.objects.filter(
        is_active=True, product_code__in={r.product_code for r in rows}
    ).order_by("display_order"):
        requirements_by_product.setdefault(req.product_code, []).append(
            requirement_json(req)
        )
    return {
        "results": [
            {
                "id": str(r.id),
                "product_code": r.product_code,
                "status": r.status,
                "answers": dict(r.answers or {}),
                "decision_reason": r.decision_reason
                if r.status == "correction_required"
                else "",
                "requirements": requirements_by_product.get(r.product_code, []),
                "editable": r.status in ("pending", "correction_required"),
                "version": r.version,
            }
            for r in rows
        ]
    }


def customer_submit(
    ctx: TenantContext, record_id: UUID, *, answers: Any
) -> dict[str, Any]:
    if not isinstance(answers, dict):
        raise CatalogValidationError("Send your answers.", field="answers")
    with transaction.atomic():
        row = (
            CustomerSetup.objects.select_for_update()
            .filter(Q(id=record_id), organization_id=ctx.organization_id)
            .first()
        )
        if row is None:
            raise CommercialRecordNotFound("No such setup record.")
        if row.status not in ("pending", "correction_required"):
            raise CatalogValidationError("This has already been sent.", field="status")
        wanted = list(
            SetupRequirement.objects.filter(is_active=True, product_code=row.product_code)
        )
        cleaned: dict[str, str] = {}
        for req in wanted:
            value = " ".join(str(answers.get(req.key) or "").split())[:MAX_ANSWER]
            if req.is_required and not value:
                raise CatalogValidationError(f"Answer: {req.label}.", field=req.key)
            cleaned[req.key] = value
        row.answers = cleaned
        row.status = "submitted"
        row.submitted_at = timezone.now()
        row.version += 1
        row.save(
            update_fields=["answers", "status", "submitted_at", "version", "updated_at"]
        )
        audit.record(
            ctx,
            "setup.submitted",
            resource_type="provisioning.customer_setup",
            resource_id=row.id,
            changes={"product": row.product_code},
        )
    tell_staff("A customer sent setup details", "Their answers are waiting for review.")
    return customer_view(ctx)

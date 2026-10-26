"""The Super Admin's payments: provider accounts, a customer's invoices and
payments, manual settlement, voiding, the event log and collection totals.

``platform.staff_payment.view`` to read; ``.manage`` (dangerous:
refused under impersonation) for every change, each audited - provider changes
in the control-plane log, invoice changes in the customer's own log.

**No cross-tenant query.** Invoices are tenant rows, so they are read one
customer at a time (inside that customer's scope). Platform-wide totals come
from the control-plane payment event log, which records each collected amount.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from workeasy360.commercial.payments import credentials, settlement
from workeasy360.commercial.payments.checkout import attempt_view
from workeasy360.commercial.payments.invoicing import organization_scope
from workeasy360.commercial.payments.refunds import refund_view
from workeasy360.contracts.errors import (
    PaymentNotAllowed,
    PaymentRecordNotFound,
    PaymentValidationError,
)
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import context_for_organization, in_scope

VIEW = "platform.staff_payment.view"
MANAGE = "platform.staff_payment.manage"

_REF_ALLOWED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def _provider_json(provider: Any) -> dict[str, Any]:
    return {
        "id": str(provider.id),
        "key": provider.key,
        "adapter": provider.adapter,
        "display_name": provider.display_name,
        "mode": provider.mode,
        "status": provider.status,
        "currencies": list(provider.currencies or []),
        "priority": provider.priority,
        "version": provider.version,
        "credential_ref": provider.credential_ref,
        "webhook_secret_ref": provider.webhook_secret_ref,
        # Presence only - never a value, never a prefix of one.
        "credential_configured": credentials.is_configured(
            provider.credential_ref, adapter=provider.adapter
        ),
        "webhook_secret_configured": credentials.is_configured(
            provider.webhook_secret_ref, adapter=provider.adapter, secret=True
        ),
        "credential_variable": credentials.api_key_variable(provider.credential_ref),
        "webhook_secret_variable": credentials.webhook_secret_variable(
            provider.webhook_secret_ref
        ),
        "webhook_path": f"/api/v1/public/payments/webhooks/{provider.key}",
    }


def _clean_ref(raw: Any, field: str) -> str:
    value = str(raw or "").strip().upper()
    if not value or len(value) > 48 or not set(value) <= _REF_ALLOWED:
        raise PaymentValidationError(
            f"{field} must be 1-48 letters, digits, '_' or '-' - the suffix of the "
            "environment variable that holds the secret, never the secret itself.",
            field=field,
        )
    return value


def _clean_currencies(raw: Any) -> list[str]:
    if not isinstance(raw, list) or not raw:
        raise PaymentValidationError("List at least one currency.", field="currencies")
    known = {c.value for c in CurrencyCode}
    values = sorted({str(c).upper() for c in raw})
    unknown = [c for c in values if c not in known]
    if unknown:
        raise PaymentValidationError(
            f"Unsupported currencies: {unknown}.", field="currencies"
        )
    return values


@in_scope
def providers(ctx: TenantContext) -> list[dict[str, Any]]:
    from workeasy360.commercial.payments.models import PaymentProvider

    authz.require(ctx, VIEW)
    return [
        _provider_json(p) for p in PaymentProvider.objects.order_by("priority", "key")
    ]


@in_scope
def create_provider(ctx: TenantContext, body: dict[str, Any]) -> dict[str, Any]:
    from workeasy360.commercial.payments.models import PaymentProvider, ProviderAdapter

    authz.require(ctx, MANAGE)
    key = str(body.get("key") or "").strip().lower()
    if not key or len(key) > 32 or not all(c.isalnum() or c == "-" for c in key):
        raise PaymentValidationError(
            "key must be 1-32 lowercase letters, digits or '-'.", field="key"
        )
    adapter = str(body.get("adapter") or "")
    if adapter not in ProviderAdapter.values:
        raise PaymentValidationError(
            f"adapter must be one of {sorted(ProviderAdapter.values)}.", field="adapter"
        )
    if adapter == ProviderAdapter.FAKE and not settings.PAYMENTS_FAKE_PROVIDER_ENABLED:
        raise PaymentValidationError(
            "The test provider is only available in development.", field="adapter"
        )
    mode = str(body.get("mode") or "test")
    if mode not in PaymentProvider.Mode.values:
        raise PaymentValidationError("mode must be test or live.", field="mode")
    name = str(body.get("display_name") or "").strip()[:64] or key
    with transaction.atomic():
        try:
            with transaction.atomic():
                provider = PaymentProvider.objects.create(
                    key=key,
                    adapter=adapter,
                    display_name=name,
                    mode=mode,
                    credential_ref=_clean_ref(
                        body.get("credential_ref"), "credential_ref"
                    ),
                    webhook_secret_ref=_clean_ref(
                        body.get("webhook_secret_ref"), "webhook_secret_ref"
                    ),
                    currencies=_clean_currencies(body.get("currencies")),
                    priority=int(body.get("priority") or 100),
                )
        except IntegrityError as exc:
            raise PaymentValidationError(
                "That key is already in use.", field="key"
            ) from exc
        audit.record(
            ctx,
            "payments.provider_created",
            resource_type="payments.provider",
            resource_id=provider.id,
            changes={
                "key": provider.key,
                "adapter": provider.adapter,
                "mode": provider.mode,
                "currencies": provider.currencies,
            },
        )
    return _provider_json(provider)


@in_scope
def update_provider(ctx: TenantContext, key: str, body: dict[str, Any]) -> dict[str, Any]:
    """Enable/disable, mode, currencies, priority, name, references. Enabling
    requires both secrets to be present - an enabled provider that cannot be
    called turns every "pay" into an error."""
    from workeasy360.commercial.payments.models import PaymentProvider, ProviderAdapter

    authz.require(ctx, MANAGE)
    expected = body.get("version")
    with transaction.atomic():
        provider = PaymentProvider.objects.select_for_update().filter(key=key).first()
        if provider is None:
            raise PaymentRecordNotFound("No such provider.")
        if expected != provider.version:
            raise PaymentNotAllowed(
                "Someone else changed this provider. Reload and retry."
            )
        before = _provider_json(provider)
        if "display_name" in body:
            provider.display_name = (
                str(body["display_name"] or "").strip()[:64] or provider.key
            )
        if "mode" in body:
            if body["mode"] not in PaymentProvider.Mode.values:
                raise PaymentValidationError("mode must be test or live.", field="mode")
            provider.mode = body["mode"]
        if "currencies" in body:
            provider.currencies = _clean_currencies(body["currencies"])
        if "priority" in body:
            if isinstance(body["priority"], bool) or not isinstance(
                body["priority"], int
            ):
                raise PaymentValidationError(
                    "priority must be an integer.", field="priority"
                )
            provider.priority = body["priority"]
        if "credential_ref" in body:
            provider.credential_ref = _clean_ref(body["credential_ref"], "credential_ref")
        if "webhook_secret_ref" in body:
            provider.webhook_secret_ref = _clean_ref(
                body["webhook_secret_ref"], "webhook_secret_ref"
            )
        if "status" in body:
            if body["status"] not in PaymentProvider.Status.values:
                raise PaymentValidationError(
                    "status must be enabled or disabled.", field="status"
                )
            if body["status"] == PaymentProvider.Status.ENABLED:
                if (
                    provider.adapter == ProviderAdapter.FAKE
                    and not settings.PAYMENTS_FAKE_PROVIDER_ENABLED
                ):
                    raise PaymentNotAllowed("The test provider cannot be enabled here.")
                if not (
                    credentials.is_configured(
                        provider.credential_ref, adapter=provider.adapter
                    )
                    and credentials.is_configured(
                        provider.webhook_secret_ref, adapter=provider.adapter, secret=True
                    )
                ):
                    raise PaymentNotAllowed(
                        "Set both secrets in the secret manager before enabling this provider."
                    )
            provider.status = body["status"]
        provider.version += 1
        provider.save()
        after = _provider_json(provider)
        audit.record(
            ctx,
            "payments.provider_updated",
            resource_type="payments.provider",
            resource_id=provider.id,
            changes={
                k: {"from": before[k], "to": after[k]}
                for k in (
                    "status",
                    "mode",
                    "currencies",
                    "priority",
                    "display_name",
                    "credential_ref",
                    "webhook_secret_ref",
                )
                if before[k] != after[k]
            },
        )
    return after


@in_scope
def event_log(ctx: TenantContext, *, limit: int = 50) -> list[dict[str, Any]]:
    from workeasy360.commercial.payments.models import PaymentWebhookEvent

    authz.require(ctx, VIEW)
    rows = PaymentWebhookEvent.objects.order_by("-received_at")[: max(1, min(limit, 200))]
    return [
        {
            "id": str(r.id),
            "provider": r.provider_key,
            "event_id": r.event_id,
            "kind": r.kind,
            "outcome": r.outcome,
            "organization_id": str(r.organization_id) if r.organization_id else None,
            "attempt_id": str(r.attempt_id) if r.attempt_id else None,
            "amount_minor": r.amount_minor,
            "currency": r.currency,
            "received_at": r.received_at.isoformat(),
        }
        for r in rows
    ]


def collections_summary(*, days: int = 30) -> dict[str, Any]:
    """Collected amounts per currency over ``days``, from the event log. For
    the Super Admin dashboard, whose caller has already authorized."""
    from workeasy360.commercial.payments.models import PaymentWebhookEvent

    since = timezone.now() - timedelta(days=days)
    rows = (
        PaymentWebhookEvent.objects.filter(
            received_at__gte=since, outcome="applied", kind="payment_succeeded"
        )
        .exclude(currency="")
        .values("currency")
        .annotate(total=Sum("amount_minor"), count=Count("id"))
        .order_by("currency")
    )
    return {
        "days": days,
        "by_currency": [
            {
                "currency": r["currency"],
                "amount_minor": r["total"] or 0,
                "payments": r["count"],
            }
            for r in rows
        ],
    }


def collections_by_day(*, start: Any, end: Any) -> list[dict[str, Any]]:
    """Collected amounts in ``[start, end)`` per currency and per UTC day, from
    the same event log as ``collections_summary``. Never converted or summed
    across currencies. For the Super Admin dashboard, whose caller has already
    authorized."""
    from django.db.models.functions import TruncDate

    from workeasy360.commercial.payments.models import PaymentWebhookEvent

    rows = (
        PaymentWebhookEvent.objects.filter(
            received_at__gte=start,
            received_at__lt=end,
            outcome="applied",
            kind="payment_succeeded",
        )
        .exclude(currency="")
        .annotate(day=TruncDate("received_at"))
        .values("currency", "day")
        .annotate(total=Sum("amount_minor"), count=Count("id"))
        .order_by("currency", "day")
    )
    by_currency: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = by_currency.setdefault(
            row["currency"],
            {"currency": row["currency"], "amount_minor": 0, "payments": 0, "daily": {}},
        )
        amount = int(row["total"] or 0)
        entry["amount_minor"] += amount
        entry["payments"] += int(row["count"])
        entry["daily"][row["day"].isoformat()] = amount
    return list(by_currency.values())


# --- one customer ------------------------------------------------------------------


def customer_billing(actor: TenantContext, organization_id: UUID) -> dict[str, Any]:
    """Every invoice of one customer with its attempts and refunds."""
    from workeasy360.commercial.payments.models import Invoice, PaymentAttempt, Refund

    authz.require(actor, VIEW)
    context_for_organization(actor, organization_id)  # 404 for the unknown
    with organization_scope(organization_id, reason="Staff reads a customer's invoices"):
        invoices = list(
            Invoice.unscoped.filter(organization_id=organization_id).order_by(
                "-issued_at"
            )[:100]
        )
        attempts = list(
            PaymentAttempt.unscoped.filter(
                organization_id=organization_id, invoice_id__in=[i.id for i in invoices]
            ).order_by("-created_at")
        )
        refunds = list(
            Refund.unscoped.filter(
                organization_id=organization_id, attempt_id__in=[a.id for a in attempts]
            ).order_by("-created_at")
        )
    by_invoice: dict[Any, list[dict[str, Any]]] = {}
    for attempt in attempts:
        view = attempt_view(attempt)
        view["refunds"] = [refund_view(r) for r in refunds if r.attempt_id == attempt.id]
        view["amount_refunded_minor"] = attempt.amount_refunded_minor
        view["created_at"] = attempt.created_at.isoformat()
        by_invoice.setdefault(attempt.invoice_id, []).append(view)
    return {
        "invoices": [
            {**invoice_json(i), "attempts": by_invoice.get(i.id, [])} for i in invoices
        ]
    }


def invoice_json(invoice: Any) -> dict[str, Any]:
    return {
        "invoice_id": str(invoice.id),
        "number": invoice.number,
        "status": invoice.status,
        "currency": invoice.currency,
        "total_minor": invoice.total_minor,
        "amount_paid_minor": invoice.amount_paid_minor,
        "amount_refunded_minor": invoice.amount_refunded_minor,
        "amount_due_minor": invoice.amount_due_minor if invoice.status == "open" else 0,
        "billing_interval": invoice.billing_interval,
        "period_start": invoice.period_start.isoformat(),
        "period_end": invoice.period_end.isoformat(),
        "issued_at": invoice.issued_at.isoformat(),
        "due_at": invoice.due_at.isoformat(),
        "paid_at": invoice.paid_at.isoformat() if invoice.paid_at else None,
        "dunning_stage": invoice.dunning_stage or None,
        "order_id": str(invoice.order_id) if invoice.order_id else None,
        "version": invoice.version,
    }


def _reason(raw: Any) -> str:
    reason = str(raw or "").strip()
    if len(reason) < 10:
        raise PaymentValidationError(
            "Give a reason of at least 10 characters.", field="reason"
        )
    return reason[:255]


def mark_invoice_paid(
    actor: TenantContext,
    organization_id: UUID,
    invoice_id: UUID,
    *,
    reference: Any,
    reason: Any,
    expected_version: Any,
) -> dict[str, Any]:
    """Record a payment received outside the platform (a bank transfer): a
    ``manual`` attempt for the amount due, settled through the same path a
    provider payment takes."""
    from workeasy360.commercial.payments.models import (
        Invoice,
        PaymentAttempt,
        PaymentWebhookEvent,
    )
    from workeasy360.platform.tenancy.context import tenant_context

    authz.require(actor, MANAGE)
    why = _reason(reason)
    note = str(reference or "").strip()[:255]
    if not note:
        raise PaymentValidationError(
            "Give the payment's reference (a bank transfer id).", field="reference"
        )
    rebound = context_for_organization(actor, organization_id)
    after = settlement.AfterCommit()
    with (
        organization_scope(organization_id, reason="Staff records a manual payment"),
        transaction.atomic(),
    ):
        invoice = (
            Invoice.unscoped.select_for_update()
            .filter(id=invoice_id, organization_id=organization_id)
            .first()
        )
        if invoice is None:
            raise PaymentRecordNotFound("No such invoice.")
        if invoice.version != expected_version:
            raise PaymentNotAllowed("The invoice changed. Reload and retry.")
        if invoice.status != Invoice.Status.OPEN or invoice.amount_due_minor <= 0:
            raise PaymentNotAllowed("This invoice has nothing to pay.")
        now = timezone.now()
        attempt = PaymentAttempt(
            organization_id=organization_id,
            invoice=invoice,
            provider_key="manual",
            amount_minor=invoice.amount_due_minor,
            currency=invoice.currency,
            idempotency_key=f"manual:{invoice.id}:{invoice.version}",
            created_by_user_id=actor.user_id,
            reference_note=note,
            expires_at=now,
        )
        attempt.save()
        settlement.record_success(
            attempt,
            amount_minor=attempt.amount_minor,
            currency=attempt.currency,
            payment_id=f"manual:{note}"[:128],
            after=after,
        )
        PaymentWebhookEvent(
            provider_key="manual",
            event_id=f"manual:{attempt.id}",
            kind="payment_succeeded",
            outcome="applied",
            organization_id=organization_id,
            attempt_id=attempt.id,
            amount_minor=attempt.amount_minor,
            currency=attempt.currency,
            payload_digest="",
        ).save()
        with tenant_context(rebound):
            audit.record(
                rebound,
                "billing.invoice_marked_paid",
                resource_type="payments.invoice",
                resource_id=invoice.id,
                changes={
                    "number": invoice.number,
                    "amount_minor": attempt.amount_minor,
                    "reference": note,
                },
                reason=why,
            )
        invoice = Invoice.unscoped.get(id=invoice_id, organization_id=organization_id)
    after.run(organization_id)
    return invoice_json(invoice)


def void_invoice(
    actor: TenantContext,
    organization_id: UUID,
    invoice_id: UUID,
    *,
    reason: Any,
    expected_version: Any,
) -> dict[str, Any]:
    """Cancel an invoice nobody paid anything towards. Open attempts on it are
    cancelled with it."""
    from workeasy360.commercial.payments.models import Invoice, PaymentAttempt
    from workeasy360.platform.tenancy.context import tenant_context

    authz.require(actor, MANAGE)
    why = _reason(reason)
    rebound = context_for_organization(actor, organization_id)
    with (
        organization_scope(organization_id, reason="Staff voids an invoice"),
        transaction.atomic(),
    ):
        invoice = (
            Invoice.unscoped.select_for_update()
            .filter(id=invoice_id, organization_id=organization_id)
            .first()
        )
        if invoice is None:
            raise PaymentRecordNotFound("No such invoice.")
        if invoice.version != expected_version:
            raise PaymentNotAllowed("The invoice changed. Reload and retry.")
        if invoice.status != Invoice.Status.OPEN or invoice.amount_paid_minor > 0:
            raise PaymentNotAllowed(
                "Only an open invoice with nothing paid can be voided."
            )
        now = timezone.now()
        PaymentAttempt.unscoped.filter(
            organization_id=organization_id,
            invoice_id=invoice.id,
            status__in=("pending", "awaiting_payment"),
        ).update(status="cancelled", checkout_url="", completed_at=now, updated_at=now)
        invoice.status = Invoice.Status.VOID
        invoice.voided_at = now
        invoice.void_reason = why
        invoice.version += 1
        invoice.save(
            update_fields=["status", "voided_at", "void_reason", "version", "updated_at"]
        )
        # Voiding an order's invoice withdraws the order with it (Phase 9).
        from workeasy360.commercial.payments.orders import on_invoice_voided

        on_invoice_voided(invoice, at=now)
        # Forgiving the debt ends the dunning it caused, exactly as paying it
        # would - unless another overdue invoice still holds the subscription.
        after = settlement.AfterCommit()
        still_overdue = (
            Invoice.unscoped.filter(
                organization_id=organization_id,
                subscription_id=invoice.subscription_id,
                status=Invoice.Status.OPEN,
            )
            .exclude(dunning_stage="")
            .exists()
        )
        if invoice.dunning_stage and not still_overdue:
            after.settle_subscription = invoice.subscription_id
            after.lift_suspension = invoice.dunning_stage == "suspended"
        with tenant_context(rebound):
            audit.record(
                rebound,
                "billing.invoice_voided",
                resource_type="payments.invoice",
                resource_id=invoice.id,
                changes={"number": invoice.number},
                reason=why,
            )
    after.run(organization_id)
    return invoice_json(invoice)

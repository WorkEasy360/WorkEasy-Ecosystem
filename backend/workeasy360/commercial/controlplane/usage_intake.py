"""Where a product's reported usage meets the wallets (2026-11-09).

A registered product reports a billable thing (a WhatsApp message sent, an API trigger,
a webhook delivered) with an idempotency key of its own. The charge follows the rate an
Admin entered, or nothing happens when there is none. The product comes from its verified
credential, never from the request. Only an organization entitled to that product can be
charged by it, so one product cannot run up a customer it does not serve.

AI tokens arrive the same way, from the view that returned the AI answer (see
``interfaces/api/v1/intelligence_product_views.py``), because ``intelligence`` and
``commercial`` may not import each other.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.conf import settings

from workeasy360.commercial.billing.services import billing_account_or_none
from workeasy360.commercial.wallets import usage
from workeasy360.contracts.errors import CatalogValidationError
from workeasy360.integration.scheduling.services import routing_context
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.tenancy.context import tenant_context
from workeasy360.platform.tenancy.db import organization_db_scope

logger = get_logger(__name__)

#: A product reports these. AI tokens are not on the list: they are measured by the
#: platform's own gateway, never taken from a product's word.
REPORTABLE = {"whatsapp", "sms", "email", "api_trigger", "webhook"}


def _currency(organization_id: UUID) -> str:
    """The customer's billing currency, read in its own scope, else the platform's."""
    default = str(settings.PUBLIC_CATALOG_DEFAULT_CURRENCY)
    ctx = routing_context(organization_id)
    if ctx is None:
        return default
    with tenant_context(ctx), organization_db_scope(organization_id):
        account = billing_account_or_none(organization_id)
        return (
            str(account.currency) if account is not None and account.currency else default
        )


#: How far ahead of the platform's clock an event may be dated (clock skew).
FUTURE_SKEW = timedelta(minutes=5)


def report(
    *,
    product_code: str,
    organization_id: UUID,
    meter: Any,
    quantity: Any,
    idempotency_key: Any,
    occurred_at: Any = None,
) -> dict[str, Any]:
    """One event from a product. Answers what happened, including a refusal: too little
    in the wallet is an outcome the product decides what to do with, not an error."""
    if meter not in REPORTABLE:
        raise CatalogValidationError("Unknown meter.", field="meter")
    moment: datetime | None = None
    if occurred_at:
        try:
            moment = datetime.fromisoformat(str(occurred_at))
        except ValueError as exc:
            raise CatalogValidationError(
                "Use an ISO 8601 time.", field="occurred_at"
            ) from exc
        if moment.tzinfo is None:
            raise CatalogValidationError(
                "The time needs a timezone.", field="occurred_at"
            )
        # 2026-11-10 audit: a plan's free allowance counts from the start of the
        # event's month, so an event dated in a future month would draw on an
        # allowance that has not started. Usage happens now or in the past; a
        # few minutes of clock skew are tolerated.
        from django.utils import timezone

        if moment > timezone.now() + FUTURE_SKEW:
            raise CatalogValidationError(
                "Usage cannot be reported for a time in the future.",
                field="occurred_at",
            )
    event = usage.record(
        organization_id=organization_id,
        product_code=product_code,
        meter=meter,
        quantity=quantity,
        idempotency_key=idempotency_key,
        currency=_currency(organization_id),
        occurred_at=moment,
    )
    return {
        "event_id": str(event.id),
        "outcome": event.outcome,
        "failure_code": event.failure_code,
        "charged_units": event.charged_units,
        "amount": event.amount,
        "duplicate": event.duplicate_count > 0,
    }


def ai_credits_exhausted(organization_id: UUID) -> bool:
    """Whether to refuse an AI call before it spends anything."""
    return usage.credits_block_ai(organization_id)


def charge_ai_tokens(
    *, product_code: str, organization_id: UUID, request_id: str, tokens: int
) -> None:
    """Charge a finished AI call, once (the key is its request id). Never raises: the
    customer already has the answer, and a charge that fails stays on the record as a
    failed event for review, which is where an Admin would look."""
    if not isinstance(tokens, int) or tokens <= 0:
        return
    try:
        usage.record(
            organization_id=organization_id,
            product_code=product_code,
            meter="ai_tokens",
            quantity=tokens,
            idempotency_key=f"ai:{request_id}",
            currency=_currency(organization_id),
        )
    except Exception as exc:  # pragma: no cover - logged for the on-call, never surfaced
        logger.error("wallets.ai_charge_failed", error_type=type(exc).__name__)

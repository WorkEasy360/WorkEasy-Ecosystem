"""The development test provider's "hosted page" outcome (Phase 8 extension).

Development only: refused unless ``PAYMENTS_FAKE_PROVIDER_ENABLED`` (pinned off
in production). The outcome a developer chose is signed exactly as a provider
signs a callback and handed to the same ``webhooks.process`` a real provider
reaches - so development exercises the real verification and settlement path,
not a shortcut around it.
"""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from django.conf import settings

from workeasy360.commercial.payments import credentials, webhooks
from workeasy360.commercial.payments.invoicing import organization_scope
from workeasy360.commercial.payments.providers import fake
from workeasy360.contracts.errors import PaymentRecordNotFound, PaymentValidationError

_KINDS = {"succeeded": "payment.succeeded", "failed": "payment.failed"}


def complete(
    organization_id: Any, *, reference: str, outcome: Any, user_id: Any
) -> dict[str, Any]:
    """Only a signed-in *member of the organization* may complete its test
    checkout (Phase 9 audit: any signed-in user could before)."""
    from workeasy360.commercial.payments.models import PaymentAttempt, PaymentProvider
    from workeasy360.contracts.enums import MembershipStatus
    from workeasy360.platform.organizations.resolver import resolve_by_organization_id

    if not settings.PAYMENTS_FAKE_PROVIDER_ENABLED:
        raise PaymentRecordNotFound("Not found.")
    if outcome not in _KINDS:
        raise PaymentValidationError(
            "outcome must be succeeded or failed.", field="outcome"
        )
    try:
        organization = UUID(str(organization_id))
    except ValueError as exc:
        raise PaymentRecordNotFound("No such payment.") from exc
    # The tenant bootstrap query: the authority on who is a member.
    resolution = resolve_by_organization_id(user_id=user_id, organization_id=organization)
    member = (
        resolution is not None and resolution.membership_status is MembershipStatus.ACTIVE
    )
    with organization_scope(organization, reason="Development test checkout"):
        attempt = (
            PaymentAttempt.unscoped.filter(
                organization_id=organization, provider_reference=reference[:128]
            ).first()
            if member
            else None
        )
    provider = (
        PaymentProvider.objects.filter(key=attempt.provider_key, adapter="fake").first()
        if attempt is not None and reference
        else None
    )
    if attempt is None or provider is None:
        raise PaymentRecordNotFound("No such payment.")
    body = fake.event_body(
        id=f"evt_test_{uuid.uuid4().hex}",
        type=_KINDS[outcome],
        reference=reference,
        payment_id=f"fake_pay_{attempt.id.hex}",
        amount_minor=attempt.amount_minor,
        currency=attempt.currency,
        attempt_id=str(attempt.id),
        organization_id=str(organization),
    )
    secret = credentials.webhook_secret(provider.webhook_secret_ref, adapter="fake")
    signature = fake.sign(body, secret)
    del secret
    return webhooks.process(provider.key, {fake.SIGNATURE_HEADER: signature}, body)

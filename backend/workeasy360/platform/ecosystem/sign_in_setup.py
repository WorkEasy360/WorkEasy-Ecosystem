"""The automatic sign-in setup's record (ADR-074): every read and write of
``ProductSignInSetup``, and the credential facts its checks compare with.

The checks themselves live in ``commercial.controlplane.sso_setup`` - they need
the network, which this module never touches (the ecosystem module makes no
outbound call). This module owns the table, as ``domains`` owns
``ProductDomain``: callers get plain values and hand plain values back.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from workeasy360.platform.ecosystem.models import (
    IntegrationCredential,
    ProductIntegration,
    ProductSignInSetup,
)


@dataclass
class SetupRecord:
    """One setup, as the checks read and update it."""

    pk: Any
    product_code: str
    state: str
    checks: list[dict[str, str]]
    application_url: str
    integration_id: Any
    created_integration: bool
    requested_by_id: Any
    key_absences: dict[str, int]
    attempts: int


def _record(row: ProductSignInSetup) -> SetupRecord:
    return SetupRecord(
        pk=row.pk,
        product_code=row.product_code,
        state=row.state,
        checks=list(row.checks or []),
        application_url=row.application_url,
        integration_id=row.integration_id,
        created_integration=row.created_integration,
        requested_by_id=row.requested_by_id,
        key_absences=dict(row.key_absences or {}),
        attempts=row.attempts,
    )


def ensure(product_code: str, *, requested_by_id: Any = None) -> SetupRecord:
    """The product's one setup row (created when absent), locked for the
    caller's transaction. ``requested_by_id`` - when given - becomes the
    staff identity the checks act for."""
    with transaction.atomic():
        row, _created = ProductSignInSetup.objects.select_for_update().get_or_create(
            product_code=str(product_code)
        )
        if requested_by_id is not None and row.requested_by_id != requested_by_id:
            row.requested_by_id = requested_by_id
            row.save(update_fields=["requested_by_id", "updated_at"])
        return _record(row)


def prepared(
    product_code: str,
    *,
    requested_by_id: Any,
    application_url: str,
    integration: ProductIntegration | None,
    created_integration: bool,
    pending_checks: list[dict[str, str]] | None,
) -> None:
    """Record what ``prepare`` did, and make the product due for a check now."""
    with transaction.atomic():
        row, _created = ProductSignInSetup.objects.select_for_update().get_or_create(
            product_code=str(product_code)
        )
        row.requested_by_id = requested_by_id
        row.application_url = application_url
        if integration is not None:
            row.integration = integration
        if created_integration:
            row.created_integration = True
        row.next_check_at = timezone.now()
        if integration is None and pending_checks is not None:
            row.state = "pending_configuration"
            row.checks = pending_checks
        row.save()


def record_result(
    pk: Any,
    *,
    state: str,
    checks: list[dict[str, str]],
    application_url: str,
    integration: ProductIntegration | None,
    created_integration: bool,
    key_absences: dict[str, int],
    connector: dict[str, str],
    activated: bool,
    next_check_at: datetime,
) -> SetupRecord:
    """Store one check's result under a row lock; return the stored record."""
    now = timezone.now()
    with transaction.atomic():
        row = ProductSignInSetup.objects.select_for_update().get(pk=pk)
        if integration is not None:
            row.integration = integration
        if created_integration:
            row.created_integration = True
        row.application_url = application_url
        row.key_absences = key_absences
        row.state = state
        row.checks = checks
        if connector:
            row.connector_name = connector.get("name", "")[:64]
            row.connector_version = connector.get("version", "")[:32]
        row.attempts = 0 if state == "active" else row.attempts + 1
        row.last_checked_at = now
        row.next_check_at = next_check_at
        if activated:
            row.activated_at = now
        row.save()
        return _record(row)


def due(*, limit: int) -> list[str]:
    """Product codes whose next check is due, oldest first."""
    return list(
        ProductSignInSetup.objects.filter(next_check_at__lte=timezone.now())
        .order_by("next_check_at")
        .values_list("product_code", flat=True)[:limit]
    )


def view(product_code: str) -> dict[str, Any] | None:
    """The stored setup as plain values, or ``None`` when there is none."""
    row = (
        ProductSignInSetup.objects.select_related("integration")
        .filter(product_code=str(product_code))
        .first()
    )
    if row is None:
        return None
    return {
        "state": row.state,
        "checks": list(row.checks or []),
        "application_url": row.application_url,
        "client_id": row.integration.client_id if row.integration else "",
        "integration_status": row.integration.status if row.integration else "",
        "connector_name": row.connector_name,
        "connector_version": row.connector_version,
        "attempts": row.attempts,
        "last_checked_at": row.last_checked_at,
        "next_check_at": row.next_check_at,
        "activated_at": row.activated_at,
    }


@dataclass(frozen=True)
class KeyFact:
    credential_id: str
    public_key_pem: str
    usable: bool
    label: str


def public_keys(integration: ProductIntegration) -> dict[str, KeyFact]:
    """This integration's public-key credentials by key id."""
    return {
        c.credential_id: KeyFact(
            credential_id=c.credential_id,
            public_key_pem=c.public_key_pem,
            usable=c.is_usable,
            label=c.label,
        )
        for c in IntegrationCredential.objects.filter(
            integration=integration, kind="public_key"
        )
    }


def awaiting_activation(product_code: str) -> bool:
    """True while the product's integration is a draft that automatic setup
    prepared and has not activated yet (the publish checklist's sign-in gate
    does not apply to it until then: ``selectors.sign_in_proven``)."""
    return ProductSignInSetup.objects.filter(
        product_code=str(product_code),
        created_integration=True,
        integration__status="draft",
    ).exists()

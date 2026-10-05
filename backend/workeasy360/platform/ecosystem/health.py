"""Recording the outcome of a product health check (Phase 11).

**This module makes no network call and never will.** The ecosystem package is
forbidden any outbound HTTP (``test_the_ecosystem_module_makes_no_outbound_http_request``):
the probe lives in ``integration/`` beside the SSRF-guarded egress client and
hands its *result* here. Keeping the two apart is what stops an authentication
module from ever waiting on someone else's server.

What is recorded is a status, a time and a short category. Not a body, not a
header, not an address: a probe of an external product must not turn a health
column into a place the product's responses are stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone

from workeasy360.platform.ecosystem.models import (
    IntegrationHealth,
    IntegrationStatus,
    ProductIntegration,
)
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

MAX_DETAIL = 64

#: Which integrations are worth probing: those that can be launched now. A
#: draft has nowhere to send anyone yet and a retired one never will.
PROBED_STATUSES = (IntegrationStatus.ACTIVE.value, IntegrationStatus.SUSPENDED.value)


@dataclass(frozen=True, slots=True)
class ProbeTarget:
    """What the probe may ask: the product's own registered address, read from
    the stored manifest - never from a request."""

    product_code: str
    url: str


def probe_targets() -> list[ProbeTarget]:
    """One target per launchable integration. The declared ``health_url``
    when there is one, else the launch URL: a product that publishes no health
    document is checked by whether its front door answers."""
    targets = []
    for row in ProductIntegration.objects.filter(status__in=PROBED_STATUSES).values(
        "product_code", "manifest"
    ):
        manifest = row["manifest"] or {}
        url = str(manifest.get("health_url") or manifest.get("launch_url") or "")
        if url:
            targets.append(ProbeTarget(product_code=row["product_code"], url=url))
    return targets


def record_health(
    product_code: str,
    *,
    status: IntegrationHealth,
    detail: str = "",
    checked_at: datetime | None = None,
) -> bool:
    """Store one check's outcome. Returns whether the status *changed*.

    Not audited per check: it is telemetry, and a check every few minutes would
    bury the audit trail that matters (who activated or suspended a product).
    A change of status is logged and counted instead.
    """
    row = ProductIntegration.objects.filter(product_code=str(product_code)).first()
    if row is None:
        return False
    changed = row.health_status != status.value
    row.health_status = status.value
    row.health_checked_at = checked_at or timezone.now()
    row.health_detail = str(detail)[:MAX_DETAIL]
    row.save(update_fields=["health_status", "health_checked_at", "health_detail"])
    if changed:
        logger.info(
            "ecosystem.integration.health_changed",
            product_code=str(product_code),
            status=status.value,
            detail=row.health_detail,
        )
        metrics.increment(
            "ecosystem_integration_health_changes_total", status=status.value
        )
    return changed

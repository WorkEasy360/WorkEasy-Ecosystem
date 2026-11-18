"""Connected Logins: what the platform truthfully knows about one person's
sign-ins to each product (ADR-072).

Three sources, each saying only what it can:

- :class:`~workeasy360.platform.ecosystem.models.ProductConnection` - durable,
  per (person, product, organization): the first and the latest code the
  product redeemed for them (the token endpoint) and when the product last
  asked about them (``/integrations/entitlements/verify`` with ``subject``,
  which a product does while their session is in use).
- ``ProductSignIn`` (ADR-069) - per WorkEasy360 sign-in, for products that take
  back-channel logout only: whether the sign-in that opened the product is
  still open, or has ended.
- ``BackchannelLogout`` (ADR-069) - whether a sign-out reached the product.

**Nothing here can say a product session exists right now.** A product's own
sign-out, or its session timing out, never reaches the platform. So the
states are "WorkEasy360 sign-in still open" (the platform sign-in that opened
the product has not ended), "last seen <time>" and "unknown" - never
"active".

Like ``backchannel``, this module makes no network call. The per-product
sign-out queues a back-channel logout for the integration layer to deliver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from workeasy360.platform.ecosystem.models import (
    BackchannelLogout,
    BackchannelLogoutStatus,
    ProductConnection,
    ProductIntegration,
    ProductSignIn,
)
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

#: ``last_activity_at`` is kept to this resolution: a product asks at most
#: every revalidation window per session, and a busy person's row is written
#: at most once per this interval however many products and tabs ask.
ACTIVITY_RESOLUTION = timedelta(minutes=5)
#: A per-product sign-out asked for on WorkEasy360 Accounts. Not one of
#: ``contracts.ports.sign_ins.REASONS``: it ends no WorkEasy360 sign-in, only
#: the product's sessions; for operators, never sent to the product.
REASON_PRODUCT_SIGNED_OUT = "product_signed_out"


def _retention_days() -> int:
    return max(30, int(getattr(settings, "ACCOUNT_EVENT_RETENTION_DAYS", 400)))


# --- writes ------------------------------------------------------------------------


def record_sign_in(*, integration: Any, user_id: Any, organization_id: Any) -> None:
    """The token endpoint redeemed a code: this person is connected to this
    product in this organization. Every product, back-channel or not."""
    now = timezone.now()
    rows = ProductConnection.objects.filter(
        user_id=user_id, integration=integration, organization_id=organization_id
    )
    if rows.update(
        last_sign_in_at=now, last_seen_at=now, sign_in_count=F("sign_in_count") + 1
    ):
        return
    try:
        with transaction.atomic():
            ProductConnection.objects.create(
                user_id=user_id,
                integration=integration,
                organization_id=organization_id,
                first_sign_in_at=now,
                last_sign_in_at=now,
                last_seen_at=now,
            )
    except IntegrityError:
        # A concurrent first sign-in inserted it: count this one too.
        rows.update(
            last_sign_in_at=now, last_seen_at=now, sign_in_count=F("sign_in_count") + 1
        )


def record_activity(*, integration: Any, subject: Any, organization_id: Any) -> bool:
    """The product asked about this person in this organization. Only an
    existing connection is touched - a product cannot create one by naming an
    arbitrary subject - and only once per ``ACTIVITY_RESOLUTION``."""
    try:
        user_id = UUID(str(subject))
    except (TypeError, ValueError):
        return False
    now = timezone.now()
    return bool(
        ProductConnection.objects.filter(
            user_id=user_id, integration=integration, organization_id=organization_id
        )
        .filter(
            Q(last_activity_at__isnull=True)
            | Q(last_activity_at__lt=now - ACTIVITY_RESOLUTION)
        )
        .update(last_activity_at=now, last_seen_at=now)
    )


def is_connected(*, user_id: Any, product_code: str) -> bool:
    """Whether this person ever signed in to this product with WorkEasy360
    (in any of their organizations)."""
    return ProductConnection.objects.filter(
        user_id=user_id, integration__product_code=str(product_code)
    ).exists()


def purge_stale(*, older_than_days: int | None = None) -> int:
    """Connections with neither a sign-in nor any activity within the
    retention window (the same window as the person's security trail)."""
    days = older_than_days if older_than_days is not None else _retention_days()
    cutoff = timezone.now() - timedelta(days=max(1, int(days)))
    deleted, _ = ProductConnection.objects.filter(last_seen_at__lt=cutoff).delete()
    return deleted


def sign_out_of_product(*, user_id: Any, product_code: str) -> dict[str, Any]:
    """ "Sign out of this product": ADR-069 back-channel logout of every
    session this person has in this one product, wherever it was opened.

    One logout token naming ``sub`` only (no ``sid``), exactly as "sign out
    everywhere" sends per product - the product ends every session of the
    person. The WorkEasy360 sign-ins themselves stay open (this is not sign
    out everywhere), so their ``ProductSignIn`` rows for this product are
    closed here: the next time this product is opened, a new row is written.

    Returns ``{"supported": bool, "queued": int, "since": datetime}``: a
    product without a back-channel URL cannot be told (``supported`` false),
    and nothing is queued for it.
    """
    from workeasy360.platform.ecosystem import backchannel

    now = timezone.now()
    integration = (
        ProductIntegration.objects.filter(
            product_code=str(product_code),
            status__in=backchannel.NOTIFIED_STATUSES,
        )
        .only("id", "manifest", "status", "product_code")
        .first()
    )
    if integration is None or not backchannel.takes_backchannel_logout(integration):
        return {"supported": False, "queued": 0, "since": now}
    with transaction.atomic():
        ProductSignIn.objects.filter(
            integration=integration, user_id=user_id, ended_at__isnull=True
        ).update(ended_at=now)
        BackchannelLogout.objects.create(
            integration=integration,
            user_id=user_id,
            sign_in_id=None,
            reason=REASON_PRODUCT_SIGNED_OUT,
            next_attempt_at=now,
        )
        transaction.on_commit(backchannel._kick_delivery)
    metrics.increment(backchannel.DELIVERIES_TOTAL, outcome="queued")
    logger.info(
        "ecosystem.backchannel.product_sign_out_queued",
        product_code=str(product_code),
        user_id=str(user_id),
    )
    return {"supported": True, "queued": 1, "since": now}


def delivery_for(user_id: Any, product_code: str, *, since: datetime) -> str | None:
    """``delivered`` | ``pending`` | ``failed`` for the newest logout to this
    product queued at or after ``since``; ``None`` when there is none."""
    row = (
        BackchannelLogout.objects.filter(
            user_id=user_id,
            integration__product_code=str(product_code),
            created_at__gte=since,
        )
        .order_by("-created_at")
        .values_list("status", flat=True)
        .first()
    )
    if row is None:
        return None
    if row == BackchannelLogoutStatus.DELIVERED.value:
        return "delivered"
    if row == BackchannelLogoutStatus.PENDING.value:
        return "pending"
    return "failed"


# --- reads -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OrganizationConnection:
    organization_id: UUID
    first_sign_in_at: datetime
    last_sign_in_at: datetime
    sign_in_count: int
    last_activity_at: datetime | None


@dataclass(slots=True)
class ProductConnections:
    """Everything recorded about one person and one product."""

    product_code: str
    display_name: str
    launch_host: str
    domain_verified: bool
    integration_status: str
    takes_backchannel_logout: bool
    organizations: list[OrganizationConnection] = field(default_factory=list)
    #: ``open`` - a WorkEasy360 sign-in that opened this product has not ended;
    #: ``ended`` - every one that did has ended (or was signed out of this
    #: product); ``untracked`` - the product takes no back-channel logout, so
    #: the platform keeps no per-sign-in record for it; ``none`` - no record.
    platform_sign_in: str = "none"
    platform_sign_in_since: datetime | None = None
    platform_sign_in_ended_at: datetime | None = None
    #: The newest back-channel logout to this product for this person.
    last_sign_out: dict[str, Any] | None = None


def _host_of(url: str) -> str:
    from urllib.parse import urlsplit

    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def connections_for_user(
    user_id: Any, *, live_sign_in_ids: set[UUID], product_codes: set[str] = frozenset()
) -> dict[str, ProductConnections]:
    """``{product_code: ProductConnections}`` for every product this person
    has a connection to, plus ``product_codes`` (the products they can use,
    connected or not). A fixed number of queries.

    ``live_sign_in_ids`` are the person's WorkEasy360 sign-ins that are still
    open (``identity.account.live_sign_in_ids``), passed in so this module
    does not decide what a live session is."""
    from workeasy360.platform.ecosystem import backchannel
    from workeasy360.platform.ecosystem.models import ProductDomain, ProductDomainStatus

    connections = list(
        ProductConnection.objects.filter(user_id=user_id)
        .select_related("integration")
        .order_by("integration__product_code", "-last_sign_in_at")
    )
    wanted = {c.integration.product_code for c in connections} | {
        str(c) for c in product_codes
    }
    integrations = {
        i.product_code: i
        for i in ProductIntegration.objects.filter(product_code__in=sorted(wanted))
    }
    verified: dict[Any, set[str]] = {}
    for integration_id, hostname in ProductDomain.objects.filter(
        integration__in=list(integrations.values()),
        status=ProductDomainStatus.VERIFIED.value,
    ).values_list("integration_id", "hostname"):
        verified.setdefault(integration_id, set()).add(hostname)

    found: dict[str, ProductConnections] = {}
    for code, integration in integrations.items():
        manifest = integration.manifest_obj
        host = _host_of(str(manifest.launch_url))
        found[code] = ProductConnections(
            product_code=code,
            display_name=manifest.display_name or code,
            launch_host=host,
            domain_verified=bool(host and host in verified.get(integration.id, set())),
            integration_status=integration.status,
            takes_backchannel_logout=backchannel.takes_backchannel_logout(integration),
        )
    for c in connections:
        entry = found.get(c.integration.product_code)
        if entry is None:  # pragma: no cover - the integration was just read
            continue
        entry.organizations.append(
            OrganizationConnection(
                organization_id=c.organization_id,
                first_sign_in_at=c.first_sign_in_at,
                last_sign_in_at=c.last_sign_in_at,
                sign_in_count=c.sign_in_count,
                last_activity_at=c.last_activity_at,
            )
        )

    sign_ins = ProductSignIn.objects.filter(
        user_id=user_id, integration__product_code__in=sorted(found)
    ).values_list("integration__product_code", "sign_in_id", "created_at", "ended_at")
    for code, sign_in_id, created_at, ended_at in sign_ins:
        entry = found[code]
        if ended_at is None and sign_in_id is not None and sign_in_id in live_sign_in_ids:
            # The WorkEasy360 sign-in that opened this product is still open.
            # Says nothing about the product's own session.
            since = entry.platform_sign_in_since
            entry.platform_sign_in_since = (
                created_at if since is None else min(since, created_at)
            )
            entry.platform_sign_in = "open"
            continue
        if entry.platform_sign_in != "open":
            entry.platform_sign_in = "ended"
        # A row whose sign-in simply expired was never ended explicitly: when
        # it ended is unknown, and is left unknown.
        if ended_at is not None and (
            entry.platform_sign_in_ended_at is None
            or ended_at > entry.platform_sign_in_ended_at
        ):
            entry.platform_sign_in_ended_at = ended_at
    for entry in found.values():
        if entry.platform_sign_in == "none" and not entry.takes_backchannel_logout:
            entry.platform_sign_in = "untracked"

    latest: dict[str, dict[str, Any]] = {}
    for code, status, reason, created_at, finished_at in (
        BackchannelLogout.objects.filter(
            user_id=user_id, integration__product_code__in=sorted(found)
        )
        .order_by("-created_at")
        .values_list(
            "integration__product_code", "status", "reason", "created_at", "finished_at"
        )[:200]
    ):
        if code in latest:
            continue
        latest[code] = {
            "status": "delivered"
            if status == BackchannelLogoutStatus.DELIVERED.value
            else (
                "pending" if status == BackchannelLogoutStatus.PENDING.value else "failed"
            ),
            "requested_at": created_at.isoformat(),
            "finished_at": finished_at.isoformat() if finished_at else None,
            "by_this_product_sign_out": reason == REASON_PRODUCT_SIGNED_OUT,
        }
    for code, delivery in latest.items():
        found[code].last_sign_out = delivery
    return found

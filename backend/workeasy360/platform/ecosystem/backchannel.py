"""OpenID Connect Back-Channel Logout 1.0: who must be told, and the record of
telling them (ADR-067).

Before this, signing out of Workeasy360 ended Workeasy360's own sessions and
nothing else: a product kept serving until its next entitlement re-check, up
to 15 minutes, and "sign out everywhere" did not reach product sessions at
all (ADR-044 consequences). A product that registers a
``backchannel_logout_uri`` (manifest ``2026-11-15``) is now told.

This module **makes no network call** - the ecosystem package is forbidden
outbound HTTP. It does three things:

1. :func:`record_product_sign_in` - at the token endpoint, remember that this
   sign-in opened this product (only for integrations that take back-channel
   logout; nobody else's token or table changes);
2. :class:`SignInsEndedRecorder` - the ``contracts.ports.sign_ins`` listener:
   inside identity's own transaction, turn ended sign-ins into
   :class:`BackchannelLogout` rows (the transactional outbox);
3. the delivery state machine - claim due rows, mint a fresh logout token,
   record the outcome - which ``integration/backchannel`` drives.

Delivery is at-least-once, so a product can receive the same logout twice;
each token has its own ``jti`` and ending an already-ended session is a no-op
on the product side.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from workeasy360.platform.ecosystem.models import (
    BackchannelLogout,
    BackchannelLogoutStatus,
    IntegrationStatus,
    ProductSignIn,
)
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

#: Delay before attempt ``n`` (index ``n - 1``). Fast enough that a product
#: that blinked during a deploy is signed out within a minute; long enough
#: that a product that is down for a day is not hammered. Then: dead.
RETRY_DELAYS_S: tuple[int, ...] = (0, 15, 60, 300, 900, 3600, 6 * 3600, 24 * 3600)
MAX_ATTEMPTS = len(RETRY_DELAYS_S)
#: A claimed row is invisible to other workers for this long.
CLAIM_LEASE_S = 120
#: Integrations that may still receive a logout. A draft has had no sign-ins;
#: a suspended one may still hold sessions that must end; a retired one is gone.
NOTIFIED_STATUSES = (IntegrationStatus.ACTIVE.value, IntegrationStatus.SUSPENDED.value)

DELIVERIES_TOTAL = "ecosystem_backchannel_logouts_total"


def takes_backchannel_logout(integration: Any) -> bool:
    return integration.manifest_obj.takes_backchannel_logout


def sid_for(integration: Any, sign_in_id: UUID | None) -> str:
    """The ``sid`` claim for this integration's ID token, or ``""``."""
    if sign_in_id is None or not takes_backchannel_logout(integration):
        return ""
    return str(sign_in_id)


# --- 1. at the token endpoint ------------------------------------------------------


def record_product_sign_in(
    *, integration: Any, user_id: UUID, sign_in_id: UUID | None
) -> None:
    """Remember that this sign-in opened this product. Idempotent per sign-in."""
    if not takes_backchannel_logout(integration):
        return
    now = timezone.now()
    with transaction.atomic():
        updated = ProductSignIn.objects.filter(
            integration=integration,
            user_id=user_id,
            sign_in_id=sign_in_id,
            ended_at__isnull=True,
        ).update(last_seen_at=now)
        if not updated:
            ProductSignIn.objects.create(
                integration=integration,
                user_id=user_id,
                sign_in_id=sign_in_id,
                created_at=now,
                last_seen_at=now,
            )


# --- 2. when sign-ins end (inside identity's transaction) ---------------------------


class SignInsEndedRecorder:
    """The ``SignInsEndedListener`` the ecosystem registers at app-ready."""

    def sign_ins_ended(
        self,
        *,
        user_id: UUID,
        sign_in_ids: tuple[UUID, ...],
        everywhere: bool,
        reason: str,
    ) -> None:
        record_sign_ins_ended(
            user_id=user_id, sign_in_ids=sign_in_ids, everywhere=everywhere, reason=reason
        )


def record_sign_ins_ended(
    *, user_id: UUID, sign_in_ids: tuple[UUID, ...], everywhere: bool, reason: str
) -> int:
    """Queue one logout per affected product. Returns how many were queued.

    ``everywhere``: one token per product the person is signed in to, naming
    only ``sub`` - the product ends every session of that person. Otherwise one
    token per (product, sign-in), naming ``sid`` too - the product ends only
    the sessions that sign-in opened, so a second device stays signed in.
    """
    live = ProductSignIn.objects.select_for_update().filter(
        user_id=user_id,
        ended_at__isnull=True,
        integration__status__in=NOTIFIED_STATUSES,
    )
    if not everywhere:
        live = live.filter(sign_in_id__in=sign_in_ids)
    rows = list(live.select_related("integration"))
    if not rows:
        return 0

    now = timezone.now()
    deliveries: list[BackchannelLogout] = []
    if everywhere:
        for integration in {row.integration_id: row.integration for row in rows}.values():
            if takes_backchannel_logout(integration):
                deliveries.append(
                    BackchannelLogout(
                        integration=integration,
                        user_id=user_id,
                        sign_in_id=None,
                        reason=reason,
                        next_attempt_at=now,
                    )
                )
    else:
        for row in rows:
            if takes_backchannel_logout(row.integration):
                deliveries.append(
                    BackchannelLogout(
                        integration=row.integration,
                        user_id=user_id,
                        sign_in_id=row.sign_in_id,
                        reason=reason,
                        next_attempt_at=now,
                    )
                )
    ProductSignIn.objects.filter(pk__in=[r.pk for r in rows]).update(ended_at=now)
    BackchannelLogout.objects.bulk_create(deliveries)
    metrics.increment(DELIVERIES_TOTAL, float(len(deliveries)), outcome="queued")
    logger.info(
        "ecosystem.backchannel.queued",
        user_id=str(user_id),
        deliveries=len(deliveries),
        everywhere=everywhere,
        reason=reason,
    )
    transaction.on_commit(_kick_delivery)
    return len(deliveries)


def _kick_delivery() -> None:
    """Ask a worker to deliver now, by task *name* (no import of integration/).

    Best effort: if the broker is unreachable the beat sweep delivers within
    one interval, because the rows - not this message - are the record.
    """
    try:
        from celery import current_app

        task = current_app.tasks.get("integration.backchannel.deliver_due")
        if task is not None and current_app.conf.task_always_eager:
            task.apply()  # development / single-process harnesses
            return
        current_app.send_task(
            "integration.backchannel.deliver_due", queue="default", expires=60
        )
    except Exception:
        logger.warning("ecosystem.backchannel.kick_failed", exc_info=True)


# --- 3. the delivery state machine ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class DueDelivery:
    id: UUID
    url: str
    client_id: str
    product_code: str
    user_id: UUID
    sid: str
    attempt: int


def claim_due(*, limit: int = 50) -> list[DueDelivery]:
    """Lease up to ``limit`` due deliveries to this worker.

    ``SKIP LOCKED`` lets several workers sweep at once without delivering the
    same row twice in one round; moving ``next_attempt_at`` forward by the
    lease is what keeps a crashed worker's rows from being lost.
    """
    now = timezone.now()
    claimed: list[DueDelivery] = []
    with transaction.atomic():
        rows = list(
            BackchannelLogout.objects.select_for_update(skip_locked=True)
            .select_related("integration")
            .filter(
                status=BackchannelLogoutStatus.PENDING.value, next_attempt_at__lte=now
            )
            .order_by("next_attempt_at")[:limit]
        )
        for row in rows:
            manifest = row.integration.manifest_obj
            row.attempts += 1
            row.next_attempt_at = now + timedelta(seconds=CLAIM_LEASE_S)
            if not manifest.backchannel_logout_uri:
                # Re-registered without the URI since this was queued.
                row.status = BackchannelLogoutStatus.FAILED.value
                row.last_outcome = "no_backchannel_uri"
                row.finished_at = now
                row.save(
                    update_fields=[
                        "attempts",
                        "next_attempt_at",
                        "status",
                        "last_outcome",
                        "finished_at",
                    ]
                )
                continue
            row.save(update_fields=["attempts", "next_attempt_at"])
            claimed.append(
                DueDelivery(
                    id=row.id,
                    url=manifest.backchannel_logout_uri,
                    client_id=row.integration.client_id,
                    product_code=row.integration.product_code,
                    user_id=row.user_id,
                    sid=str(row.sign_in_id) if row.sign_in_id else "",
                    attempt=row.attempts,
                )
            )
    return claimed


def logout_token_for(delivery: DueDelivery) -> str:
    """A fresh logout token for this attempt. Never stored, never logged."""
    from workeasy360.platform.ecosystem import tokens

    return tokens.issue_logout_token(
        client_id=delivery.client_id, user_id=delivery.user_id, sid=delivery.sid
    ).value


def record_delivered(delivery: DueDelivery, *, outcome: str) -> None:
    _finish(delivery, status=BackchannelLogoutStatus.DELIVERED, outcome=outcome)


def record_refused(delivery: DueDelivery, *, outcome: str) -> None:
    """The product answered and said no, or the address is not callable."""
    _finish(delivery, status=BackchannelLogoutStatus.FAILED, outcome=outcome)
    logger.warning(
        "ecosystem.backchannel.refused",
        product_code=delivery.product_code,
        outcome=outcome,
    )


def record_transient_failure(delivery: DueDelivery, *, outcome: str) -> None:
    now = timezone.now()
    if delivery.attempt >= MAX_ATTEMPTS:
        _finish(delivery, status=BackchannelLogoutStatus.DEAD, outcome=outcome)
        logger.error(
            "ecosystem.backchannel.dead",
            product_code=delivery.product_code,
            outcome=outcome,
            attempts=delivery.attempt,
        )
        return
    delay = RETRY_DELAYS_S[delivery.attempt]
    BackchannelLogout.objects.filter(
        pk=delivery.id, status=BackchannelLogoutStatus.PENDING.value
    ).update(next_attempt_at=now + timedelta(seconds=delay), last_outcome=outcome[:64])
    metrics.increment(DELIVERIES_TOTAL, outcome="retry")


def _finish(
    delivery: DueDelivery, *, status: BackchannelLogoutStatus, outcome: str
) -> None:
    BackchannelLogout.objects.filter(
        pk=delivery.id, status=BackchannelLogoutStatus.PENDING.value
    ).update(status=status.value, last_outcome=outcome[:64], finished_at=timezone.now())
    metrics.increment(DELIVERIES_TOTAL, outcome=status.value)


# --- read side: what operators and the person see ------------------------------------


def delivery_summary(integration: Any, *, since_hours: int = 24) -> dict[str, Any]:
    """Counts per status over the window, and the last non-success outcome."""
    since = timezone.now() - timedelta(hours=since_hours)
    rows = BackchannelLogout.objects.filter(
        integration=integration, created_at__gte=since
    )
    counts = {s.value: 0 for s in BackchannelLogoutStatus}
    for status in rows.values_list("status", flat=True):
        counts[status] = counts.get(status, 0) + 1
    last_problem = (
        rows.exclude(status=BackchannelLogoutStatus.DELIVERED.value)
        .exclude(last_outcome="")
        .order_by("-created_at")
        .values_list("last_outcome", flat=True)
        .first()
    )
    return {
        "enabled": takes_backchannel_logout(integration),
        "window_hours": since_hours,
        "counts": counts,
        "last_problem": last_problem or "",
    }


def pending_for_user(user_id: UUID) -> list[dict[str, Any]]:
    """Product sign-outs still being delivered for this person, newest first.

    What "sign out everywhere" shows instead of claiming it finished: products
    that have not yet confirmed are listed by name, never hidden.
    """
    since = timezone.now() - timedelta(hours=24)
    rows = (
        BackchannelLogout.objects.filter(user_id=user_id, created_at__gte=since)
        .select_related("integration")
        .order_by("-created_at")[:50]
    )
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = row.integration.product_code
        if code in latest:
            continue
        latest[code] = {
            "product_code": code,
            "display_name": row.integration.manifest_obj.display_name or code,
            "status": row.status,
            "attempts": row.attempts,
            "requested_at": row.created_at.isoformat(),
        }
    return list(latest.values())


def purge_finished(*, older_than_days: int = 30) -> int:
    cutoff = timezone.now() - timedelta(days=older_than_days)
    deleted, _ = BackchannelLogout.objects.filter(finished_at__lt=cutoff).delete()
    ended, _ = ProductSignIn.objects.filter(ended_at__lt=cutoff).delete()
    return deleted + ended

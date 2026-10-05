"""A notification is sent outside any transaction, under a committed lease
(Phase 12, final audit).

The G-30 claim held the row lock *and an open transaction* across the SMTP
send. With ``idle_in_transaction_session_timeout`` (15 s) a slow provider ended
the session: the SENT update and ``attempts + 1`` rolled back, and the sweeper
sent the email again, without end. Now: claim (commit) -> send -> record.
"""

from __future__ import annotations

from datetime import timedelta

import psycopg
import pytest
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.db import connection
from django.utils import timezone

from workeasy360.contracts.enums import DeliveryStatus, NotificationChannel
from workeasy360.contracts.ports.notifications import DeliveryResult, NotificationRequest
from workeasy360.platform.notifications import channels, services
from workeasy360.platform.notifications.models import NotificationDelivery

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.concurrency]


def _row_seen_by_another_worker(org_id, delivery_id) -> tuple[str, int]:
    """The row as a second session (the runtime role) sees it: committed state only."""
    db = connection.settings_dict
    with (
        psycopg.connect(
            host=db["HOST"],
            port=db["PORT"],
            dbname=db["NAME"],
            user=settings.W360_TEST_APP_USER,
            password=settings.W360_TEST_APP_PASSWORD,
        ) as other,
        other.cursor() as cur,
    ):
        cur.execute("SELECT set_config('app.organization_id', %s, true)", [str(org_id)])
        cur.execute(
            "SELECT status, attempts FROM notifications_delivery WHERE id = %s",
            [str(delivery_id)],
        )
        status, attempts = cur.fetchone()
        return status, attempts


class ObservingEmail:
    channel = NotificationChannel.EMAIL

    def __init__(self, org_id, behaviour="ok") -> None:
        self.org_id = org_id
        self.behaviour = behaviour
        self.calls: list[dict] = []

    def send(self, request, rendered):
        self.calls.append(
            {
                "in_transaction": connection.in_atomic_block,
                "committed": _row_seen_by_another_worker(self.org_id, self.delivery_id),
            }
        )
        if self.behaviour == "budget":
            raise SoftTimeLimitExceeded()
        return DeliveryResult(success=True, provider_message_id="m-1")

    def health(self):
        return True


def as_task(ctx):
    """What ``TenantTask`` gives a task: the tenant context, no transaction and
    no database scope (``attempt_delivery`` enters its own, per step)."""
    from workeasy360.platform.tenancy.context import tenant_context

    return tenant_context(ctx)


@pytest.fixture
def queued(two_tenants, in_tenant, monkeypatch):
    from workeasy360.platform.notifications import tasks

    monkeypatch.setattr(tasks.deliver_notification, "apply_async", lambda *a, **k: None)
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        notification_id = services.notify(
            NotificationRequest(
                organization_id=a["org"].id,
                template_key="platform.organization.suspended",
                recipient_membership_id=a["membership"].id,
                channels=(NotificationChannel.EMAIL,),
                context={"organization_name": "Acme"},
            )
        )
        delivery_id = NotificationDelivery.objects.get(notification_id=notification_id).id
    return a, delivery_id


def test_the_provider_call_runs_outside_any_transaction_after_the_claim_commits(
    queued, in_tenant, monkeypatch
):
    a, delivery_id = queued
    email = ObservingEmail(a["org"].id)
    email.delivery_id = delivery_id
    monkeypatch.setitem(channels._channels, "email", email)

    with as_task(a["ctx"]):
        status = services.attempt_delivery(delivery_id, organization_id=a["org"].id)

    assert status == DeliveryStatus.SENT.value
    [call] = email.calls
    assert call["in_transaction"] is False
    # Another worker already sees the claim while the provider is being called.
    assert call["committed"] == (DeliveryStatus.SENDING.value, 1)
    assert _row_seen_by_another_worker(a["org"].id, delivery_id) == (
        DeliveryStatus.SENT.value,
        1,
    )


def test_a_worker_that_dies_mid_send_leaves_a_lease_then_the_attempt_counts(
    queued, in_tenant, monkeypatch
):
    a, delivery_id = queued
    dying = ObservingEmail(a["org"].id, behaviour="budget")
    dying.delivery_id = delivery_id
    monkeypatch.setitem(channels._channels, "email", dying)

    with as_task(a["ctx"]), pytest.raises(SoftTimeLimitExceeded):
        services.attempt_delivery(delivery_id, organization_id=a["org"].id)
    assert _row_seen_by_another_worker(a["org"].id, delivery_id) == (
        DeliveryStatus.SENDING.value,
        1,
    )

    # While the lease runs, a duplicate (the sweeper's copy) sends nothing.
    healthy = ObservingEmail(a["org"].id)
    healthy.delivery_id = delivery_id
    monkeypatch.setitem(channels._channels, "email", healthy)
    with as_task(a["ctx"]):
        assert (
            services.attempt_delivery(delivery_id, organization_id=a["org"].id)
            == "in_progress"
        )
    assert healthy.calls == []

    # Once it is over, the delivery is retried, and the first attempt counted.
    with in_tenant(a["ctx"]):
        NotificationDelivery.objects.filter(id=delivery_id).update(
            available_at=timezone.now() - timedelta(seconds=1)
        )
    with as_task(a["ctx"]):
        assert (
            services.attempt_delivery(delivery_id, organization_id=a["org"].id)
            == DeliveryStatus.SENT.value
        )
    assert len(healthy.calls) == 1
    assert _row_seen_by_another_worker(a["org"].id, delivery_id) == (
        DeliveryStatus.SENT.value,
        2,
    )


def test_an_expired_claim_is_swept_again(queued, in_tenant, monkeypatch):
    from workeasy360.platform.notifications import tasks

    a, delivery_id = queued
    dispatched: list[str] = []
    monkeypatch.setattr(
        tasks.deliver_notification,
        "apply_async",
        lambda args=None, **k: dispatched.append(args[0]),
    )
    with in_tenant(a["ctx"]):
        NotificationDelivery.objects.filter(id=delivery_id).update(
            status=DeliveryStatus.SENDING.value,
            attempts=1,
            available_at=timezone.now() + timedelta(minutes=5),
        )
    tasks.sweep_pending_deliveries()
    assert str(delivery_id) not in dispatched  # a live claim is left alone

    with in_tenant(a["ctx"]):
        NotificationDelivery.objects.filter(id=delivery_id).update(
            available_at=timezone.now() - timedelta(seconds=1)
        )
    tasks.sweep_pending_deliveries()
    assert str(delivery_id) in dispatched


def test_an_overtaken_attempt_never_overwrites_the_later_one(queued, in_tenant):
    a, delivery_id = queued
    with in_tenant(a["ctx"]):
        # Attempt 1's lease ran out; attempt 2 took the row and finished it.
        NotificationDelivery.objects.filter(id=delivery_id).update(
            status=DeliveryStatus.SENT.value, attempts=2
        )
        status = services._record(
            delivery_id,
            a["org"].id,
            1,
            DeliveryResult(success=False, error="late failure", retryable=True),
        )
    assert status == DeliveryStatus.SENT.value
    assert _row_seen_by_another_worker(a["org"].id, delivery_id) == (
        DeliveryStatus.SENT.value,
        2,
    )

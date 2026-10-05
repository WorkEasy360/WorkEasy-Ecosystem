"""A delivery is sent at most once by concurrent workers (Phase 12, G-30).

The sweeper re-dispatches every due PENDING/FAILED delivery, so the original
task and a sweeper copy can run at the same time. Both used to read the row,
pass the "not terminal" check and send. The row is now claimed with
``FOR UPDATE SKIP LOCKED`` for the whole attempt.
"""

from __future__ import annotations

import psycopg
import pytest
from django.conf import settings
from django.db import connection

from workeasy360.contracts.enums import DeliveryStatus, NotificationChannel
from workeasy360.contracts.ports.notifications import DeliveryResult, NotificationRequest
from workeasy360.platform.notifications import channels, services
from workeasy360.platform.notifications.models import NotificationDelivery

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.concurrency]


class CountingEmail:
    channel = NotificationChannel.EMAIL

    def __init__(self) -> None:
        self.sent = 0

    def send(self, request, rendered):
        self.sent += 1
        return DeliveryResult(success=True, provider_message_id=f"m-{self.sent}")

    def health(self):
        return True


def _app_connection():
    """A second session as the runtime role, as another worker would hold."""
    db = connection.settings_dict
    return psycopg.connect(
        host=db["HOST"],
        port=db["PORT"],
        dbname=db["NAME"],
        user=settings.W360_TEST_APP_USER,
        password=settings.W360_TEST_APP_PASSWORD,
        autocommit=False,
    )


def test_a_delivery_locked_by_another_worker_is_skipped_not_sent_twice(
    two_tenants, in_tenant, monkeypatch
):
    from workeasy360.platform.notifications import tasks

    email = CountingEmail()
    monkeypatch.setitem(channels._channels, "email", email)
    # Leave the delivery queued: the test plays both workers itself.
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

    other = _app_connection()
    try:
        with other.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.organization_id', %s, true)", [str(a["org"].id)]
            )
            cur.execute(
                "SELECT id FROM notifications_delivery WHERE id = %s FOR UPDATE",
                [str(delivery_id)],
            )
            assert cur.fetchone() is not None
        with in_tenant(a["ctx"]):
            assert services.attempt_delivery(delivery_id) == "in_progress"
        assert email.sent == 0
    finally:
        other.rollback()
        other.close()

    with in_tenant(a["ctx"]):
        assert services.attempt_delivery(delivery_id) == DeliveryStatus.SENT.value
        # A late duplicate reads the committed terminal state.
        assert services.attempt_delivery(delivery_id) == DeliveryStatus.SENT.value
    assert email.sent == 1


@pytest.mark.django_db
def test_expired_pending_authorizations_are_pruned_by_the_scheduled_task(
    signing_key, register_product
):
    """Phase 12 (G-33): the prune existed and nothing called it; every
    anonymous /authorize hit parks a row."""
    from datetime import timedelta

    from django.utils import timezone

    from workeasy360.platform.ecosystem.models import (
        PendingAuthorization,
        ProductIntegration,
    )
    from workeasy360.platform.ecosystem.tasks import prune_authorization_codes

    registration = register_product("crm")
    integration = ProductIntegration.objects.get(client_id=registration["client_id"])
    old = timezone.now() - timedelta(hours=3)
    row = {
        "integration": integration,
        "redirect_uri": registration["redirect_uri"],
        "state": "s",
        "scope": "openid",
        "code_challenge": "c" * 43,
        "code_challenge_method": "S256",
        "nonce": "n",
        "expires_at": old,
        "token_hash": "a" * 64,
    }
    PendingAuthorization.objects.create(**row)
    assert prune_authorization_codes() >= 1
    assert not PendingAuthorization.objects.exists()


def test_a_sweep_that_outruns_its_budget_stops_instead_of_swallowing_it(monkeypatch):
    """Phase 12 (G-32)."""
    import uuid

    from celery.exceptions import SoftTimeLimitExceeded

    from workeasy360.commercial.subscriptions import selectors, system
    from workeasy360.platform.organizations import selectors as organizations

    monkeypatch.setattr(organizations, "all_organization_ids", lambda: [uuid.uuid4()])

    def budget_spent(*args, **kwargs):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(selectors, "subscriptions_due_for_attention", budget_spent)
    with pytest.raises(SoftTimeLimitExceeded):
        system.process_due_subscriptions()

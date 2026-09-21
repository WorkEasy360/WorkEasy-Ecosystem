"""Notifications: never inline, bounded retries, failures recorded not raised."""

from __future__ import annotations

import pytest

from workeasy360.contracts.enums import DeliveryStatus, NotificationChannel
from workeasy360.contracts.ports.notifications import NotificationRequest
from workeasy360.platform.notifications import services
from workeasy360.platform.notifications.models import (
    Notification,
    NotificationDelivery,
)

pytestmark = pytest.mark.django_db


def _request(org_id, membership_id, **kwargs):
    return NotificationRequest(
        organization_id=org_id,
        template_key=kwargs.pop("template_key", "platform.organization.suspended"),
        recipient_membership_id=membership_id,
        channels=kwargs.pop("channels", (NotificationChannel.EMAIL,)),
        context=kwargs.pop("context", {"organization_name": "Acme"}),
        **kwargs,
    )


# --- intent is persisted, delivery is deferred ---------------------------


def test_notify_persists_intent_and_queues_delivery(two_tenants, in_tenant):
    """Persist intent, queue delivery, return. Nothing is sent inline."""
    a = two_tenants["a"]

    with in_tenant(a["ctx"]):
        notification_id = services.notify(_request(a["org"].id, a["membership"].id))
        notification = Notification.objects.get(id=notification_id)
        deliveries = list(NotificationDelivery.objects.filter(notification=notification))

    assert notification.template_key == "platform.organization.suspended"
    assert len(deliveries) == 1
    assert deliveries[0].channel == NotificationChannel.EMAIL.value


def test_a_provider_outage_does_not_fail_the_caller(two_tenants, in_tenant, monkeypatch):
    """An email provider outage must never become request latency, and must
    never fail the business transaction that triggered the notification
    (09-failure-resilience.md section 3.3)."""
    from workeasy360.platform.notifications import channels

    a = two_tenants["a"]

    class BrokenEmail:
        channel = NotificationChannel.EMAIL

        def send(self, request, rendered):
            raise ConnectionError("provider is down")

        def health(self):
            return False

    monkeypatch.setitem(channels._channels, "email", BrokenEmail())

    with in_tenant(a["ctx"]):
        # notify() succeeds regardless: the provider is not on this path.
        notification_id = services.notify(_request(a["org"].id, a["membership"].id))
        assert Notification.objects.filter(id=notification_id).exists()


def test_broker_outage_leaves_delivery_recoverable(two_tenants, in_tenant, monkeypatch):
    """If the broker is down the delivery stays pending and the sweeper picks
    it up. Failing the user's action because a Tier-3 notification could not
    be *queued* would invert the dependency tiers."""
    from workeasy360.platform.notifications import tasks

    a = two_tenants["a"]

    def explode(*args, **kwargs):
        raise ConnectionError("broker unreachable")

    monkeypatch.setattr(tasks.deliver_notification, "delay", explode)

    with in_tenant(a["ctx"]):
        notification_id = services.notify(_request(a["org"].id, a["membership"].id))
        delivery = NotificationDelivery.objects.get(notification_id=notification_id)

    assert delivery.status == DeliveryStatus.PENDING.value


# --- idempotency ----------------------------------------------------------


def test_duplicate_idempotency_key_does_not_send_twice(two_tenants, in_tenant):
    """A key derived from durable state makes a retry a retry, rather than a
    second notification."""
    a = two_tenants["a"]

    with in_tenant(a["ctx"]):
        first = services.notify(
            _request(a["org"].id, a["membership"].id, idempotency_key="org:suspended:1")
        )
        second = services.notify(
            _request(a["org"].id, a["membership"].id, idempotency_key="org:suspended:1")
        )
        assert first == second
        assert Notification.objects.count() == 1


def test_different_keys_produce_different_notifications(two_tenants, in_tenant):
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        services.notify(_request(a["org"].id, a["membership"].id, idempotency_key="a"))
        services.notify(_request(a["org"].id, a["membership"].id, idempotency_key="b"))
        assert Notification.objects.count() == 2


# --- delivery outcomes ----------------------------------------------------


def test_successful_delivery_is_recorded(two_tenants, in_tenant):
    a = two_tenants["a"]

    with in_tenant(a["ctx"]):
        notification_id = services.notify(_request(a["org"].id, a["membership"].id))
        delivery = NotificationDelivery.objects.get(notification_id=notification_id)
        status = services.attempt_delivery(delivery.id)
        delivery.refresh_from_db()

    assert status == DeliveryStatus.SENT.value
    assert delivery.sent_at is not None
    assert delivery.attempts == 1


def test_permanent_failure_dead_letters_without_retrying(
    two_tenants, in_tenant, monkeypatch
):
    """Retrying a permanent failure five times is wasted work and alert noise.

    Transient vs permanent is decided by exception type at the adapter, never
    by string-matching an error message.
    """
    from workeasy360.contracts.ports.notifications import DeliveryResult
    from workeasy360.platform.notifications import channels

    a = two_tenants["a"]

    class PermanentlyBroken:
        channel = NotificationChannel.EMAIL

        def send(self, request, rendered):
            return DeliveryResult(
                success=False, error="mailbox does not exist", retryable=False
            )

        def health(self):
            return True

    monkeypatch.setitem(channels._channels, "email", PermanentlyBroken())

    with in_tenant(a["ctx"]):
        notification_id = services.notify(_request(a["org"].id, a["membership"].id))
        delivery = NotificationDelivery.objects.get(notification_id=notification_id)
        services.attempt_delivery(delivery.id)
        delivery.refresh_from_db()

    assert delivery.status == DeliveryStatus.DEAD_LETTERED.value
    assert delivery.attempts == 1


def test_transient_failure_schedules_a_bounded_retry(
    two_tenants, in_tenant, monkeypatch, settings
):
    from workeasy360.contracts.ports.notifications import DeliveryResult
    from workeasy360.platform.notifications import channels

    settings.NOTIFICATION_MAX_ATTEMPTS = 3
    a = two_tenants["a"]

    class Flaky:
        channel = NotificationChannel.EMAIL

        def send(self, request, rendered):
            return DeliveryResult(success=False, error="timeout", retryable=True)

        def health(self):
            return True

    monkeypatch.setitem(channels._channels, "email", Flaky())

    with in_tenant(a["ctx"]):
        notification_id = services.notify(_request(a["org"].id, a["membership"].id))
        delivery = NotificationDelivery.objects.get(notification_id=notification_id)

        for _ in range(3):
            services.attempt_delivery(delivery.id)
            delivery.refresh_from_db()
            delivery.available_at = delivery.created_at  # skip the backoff
            delivery.status = DeliveryStatus.FAILED.value
            delivery.save(update_fields=["available_at", "status"])

        services.attempt_delivery(delivery.id)
        delivery.refresh_from_db()

    assert delivery.attempts <= settings.NOTIFICATION_MAX_ATTEMPTS + 1
    assert delivery.status == DeliveryStatus.DEAD_LETTERED.value


def test_unknown_template_fails_permanently(two_tenants, in_tenant):
    """No number of retries will invent a template."""
    a = two_tenants["a"]

    with in_tenant(a["ctx"]):
        notification_id = services.notify(
            _request(a["org"].id, a["membership"].id, template_key="does.not.exist")
        )
        delivery = NotificationDelivery.objects.get(notification_id=notification_id)
        services.attempt_delivery(delivery.id)
        delivery.refresh_from_db()

    assert delivery.status == DeliveryStatus.DEAD_LETTERED.value


# --- templates ------------------------------------------------------------


def test_templates_are_pre_registered_not_supplied_at_call_time():
    """A user-controlled template string is a server-side template injection
    primitive (12-security.md section 7)."""
    from workeasy360.platform.notifications.templates import (
        UnknownTemplate,
        registered_keys,
        render,
    )

    assert registered_keys()
    with pytest.raises(UnknownTemplate):
        render("{{ evil }}", {})


def test_template_missing_variable_is_a_clear_error():
    """Better than the literal string "None" arriving in a customer inbox."""
    from workeasy360.platform.notifications.templates import render

    with pytest.raises(ValueError, match="missing context variables"):
        render("platform.organization.suspended", {})


# --- isolation ------------------------------------------------------------


def test_notifications_are_tenant_scoped(two_tenants, in_tenant):
    a, b = two_tenants["a"], two_tenants["b"]

    with in_tenant(a["ctx"]):
        services.notify(_request(a["org"].id, a["membership"].id))
    with in_tenant(b["ctx"]):
        services.notify(_request(b["org"].id, b["membership"].id))
        assert Notification.objects.count() == 1


def test_in_app_channel_works_when_email_is_down(two_tenants, in_tenant, monkeypatch):
    """The degradation matrix's "in-app only" row: an email outage still
    leaves the customer informed."""
    from workeasy360.platform.notifications import channels

    a = two_tenants["a"]

    class BrokenEmail:
        channel = NotificationChannel.EMAIL

        def send(self, request, rendered):
            raise ConnectionError("down")

        def health(self):
            return False

    monkeypatch.setitem(channels._channels, "email", BrokenEmail())

    with in_tenant(a["ctx"]):
        notification_id = services.notify(
            _request(
                a["org"].id,
                a["membership"].id,
                channels=(NotificationChannel.EMAIL, NotificationChannel.IN_APP),
            )
        )
        deliveries = {
            d.channel: d
            for d in NotificationDelivery.objects.filter(notification_id=notification_id)
        }
        in_app_status = services.attempt_delivery(deliveries["in_app"].id)

    assert in_app_status == DeliveryStatus.SENT.value

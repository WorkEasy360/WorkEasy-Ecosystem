"""Failure injection for the Phase 8 extension: a broker that is down when a
payment starts, rotated and lost seal keys, a secret that is not configured,
a notice that cannot be queued."""

from __future__ import annotations

import uuid

import pytest

from tests.extension.conftest import buy_directly, url
from tests.extension.test_payments import (  # noqa: F401
    _pay,
    _status,
    paying_org,
    provider_server,
    providers,
)

pytestmark = pytest.mark.django_db


def test_a_payment_started_during_a_broker_outage_is_picked_up_by_the_sweeper(
    paying_org,  # noqa: F811
    providers,  # noqa: F811
):
    """The request records the attempt and a due-work timer in its own
    transaction; losing the after-commit enqueue loses latency, never the
    checkout (the sweeper dispatches the timer)."""
    from workeasy360.integration.scheduling import services as scheduling
    from workeasy360.integration.scheduling.models import DueWork

    attempt = _pay(paying_org)
    assert DueWork.objects.filter(
        kind="payments.checkout", target_id=attempt["attempt_id"]
    ).exists()
    assert _status(paying_org, attempt)["status"] == "pending"

    scheduling.dispatch(scheduling.claim_due())  # eager in tests: runs the task
    assert _status(paying_org, attempt)["status"] == "awaiting_payment"
    assert not DueWork.objects.filter(
        kind="payments.checkout", target_id=attempt["attempt_id"]
    ).exists()


def test_seal_keys_rotate_and_a_lost_key_fails_closed(settings):
    import base64
    import hashlib

    from workeasy360.platform.notifications import sealing

    def key(material):
        return base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest())

    org, notification = uuid.uuid4(), uuid.uuid4()
    settings.NOTIFICATION_SEAL_KEYS = [key("old-key-material-0123456789abcdef")]
    token = sealing.seal(
        {"invitation_url": "https://x/#token=secret"},
        organization_id=org,
        notification_id=notification,
    )
    assert "secret" not in token

    settings.NOTIFICATION_SEAL_KEYS = [
        key("new-key-material-0123456789abcdef"),
        key("old-key-material-0123456789abcdef"),
    ]
    assert sealing.open_sealed(
        token, organization_id=org, notification_id=notification
    ) == {"invitation_url": "https://x/#token=secret"}
    with pytest.raises(sealing.SealedContextUnavailable):
        sealing.open_sealed(
            token, organization_id=org, notification_id=uuid.uuid4()
        )  # copied row
    settings.NOTIFICATION_SEAL_KEYS = [key("new-key-material-0123456789abcdef")]
    with pytest.raises(sealing.SealedContextUnavailable):
        sealing.open_sealed(token, organization_id=org, notification_id=notification)


def test_an_undeliverable_sealed_notification_is_dead_lettered_and_wiped(
    console_org, login, settings
):
    import base64
    import hashlib

    from django.core import mail

    from tests.extension.test_invitations import _deliver_all, _notifications

    org = console_org()
    login(org["owner"]).post(
        url(org["slug"], "invitations"),
        {"email": "x@example.com"},
        content_type="application/json",
    )
    settings.NOTIFICATION_SEAL_KEYS = [
        base64.urlsafe_b64encode(
            hashlib.sha256(b"a key nobody sealed with at all").digest()
        )
    ]
    _deliver_all(org["org"].id)
    assert mail.outbox == []  # never sent half-rendered
    (notification,) = _notifications(org["org"].id)
    assert notification.sealed_context == ""
    from workeasy360.platform.notifications.models import NotificationDelivery
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="Test reads the delivery outcome", module="tests"),
        organization_db_scope(org["org"].id),
    ):
        delivery = NotificationDelivery.unscoped.get(notification_id=notification.id)
    assert (
        delivery.status == "dead_lettered" and "secret" not in delivery.last_error.lower()
    )


def test_a_webhook_with_no_configured_secret_is_refused(
    paying_org,  # noqa: F811
    providers,  # noqa: F811
    client,
    monkeypatch,
):
    monkeypatch.delenv("W360_PAYMENT_WEBHOOK_SECRET_STRIPE_TEST")
    response = client.post(
        "/api/v1/public/payments/webhooks/stripe-uk",
        data=b"{}",
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE="t=1,v1=00",
    )
    assert (response.status_code, response.json()["code"]) == (400, "webhook_rejected")


def test_a_notice_that_cannot_be_queued_never_fails_the_invoice(
    console_org, login, catalog, monkeypatch
):
    from workeasy360.platform.notifications import services

    def broken(*_args, **_kwargs):
        raise RuntimeError("notification store unavailable")

    monkeypatch.setattr(services, "notify", broken)
    org = console_org()
    owner = login(org["owner"], aal2=True)
    buy_directly(org, catalog["versions"]["crm_starter"].id)
    assert len(owner.get(url(org["slug"], "billing/invoices")).json()["results"]) == 1


def test_an_invoicing_failure_never_fails_the_purchase_and_the_clock_repairs_it(
    console_org, login, catalog, monkeypatch
):
    from workeasy360.commercial.payments import invoicing
    from workeasy360.commercial.payments.dunning import process_due

    real = invoicing.ensure_invoices
    monkeypatch.setattr(
        invoicing,
        "ensure_invoices",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db blip")),
    )
    org = console_org()
    owner = login(org["owner"], aal2=True)
    buy_directly(org, catalog["versions"]["crm_starter"].id)
    assert owner.get(url(org["slug"], "billing/invoices")).json()["results"] == []

    monkeypatch.setattr(invoicing, "ensure_invoices", real)
    from workeasy360.commercial.payments import dunning

    monkeypatch.setattr(dunning, "ensure_invoices", real)
    process_due()
    assert len(owner.get(url(org["slug"], "billing/invoices")).json()["results"]) == 1

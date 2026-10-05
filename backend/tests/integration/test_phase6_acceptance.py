"""The Phase 6 acceptance proof (Task 93), end to end over HTTP.

An independently developed product - the reference client, which imports
nothing from ``workeasy360`` - authenticates with its Phase 3 credential and
publishes its own namespaced event. An organization owner has configured a
webhook connection and an automation through the customer API. The event is
accepted exactly once, fans out after commit, and a real loopback receiver -
which also imports nothing from ``workeasy360`` - gets a signed request it
verifies with the secret it was shown once.

``transaction=True`` so every commit is real: the after-commit enqueue fires,
the eager task runs, and the deliveries genuinely traverse the egress client
to a socket. Nothing on the delivery path is mocked.
"""

from __future__ import annotations

import uuid

import pytest
from django.utils import timezone

from tests.factories.webhook_receiver import verify_signature

pytestmark = [pytest.mark.django_db(transaction=True)]

EVENT_REF = "product.example.record.created.v1"


def _api(org):
    return f"/api/v1/orgs/{org['org'].slug}"


def _in_org(organization_id):
    """Both isolation layers for reading an organization's rows in a test."""
    from contextlib import ExitStack

    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    stack = ExitStack()
    stack.enter_context(unscoped_access(reason="acceptance assertion", module="tests"))
    stack.enter_context(organization_db_scope(organization_id))
    return stack


def _deliveries(organization_id):
    from workeasy360.integration.webhooks.models import Delivery

    with _in_org(organization_id):
        return list(
            Delivery.unscoped.filter(organization_id=organization_id)
            .order_by("created_at")
            .values("id", "status", "origin", "attempts", "dead_reason", "replay_of_id")
        )


def _fast_forward(organization_id):
    """Make every retry due now - the clock, not the logic, is what we skip."""
    from workeasy360.integration.scheduling.models import DueWork
    from workeasy360.integration.webhooks.models import Delivery

    past = timezone.now() - timezone.timedelta(seconds=1)
    with _in_org(organization_id):
        Delivery.unscoped.filter(
            organization_id=organization_id, status__in=["retry", "pending"]
        ).update(next_attempt_at=past)
    DueWork.objects.filter(organization_id=organization_id).update(due_at=past)


def _sweep():
    from workeasy360.integration.scheduling.tasks import dispatch_due

    return dispatch_due()


def _create_connection(client, org, url, name="Orders endpoint"):
    response = client.post(
        f"{_api(org)}/connections",
        {"connector_key": "webhook", "display_name": name, "config": {"url": url}},
        content_type="application/json",
    )
    assert response.status_code == 201, response.content
    body = response.json()
    return body["connection"], body["secret"]


def _activate_with(client, org, connection, filters):
    path = f"{_api(org)}/connections/{connection['id']}"
    response = client.put(
        f"{path}/subscriptions",
        {"event_filters": filters},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    response = client.post(
        f"{path}/status", {"status": "active"}, content_type="application/json"
    )
    assert response.status_code == 200, response.content


def test_phase_6_acceptance_flow(
    client,
    signing_key,
    example_product,
    integration_org,
    product_client,
    signed_in,
    local_egress,
    webhook_receiver,
    product_event,
    register_product,
    settings,
):
    settings.WEBHOOK_MAX_ATTEMPTS = 2

    # 1. An organization owner logs in (with step-up: managing connections
    #    is a dangerous permission).
    org_a = integration_org()
    signed_in(org_a["user"], aal=2)

    # 2-3. Creates a webhook connection. The platform validates the endpoint
    #      safely: a metadata-service URL is refused and nothing is written.
    refused = client.post(
        f"{_api(org_a)}/connections",
        {
            "connector_key": "webhook",
            "display_name": "Sneaky",
            "config": {"url": "http://169.254.169.254/latest/meta-data/"},
        },
        content_type="application/json",
    )
    assert refused.status_code == 400
    assert refused.json()["field"] == "config.url"
    connection, secret = _create_connection(
        client, org_a, f"{webhook_receiver.url}/hooks"
    )
    assert secret.startswith("whsec_")
    assert "secret" not in client.get(
        f"{_api(org_a)}/connections/{connection['id']}"
    ).content.decode().lower().replace("secrets", "")

    # 4. Subscribes it to product.example.record.created.v1 and activates it.
    _activate_with(client, org_a, connection, [EVENT_REF])

    # 8 (set-up). A generic automation on the same event: if amount > 100,
    #    send the event to the connection as well.
    rule = client.post(
        f"{_api(org_a)}/automations",
        {
            "name": "Large records",
            "definition": {
                "trigger": {"kind": "event", "event": EVENT_REF},
                "conditions": {
                    "match": "all",
                    "rules": [
                        {"path": "payload.amount", "op": "greater_than", "value": 100}
                    ],
                },
                "actions": [
                    {
                        "type": "connector.invoke",
                        "config": {"connection_id": connection["id"]},
                    }
                ],
            },
        },
        content_type="application/json",
    )
    assert rule.status_code == 201, rule.content
    activated = client.post(
        f"{_api(org_a)}/automations/{rule.json()['id']}/status",
        {"status": "active", "revision": rule.json()["revision"]},
        content_type="application/json",
    )
    assert activated.status_code == 200, activated.content

    # 5-6. The independent product authenticates (private_key_jwt, Phase 3)
    #      and publishes its own event for Organization A.
    product = product_client(example_product)
    event = product_event()
    status, body = product.publish_event(
        organization_id=org_a["organization_id"], event=event
    )
    assert (status, body["status"]) == (202, "accepted")

    # 7. Stored exactly once.
    from workeasy360.integration.events.models import RoutedEvent

    with _in_org(org_a["org"].id):
        stored = RoutedEvent.unscoped.filter(organization_id=org_a["org"].id)
        assert stored.count() == 1
        assert stored.get().routing_status == "routed"

    # 8. The automation evaluated (amount 250 > 100) and ran its action.
    from workeasy360.integration.automation.models import AutomationExecution

    with _in_org(org_a["org"].id):
        execution = AutomationExecution.unscoped.get(organization_id=org_a["org"].id)
    assert execution.status == "succeeded"
    assert execution.conditions_matched is True
    assert execution.action_results[0]["reference"].startswith("delivery:")

    # 9-12. Deliveries were queued after commit and sent: the receiver holds
    #       signed requests it verifies with the secret it was shown once.
    deliveries = _deliveries(org_a["org"].id)
    assert {d["origin"] for d in deliveries} == {"subscription", "automation"}
    assert all(d["status"] == "succeeded" for d in deliveries)
    verified = webhook_receiver.verified(secret)
    assert len(verified) == 2 == len(webhook_receiver.received)
    assert all(r.json["event"]["id"] == event["event_id"] for r in verified)
    assert verified[0].json["schema"] == "workeasy360.webhook.v1"
    assert verified[0].json["event"]["data"] == event["data"]
    assert not verify_signature(
        secret="whsec_wrong",
        header=verified[0].headers["x-w360-signature"],
        body=verified[0].body,
    )
    # At-least-once, made harmless by the receiver deduplicating on event.id.
    assert list(webhook_receiver.unique_events(secret)) == [event["event_id"]]

    # 13. The same event submitted again changes nothing downstream.
    for _ in range(3):
        status, body = product.publish_event(
            organization_id=org_a["organization_id"], event=event
        )
        assert (status, body["status"]) == (200, "duplicate")
    assert len(_deliveries(org_a["org"].id)) == 2
    assert len(webhook_receiver.received) == 2
    with _in_org(org_a["org"].id):
        assert (
            AutomationExecution.unscoped.filter(organization_id=org_a["org"].id).count()
            == 1
        )

    # 14. Product B cannot publish in Product A's namespace, nor its own
    #     undeclared events.
    other = product_client(register_product("crm"))
    status, body = other.publish_event(
        organization_id=org_a["organization_id"], event=product_event()
    )
    assert (status, body["error"]) == (403, "invalid_request")
    status, _ = other.publish_event(
        organization_id=org_a["organization_id"],
        event=product_event(event_type="product.crm.deal.won"),
    )
    assert status == 422
    status, _ = other.publish_event(
        organization_id=org_a["organization_id"],
        event=product_event(event_type="platform.identity.user_created"),
    )
    assert status == 403

    # 15. Organization B, subscribed to the same event, receives nothing of
    #     Organization A's.
    from tests.factories.webhook_receiver import WebhookReceiver

    receiver_b = WebhookReceiver()
    url_b = receiver_b.start()
    try:
        org_b = integration_org()
        client.logout()
        signed_in(org_b["user"], aal=2)
        connection_b, _secret_b = _create_connection(client, org_b, f"{url_b}/hooks")
        _activate_with(client, org_b, connection_b, [EVENT_REF])
        status, _ = product.publish_event(
            organization_id=org_a["organization_id"], event=product_event()
        )
        assert status == 202
        assert receiver_b.received == []
        assert _deliveries(org_b["org"].id) == []
    finally:
        receiver_b.stop()

    # 16. A failing destination is retried with bounded backoff.
    client.logout()
    signed_in(org_a["user"], aal=2)
    webhook_receiver.reply(500, times=2)
    before = timezone.now()
    status, _ = product.publish_event(
        organization_id=org_a["organization_id"], event=product_event(data={"amount": 1})
    )
    assert status == 202
    failing = [d for d in _deliveries(org_a["org"].id) if d["status"] == "retry"]
    assert len(failing) == 1
    from workeasy360.integration.webhooks.models import Delivery

    with _in_org(org_a["org"].id):
        retry = Delivery.unscoped.get(id=failing[0]["id"])
    delay = (retry.next_attempt_at - before).total_seconds()
    assert (
        settings.WEBHOOK_RETRY_BASE_S / 2 - 1
        <= delay
        <= settings.WEBHOOK_RETRY_BASE_S + 5
    )

    # 17. Exhausted, it is dead - with its reason and both attempts kept.
    _fast_forward(org_a["org"].id)
    _sweep()
    with _in_org(org_a["org"].id):
        retry.refresh_from_db()
    assert (retry.status, retry.dead_reason, retry.attempts) == (
        "dead",
        "retries_exhausted",
        2,
    )

    # 18. An authorized user replays it: a new delivery, the original left
    #     untouched, and an audit entry naming who did it.
    response = client.post(f"{_api(org_a)}/deliveries/{retry.id}/replay")
    assert response.status_code == 202, response.content
    replay_id = uuid.UUID(response.json()["delivery_id"])
    with _in_org(org_a["org"].id):
        retry.refresh_from_db()
        replayed = Delivery.unscoped.get(id=replay_id)
    assert retry.status == "dead"
    assert (replayed.status, replayed.replay_of_id) == ("succeeded", retry.id)
    from workeasy360.platform.audit.models import AuditEvent

    with _in_org(org_a["org"].id):
        audit = AuditEvent.unscoped.get(
            organization_id=org_a["org"].id, action="integration.delivery.replayed"
        )
    assert audit.actor_user_id == org_a["user"].id

    # 19. A broker outage loses nothing: the event is accepted and durable,
    #     and the sweeper delivers it once the broker is back.
    from workeasy360.integration.routing import tasks as routing_tasks

    original = routing_tasks.route_event.apply_async

    def broker_down(*args, **kwargs):
        raise ConnectionError("broker unreachable")

    routing_tasks.route_event.apply_async = broker_down
    try:
        outage_event = product_event()
        status, _ = product.publish_event(
            organization_id=org_a["organization_id"], event=outage_event
        )
        assert status == 202
    finally:
        routing_tasks.route_event.apply_async = original
    with _in_org(org_a["org"].id):
        pending = RoutedEvent.unscoped.get(
            organization_id=org_a["org"].id, event_id=outage_event["event_id"]
        )
    assert pending.routing_status == "pending"
    received_before = len(webhook_receiver.received)
    _fast_forward(org_a["org"].id)
    _sweep()
    assert len(webhook_receiver.received) > received_before
    assert outage_event["event_id"] in webhook_receiver.unique_events(secret)

    # 20. None of this touches readiness - not even with the endpoint down.
    webhook_receiver.reply(503, times=50)
    product.publish_event(organization_id=org_a["organization_id"], event=product_event())
    ready = client.get("/healthz/ready")
    assert ready.status_code == 200
    assert "integration" not in {c["name"] for c in ready.json()["checks"]}

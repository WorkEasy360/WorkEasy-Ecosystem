"""Phase 6 security: authorization, isolation, secrets, D-016, ingestion.

Each test names the task it proves. Organized by threat rather than by
module, because an attacker does not care which module a hole is in.
"""

from __future__ import annotations

import json
import logging
import uuid

import pytest

pytestmark = [pytest.mark.django_db, pytest.mark.security]

EVENT_REF = "product.example.record.created.v1"


def _api(org):
    return f"/api/v1/orgs/{org['org'].slug}"


def _post(client, path, body):
    return client.post(path, body, content_type="application/json")


def _count(model, organization_id, **filters):
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="security assertion", module="tests"),
        organization_db_scope(organization_id),
    ):
        return model.unscoped.filter(organization_id=organization_id, **filters).count()


@pytest.fixture
def admin(integration_org, signed_in, client):
    org = integration_org()
    signed_in(org["user"])
    return org


@pytest.fixture
def connection(admin, client, local_egress):
    response = _post(
        client,
        f"{_api(admin)}/connections",
        {
            "connector_key": "webhook",
            "display_name": "Hooks",
            "config": {"url": "http://127.0.0.1:9/h"},
        },
    )
    assert response.status_code == 201, response.content
    return response.json()


# --- authorization (Tasks 52, 53, 55, 56) --------------------------------------


def test_a_member_without_the_permission_is_refused(integration_org, signed_in, client):
    org = integration_org(permissions=())
    signed_in(org["user"])
    assert client.get(f"{_api(org)}/connections").status_code == 403
    assert client.get(f"{_api(org)}/automations").status_code == 403
    assert client.get(f"{_api(org)}/deliveries").status_code == 403


def test_replay_needs_its_own_permission(integration_org, signed_in, client):
    org = integration_org(permissions=("platform.connection.view",))
    signed_in(org["user"])
    response = client.post(f"{_api(org)}/deliveries/{uuid.uuid4()}/replay")
    assert response.status_code == 403


def test_removing_an_administrator_removes_their_access_at_once(
    admin, client, connection
):
    """Task 55: every request re-resolves the live membership; there is no
    cached grant or projection a removed administrator keeps using."""
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import tenant_db_scope

    assert client.get(f"{_api(admin)}/connections").status_code == 200
    ctx = admin["ctx"]
    with tenant_context(ctx), tenant_db_scope(ctx):
        Membership.objects.filter(id=admin["membership"].id).update(status="removed")
    assert client.get(f"{_api(admin)}/connections").status_code in (403, 404)
    assert client.post(
        f"{_api(admin)}/connections/{connection['connection']['id']}/secret"
    ).status_code in (403, 404)


def test_every_phase_6_manage_permission_is_dangerous():
    from workeasy360.platform.authz.registry import permission_registry

    for key in ("platform.connection.manage", "platform.automation.manage"):
        assert permission_registry.get(key).dangerous
    assert not permission_registry.get("platform.connection.view").dangerous


# --- tenant isolation and existence leakage (Tasks 57-59) ----------------------


def test_another_organizations_objects_are_404_never_403(
    integration_org, signed_in, client, local_egress
):
    org_a = integration_org()
    signed_in(org_a["user"])
    created = _post(
        client,
        f"{_api(org_a)}/connections",
        {
            "connector_key": "webhook",
            "display_name": "A",
            "config": {"url": "http://127.0.0.1:9/a"},
        },
    ).json()["connection"]
    rule = _post(
        client,
        f"{_api(org_a)}/automations",
        {
            "name": "A rule",
            "definition": {
                "trigger": {"kind": "event", "event": EVENT_REF},
                "actions": [
                    {
                        "type": "connector.invoke",
                        "config": {"connection_id": created["id"]},
                    }
                ],
            },
        },
    ).json()

    org_b = integration_org()
    client.logout()
    signed_in(org_b["user"])
    base = _api(org_b)
    for path in (
        f"{base}/connections/{created['id']}",
        f"{base}/connections/{created['id']}/subscriptions",
        f"{base}/automations/{rule['id']}",
        f"{base}/automations/{rule['id']}/executions",
        f"{base}/deliveries/{uuid.uuid4()}",
    ):
        response = client.get(path)
        assert response.status_code == 404, path
    # Writes too - and a guessed id reads exactly like a random one.
    for path, body in (
        (f"{base}/connections/{created['id']}/status", {"status": "disabled"}),
        (f"{base}/connections/{created['id']}/ping", {}),
        (f"{base}/connections/{uuid.uuid4()}/ping", {}),
        (f"{base}/automations/{rule['id']}/status", {"status": "active", "revision": 1}),
    ):
        response = _post(client, path, body)
        assert response.status_code == 404, path
    # A rule in B cannot target A's connection: indistinguishable from no
    # such connection.
    response = _post(
        client,
        f"{base}/automations",
        {
            "name": "Exfiltrate",
            "definition": {
                "trigger": {"kind": "event", "event": EVENT_REF},
                "actions": [
                    {
                        "type": "connector.invoke",
                        "config": {"connection_id": created["id"]},
                    }
                ],
            },
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "No such connection."


def test_the_database_refuses_a_cross_tenant_delivery(integration_org, local_egress):
    """Layer 4: even code that got another tenant's id cannot store a
    reference to it (composite foreign key, checked at commit)."""
    from django.db import IntegrityError, transaction

    from workeasy360.integration.connectors import services as connectors
    from workeasy360.integration.events.services import record_platform_event
    from workeasy360.integration.webhooks.models import Delivery
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import tenant_db_scope

    org_a, org_b = integration_org(), integration_org()
    with tenant_context(org_a["ctx"]), tenant_db_scope(org_a["ctx"]):
        foreign = connectors.create_connection(
            org_a["ctx"],
            connector_key="webhook",
            display_name="A",
            config={"url": "http://127.0.0.1:9/a"},
        ).connection
    with pytest.raises(IntegrityError), transaction.atomic():
        with tenant_context(org_b["ctx"]), tenant_db_scope(org_b["ctx"]):
            event, _ = record_platform_event(
                "integration.webhook.pinged",
                1,
                {"connection_id": str(foreign.id), "message": "x"},
                direct=True,
            )
            from django.utils import timezone

            Delivery(
                connection_id=foreign.id,
                event_id=event.id,
                origin="ping",
                capability="deliver_event",
                max_attempts=1,
                next_attempt_at=timezone.now(),
            ).save()
            from django.db import connection as db

            with db.cursor() as cur:
                cur.execute("SET CONSTRAINTS ALL IMMEDIATE")


# --- secrets (Tasks 4, 30, 62, 82) ------------------------------------------------


def test_a_secret_is_shown_once_and_never_again(admin, client, connection):
    secret = connection["secret"]
    connection_id = connection["connection"]["id"]
    detail = client.get(f"{_api(admin)}/connections/{connection_id}")
    listing = client.get(f"{_api(admin)}/connections")
    for response in (detail, listing):
        assert response.status_code == 200
        assert secret not in response.content.decode()
        assert "ciphertext" not in response.content.decode()
    assert detail.json()["secrets"][0]["hint"] == secret[-4:]


def test_the_database_holds_ciphertext_bound_to_its_row(admin, connection):
    from workeasy360.integration.connectors import secrets
    from workeasy360.integration.connectors.models import ConnectionSecret
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    plaintext = connection["secret"]
    connection_id = uuid.UUID(connection["connection"]["id"])
    with (
        unscoped_access(reason="security assertion", module="tests"),
        organization_db_scope(admin["org"].id),
    ):
        row = ConnectionSecret.unscoped.get(connection_id=connection_id)
    assert plaintext not in row.ciphertext
    assert (
        secrets.unseal(
            row.ciphertext,
            organization_id=admin["org"].id,
            connection_id=connection_id,
            version=row.version,
        )
        == plaintext
    )
    # Copied onto another row - another connection or another organization -
    # the ciphertext refuses to open.
    with pytest.raises(secrets.SecretUnavailable):
        secrets.unseal(
            row.ciphertext,
            organization_id=admin["org"].id,
            connection_id=uuid.uuid4(),
            version=1,
        )
    with pytest.raises(secrets.SecretUnavailable):
        secrets.unseal(
            row.ciphertext,
            organization_id=uuid.uuid4(),
            connection_id=connection_id,
            version=1,
        )


def test_rotation_overlaps_then_revokes(admin, client, connection):
    from workeasy360.integration.connectors.selectors import secret_material
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import tenant_db_scope

    connection_id = connection["connection"]["id"]
    rotated = client.post(f"{_api(admin)}/connections/{connection_id}/secret")
    assert rotated.status_code == 201
    new = rotated.json()["secret"]
    assert new != connection["secret"]
    with tenant_context(admin["ctx"]), tenant_db_scope(admin["ctx"]):
        material = secret_material(uuid.UUID(connection_id))
    assert material.secrets == (new, connection["secret"])
    assert "whsec" not in repr(material)

    assert (
        client.delete(f"{_api(admin)}/connections/{connection_id}/secret").status_code
        == 200
    )
    with tenant_context(admin["ctx"]), tenant_db_scope(admin["ctx"]):
        assert secret_material(uuid.UUID(connection_id)).secrets == (new,)


def test_no_secret_reaches_a_log_line(
    admin,
    client,
    local_egress,
    caplog,
    webhook_receiver,
    django_capture_on_commit_callbacks,
):
    caplog.set_level(logging.DEBUG)
    response = _post(
        client,
        f"{_api(admin)}/connections",
        {
            "connector_key": "webhook",
            "display_name": "Logged",
            "config": {"url": f"{webhook_receiver.url}/tok/SECRETPATH"},
        },
    )
    secret = response.json()["secret"]
    connection_id = response.json()["connection"]["id"]
    base = f"{_api(admin)}/connections/{connection_id}"
    _post(client, f"{base}/status", {"status": "active"})
    with django_capture_on_commit_callbacks(execute=True):
        _post(client, f"{base}/ping", {})  # a real signed delivery
    rotated = client.post(f"{base}/secret").json()["secret"]
    with django_capture_on_commit_callbacks(execute=True):
        _post(client, f"{base}/ping", {})  # signed with both secrets (overlap)

    assert len(webhook_receiver.verified(secret)) == 2
    assert len(webhook_receiver.verified(rotated)) == 1
    signatures = [
        value
        for request in webhook_receiver.received
        for item in request.headers["x-w360-signature"].split(",")
        for key, value in [item.split("=", 1)]
        if key == "v1"
    ]
    assert len(signatures) == 3

    text = caplog.text
    for sensitive in (secret, rotated, "SECRETPATH", *signatures):
        assert sensitive not in text


# --- D-016: nothing written ahead of a 4xx (Task 41) -----------------------------


def test_a_rejected_connection_writes_nothing(admin, client):
    from workeasy360.integration.connectors.models import Connection
    from workeasy360.platform.audit.models import AuditEvent

    response = _post(
        client,
        f"{_api(admin)}/connections",
        {
            "connector_key": "webhook",
            "display_name": "Bad",
            "config": {"url": "https://10.0.0.1/"},
        },
    )
    assert response.status_code == 400
    assert _count(Connection, admin["org"].id) == 0
    assert _count(AuditEvent, admin["org"].id, action__startswith="integration.") == 0


def test_a_stale_or_invalid_edit_changes_nothing(admin, client, connection):
    from workeasy360.integration.connectors.models import Connection
    from workeasy360.platform.audit.models import AuditEvent

    path = f"{_api(admin)}/connections/{connection['connection']['id']}"
    before = _count(AuditEvent, admin["org"].id)
    stale = client.patch(
        path, {"revision": 99, "display_name": "Renamed"}, content_type="application/json"
    )
    assert stale.status_code == 409
    invalid = client.patch(
        path,
        {"revision": 1, "display_name": "Renamed", "config": {"url": "gopher://x"}},
        content_type="application/json",
    )
    assert invalid.status_code == 400
    assert _count(Connection, admin["org"].id, display_name="Renamed") == 0
    assert _count(AuditEvent, admin["org"].id) == before


def test_one_invalid_filter_adds_no_subscription(admin, client, connection):
    from workeasy360.integration.webhooks.models import EventSubscription

    response = client.put(
        f"{_api(admin)}/connections/{connection['connection']['id']}/subscriptions",
        {"event_filters": [EVENT_REF, "product.books.invoice.issued.v1"]},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["field"] == "event_filters[1]"
    assert _count(EventSubscription, admin["org"].id) == 0


def test_an_invalid_rule_edit_creates_no_version(admin, client, connection):
    from workeasy360.integration.automation.models import AutomationRuleVersion

    rule = _post(
        client,
        f"{_api(admin)}/automations",
        {
            "name": "R",
            "definition": {
                "trigger": {"kind": "event", "event": EVENT_REF},
                "actions": [
                    {
                        "type": "connector.invoke",
                        "config": {"connection_id": connection["connection"]["id"]},
                    }
                ],
            },
        },
    ).json()
    bad = client.put(
        f"{_api(admin)}/automations/{rule['id']}",
        {
            "revision": rule["revision"],
            "definition": {
                "trigger": {"kind": "event", "event": EVENT_REF},
                "conditions": {
                    "rules": [{"path": "payload.x", "op": "eval", "value": "1"}]
                },
                "actions": [
                    {
                        "type": "connector.invoke",
                        "config": {"connection_id": connection["connection"]["id"]},
                    }
                ],
            },
        },
        content_type="application/json",
    )
    assert bad.status_code == 400
    assert _count(AutomationRuleVersion, admin["org"].id) == 1
    assert (
        client.get(f"{_api(admin)}/automations/{rule['id']}").json()["revision"]
        == rule["revision"]
    )


def test_a_refused_rotation_writes_nothing(admin, client, connection):
    from workeasy360.integration.connectors.models import ConnectionSecret

    connection_id = connection["connection"]["id"]
    assert (
        _post(
            client,
            f"{_api(admin)}/connections/{connection_id}/status",
            {"status": "disabled"},
        ).status_code
        == 200
    )
    before = _count(ConnectionSecret, admin["org"].id)
    assert (
        client.post(f"{_api(admin)}/connections/{connection_id}/secret").status_code
        == 409
    )
    assert _count(ConnectionSecret, admin["org"].id) == before


# --- ingestion: identity, namespace, tenant binding, bounds (Tasks 8-11, 60, 61) --


@pytest.fixture
def publisher(example_product, product_client, signing_key):
    return product_client(example_product)


def test_ingestion_ignores_a_browser_session(
    client, signing_key, integration_org, signed_in, product_event
):
    """CSRF-exempt is safe only because a cookie authenticates nothing here."""
    org = integration_org()
    signed_in(org["user"])
    response = client.post(
        "/api/v1/integrations/events",
        {"organization_id": org["organization_id"], "event": json.dumps(product_event())},
    )
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


def test_an_unknown_organization_reads_exactly_like_an_unentitled_one(
    publisher, integration_org, organization_factory, product_event
):
    stranger, _, _ = organization_factory()  # exists, not entitled
    unknown = publisher.publish_event(
        organization_id=str(uuid.uuid4()), event=product_event()
    )
    unentitled = publisher.publish_event(
        organization_id=str(stranger.id), event=product_event()
    )

    def comparable(reply):
        status, body = reply
        return status, {k: v for k, v in body.items() if k != "correlation_id"}

    assert comparable(unknown) == comparable(unentitled)
    assert unknown[0] == 403 and unknown[1]["error"] == "product_not_entitled"


def test_a_suspended_organization_accepts_no_new_events(
    publisher, integration_org, product_event
):
    """The live organization status is checked at ingestion as well as the
    entitlement decision, so a suspension the entitlement snapshot has not
    caught up with yet still starts nothing - answered in the protocol's
    shape, not as the persistence layer's R-10 402."""
    from workeasy360.integration.events.models import RoutedEvent
    from workeasy360.platform.organizations.models import Organization
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    org = integration_org()
    with unscoped_access(reason="test suspends an organization", module="tests"):
        Organization.objects.filter(id=org["org"].id).update(status="suspended")

    status, body = publisher.publish_event(
        organization_id=org["organization_id"], event=product_event()
    )
    assert (status, body["error"]) == (403, "organization_inactive")
    assert _count(RoutedEvent, org["org"].id) == 0


def test_a_suspended_product_cannot_publish(
    publisher, example_product, platform_admin, in_tenant, integration_org, product_event
):
    from workeasy360.platform.ecosystem import services

    org = integration_org()
    with in_tenant(platform_admin["ctx"]):
        services.transition(
            platform_admin["ctx"],
            client_id=example_product["client_id"],
            to_status="suspended",
            reason="Security test suspends the product.",
        )
    status, body = publisher.publish_event(
        organization_id=org["organization_id"], event=product_event()
    )
    assert (status, body["error"]) == (403, "integration_suspended")


@pytest.mark.parametrize(
    ("mutate", "status"),
    [
        (lambda e: e.update(event_id="not-a-uuid"), 400),
        (lambda e: e.update(event_version=0), 400),
        (lambda e: e.update(event_version=True), 400),
        (lambda e: e.update(occurred_at="2001-01-01T00:00:00+00:00"), 400),
        (lambda e: e.update(occurred_at="2026-09-23T10:00:00"), 400),
        (lambda e: e.pop("occurred_at"), 400),
        (lambda e: e.update(extra="field"), 400),
        (lambda e: e.update(data=[1, 2, 3]), 400),
        (lambda e: e.update(data={"s": "x" * 9000}), 400),
        (lambda e: e.update(data={"l": list(range(600))}), 400),
        (
            lambda e: e.update(
                data={"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": 1}}}}}}}}}
            ),
            400,
        ),
        (lambda e: e.update(event_type="product.example.record.deleted"), 422),
        (
            lambda e: e.update(
                event_type="product.example.record.created", event_version=2
            ),
            422,
        ),
        (lambda e: e.update(event_type="integration.webhook.pinged"), 403),
    ],
)
def test_malformed_or_undeclared_events_are_refused(
    publisher, integration_org, product_event, mutate, status
):
    org = integration_org()
    event = product_event()
    mutate(event)
    code, _body = publisher.publish_event(
        organization_id=org["organization_id"], event=event
    )
    assert code == status


def test_an_oversized_event_is_refused_before_parsing(
    client, signing_key, publisher, integration_org, settings
):
    org = integration_org()
    big = json.dumps({"x": "y" * (settings.INTEGRATION_EVENT_MAX_BYTES + 20_000)})
    response = client.post(
        "/api/v1/integrations/events",
        {"organization_id": org["organization_id"], "event": big},
    )
    assert response.status_code == 413


def test_nan_is_not_json(client, signing_key, publisher, integration_org, product_event):
    org = integration_org()
    body = {
        "organization_id": org["organization_id"],
        "event": json.dumps(product_event()).replace('"amount": 250', '"amount": NaN'),
    }
    body.update(publisher._client_authentication())
    assert client.post("/api/v1/integrations/events", body).status_code == 400


def test_one_event_id_cannot_carry_two_events(publisher, integration_org, product_event):
    org = integration_org()
    event = product_event()
    assert (
        publisher.publish_event(organization_id=org["organization_id"], event=event)[0]
        == 202
    )
    event["data"] = {"record_id": "different"}
    code, body = publisher.publish_event(
        organization_id=org["organization_id"], event=event
    )
    assert (code, body["error"]) == (409, "invalid_request")


def test_ingestion_is_rate_limited_per_product(
    publisher, integration_org, product_event, settings, frozen_throttle_window
):
    settings.ECOSYSTEM_RATE_LIMITS = {
        **settings.ECOSYSTEM_RATE_LIMITS,
        "event_ingest": (3, 60),
    }
    org = integration_org()
    codes = [
        publisher.publish_event(
            organization_id=org["organization_id"], event=product_event()
        )[0]
        for _ in range(5)
    ]
    assert codes[:3] == [202, 202, 202]
    assert codes[3:] == [429, 429]


def test_a_manifest_cannot_declare_another_products_events():
    from tests.factories.manifests import manifest_for
    from workeasy360.contracts.values.manifest import ManifestError, parse_manifest

    with pytest.raises(ManifestError, match="namespace"):
        parse_manifest(
            manifest_for(
                "crm",
                manifest_version="2026-10-01",
                events=[{"type": "product.books.invoice.issued", "version": 1}],
            ),
            allow_insecure_urls=True,
        )
    with pytest.raises(ManifestError, match="namespace"):
        parse_manifest(
            manifest_for(
                "crm",
                manifest_version="2026-10-01",
                events=[{"type": "platform.identity.user_created", "version": 1}],
            ),
            allow_insecure_urls=True,
        )


def test_namespace_ownership_is_structural():
    from workeasy360.contracts.events.namespaces import (
        candidate_filters,
        is_platform_owned,
        parse_event_filter,
        product_owns,
    )

    assert product_owns("crm", "product.crm.deal.won")
    assert not product_owns("crm", "product.books.invoice.issued")
    assert not product_owns("crm", "product.crmx.deal.won")
    assert not product_owns("crm", "platform.identity.user_created")
    assert not product_owns("crm", "product.crm")
    for root in ("platform", "commercial", "integration", "organizations", "identity"):
        assert is_platform_owned(f"{root}.x.y")
    assert parse_event_filter("product.*") is None
    assert parse_event_filter("product.crm.deal.*") is None
    assert parse_event_filter("*") is None
    assert candidate_filters("product.crm.deal.won", 1) == (
        "product.crm.deal.won.v1",
        "product.crm.*",
    )


# --- audit (Task 64) ---------------------------------------------------------------


def test_control_plane_changes_are_audited_without_secrets(admin, client, connection):
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    connection_id = connection["connection"]["id"]
    base = f"{_api(admin)}/connections/{connection_id}"
    client.put(
        f"{base}/subscriptions",
        {"event_filters": [EVENT_REF]},
        content_type="application/json",
    )
    _post(client, f"{base}/status", {"status": "active"})
    client.post(f"{base}/secret")
    with (
        unscoped_access(reason="security assertion", module="tests"),
        organization_db_scope(admin["org"].id),
    ):
        entries = list(
            AuditEvent.unscoped.filter(
                organization_id=admin["org"].id, action__startswith="integration."
            )
            .order_by("occurred_at")
            .values_list("action", "changes")
        )
    actions = [a for a, _ in entries]
    assert actions == [
        "integration.connection.created",
        "integration.subscription.changed",
        "integration.connection.activated",
        "integration.connection.secret_rotated",
    ]
    assert connection["secret"] not in json.dumps([c for _, c in entries])
    assert "/h" not in json.dumps(entries[0][1])

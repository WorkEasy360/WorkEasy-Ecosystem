"""Connection lifecycle and credentials - the application services.

Every mutating function here follows the same order, and the order is the
D-016 rule rather than a style: **authorize, then validate everything, then
write** - and every multi-row write sits inside one ``transaction.atomic()``
that any later failure propagates out of. A 4xx does not roll back the
request transaction, so a service that wrote first and validated second
would commit half an operation behind a 400. The regression tests in
``tests/security/test_phase6_security.py`` pin this for create, edit and
rotate.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import F, Max
from django.utils import timezone

from workeasy360.contracts.errors import (
    IntegrationConflict,
    IntegrationNotFound,
    IntegrationValidationError,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.integration.connectors import registry, secrets
from workeasy360.integration.connectors.contract import Connector, ConnectorConfigError
from workeasy360.integration.connectors.models import (
    CONNECTION_TRANSITIONS,
    Connection,
    ConnectionSecret,
    ConnectionStatus,
    SecretStatus,
)
from workeasy360.integration.connectors.permissions import CONNECTION_MANAGE
from workeasy360.integration.connectors.selectors import ConnectionDTO, to_dto
from workeasy360.integration.egress.policy import EgressPolicy
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

RESOURCE = "integration.connection"

_TRANSITION_ACTIONS = {
    ConnectionStatus.ACTIVE: "integration.connection.activated",
    ConnectionStatus.SUSPENDED: "integration.connection.suspended",
    ConnectionStatus.DISABLED: "integration.connection.disabled",
}
MAX_NAME_LENGTH = 120
MAX_REASON_LENGTH = 200


@dataclass(frozen=True, slots=True)
class CreatedConnection:
    connection: ConnectionDTO
    #: The generated signing secret. Returned exactly once, here; nothing
    #: stores it in the clear and no read path can produce it (Task 82).
    secret: str | None


@dataclass(frozen=True, slots=True)
class IssuedSecret:
    version: int
    secret: str
    previous_valid_until: Any


# --- create / edit / lifecycle ---------------------------------------------


def create_connection(
    ctx: TenantContext,
    *,
    connector_key: Any,
    display_name: Any,
    config: Any,
) -> CreatedConnection:
    authz.require(ctx, CONNECTION_MANAGE)

    connector = _connector(connector_key)
    name = _validated_name(display_name)
    normalized = _validated_config(connector, config)
    limit = int(settings.INTEGRATION_MAX_CONNECTIONS)
    if (
        Connection.objects.exclude(status=ConnectionStatus.DISABLED.value).count()
        >= limit
    ):
        raise IntegrationConflict(
            f"This organization already has {limit} connections. Disable one first."
        )

    plaintext = secrets.generate_secret() if connector.generates_secret else None
    with transaction.atomic():
        connection = Connection(
            connector_key=connector.key,
            display_name=name,
            config=normalized,
            status=ConnectionStatus.DRAFT.value,
            created_by_id=ctx.user_id,
        )
        connection.save()
        if plaintext is not None:
            _store_secret(ctx, connection, version=1, secret=plaintext)
        audit.record(
            ctx,
            "integration.connection.created",
            resource_type=RESOURCE,
            resource_id=connection.id,
            changes={
                "connector": connector.key,
                "display_name": name,
                **connector.describe_config(normalized),
            },
        )

    metrics.increment("integration_connections_created_total", connector=connector.key)
    return CreatedConnection(connection=to_dto(connection), secret=plaintext)


def update_connection(
    ctx: TenantContext,
    connection_id: Any,
    *,
    expected_revision: Any,
    display_name: Any = None,
    config: Any = None,
) -> ConnectionDTO:
    """Change a connection's name and/or configuration.

    ``expected_revision`` is mandatory: two administrators editing the same
    endpoint must not silently overwrite each other (the lost-update case).
    """
    authz.require(ctx, CONNECTION_MANAGE)
    connection = _load(connection_id)
    revision = _validated_revision(expected_revision)
    if connection.status_enum is ConnectionStatus.DISABLED:
        raise IntegrationConflict("A disabled connection cannot be changed.")

    connector = _connector(connection.connector_key)
    fields: dict[str, Any] = {}
    changes: dict[str, Any] = {}
    if display_name is not None:
        name = _validated_name(display_name)
        if name != connection.display_name:
            fields["display_name"] = name
            changes["display_name"] = {"from": connection.display_name, "to": name}
    if config is not None:
        normalized = _validated_config(connector, config)
        if normalized != connection.config:
            fields["config"] = normalized
            changes["destination"] = {
                "from": connector.describe_config(connection.config),
                "to": connector.describe_config(normalized),
            }
    if not fields:
        return to_dto(connection)

    with transaction.atomic():
        updated = Connection.objects.filter(id=connection.id, revision=revision).update(
            **fields, revision=F("revision") + 1, updated_at=timezone.now()
        )
        if not updated:
            raise IntegrationConflict(
                "This connection was changed by someone else. Reload and retry.",
                resource="connection",
            )
        audit.record(
            ctx,
            "integration.connection.updated",
            resource_type=RESOURCE,
            resource_id=connection.id,
            changes=changes,
        )
    connection.refresh_from_db()
    return to_dto(connection)


def transition_connection(
    ctx: TenantContext, connection_id: Any, *, to_status: Any, reason: Any = ""
) -> ConnectionDTO:
    """Move a connection through its lifecycle (table in ``models``)."""
    authz.require(ctx, CONNECTION_MANAGE)
    try:
        target = ConnectionStatus(str(to_status))
    except ValueError:
        raise IntegrationValidationError(
            "Unknown connection status.", field="status"
        ) from None
    note = str(reason or "")[:MAX_REASON_LENGTH]
    connection = _load(connection_id)
    current = connection.status_enum
    if target not in CONNECTION_TRANSITIONS[current]:
        raise IntegrationConflict(
            f"A {current.value} connection cannot become {target.value}."
        )

    now = timezone.now()
    fields: dict[str, Any] = {
        "status": target.value,
        "status_reason": note,
        "updated_at": now,
    }
    if target is ConnectionStatus.ACTIVE:
        # Reactivation is an administrator's statement that the endpoint is
        # fixed: the breaker closes and held deliveries resume.
        fields.update(consecutive_failures=0, circuit_open_until=None)
        if connection.activated_at is None:
            fields["activated_at"] = now

    with transaction.atomic():
        # Conditional on the status just read, so two racing transitions
        # (activate vs. disable) resolve to exactly one winner.
        if not Connection.objects.filter(id=connection.id, status=current.value).update(
            **fields
        ):
            raise IntegrationConflict(
                "This connection's status changed concurrently. Reload and retry."
            )
        if target is ConnectionStatus.DISABLED:
            ConnectionSecret.objects.filter(
                connection_id=connection.id,
                status__in=[SecretStatus.ACTIVE.value, SecretStatus.PREVIOUS.value],
            ).update(status=SecretStatus.REVOKED.value, revoked_at=now)
        audit.record(
            ctx,
            _TRANSITION_ACTIONS[target],
            resource_type=RESOURCE,
            resource_id=connection.id,
            changes={"from": current.value, "to": target.value},
            reason=note,
        )
    connection.refresh_from_db()
    return to_dto(connection)


# --- credentials ------------------------------------------------------------


def rotate_secret(ctx: TenantContext, connection_id: Any) -> IssuedSecret:
    """Issue a new signing secret; the old one keeps signing for the overlap.

    Rotations of one connection are serialised on its row lock, and the
    "one active secret" partial unique index makes the invariant hold even
    for a writer that forgot the lock.
    """
    authz.require(ctx, CONNECTION_MANAGE)
    connection = _load(connection_id)
    connector = _connector(connection.connector_key)
    if not connector.generates_secret:
        raise IntegrationConflict("This connection type has no secret to rotate.")
    if connection.status_enum is ConnectionStatus.DISABLED:
        raise IntegrationConflict("A disabled connection's secrets are revoked.")

    plaintext = secrets.generate_secret()
    overlap = int(settings.INTEGRATION_SECRET_OVERLAP_S)
    with transaction.atomic():
        Connection.objects.select_for_update().filter(id=connection.id).first()
        now = timezone.now()
        valid_until = now + timezone.timedelta(seconds=overlap)
        # Only one generation of overlap: anything already "previous" ends
        # now, so at most two secrets ever sign a delivery.
        ConnectionSecret.objects.filter(
            connection_id=connection.id, status=SecretStatus.PREVIOUS.value
        ).update(status=SecretStatus.REVOKED.value, revoked_at=now)
        ConnectionSecret.objects.filter(
            connection_id=connection.id, status=SecretStatus.ACTIVE.value
        ).update(status=SecretStatus.PREVIOUS.value, expires_at=valid_until)
        latest = (
            ConnectionSecret.objects.filter(connection_id=connection.id).aggregate(
                v=Max("version")
            )["v"]
            or 0
        )
        version = latest + 1
        _store_secret(ctx, connection, version=version, secret=plaintext)
        audit.record(
            ctx,
            "integration.connection.secret_rotated",
            resource_type=RESOURCE,
            resource_id=connection.id,
            changes={"version": version, "previous_valid_until": valid_until.isoformat()},
        )
    return IssuedSecret(
        version=version, secret=plaintext, previous_valid_until=valid_until
    )


def revoke_previous_secrets(ctx: TenantContext, connection_id: Any) -> int:
    """End a rotation's overlap now - e.g. because the old secret leaked."""
    authz.require(ctx, CONNECTION_MANAGE)
    connection = _load(connection_id)
    with transaction.atomic():
        revoked = ConnectionSecret.objects.filter(
            connection_id=connection.id, status=SecretStatus.PREVIOUS.value
        ).update(status=SecretStatus.REVOKED.value, revoked_at=timezone.now())
        audit.record(
            ctx,
            "integration.connection.previous_secret_revoked",
            resource_type=RESOURCE,
            resource_id=connection.id,
            changes={"revoked": revoked},
        )
    return revoked


# --- delivery feedback (system; called by the delivery pipeline) -----------


def record_delivery_result(connection_id: UUID, *, succeeded: bool) -> None:
    """Update the connection's health and circuit after one attempt.

    Runs inside the pipeline's result transaction, in the organization's
    scope, with the routing context (no actor).

    The breaker (Task 49): after ``INTEGRATION_CIRCUIT_FAILURE_THRESHOLD``
    consecutive failures the circuit opens for a period that doubles with
    every further failure (capped); while open, deliveries are held, not
    attempted and not charged an attempt. After
    ``INTEGRATION_AUTO_SUSPEND_FAILURES`` the connection is suspended
    outright and an administrator must reactivate it (Task 50) - a
    permanently broken endpoint stops consuming delivery capacity instead of
    being probed forever.
    """
    now = timezone.now()
    connection = Connection.objects.select_for_update().filter(id=connection_id).first()
    if connection is None:
        return

    if succeeded:
        fields: dict[str, Any] = {}
        if connection.consecutive_failures or connection.circuit_open_until:
            fields.update(consecutive_failures=0, circuit_open_until=None)
        # At most one write a minute for the timestamp alone: a busy healthy
        # endpoint must not turn every delivery into a row update.
        if (
            fields
            or connection.last_success_at is None
            or (now - connection.last_success_at).total_seconds() >= 60
        ):
            fields["last_success_at"] = now
            Connection.objects.filter(id=connection.id).update(**fields)
        return

    failures = connection.consecutive_failures + 1
    fields = {"consecutive_failures": failures, "last_failure_at": now}
    threshold = int(settings.INTEGRATION_CIRCUIT_FAILURE_THRESHOLD)
    if failures >= threshold:
        from workeasy360.contracts.values.backoff import ceiling

        open_for = ceiling(
            failures - threshold,
            base=float(settings.INTEGRATION_CIRCUIT_BASE_S),
            cap=float(settings.INTEGRATION_CIRCUIT_CAP_S),
        )
        fields["circuit_open_until"] = now + timezone.timedelta(seconds=open_for)
        if failures == threshold:
            metrics.increment(
                "integration_circuit_opened_total", connector=connection.connector_key
            )

    suspend = failures >= int(
        settings.INTEGRATION_AUTO_SUSPEND_FAILURES
    ) and connection.status == (ConnectionStatus.ACTIVE.value)
    if suspend:
        fields.update(
            status=ConnectionStatus.SUSPENDED.value,
            status_reason=f"Suspended automatically after {failures} consecutive "
            f"delivery failures.",
        )
    Connection.objects.filter(id=connection.id).update(**fields)

    if suspend:
        metrics.increment(
            "integration_connections_auto_suspended_total",
            connector=connection.connector_key,
        )
        logger.warning(
            "integration.connection.auto_suspended",
            connection_id=str(connection.id),
            failures=failures,
        )
        audit.record_system(
            connection.organization_id,
            "integration.connection.auto_suspended",
            resource_type=RESOURCE,
            resource_id=connection.id,
            changes={"consecutive_failures": failures},
            reason="Sustained delivery failure; reactivate once the endpoint is fixed.",
        )


# --- internals ------------------------------------------------------------


def _store_secret(
    ctx: TenantContext, connection: Connection, *, version: int, secret: str
) -> None:
    ConnectionSecret(
        connection_id=connection.id,
        version=version,
        ciphertext=secrets.seal(
            secret,
            organization_id=connection.organization_id,
            connection_id=connection.id,
            version=version,
        ),
        hint=secrets.hint(secret),
        status=SecretStatus.ACTIVE.value,
        created_by_id=ctx.user_id,
    ).save()


def _connector(key: Any) -> Connector:
    connector = registry.get_connector(str(key or ""))
    if connector is None:
        raise IntegrationValidationError("Unknown connector.", field="connector_key")
    return connector


def _validated_name(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise IntegrationValidationError("A name is required.", field="display_name")
    name = " ".join(raw.split())
    if len(name) > MAX_NAME_LENGTH:
        raise IntegrationValidationError(
            f"The name may be at most {MAX_NAME_LENGTH} characters.",
            field="display_name",
        )
    return name


def _validated_config(connector: Connector, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise IntegrationValidationError("config must be an object.", field="config")
    try:
        return connector.validate_config(raw, policy=EgressPolicy.from_settings())
    except ConnectorConfigError as exc:
        metrics.increment("integration_config_rejected_total", connector=connector.key)
        raise IntegrationValidationError(
            exc.message, field=f"config.{exc.field}" if exc.field else "config"
        ) from None


def _validated_revision(raw: Any) -> int:
    if isinstance(raw, bool):
        raise IntegrationValidationError("revision is required.", field="revision")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise IntegrationValidationError(
            "revision is required.", field="revision"
        ) from None


def _load(connection_id: Any) -> Connection:
    try:
        key = UUID(str(connection_id))
    except ValueError:
        raise IntegrationNotFound(resource="connection") from None
    connection = Connection.objects.filter(id=key).first()
    if connection is None:
        raise IntegrationNotFound(resource="connection")
    return connection

"""The connection registry and its credentials.

Both tenant-owned: ``TenantScopedModel`` (fail-closed manager, R-10 write
block), RLS in ``tenancy.0005``, composite foreign keys for every
tenant-to-tenant reference (B4), and an ``organization``-leading index on
every query path (B3).
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models

from workeasy360.contracts.enums import StrEnum
from workeasy360.platform.tenancy.models import TenantScopedModel


class ConnectionStatus(StrEnum):
    """The connection lifecycle (Task 3).

    - ``draft``     configured, secret issued, reachable by a test ping only.
                    Where a connection sits while its receiver is being built.
    - ``active``    receives subscribed events and automation actions.
    - ``suspended`` paused - by an administrator, or automatically after
                    sustained delivery failure. Pending deliveries are held,
                    not dropped, and resume on reactivation.
    - ``disabled``  terminal. Secrets are revoked; history is retained under
                    the delivery retention policy. A disabled connection is
                    the deletion of a connection: nothing can send through it
                    again, and nothing about what it did is lost early.
    """

    DRAFT = "draft"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DISABLED = "disabled"


#: Legal transitions. A table, so the whole machine is visible at once.
CONNECTION_TRANSITIONS: dict[ConnectionStatus, frozenset[ConnectionStatus]] = {
    ConnectionStatus.DRAFT: frozenset(
        {ConnectionStatus.ACTIVE, ConnectionStatus.DISABLED}
    ),
    ConnectionStatus.ACTIVE: frozenset(
        {ConnectionStatus.SUSPENDED, ConnectionStatus.DISABLED}
    ),
    ConnectionStatus.SUSPENDED: frozenset(
        {ConnectionStatus.ACTIVE, ConnectionStatus.DISABLED}
    ),
    ConnectionStatus.DISABLED: frozenset(),
}


class SecretStatus(StrEnum):
    #: Signs every delivery. Exactly one per connection (partial unique index).
    ACTIVE = "active"
    #: The secret before the latest rotation. Still signs, alongside the
    #: active one, until ``expires_at`` - the overlap window a receiver uses
    #: to switch over without dropping deliveries.
    PREVIOUS = "previous"
    #: Never used again.
    REVOKED = "revoked"


def _choices(enum_cls: Any) -> list[tuple[str, str]]:
    return [(m.value, m.name.title()) for m in enum_cls]


class Connection(TenantScopedModel):
    """One organization's configured instance of a connector."""

    connector_key = models.CharField(max_length=32)
    display_name = models.CharField(max_length=120)
    status = models.CharField(
        max_length=16,
        choices=_choices(ConnectionStatus),
        default=ConnectionStatus.DRAFT.value,
    )
    #: Validated by the connector, **non-secret** by contract. Secrets live
    #: in ``ConnectionSecret`` and nowhere else. (The webhook URL is treated
    #: as sensitive anyway - see ``describe_config`` - but it is not a
    #: credential the platform relies on.)
    config = models.JSONField(default=dict)
    #: Optimistic-concurrency token for configuration edits. An update names
    #: the revision it read; a stale one is refused (409) rather than
    #: silently overwriting a colleague's change.
    revision = models.PositiveIntegerField(default=1)
    status_reason = models.CharField(max_length=200, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    activated_at = models.DateTimeField(null=True, blank=True)

    # --- health and the circuit breaker (Task 49) --------------------------
    #
    # Per connection, in this row, rather than in any shared store: a
    # breaker's state must survive a worker restart and be the same for
    # every worker, and PostgreSQL already is that. Updated in the same
    # transaction that records each attempt.
    consecutive_failures = models.PositiveIntegerField(default=0)
    circuit_open_until = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_failure_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "connectors_connection"
        constraints = [
            # The target of every composite foreign key into this table.
            models.UniqueConstraint(
                fields=["id", "organization"], name="uq_connection_id_org"
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "-created_at", "id"], name="ix_connection_list"
            ),
            models.Index(
                fields=["organization", "status", "connector_key"],
                name="ix_connection_status",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.display_name} ({self.connector_key}, {self.status})"

    @property
    def status_enum(self) -> ConnectionStatus:
        return ConnectionStatus(self.status)


class ConnectionSecret(TenantScopedModel):
    """One version of a connection's credential, sealed (``secrets.seal``).

    Immutable after insert apart from its lifecycle columns - a trigger in
    ``tenancy.0005`` refuses changes to ``connection``, ``version`` and
    ``ciphertext``, so a rotation can only ever add a version.
    """

    connection = models.ForeignKey(Connection, on_delete=models.CASCADE, related_name="+")
    version = models.PositiveIntegerField()
    #: Fernet token over ``{org, connection, version, secret}``. Never the
    #: plaintext, never returned by any read path.
    ciphertext = models.TextField()
    #: Last four characters, for "which secret is this?" in the UI.
    hint = models.CharField(max_length=8)
    status = models.CharField(
        max_length=16,
        choices=_choices(SecretStatus),
        default=SecretStatus.ACTIVE.value,
    )
    #: For ``previous``: when the overlap window ends.
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        db_table = "connectors_secret"
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "version"], name="uq_connection_secret_version"
            ),
            # Exactly one signing secret at a time, enforced by the database
            # so that two concurrent rotations cannot both leave an active one.
            models.UniqueConstraint(
                fields=["connection"],
                condition=models.Q(status="active"),
                name="uq_connection_one_active_secret",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "connection", "status"],
                name="ix_connection_secret",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"secret v{self.version} of {self.connection_id} ({self.status})"

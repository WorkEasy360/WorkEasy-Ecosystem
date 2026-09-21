"""The audit log - append-only and hash-chained.

**Audit is not logging.** Logs are operational, sampled, and may be dropped
under load. Audit is a business and legal record: complete, immutable,
tenant-visible and never sampled. Conflating them means either dropping audit
records under load or paying audit-grade storage for debug lines
(11-observability.md section 2).

Append-only is enforced by **database grants and a trigger**, not by
application discipline (see ``make_append_only`` in the migration). An
ORM-level guard is bypassed by raw SQL, a data-fix script or the admin; a
revoked grant is not.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from django.db import models

from workeasy360.contracts.enums import ActorKind, AuditOutcome
from workeasy360.platform.tenancy.models import TenantScopedModel


class AuditEvent(TenantScopedModel):
    """One audited action.

    Written in the same transaction as the change it records, so the two
    cannot diverge. If the audit write fails, the operation fails - the one
    place where a logging failure is allowed to fail the request, because an
    unaudited privileged action is worse than a failed one.
    """

    # The single documented exemption from the R-10 suspension write block.
    # A suspended organization must still accrue audit entries - including the
    # record of each *denied* write - or the period a customer most needs a
    # trail is the period that has none. Safe because this table is
    # append-only and holds no business state.
    tenancy_write_block_exempt = True

    # Overrides TenantScopedModel's cascading FK.
    #
    # Audit outlives the organization it describes: on tenant purge, audit is
    # **retained** as a tombstone while business data is deleted
    # (08-data-ownership.md section 6). A cascading FK would delete the record
    # of a deletion, which is precisely the event most worth keeping - and it
    # would also fail outright, because the application role has no DELETE on
    # this table.
    #
    # db_constraint=False removes the database-level FK while keeping the
    # column, so RLS still scopes the table and customers still read only
    # their own log.
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        db_index=True,
        related_name="+",
    )

    occurred_at = models.DateTimeField(db_index=True)

    actor_kind = models.CharField(
        max_length=24, choices=[(k.value, k.name.title()) for k in ActorKind]
    )
    actor_user_id = models.UUIDField(null=True, blank=True, db_index=True)
    actor_membership_id = models.UUIDField(null=True, blank=True)
    #: Impersonation target, when a staff member acted as someone.
    on_behalf_of = models.UUIDField(null=True, blank=True)

    action = models.CharField(max_length=128, db_index=True)
    resource_type = models.CharField(max_length=128)
    resource_id = models.CharField(max_length=128, blank=True, default="")

    #: Field-level before/after, already redacted by the service. Never holds
    #: secrets or full PII - see audit/services.py.
    changes = models.JSONField(null=True, blank=True)

    outcome = models.CharField(
        max_length=16,
        choices=[(o.value, o.name.title()) for o in AuditOutcome],
        default=AuditOutcome.SUCCESS.value,
    )
    #: Required for break-glass and overrides.
    reason = models.TextField(blank=True, default="")

    #: Hashed, not stored raw: an IP address is personal data, and the audit
    #: log is customer-readable.
    ip_hash = models.CharField(max_length=64, blank=True, default="")
    correlation_id = models.UUIDField(null=True, blank=True, db_index=True)

    #: Tamper-evident chain, per organization. Silent modification of an
    #: earlier entry breaks every subsequent hash, which a scheduled
    #: verification job detects.
    prev_hash = models.CharField(max_length=64, blank=True, default="")
    entry_hash = models.CharField(max_length=64)

    class Meta:
        db_table = "audit_event"
        indexes = [
            models.Index(
                fields=["organization", "-occurred_at"], name="ix_audit_org_time"
            ),
            models.Index(fields=["organization", "action"], name="ix_audit_org_action"),
            models.Index(
                fields=["organization", "actor_user_id"], name="ix_audit_org_actor"
            ),
            models.Index(
                fields=["organization", "resource_type", "resource_id"],
                name="ix_audit_org_resource",
            ),
            models.Index(
                fields=["organization", "correlation_id"], name="ix_audit_org_corr"
            ),
        ]
        # No ordering by default: an unbounded ordered scan of an audit table
        # is exactly the query that gets slow first.

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.action} on {self.resource_type}:{self.resource_id}"

    def compute_hash(self) -> str:
        """Hash over the entry's immutable content plus the previous hash.

        ``sort_keys`` and a fixed separator make the serialisation canonical,
        so the same entry always hashes identically. Without that, a rebuild
        that happened to order keys differently would appear to be tampering.
        """
        material = json.dumps(
            {
                "organization_id": str(self.organization_id),
                "occurred_at": self.occurred_at.isoformat(),
                "actor_kind": self.actor_kind,
                "actor_user_id": str(self.actor_user_id or ""),
                "actor_membership_id": str(self.actor_membership_id or ""),
                "action": self.action,
                "resource_type": self.resource_type,
                "resource_id": self.resource_id,
                "changes": self.changes,
                "outcome": self.outcome,
                "prev_hash": self.prev_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Append-only at the application layer too.

        The database trigger is the real enforcement; this raises earlier with
        a message that explains *why*, which is more useful than a raw
        ``restrict_violation`` from PostgreSQL.
        """
        if self._state.adding is False:
            raise PermissionError(
                "AuditEvent is append-only. Corrections are new entries, "
                "never edits: an audit trail that can be rewritten is not an "
                "audit trail (11-observability.md section 9)."
            )
        if not self.entry_hash:
            self.entry_hash = self.compute_hash()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any):
        raise PermissionError(
            "AuditEvent is append-only and cannot be deleted. Retention is "
            "enforced by partition drops, not by row deletion."
        )

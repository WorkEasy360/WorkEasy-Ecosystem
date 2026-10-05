"""The due-work timer table.

**Not a ``TenantScopedModel``, deliberately** - the same exemption, for the
same reason, as ``events.OutboxEvent`` (W360.E001 ``TENANT_SCOPE_EXEMPT``,
debt-register D-017, ADR-024). It must be claimable across tenants without a
tenant context, and it carries nothing a tenant owns: a kind, two
identifiers and a time. The work itself - the delivery, the execution, the
rule - stays in its tenant-scoped, RLS-protected table, and the task that
does it enters that one organization's scope before reading anything.
"""

from __future__ import annotations

from django.db import models


class DueWork(models.Model):
    """One pending unit of work and when it is next due.

    ``(kind, target_id)`` is unique: a unit of work has exactly one timer,
    and re-scheduling it moves the timer rather than adding a second one.
    """

    id = models.BigAutoField(primary_key=True)
    #: Which consumer handles it, e.g. ``webhooks.delivery``. A bounded,
    #: code-declared set - see ``services.register_dispatcher``.
    kind = models.CharField(max_length=48)
    #: Routing only: which tenant scope the consumer must enter.
    organization_id = models.UUIDField()
    #: The tenant-scoped row the work is about.
    target_id = models.UUIDField()
    due_at = models.DateTimeField()
    #: Dispatches since the consumer last acknowledged the timer. Drives the
    #: growing claim lease, so a backlog is re-dispatched ever less often
    #: rather than once per lease forever.
    claims = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "scheduling_due_work"
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "target_id"], name="uq_due_work_kind_target"
            )
        ]
        indexes = [
            # Exactly the claim predicate's order. Without it the sweeper
            # scans every future timer to find the few that are due.
            models.Index(fields=["due_at", "id"], name="ix_due_work_due"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.kind}:{self.target_id} @ {self.due_at}"

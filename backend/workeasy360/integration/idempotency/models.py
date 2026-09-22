"""The idempotency ledger.

Tenant-scoped rather than an infrastructure table: a command key is issued by
one organization's client, and a key from tenant A must never resolve a
command for tenant B. Making it a ``TenantScopedModel`` means RLS, the
suspension block and the parametrised isolation suite all apply without this
module arranging any of it.
"""

from __future__ import annotations

from django.db import models

from workeasy360.platform.tenancy.models import TenantScopedModel


class IdempotencyRecord(TenantScopedModel):
    """One claimed command.

    Inserted **inside the business transaction**, before the effect. If the
    command fails, the record rolls back with it and the retry genuinely
    retries. Inserting it first in its own transaction produces the classic
    bug where a crash mid-command permanently marks work done that never was.
    """

    #: The command family, e.g. ``commercial.subscription.activate``. Scoping
    #: by command means a client may reuse one request id across two
    #: different operations without them colliding.
    scope = models.CharField(max_length=64)
    #: Caller-supplied. Opaque to us.
    key = models.CharField(max_length=128)

    #: SHA-256 of the normalised arguments. Replaying a key with different
    #: arguments is refused rather than served: otherwise one authorisation
    #: could be made to perform two different operations.
    fingerprint = models.CharField(max_length=64)

    #: A soft reference to what the command produced
    #: (``subscriptions.subscription:<uuid>``), so a duplicate returns the
    #: original answer rather than a bare "already done".
    result_ref = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "idempotency_record"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "scope", "key"],
                name="uq_idempotency_org_scope_key",
            )
        ]
        indexes = [
            models.Index(
                fields=["organization", "scope", "key"], name="ix_idem_org_scope_key"
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.scope}:{self.key}"

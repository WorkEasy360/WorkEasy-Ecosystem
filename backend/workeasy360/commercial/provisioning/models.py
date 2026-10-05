"""Provisioning's one table: a read model of what each customer holds.

``CustomerSummary`` exists for a single reason. Every table that says what a
customer bought - subscriptions, entitlements, seats - is tenant-scoped, and a
tenant table returns **no rows** without a tenant variable. The Super Admin
customer directory must page through customers and show each one's
subscription status and products, and it must do so without entering one
organization's scope per row (which is exactly the N+1 the directory is
forbidden to have).

So provisioning - which already computes each organization's desired state in
full on every reconciliation - writes the headline of that computation here,
in the same call. It is:

- **derived**, never authoritative: nothing reads it to decide access, and
  entitlement checks never touch it;
- **rebuildable**: ``reconcile_all`` rewrites every row from durable state,
  so losing the table loses nothing;
- **control-plane**, not tenant-scoped, for the reason above. It carries an
  ``organization`` reference and no customer business data - product codes
  and a status, the same facts the customer's own entitlements page shows -
  and is read only through ``selectors.summaries_for_organizations``, which
  serves staff and, filtered to the partner's own relationships, partners.

Seat *assignment* is deliberately not summarised: it changes without a
reconciliation, so a copy would drift, and the authoritative count is one
query away in the customer detail (Task 15: do not duplicate seat accounting).
Only the **capacity** the entitlement computation produced is recorded.
"""

from __future__ import annotations

from django.db import models


class CustomerSummary(models.Model):
    organization = models.OneToOneField(
        "organizations.Organization",
        primary_key=True,
        on_delete=models.CASCADE,
        related_name="+",
    )
    #: The open subscription's status, or empty when there is none.
    subscription_status = models.CharField(max_length=16, blank=True, default="")
    #: Products that granted access at the moment of computation.
    product_codes = models.JSONField(default=list)
    #: Total seat capacity across pools, or null when nothing is seated.
    seat_capacity = models.IntegerField(null=True, blank=True)
    #: Phase 9: the organization's commercial access - ``customer`` (a paid
    #: or granted product is live), ``trial`` (only trials) or empty (nothing:
    #: its people are Explorers). Derived from the same computation as
    #: ``product_codes``; what the Super Admin's user segments are built on.
    access_class = models.CharField(max_length=16, blank=True, default="")
    #: The entitlement snapshot version this summary was computed with.
    entitlement_version = models.BigIntegerField(default=0)
    computed_at = models.DateTimeField()

    class Meta:
        db_table = "provisioning_customer_summary"
        indexes = [
            models.Index(fields=["subscription_status"], name="ix_custsummary_status"),
            models.Index(fields=["access_class"], name="ix_custsummary_access"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"summary of {self.organization_id}"

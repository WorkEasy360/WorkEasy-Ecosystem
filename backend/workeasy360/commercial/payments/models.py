"""Payment records.

Two control-plane tables (no organization: the platform's provider accounts
and the log of provider callbacks, which arrive before anyone knows whose they
are) and four tenant tables (an organization's invoices, their lines, the
attempts to pay them, and refunds), RLS-protected with composite foreign keys
like every other tenant table (tenancy migration 0010).

**Money** is integer minor units with the currency beside it, never a float
(R-08) - the same representation as prices and subscription snapshots.

**No card data, anywhere.** Checkout happens on the provider's hosted page;
what comes back is the provider's own identifiers (a checkout session or
payment link, a payment, a refund). ``checkout_url`` is the hosted page's
address - a short-lived link the payer is sent to, cleared once the attempt
is finished.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone

from workeasy360.platform.tenancy.models import TenantScopedModel


class ProviderAdapter(models.TextChoices):
    STRIPE = "stripe", "Stripe"
    RAZORPAY = "razorpay", "Razorpay"
    #: Development and tests only: a hosted page served by the platform's own
    #: frontend. Refused whenever ``PAYMENTS_FAKE_PROVIDER_ENABLED`` is off,
    #: which production pins.
    FAKE = "fake", "Test provider (development only)"


class PaymentProvider(models.Model):
    """A payment account the platform collects through. **Control plane.**

    Holds a *reference* to its credentials (``credential_ref``,
    ``webhook_secret_ref``), never a secret: the value lives in the
    deployment's secret manager as ``W360_PAYMENT_CREDENTIAL_<REF>`` /
    ``W360_PAYMENT_WEBHOOK_SECRET_<REF>`` and is read per call.
    """

    class Mode(models.TextChoices):
        TEST = "test", "Test"
        LIVE = "live", "Live"

    class Status(models.TextChoices):
        ENABLED = "enabled", "Enabled"
        DISABLED = "disabled", "Disabled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    #: URL-safe identity, part of the webhook URL (``/public/payments/webhooks/<key>``).
    key = models.SlugField(max_length=32, unique=True)
    adapter = models.CharField(max_length=16, choices=ProviderAdapter.choices)
    display_name = models.CharField(max_length=64)
    mode = models.CharField(max_length=8, choices=Mode.choices, default=Mode.TEST)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.DISABLED
    )
    credential_ref = models.CharField(max_length=64)
    webhook_secret_ref = models.CharField(max_length=64)
    #: ISO codes this account may collect. Empty = none (a provider that
    #: takes nothing is not a provider).
    currencies = models.JSONField(default=list)
    #: Lower wins when two enabled providers take the same currency.
    priority = models.IntegerField(default=100)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payments_provider"
        ordering = ["priority", "key"]

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.key


class Order(TenantScopedModel):
    """A customer's request to buy something, priced by the server and paid
    **before** anything is granted (Phase 9, pay-first).

    ``pending_payment`` -> ``paid`` (a signed webhook or staff settled its
    invoice) -> ``fulfilled`` (the subscription line exists and provisioning
    has been told). Otherwise it ends ``cancelled`` (by the customer),
    ``expired`` (unpaid past ``expires_at``) or ``failed`` (paid, but what it
    bought could no longer be added - the payment is refunded automatically).

    A trial, a free plan and a sales-led (``invoice``/``partner_billed``)
    account owe nothing now: their orders are recorded ``fulfilled`` at once,
    with nothing due, so the history is complete.

    **Nothing here came from the browser but identifiers**: the price is the
    server's resolution at placement, frozen in ``price_snapshot`` and charged
    through the order's invoice; fulfilment uses the frozen price, never a new
    one. At most one order per organization may await payment (a partial
    unique index), which is what makes "two orders for one product" and
    "paid twice for the same thing" unrepresentable rather than merely checked.
    """

    class Kind(models.TextChoices):
        PLAN = "plan", "Plan"
        BUNDLE = "bundle", "Bundle"
        #: Converting a trial to paid.
        ACTIVATION = "activation", "Activation"

    class Status(models.TextChoices):
        PENDING_PAYMENT = "pending_payment", "Awaiting payment"
        PAID = "paid", "Paid"
        FULFILLED = "fulfilled", "Fulfilled"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"
        FAILED = "failed", "Failed"

    #: ``ORD-000123``, from a database sequence like invoice numbers.
    number = models.CharField(max_length=32, unique=True)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING_PAYMENT,
        db_index=True,
    )
    plan_version = models.ForeignKey(
        "catalog.PlanVersion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    bundle_version = models.ForeignKey(
        "catalog.BundleVersion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    #: The trial an activation converts; after fulfilment, the subscription
    #: the order's line joined.
    subscription = models.ForeignKey(
        "subscriptions.Subscription",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    #: Optional bundle components the buyer declined (plan version ids).
    declined_plan_version_ids = models.JSONField(default=list, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    billing_interval = models.CharField(max_length=16)
    currency = models.CharField(max_length=3)
    #: What the buyer sees in listings: "CRM Professional x1 (monthly)".
    description = models.CharField(max_length=255)
    #: The products the order grants, for listings - codes only.
    product_codes = models.JSONField(default=list, blank=True)
    #: The agreed price per billing period, and what is owed now (the same
    #: for a new subscription; the prorated rest of the current period when a
    #: line joins a running one).
    recurring_amount_minor = models.BigIntegerField(default=0)
    amount_due_minor = models.BigIntegerField(default=0)
    #: ``PriceQuote.as_snapshot()`` at placement - what fulfilment charges.
    price_snapshot = models.JSONField(default=dict, blank=True)
    is_trial = models.BooleanField(default=False)
    placed_by_user_id = models.UUIDField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=128)
    expires_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.CharField(max_length=64, blank=True, default="")
    #: The line fulfilment created (a soft reference, like invoice lines).
    subscription_item_id = models.UUIDField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "payments_order"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                name="uq_order_org_idempotency",
            ),
            models.UniqueConstraint(
                fields=["id", "organization"], name="uq_order_id_org"
            ),
            models.UniqueConstraint(
                fields=["organization"],
                condition=models.Q(status="pending_payment"),
                name="uq_order_one_pending",
            ),
            models.CheckConstraint(
                condition=models.Q(amount_due_minor__gte=0)
                & models.Q(recurring_amount_minor__gte=0)
                & models.Q(quantity__gte=1),
                name="ck_order_amounts",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(kind="plan", plan_version__isnull=False)
                    | models.Q(kind="bundle", bundle_version__isnull=False)
                    | models.Q(kind="activation", subscription__isnull=False)
                ),
                name="ck_order_names_what_it_buys",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "-created_at"], name="ix_order_org_created"
            ),
            models.Index(
                fields=["organization", "status", "expires_at"],
                name="ix_order_org_status",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.number


class OrderSummary(models.Model):
    """The staff view of every order, across organizations. **Control plane**,
    like ``provisioning.CustomerSummary``: an order is a tenant row, and the
    Super Admin's order list cannot enter one organization's scope per row.

    Derived and written by ``orders`` in the same transaction as the order, on
    every status change; identifiers, codes and amounts only - no name, email
    or address. Read only through ``orders.staff_orders`` behind
    ``platform.staff_payment.view``.
    """

    order_id = models.UUIDField(primary_key=True)
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="+"
    )
    number = models.CharField(max_length=32)
    kind = models.CharField(max_length=16)
    status = models.CharField(max_length=20)
    description = models.CharField(max_length=255)
    product_codes = models.JSONField(default=list, blank=True)
    currency = models.CharField(max_length=3)
    amount_due_minor = models.BigIntegerField(default=0)
    created_at = models.DateTimeField()
    paid_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payments_order_summary"
        indexes = [
            models.Index(
                fields=["-created_at", "-order_id"], name="ix_order_summary_time"
            ),
            models.Index(
                fields=["status", "-created_at"], name="ix_order_summary_status"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.number


class Invoice(TenantScopedModel):
    """What an organization owes: for one subscription period, or for one
    order (Phase 9).

    A period invoice is issued once per ``(subscription, period_start)`` - the
    unique constraint is what makes issuance idempotent under a retried clock,
    a duplicate listener call or two workers at once. Built from the price
    snapshots on the subscription's lines, so it reproduces exactly what was
    agreed.

    An order invoice (``order`` set) is what a pay-first purchase is paid
    through. It exists before the subscription does, so ``subscription`` is
    empty until fulfilment; fulfilling a new subscription then names it and
    that subscription's first period, so the period invoicing finds the period
    already invoiced (and paid) and never bills it twice.
    """

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        PAID = "paid", "Paid"
        VOID = "void", "Void"

    subscription = models.ForeignKey(
        "subscriptions.Subscription",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    order = models.OneToOneField(
        Order,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="invoice",
    )
    #: Globally unique and sequential (``INV-000123``), from a database
    #: sequence - never derived from a count, which two issuers would share.
    number = models.CharField(max_length=32, unique=True)
    status = models.CharField(
        max_length=8, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    currency = models.CharField(max_length=3)
    total_minor = models.BigIntegerField()
    amount_paid_minor = models.BigIntegerField(default=0)
    amount_refunded_minor = models.BigIntegerField(default=0)
    billing_interval = models.CharField(max_length=16)
    collection_mode = models.CharField(max_length=16)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    issued_at = models.DateTimeField(default=timezone.now)
    due_at = models.DateTimeField()
    paid_at = models.DateTimeField(null=True, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=255, blank=True, default="")
    #: How far dunning has gone *because of this invoice*: "" | past_due |
    #: grace | suspended. Settlement lifts a suspension only when this invoice
    #: caused it (``subscriptions.system.settle_after_payment``).
    dunning_stage = models.CharField(max_length=16, blank=True, default="")
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "payments_invoice"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "subscription", "period_start"],
                name="uq_invoice_subscription_period",
            ),
            models.UniqueConstraint(
                fields=["id", "organization"], name="uq_invoice_id_org"
            ),
            models.CheckConstraint(
                condition=models.Q(total_minor__gte=0)
                & models.Q(amount_paid_minor__gte=0)
                & models.Q(amount_refunded_minor__gte=0)
                & models.Q(amount_refunded_minor__lte=models.F("amount_paid_minor")),
                name="ck_invoice_amounts",
            ),
            models.CheckConstraint(
                condition=models.Q(subscription__isnull=False)
                | models.Q(order__isnull=False),
                name="ck_invoice_for_subscription_or_order",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "status", "due_at"], name="ix_invoice_org_status"
            ),
            models.Index(
                fields=["organization", "-issued_at"], name="ix_invoice_org_issued"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.number

    @property
    def amount_due_minor(self) -> int:
        return max(0, self.total_minor - self.amount_paid_minor)


class InvoiceLine(TenantScopedModel):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    #: The priced subscription line this came from (a soft reference: the
    #: line's own history is immutable, and the invoice keeps its copy). An
    #: order invoice's line has none until the order is fulfilled.
    subscription_item_id = models.UUIDField(null=True, blank=True)
    description = models.CharField(max_length=255)
    product_code = models.CharField(max_length=64, blank=True, default="")
    plan_code = models.CharField(max_length=64, blank=True, default="")
    bundle_code = models.CharField(max_length=64, blank=True, default="")
    quantity = models.IntegerField()
    unit_amount_minor = models.BigIntegerField(null=True, blank=True)
    amount_minor = models.BigIntegerField()

    class Meta:
        db_table = "payments_invoice_line"
        indexes = [
            models.Index(fields=["organization", "invoice"], name="ix_invoice_line_org")
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.description


class PaymentAttempt(TenantScopedModel):
    """One attempt to pay one invoice through one provider.

    ``pending`` - created, the hosted checkout not yet obtained (a task is
    asking the provider, outside any transaction); ``awaiting_payment`` - the
    payer has a checkout page; then exactly one of ``succeeded``, ``failed``,
    ``expired``, ``cancelled``. Every move is a conditional update on the
    current status, so a webhook, the clock and a retry that race each settle
    it once.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        AWAITING_PAYMENT = "awaiting_payment", "Awaiting payment"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    invoice = models.ForeignKey(
        Invoice, on_delete=models.PROTECT, related_name="attempts"
    )
    #: ``manual`` for a payment a Super Admin recorded (a bank transfer).
    provider_key = models.CharField(max_length=32)
    provider_mode = models.CharField(max_length=8, blank=True, default="")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3)
    amount_refunded_minor = models.BigIntegerField(default=0)
    #: The caller's command id: a retried "pay" returns the same attempt.
    idempotency_key = models.CharField(max_length=128)
    #: The provider's checkout session / payment link id.
    provider_reference = models.CharField(max_length=128, blank=True, default="")
    #: The provider's payment id, once there is one - what a refund names.
    provider_payment_id = models.CharField(max_length=128, blank=True, default="")
    checkout_url = models.TextField(blank=True, default="")
    failure_code = models.CharField(max_length=64, blank=True, default="")
    #: Who started it (a member) or recorded it (staff, for a manual one).
    created_by_user_id = models.UUIDField(null=True, blank=True)
    reference_note = models.CharField(max_length=255, blank=True, default="")
    expires_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "payments_attempt"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                name="uq_attempt_org_idempotency",
            ),
            models.UniqueConstraint(
                fields=["provider_key", "provider_reference"],
                condition=~models.Q(provider_reference=""),
                name="uq_attempt_provider_reference",
            ),
            models.UniqueConstraint(
                fields=["id", "organization"], name="uq_attempt_id_org"
            ),
            models.CheckConstraint(
                condition=models.Q(amount_minor__gt=0)
                & models.Q(amount_refunded_minor__gte=0)
                & models.Q(amount_refunded_minor__lte=models.F("amount_minor")),
                name="ck_attempt_amounts",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "invoice", "status"],
                name="ix_attempt_org_invoice",
            ),
            models.Index(
                fields=["organization", "status", "expires_at"],
                name="ix_attempt_org_expiry",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.provider_key}:{self.status}"


class Refund(TenantScopedModel):
    """Money returned against a succeeded attempt. Staff-initiated.

    ``requested`` (a task will ask the provider) -> ``submitting`` (the call is
    in flight) -> ``pending`` (the provider accepted it and will confirm) ->
    ``succeeded`` | ``failed``. A refund found still ``submitting`` after a
    crash is **not** retried blindly for a provider without idempotent refunds:
    it becomes ``needs_review``, because sending it twice returns money twice.
    """

    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        SUBMITTING = "submitting", "Submitting"
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        NEEDS_REVIEW = "needs_review", "Needs review"

    attempt = models.ForeignKey(
        PaymentAttempt, on_delete=models.PROTECT, related_name="refunds"
    )
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3)
    reason = models.CharField(max_length=255)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.REQUESTED, db_index=True
    )
    provider_refund_id = models.CharField(max_length=128, blank=True, default="")
    failure_code = models.CharField(max_length=64, blank=True, default="")
    requested_by_user_id = models.UUIDField()
    #: The staff member's command id: a double-submitted refund is one refund.
    idempotency_key = models.CharField(max_length=128)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "payments_refund"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount_minor__gt=0), name="ck_refund_amount_positive"
            ),
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                name="uq_refund_org_idempotency",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "attempt"], name="ix_refund_org_attempt"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"refund {self.amount_minor} {self.currency}"


class PaymentWebhookEvent(models.Model):
    """Every verified provider callback, once. **Control plane**: a callback
    arrives before anyone knows which organization it concerns.

    ``(provider_key, event_id)`` is unique - the de-duplication that makes a
    provider's at-least-once delivery apply exactly once. Stores what was
    *decided*, never the payload (which carries payer details the platform
    has no need to keep).
    """

    class Outcome(models.TextChoices):
        APPLIED = "applied", "Applied"
        IGNORED = "ignored", "Ignored"
        UNMATCHED = "unmatched", "No matching payment"
        MISMATCH = "mismatch", "Amount or currency mismatch"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider_key = models.CharField(max_length=32)
    event_id = models.CharField(max_length=128)
    kind = models.CharField(max_length=32)
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    organization_id = models.UUIDField(null=True, blank=True)
    attempt_id = models.UUIDField(null=True, blank=True)
    #: For a payment success only: what the provider reported collecting. The
    #: Super Admin dashboard totals collections from these control-plane rows
    #: rather than scanning every tenant's invoices (a cross-tenant query is
    #: not permitted).
    amount_minor = models.BigIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True, default="")
    payload_digest = models.CharField(max_length=64)
    received_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "payments_webhook_event"
        constraints = [
            models.UniqueConstraint(
                fields=["provider_key", "event_id"], name="uq_webhook_event_once"
            )
        ]
        indexes = [models.Index(fields=["-received_at"], name="ix_webhook_event_time")]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.provider_key}:{self.event_id}"

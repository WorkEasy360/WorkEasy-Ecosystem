"""The ledger, what it charges for and what arrived to be charged.

All control plane, the same class as ``commissions.CommissionAccrual``: a wallet's
owner is an organization or a partner, so one tenant variable could never hold them
all, and the Admin's lists span every owner. Customers reach their own rows only
through functions that take the organization from a verified context. Amounts and
identifiers only.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class Wallet(models.Model):
    class Kind(models.TextChoices):
        PLATFORM = "platform", "Platform Wallet"
        AI_CREDITS = "ai_credits", "AI Credits"
        CONSUMPTION = "consumption", "Consumption Wallet"

    class OwnerType(models.TextChoices):
        ORGANIZATION = "organization", "Organization"
        PARTNER = "partner", "Partner"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    owner_type = models.CharField(max_length=16, choices=OwnerType.choices)
    #: A soft reference: a partner and an organization live in different tables.
    owner_id = models.UUIDField()
    #: Minor units of this currency, or empty for AI Credits, which are whole credits.
    currency = models.CharField(max_length=3, blank=True, default="")
    #: Changed only by ``ledger``, in the same transaction as a new entry.
    balance = models.BigIntegerField(default=0)
    #: What counts as low for this wallet. Entered by an Admin, never assumed.
    low_balance_threshold = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "wallets_wallet"
        constraints = [
            models.UniqueConstraint(
                fields=["owner_type", "owner_id", "kind", "currency"],
                name="uq_wallet_owner_kind_currency",
            ),
            # The balance can never be overdrawn, whatever the code above it does.
            models.CheckConstraint(
                condition=Q(balance__gte=0), name="ck_wallet_balance_never_negative"
            ),
            # A partner holds a Platform Wallet only.
            models.CheckConstraint(
                condition=Q(owner_type="organization") | Q(kind="platform"),
                name="ck_wallet_partner_platform_only",
            ),
            # Credits have no currency; money always does.
            models.CheckConstraint(
                condition=(Q(kind="ai_credits") & Q(currency=""))
                | (~Q(kind="ai_credits") & ~Q(currency="")),
                name="ck_wallet_currency_matches_kind",
            ),
        ]
        indexes = [
            models.Index(fields=["kind", "-balance"], name="ix_wallet_kind_balance"),
            models.Index(fields=["owner_id"], name="ix_wallet_owner"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.kind}:{self.owner_id}"

    @property
    def is_low(self) -> bool:
        threshold = self.low_balance_threshold
        return threshold is not None and self.balance < threshold


class WalletEntry(models.Model):
    """One movement. Never edited or deleted (database grant and trigger); a mistake
    is answered by a reversal that names this row."""

    class Type(models.TextChoices):
        TOP_UP = "top_up", "Top-up"
        CREDIT_PURCHASE = "credit_purchase", "Credit pack purchase"
        SUBSCRIPTION = "subscription", "Subscription deduction"
        USAGE = "usage", "Usage charge"
        REFUND = "refund", "Refund"
        ADJUSTMENT = "adjustment", "Manual adjustment"
        REVERSAL = "reversal", "Reversal"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="entries")
    entry_type = models.CharField(max_length=16, choices=Type.choices)
    #: Signed: positive adds to the balance, negative takes from it.
    amount = models.BigIntegerField()
    balance_after = models.BigIntegerField()
    reason = models.CharField(max_length=255)
    #: Who did it, when a person did. Empty for an automatic charge.
    actor_user_id = models.UUIDField(null=True, blank=True)
    #: What caused it: ``order``, ``invoice``, ``usage_event``, ``refund``...
    source_type = models.CharField(max_length=32, blank=True, default="")
    source_id = models.CharField(max_length=64, blank=True, default="")
    #: The original transaction a reversal or refund answers.
    reverses = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversals"
    )
    #: The same key is the same movement, however often it is retried.
    idempotency_key = models.CharField(max_length=128, unique=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "wallets_entry"
        constraints = [
            models.CheckConstraint(condition=~Q(amount=0), name="ck_wallet_entry_not_zero"),
            models.CheckConstraint(
                condition=Q(balance_after__gte=0), name="ck_wallet_entry_balance_after"
            ),
            models.CheckConstraint(
                condition=~Q(entry_type="reversal") | Q(reverses__isnull=False),
                name="ck_wallet_reversal_names_original",
            ),
        ]
        indexes = [
            models.Index(fields=["wallet", "-created_at"], name="ix_wallet_entry_wallet"),
            models.Index(fields=["-created_at", "-id"], name="ix_wallet_entry_time"),
            models.Index(fields=["entry_type", "-created_at"], name="ix_wallet_entry_type"),
            models.Index(fields=["source_type", "source_id"], name="ix_wallet_entry_source"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.entry_type} {self.amount}"


class UsageRate(models.Model):
    """What one meter costs, as an Admin entered it. Versioned: a change closes the
    live row and opens another, so a past charge always names the rate it used. With
    no live rate a meter is not charged at all."""

    class Meter(models.TextChoices):
        AI_TOKENS = "ai_tokens", "AI tokens"
        WHATSAPP = "whatsapp", "WhatsApp messages"
        SMS = "sms", "SMS messages"
        EMAIL = "email", "Emails"
        API_TRIGGER = "api_trigger", "Billable API triggers"
        WEBHOOK = "webhook", "Webhook deliveries"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    meter = models.CharField(max_length=16, choices=Meter.choices)
    #: Empty for AI tokens, which charge whole credits.
    currency = models.CharField(max_length=3, blank=True, default="")
    #: Charged per ``per_units``: minor units of the currency, or credits for AI.
    unit_amount = models.BigIntegerField()
    per_units = models.PositiveIntegerField(default=1)
    #: Units included free each calendar month, per organization.
    included_units = models.BigIntegerField(default=0)
    effective_from = models.DateTimeField(default=timezone.now)
    effective_to = models.DateTimeField(null=True, blank=True)
    created_by_user_id = models.UUIDField()
    reason = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "wallets_usage_rate"
        constraints = [
            models.UniqueConstraint(
                fields=["meter", "currency"],
                condition=Q(effective_to__isnull=True),
                name="uq_usage_rate_one_live",
            ),
            models.CheckConstraint(
                condition=Q(unit_amount__gt=0) & Q(per_units__gt=0) & Q(included_units__gte=0),
                name="ck_usage_rate_positive",
            ),
            models.CheckConstraint(
                condition=(Q(meter="ai_tokens") & Q(currency=""))
                | (~Q(meter="ai_tokens") & ~Q(currency="")),
                name="ck_usage_rate_currency_matches_meter",
            ),
        ]
        indexes = [models.Index(fields=["meter", "-effective_from"], name="ix_usage_rate_meter")]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.meter} {self.unit_amount}/{self.per_units}"


class CreditPack(models.Model):
    """An AI credit pack on sale, as an Admin entered it. Archived rather than
    deleted: an order may name it."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(max_length=48, unique=True)
    name = models.CharField(max_length=120)
    credits = models.BigIntegerField()
    price_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "wallets_credit_pack"
        constraints = [
            models.CheckConstraint(
                condition=Q(credits__gt=0) & Q(price_minor__gt=0), name="ck_credit_pack_positive"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.code


class UsageEvent(models.Model):
    """One billable thing as it arrived from a product, once. Charged, failed or
    included: all three are kept, so a failure can be reviewed and retried. A repeat
    of the same key adds to ``duplicate_count`` and charges nothing."""

    class Outcome(models.TextChoices):
        CHARGED = "charged", "Charged"
        INCLUDED = "included", "Within the free allowance"
        NOT_RATED = "not_rated", "No rate set"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization_id = models.UUIDField()
    product_code = models.CharField(max_length=64, blank=True, default="")
    meter = models.CharField(max_length=16, choices=UsageRate.Meter.choices)
    quantity = models.BigIntegerField()
    idempotency_key = models.CharField(max_length=128)
    occurred_at = models.DateTimeField()
    received_at = models.DateTimeField(default=timezone.now)
    outcome = models.CharField(max_length=12, choices=Outcome.choices)
    failure_code = models.CharField(max_length=32, blank=True, default="")
    #: The units of ``quantity`` beyond the free allowance, and what they cost.
    charged_units = models.BigIntegerField(default=0)
    amount = models.BigIntegerField(default=0)
    rate = models.ForeignKey(
        UsageRate, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    entry = models.ForeignKey(
        WalletEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    duplicate_count = models.PositiveIntegerField(default=0)
    last_duplicate_at = models.DateTimeField(null=True, blank=True)
    #: A person looked at a failed or duplicated event.
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by_user_id = models.UUIDField(null=True, blank=True)
    review_note = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "wallets_usage_event"
        constraints = [
            models.UniqueConstraint(
                fields=["organization_id", "product_code", "idempotency_key"],
                name="uq_usage_event_once",
            ),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="ck_usage_event_quantity"),
            models.CheckConstraint(
                condition=Q(charged_units__lte=F("quantity")), name="ck_usage_event_charged_units"
            ),
        ]
        indexes = [
            models.Index(fields=["-received_at", "-id"], name="ix_usage_event_time"),
            models.Index(fields=["outcome", "-received_at"], name="ix_usage_event_outcome"),
            models.Index(
                fields=["organization_id", "meter", "occurred_at"], name="ix_usage_event_org_meter"
            ),
            models.Index(fields=["product_code", "occurred_at"], name="ix_usage_event_product"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.meter} x{self.quantity} [{self.outcome}]"

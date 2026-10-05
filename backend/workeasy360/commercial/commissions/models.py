"""Commission, referral and payout data (Phase 7, ADR-029).

**Classification: partner-scoped control plane**, like ``commercial/partners``
and for the same reason - none of it is a customer's data (see that module's
docstring). ``CommissionAccrual.organization_id`` is a plain UUID, deliberately
*not* a foreign key: the ledger must outlive a purged customer, and a
``PROTECT`` reference would either block the purge or, worse, tempt someone to
delete ledger rows to unblock it.

**The ledger is append-only, in the database.** ``CommissionAccrual`` accepts
INSERT and SELECT only: the migration revokes UPDATE and DELETE from the
application role and installs a trigger that raises on either, so a bug, a
data-fix script or a compromised application cannot rewrite what a partner was
told it earned (F2). A correction is a *new row* - a reversal or an
adjustment - referencing the one it corrects, and the balance is the sum.
``PayoutLine`` ties a ledger row to the payout that pays it; that link is the
only mutable state around the ledger, and it is not on the ledger.

**Money is integer minor units plus a currency, never a float** (F1). Rates
are integer basis points.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db import models

from workeasy360.contracts.enums import (
    AccrualKind,
    GrantStatus,
    PayoutStatus,
    ReferralStatus,
)
from workeasy360.contracts.values.money import CurrencyCode


def _choices(enum_cls: Any) -> list[tuple[str, str]]:
    return [(member.value, member.name.replace("_", " ").title()) for member in enum_cls]


class ReferralCode(models.Model):
    """A public referral code belonging to one partner.

    The code is **not a secret** - it is meant to appear in a URL - so it is
    short, unambiguous to read aloud, and grants nothing: knowing one lets you
    record that you were referred, and that is all. It carries no customer
    data and no partner id.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    partner = models.ForeignKey(
        "partners.PartnerAccount", on_delete=models.PROTECT, related_name="referral_codes"
    )
    code = models.CharField(max_length=16, unique=True)
    #: A campaign label the partner chose. Free-form text, escaped on render.
    label = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(
        max_length=16, choices=_choices(GrantStatus), default=GrantStatus.ACTIVE.value
    )
    created_by = models.ForeignKey(
        "identity.User", on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "commissions_referral_code"
        indexes = [models.Index(fields=["partner", "status"], name="ix_refcode_partner")]

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.code


class Referral(models.Model):
    """One captured referral, and - once - its conversion into a customer.

    **Attribution rule: first touch, fixed at conversion.** A prospect may
    carry several captured referrals; the customer is attributed to the
    partner of the *one that is converted*, and a converted referral is never
    re-pointed. ``organization_id`` is unique, so a customer is attributed at
    most once, ever (Task 39). The rule is stored on the row so a later
    change of default does not reinterpret history.

    **No personal data.** There is no email, name or IP here: only a hash of
    the bearer token (so the row is not itself a credential), the partner's
    code, timestamps and - after conversion - the customer's organization id.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.ForeignKey(
        ReferralCode, on_delete=models.PROTECT, related_name="referrals"
    )
    #: Denormalised from the code so partner-scoped lists need no join.
    partner = models.ForeignKey(
        "partners.PartnerAccount", on_delete=models.PROTECT, related_name="referrals"
    )
    #: SHA-256 of the capture token. The token itself is returned once to the
    #: visitor's browser and never stored: a database read must not yield a
    #: token that could be redeemed against a customer.
    token_hash = models.CharField(max_length=64, unique=True)
    status = models.CharField(
        max_length=16,
        choices=_choices(ReferralStatus),
        default=ReferralStatus.CAPTURED.value,
    )
    captured_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    converted_at = models.DateTimeField(null=True, blank=True)
    organization = models.OneToOneField(
        "organizations.Organization",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    attribution_rule = models.CharField(max_length=24, default="first_touch")

    class Meta:
        db_table = "commissions_referral"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status="converted", organization__isnull=False)
                | ~models.Q(status="converted"),
                name="ck_referral_converted_has_customer",
            ),
        ]
        indexes = [
            models.Index(
                fields=["partner", "status", "-captured_at"], name="ix_referral_partner"
            ),
            models.Index(fields=["status", "expires_at"], name="ix_referral_retention"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"referral {self.id} [{self.status}]"


class CommissionRule(models.Model):
    """One version of a commission rate.

    **A rule row is a fact, not a setting.** Changing a percentage creates the
    next version and retires the previous one; an accrual records the exact
    ``(rule, version, rate)`` it was computed under, so an old sale keeps its
    old rate forever and nothing recalculates retroactively (Task 43). Every
    column except ``retired_at`` is frozen by a database trigger.

    ``partner`` null is the programme default; a partner-specific rule takes
    precedence over it, and a product-specific rule over an all-products one
    (``domain.select_rule``).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(max_length=48)
    version = models.PositiveIntegerField()
    partner = models.ForeignKey(
        "partners.PartnerAccount",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="commission_rules",
    )
    #: Empty applies to every product.
    product_code = models.CharField(max_length=32, blank=True, default="")
    rate_bp = models.PositiveIntegerField()
    effective_from = models.DateTimeField()
    retired_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=200)
    created_by = models.ForeignKey(
        "identity.User", on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "commissions_rule"
        constraints = [
            models.UniqueConstraint(
                fields=["code", "version"], name="uq_rule_code_version"
            ),
            models.UniqueConstraint(
                fields=["code"],
                condition=models.Q(retired_at__isnull=True),
                name="uq_rule_one_live_version",
            ),
            models.CheckConstraint(
                condition=models.Q(rate_bp__lte=10000), name="ck_rule_rate_at_most_100pct"
            ),
        ]
        indexes = [
            models.Index(
                fields=["partner", "product_code", "effective_from"],
                name="ix_rule_selection",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.code} v{self.version} ({self.rate_bp}bp)"


class CommissionAccrual(models.Model):
    """One ledger entry. Append-only. Never edited, never deleted.

    ``kind`` says what it is: an ``accrual`` (earned on a sale, amount > 0), a
    ``reversal`` (offsets an earlier entry, amount < 0, ``reverses`` set) or an
    ``adjustment`` (a staff correction, any non-zero amount, reason required).
    A partner's balance is the sum of its rows, so every figure is
    reproducible from the ledger alone.

    ``idempotency_key`` is unique, and it is what makes double accrual
    *impossible* rather than merely unlikely: the key for a sale is derived
    from the sale (``accrual:<item id>``), so two racing workers, a retried
    task and a manual re-run all collapse to one row.

    The commercial inputs are **snapshotted**: the sale amount, the rate and
    the rule version are stored beside the result, so an entry can be
    re-derived and audited without trusting any table that may since have
    changed.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    partner = models.ForeignKey(
        "partners.PartnerAccount", on_delete=models.PROTECT, related_name="accruals"
    )
    #: Soft reference; see the module docstring.
    organization_id = models.UUIDField(null=True, blank=True)
    kind = models.CharField(max_length=16, choices=_choices(AccrualKind))
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3, choices=_choices(CurrencyCode))
    #: ``subscriptions.item:<uuid>`` for a sale; ``ledger.entry:<uuid>`` for a
    #: reversal of one; ``manual`` for an adjustment.
    source_ref = models.CharField(max_length=128)
    event_at = models.DateTimeField()
    clears_at = models.DateTimeField()

    rule = models.ForeignKey(
        CommissionRule,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="accruals",
    )
    rule_version = models.PositiveIntegerField(null=True, blank=True)
    rate_bp = models.PositiveIntegerField(null=True, blank=True)
    basis_minor = models.BigIntegerField(null=True, blank=True)
    basis_currency = models.CharField(max_length=3, blank=True, default="")
    attribution_kind = models.CharField(max_length=16, blank=True, default="")
    attribution_ref = models.CharField(max_length=64, blank=True, default="")

    reverses = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reversals",
    )
    reason_code = models.CharField(max_length=32, blank=True, default="")
    reason = models.CharField(max_length=200, blank=True, default="")
    created_by = models.ForeignKey(
        "identity.User",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    idempotency_key = models.CharField(max_length=160, unique=True)

    class Meta:
        db_table = "commissions_accrual"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    kind="accrual", amount_minor__gt=0, reverses__isnull=True
                )
                | models.Q(kind="reversal", amount_minor__lt=0, reverses__isnull=False)
                | models.Q(kind="adjustment", reverses__isnull=True)
                & ~models.Q(amount_minor=0),
                name="ck_accrual_kind_matches_shape",
            ),
            models.CheckConstraint(
                condition=~models.Q(kind="adjustment") | ~models.Q(reason=""),
                name="ck_adjustment_has_a_reason",
            ),
        ]
        indexes = [
            models.Index(
                fields=["partner", "-event_at", "-id"], name="ix_accrual_partner"
            ),
            models.Index(
                fields=["partner", "currency", "clears_at"], name="ix_accrual_payable"
            ),
            models.Index(fields=["source_ref"], name="ix_accrual_source"),
            models.Index(fields=["organization_id"], name="ix_accrual_org"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.kind} {self.amount_minor} {self.currency}"


class Payout(models.Model):
    """A batch of ledger rows prepared for payment. **Payment is not executed here.**

    ``draft -> ready -> approved -> paid``, or ``cancelled`` from any state
    before ``paid``. ``paid`` records that a staff member confirmed, with an
    external reference, that money left by some other channel; this platform
    moves no money (Task 46-47). The totals are snapshotted when the payout is
    prepared and re-derived from its lines at approval; a mismatch refuses
    approval (Task 105).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    partner = models.ForeignKey(
        "partners.PartnerAccount", on_delete=models.PROTECT, related_name="payouts"
    )
    currency = models.CharField(max_length=3, choices=_choices(CurrencyCode))
    period_end = models.DateField()
    status = models.CharField(
        max_length=16, choices=_choices(PayoutStatus), default=PayoutStatus.DRAFT.value
    )

    gross_minor = models.BigIntegerField(default=0)
    adjustments_minor = models.BigIntegerField(default=0)
    payable_minor = models.BigIntegerField(default=0)
    line_count = models.PositiveIntegerField(default=0)

    version = models.IntegerField(default=0)
    created_by = models.ForeignKey(
        "identity.User", on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        "identity.User",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approval_reason = models.CharField(max_length=200, blank=True, default="")
    paid_at = models.DateTimeField(null=True, blank=True)
    paid_by = models.ForeignKey(
        "identity.User",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    #: The external transfer reference a staff member confirms. Deliberately a
    #: short opaque string: no account numbers, no sort codes, no card data.
    paid_reference = models.CharField(max_length=64, blank=True, default="")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        "identity.User",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    cancel_reason = models.CharField(max_length=200, blank=True, default="")

    idempotency_key = models.CharField(max_length=160, unique=True)

    class Meta:
        db_table = "commissions_payout"
        constraints = [
            # Nothing is marked paid without an explicit confirmation.
            models.CheckConstraint(
                condition=~models.Q(status="paid")
                | (
                    models.Q(paid_at__isnull=False, paid_by__isnull=False)
                    & ~models.Q(paid_reference="")
                ),
                name="ck_payout_paid_is_confirmed",
            ),
            # Approval names a person other than the preparer.
            models.CheckConstraint(
                condition=models.Q(approved_by__isnull=True)
                | ~models.Q(approved_by=models.F("created_by")),
                name="ck_payout_approver_is_not_the_preparer",
            ),
            models.CheckConstraint(
                condition=models.Q(payable_minor__gte=0),
                name="ck_payout_payable_non_negative",
            ),
        ]
        indexes = [
            models.Index(
                fields=["partner", "-created_at", "-id"], name="ix_payout_partner"
            ),
            models.Index(fields=["status", "-created_at"], name="ix_payout_status"),
            # The staff payout list, unfiltered: newest first, keyset.
            models.Index(fields=["-created_at", "-id"], name="ix_payout_created_id"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"payout {self.id} [{self.status}]"

    @property
    def status_enum(self) -> PayoutStatus:
        return PayoutStatus(self.status)


class PayoutLine(models.Model):
    """Ties one ledger row to the payout that pays it.

    ``accrual`` is unique while unreleased, so one ledger entry can be in at
    most one *live* payout - the database, not a check, is what stops a
    commission being paid twice. Cancelling a payout **releases** its lines
    (``released_at``) so the entries return to the payable pool; the rows stay
    as history.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payout = models.ForeignKey(Payout, on_delete=models.PROTECT, related_name="lines")
    accrual = models.ForeignKey(
        CommissionAccrual, on_delete=models.PROTECT, related_name="payout_lines"
    )
    #: Snapshot of the entry's amount at the moment it was included.
    amount_minor = models.BigIntegerField()
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "commissions_payout_line"
        constraints = [
            models.UniqueConstraint(
                fields=["accrual"],
                condition=models.Q(released_at__isnull=True),
                name="uq_ledger_entry_in_one_live_payout",
            ),
        ]
        indexes = [models.Index(fields=["payout"], name="ix_payoutline_payout")]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"payout {self.payout_id} pays {self.accrual_id}"

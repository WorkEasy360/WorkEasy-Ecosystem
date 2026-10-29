"""Commission accrual: derived from immutable commercial facts, never from AI,
never from a client, never inside the sale's own transaction (Phase 7).

**Why accrual runs after the sale, not within it.** A partner's commission is
a consequence of a sale; the customer's purchase must never be held hostage to
it. So the sale commits first, and accrual is a separate step that reads what
was durably written - the priced ``SubscriptionItem``, which a trigger freezes
- and derives the commission from it. If accrual fails, the sale is
unaffected, the failure is counted and logged, and the next run (the change
listener, the scheduled sweep, or an operator's ``reconcile``) produces the
same answer.

**Why running it many times is safe.** The ledger row's idempotency key is
derived from the sale (``accrual:subscriptions.item:<id>``) and is unique in
the database. Eight racing workers, a retried task and a manual re-run all
converge on one row; the losers hit the unique index, roll back their
savepoint and count a duplicate. Nothing here relies on a lock or on the
caller being alone.

**Why it can be checked.** ``evaluate_sale`` is a pure function of the sale
fact, the attribution, the partner's capability history and the rule set -
all of it durable and time-stamped - so ``reconcile`` can recompute what
*should* exist and report what is missing, duplicated or computed under the
wrong rule, without rewriting anything.

**A trial is not a sale** (Phase 7 final audit). A line sold into a trial
earns nothing until the subscription is *activated* - converted to paid - and
then earns as of that moment: attribution, capability, rule and the holdback
all run from the conversion, when money is first owed. A trial that lapses
unconverted never earns. Before this, commission accrued on the trial's list
price the moment it started and was never reversed when the trial expired, so
a partner could provision a customer, start a trial and be paid for a sale
the platform itself knew never happened.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

# Phase 12 (G-32): SoftTimeLimitExceeded subclasses Exception. Swallowed
# here, a pass that outran its budget kept going until the hard kill, with
# no summary, and the organizations enumerated last were never reached.
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from workeasy360.commercial.catalog import selectors as catalog_selectors
from workeasy360.commercial.commissions import domain, selectors
from workeasy360.commercial.partners import capabilities
from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.contracts.enums import AccrualKind
from workeasy360.contracts.events import partner_events as schemas
from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.platform.audit import services as audit
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

COMMISSION_ACCRUALS = "commission_accruals_total"
COMMISSION_FAILURES = "commission_accrual_failures_total"

#: Attribution by referral earns only on sales within this many days of the
#: referral converting (Task 39: an explicit, bounded window).
REFERRAL_EARNING_DAYS: Final = 90
_IMMEDIATE_CANCEL_TOLERANCE: Final = timedelta(seconds=2)


def holdback_days() -> int:
    return int(getattr(settings, "W360_COMMISSION_HOLDBACK_DAYS", 30))


@dataclass(frozen=True, slots=True)
class Evaluation:
    """What one sale is worth to whom - or why it is worth nothing."""

    accrues: bool
    reason: str = ""
    partner_id: UUID | None = None
    rule: domain.RuleView | None = None
    basis_minor: int = 0
    amount_minor: int = 0
    currency: str = ""
    attribution_kind: str = ""
    attribution_ref: str = ""
    #: ADR-053: set when the sale earns a margin instead of a commission.
    margin: domain.MarginView | None = None
    cost_minor: int | None = None
    margin_rate_bp: int = 0
    #: ADR-054: ``payments.invoice_line:<uuid>`` - the paid transaction.
    payment_ref: str = ""

    @property
    def earning(self) -> str:
        return "margin" if self.margin is not None else "commission"

    @property
    def version(self) -> int | None:
        if self.margin is not None:
            return self.margin.version
        return self.rule.version if self.rule is not None else None

    @property
    def rate_bp(self) -> int | None:
        if self.margin is not None:
            return self.margin_rate_bp
        return self.rule.rate_bp if self.rule is not None else None


#: ``(fact, moment) -> list price of the fact's line in its currency``, or
#: ``None`` when the catalog has none. Injected so evaluation stays a pure
#: function of its inputs in tests.
ListPrice = Callable[[Any, datetime], "int | None"]


def list_total(fact: Any, moment: datetime) -> int | None:
    """The line's **list** price at ``moment``: the default direct book, never
    a contract or partner price. Price books are effective-dated and immutable
    once published, so the answer for a past moment is stable."""
    from workeasy360.commercial.pricing.resolution import resolve_price
    from workeasy360.contracts.enums import BillingInterval
    from workeasy360.contracts.errors import PriceNotAvailable

    plan_version_id = getattr(fact, "plan_version_id", None)
    bundle_version_id = getattr(fact, "bundle_version_id", None)
    if (plan_version_id is None) == (bundle_version_id is None):
        return None
    try:
        quote = resolve_price(
            plan_version_id=plan_version_id,
            bundle_version_id=bundle_version_id,
            organization_id=None,
            currency=CurrencyCode(fact.currency),
            interval=BillingInterval(fact.interval),
            quantity=max(int(fact.quantity or 1), 1),
            at=moment,
        )
    except (PriceNotAvailable, ValueError):
        return None
    return quote.total.minor_units


def _experience_at(partner_id: UUID, moment: datetime) -> str:
    """The experience whose margin default applies: White-label when the
    partner held its brand capability at the sale, else Reseller."""
    if partner_selectors.had_capability_at(
        partner_id, capabilities.BRANDING_MANAGE, moment
    ):
        return capabilities.WHITE_LABEL
    return capabilities.RESELLER


def _margin_evaluation(
    *,
    partner_id: UUID,
    kind: str,
    ref: str,
    product_code: str,
    currency: str,
    customer_minor: int,
    basis_minor: int,
    whole: bool,
    fact: Any,
    moment: datetime,
    margins: Iterable[domain.MarginView],
    list_price: ListPrice | None,
    renewal: bool = False,
) -> Evaluation | None:
    """A managed sale under a margin rule earns ``customer price - partner
    cost`` instead of a commission (ADR-053, never both). ``None`` when no
    margin rule governs it - the commission rules then apply as before."""
    if kind != "managed":
        return None
    margin = domain.select_margin(
        margins,
        partner_id=partner_id,
        experience=_experience_at(partner_id, moment),
        product_code=product_code,
        at=moment,
    )
    if margin is None:
        return None
    if renewal and not margin.recurring:
        return _skip("not_recurring")
    list_minor = (list_price or list_total)(fact, moment) if fact is not None else None
    if list_minor is None:
        return _skip("no_list_price")
    cost = domain.partner_cost(
        list_minor=list_minor,
        currency=CurrencyCode(currency),
        discount_bp=margin.discount_bp,
    )
    rate = domain.margin_bp(customer_minor=customer_minor, cost_minor=cost)
    if customer_minor - cost <= 0 or rate <= 0:
        return _skip("no_margin")
    amount = (
        customer_minor - cost
        if whole
        else domain.commission_amount(
            basis_minor=basis_minor, currency=CurrencyCode(currency), rate_bp=rate
        )
    )
    if amount <= 0:
        return _skip("no_margin")
    return Evaluation(
        accrues=True,
        partner_id=partner_id,
        basis_minor=basis_minor,
        amount_minor=amount,
        currency=currency,
        attribution_kind=kind,
        attribution_ref=ref,
        margin=margin,
        cost_minor=cost,
        margin_rate_bp=rate,
    )


def _skip(reason: str) -> Evaluation:
    return Evaluation(accrues=False, reason=reason)


def sale_moment(fact: Any) -> datetime | None:
    """When ``fact`` became a paid sale: the later of the line opening and the
    subscription's activation. ``None`` while it has never been activated."""
    activated_at = getattr(fact, "subscription_activated_at", None)
    if activated_at is None:
        return None
    return max(fact.added_at, activated_at)


def evaluate_sale(
    fact: Any,
    *,
    rules: list[domain.RuleView],
    now: datetime,
    margins: Iterable[domain.MarginView] = (),
    list_price: ListPrice | None = None,
    payment: dict[str, Any] | None = None,
) -> Evaluation:
    """Decide the commission (or margin) for one sale. Pure given its durable
    inputs and the list-price resolver.

    ``payment`` is the sale's first paid invoice line (``paid_item_lines``),
    or ``None`` while it is unpaid. ADR-054: a **referral** earns only on a
    paid sale - a commission names the payment it was earned on - and a
    product link earns only on the product it promoted."""
    moment = sale_moment(fact)
    if moment is None:
        return _skip("not_activated")
    if moment > now:
        return _skip("not_yet_effective")

    attribution = _attribute(fact, moment)
    if attribution is None:
        return _skip("no_attribution")
    partner_id, kind, ref, promoted = attribution
    if kind == "referral":
        problem = _referral_problem(fact.product_code, promoted)
        if problem is not None:
            return _skip(problem)
        if payment is None:
            return _skip("awaiting_payment")
    payment_ref = _payment_ref(payment)

    problem = _partner_problem(partner_id, moment, fact.currency)
    if problem is not None:
        return _skip(problem)

    basis = domain.sale_basis(
        total_minor=fact.total_amount_minor,
        predecessor_total_minor=fact.predecessor_total_minor,
    )
    if basis <= 0:
        return _skip("no_basis")

    margin = _margin_evaluation(
        partner_id=partner_id,
        kind=kind,
        ref=ref,
        product_code=fact.product_code,
        currency=fact.currency,
        customer_minor=fact.total_amount_minor,
        basis_minor=basis,
        whole=fact.predecessor_total_minor is None,
        fact=fact,
        moment=moment,
        margins=margins,
        list_price=list_price,
    )
    if margin is not None:
        return replace(margin, payment_ref=payment_ref) if margin.accrues else margin

    rule = domain.select_rule(
        rules, partner_id=partner_id, product_code=fact.product_code, at=moment
    )
    if rule is None:
        return _skip("no_rule")

    amount = domain.commission_amount(
        basis_minor=basis, currency=CurrencyCode(fact.currency), rate_bp=rule.rate_bp
    )
    if amount <= 0:
        return _skip("zero_commission")
    return Evaluation(
        accrues=True,
        partner_id=partner_id,
        rule=rule,
        basis_minor=basis,
        amount_minor=amount,
        currency=fact.currency,
        attribution_kind=kind,
        attribution_ref=ref,
        payment_ref=payment_ref,
    )


def _referral_problem(product_code: str, promoted: str) -> str | None:
    """Why a referral cannot earn on a sale of ``product_code``, or ``None``."""
    if "affiliate" not in catalog_selectors.channels_open(product_code):
        # ADR-051: Product Manager -> Availability -> Affiliate "Not allowed".
        # Read when the sale is evaluated; an accrual already written stands.
        return "product_not_open_to_affiliates"
    if promoted and promoted != product_code:
        # ADR-054: a product link earns on the product it promoted.
        return "not_the_promoted_product"
    return None


def _payment_ref(line: dict[str, Any] | None) -> str:
    return f"payments.invoice_line:{line['line_id']}" if line else ""


def _partner_problem(partner_id: UUID, moment: datetime, currency: str) -> str | None:
    """Why this partner cannot earn at ``moment`` in ``currency``, or ``None``."""
    partner = partner_selectors.partner_row(partner_id)
    if partner is None:
        return "no_attribution"
    if partner.terminated_at is not None and partner.terminated_at <= moment:
        return "partner_terminated"
    if not partner_selectors.had_capability_at(
        partner_id, capabilities.COMMISSION_EARN, moment
    ):
        return "not_entitled_to_earn"
    if currency != partner.payout_currency:
        # No conversion is ever attempted (Task 92). Reported, not hidden.
        return "unsupported_currency"
    return None


# --- recurring commission on renewals (ADR-052) ----------------------------


@dataclass(frozen=True, slots=True)
class Renewal:
    """One paid renewal of a sale: an invoice line for a later period of a
    subscription line whose first paid period was the sale itself."""

    organization_id: UUID
    source_ref: str
    product_code: str
    amount_minor: int
    currency: str
    paid_at: datetime
    refunded: bool
    #: The subscription line renewed (ADR-053: a margin renewal re-prices it).
    item_id: str = ""


def renewals_of(
    organization_id: UUID, lines_by_item: dict[str, list[dict[str, Any]]] | None = None
) -> list[Renewal]:
    """Every paid renewal of this customer's subscription lines. The first
    paid line of each subscription line is the sale (it earns through
    ``evaluate_sale``); every later one is a renewal."""
    from workeasy360.commercial.payments.selectors import paid_item_lines

    if lines_by_item is None:
        lines_by_item = paid_item_lines(organization_id)
    out = []
    for item_id, lines in lines_by_item.items():
        for line in lines[1:]:
            if line["paid_at"] is None:
                continue
            out.append(
                Renewal(
                    organization_id=organization_id,
                    source_ref=f"payments.invoice_line:{line['line_id']}",
                    product_code=line["product_code"],
                    amount_minor=line["amount_minor"],
                    currency=line["currency"],
                    paid_at=line["paid_at"],
                    refunded=line["refunded"],
                    item_id=item_id,
                )
            )
    return out


def evaluate_renewal(
    renewal: Renewal,
    *,
    rules: list[domain.RuleView],
    now: datetime,
    margins: Iterable[domain.MarginView] = (),
    fact: Any = None,
    list_price: ListPrice | None = None,
) -> Evaluation:
    """Decide the commission for one paid renewal: only under a **recurring**
    rule, attributed as of the payment. A referral attributes a renewal for
    as long as the referred customer stays (the rule's own choice to recur);
    a managed relationship outranks it, as for a sale."""
    moment = renewal.paid_at
    if moment > now:
        return _skip("not_yet_effective")
    if renewal.refunded:
        return _skip("refunded")
    link = partner_selectors.link_at(renewal.organization_id, moment)
    if link is not None:
        partner_id, kind, ref = link.partner_id, "managed", str(link.id)
    else:
        referral = selectors.referral_conversion_for(renewal.organization_id, moment)
        if referral is None:
            return _skip("no_attribution")
        partner_id, kind, ref = referral.partner_id, "referral", str(referral.id)
        problem = _referral_problem(renewal.product_code, referral.code.product_code)
        if problem is not None:
            return _skip(problem)
    problem = _partner_problem(partner_id, moment, renewal.currency)
    if problem is not None:
        return _skip(problem)
    margin = _margin_evaluation(
        partner_id=partner_id,
        kind=kind,
        ref=ref,
        product_code=renewal.product_code,
        currency=renewal.currency,
        customer_minor=renewal.amount_minor,
        basis_minor=renewal.amount_minor,
        whole=True,
        fact=fact,
        moment=moment,
        margins=margins,
        list_price=list_price,
        renewal=True,
    )
    if margin is not None:
        return (
            replace(margin, payment_ref=renewal.source_ref) if margin.accrues else margin
        )
    rule = domain.select_rule(
        rules, partner_id=partner_id, product_code=renewal.product_code, at=moment
    )
    if rule is None:
        return _skip("no_rule")
    if not rule.recurring:
        return _skip("not_recurring")
    amount = domain.commission_amount(
        basis_minor=renewal.amount_minor,
        currency=CurrencyCode(renewal.currency),
        rate_bp=rule.rate_bp,
    )
    if amount <= 0:
        return _skip("zero_commission")
    return Evaluation(
        accrues=True,
        partner_id=partner_id,
        rule=rule,
        basis_minor=renewal.amount_minor,
        amount_minor=amount,
        currency=renewal.currency,
        attribution_kind=kind,
        attribution_ref=ref,
        payment_ref=renewal.source_ref,
    )


def _accrue_renewals(
    organization_id: UUID,
    *,
    rules: list[domain.RuleView],
    margins: list[domain.MarginView],
    facts: dict[str, Any],
    lines_by_item: dict[str, list[dict[str, Any]]],
    now: datetime,
    stats: AccrualStats,
) -> None:
    from workeasy360.commercial.commissions.models import CommissionAccrual

    renewals = renewals_of(organization_id, lines_by_item)
    for renewal in renewals:
        fact = facts.get(renewal.item_id)
        evaluation = evaluate_renewal(
            renewal, rules=rules, now=now, margins=margins, fact=fact
        )
        if not evaluation.accrues:
            stats.skip(f"renewal_{evaluation.reason}")
            continue
        if _insert_accrual(
            renewal,
            evaluation,
            moment=renewal.paid_at,
            plan_code=getattr(fact, "plan_code", ""),
        ):
            stats.created += 1
        else:
            stats.duplicates += 1
    # A renewal refunded while its commission is still in the holdback is
    # reversed - the same no-judgement clawback as an immediate cancellation.
    refunded = {r.source_ref for r in renewals if r.refunded}
    if not refunded:
        return
    for accrual in CommissionAccrual.objects.filter(
        organization_id=organization_id,
        kind=AccrualKind.ACCRUAL.value,
        source_ref__in=refunded,
        clears_at__gt=now,
    ):
        if insert_reversal(
            accrual,
            amount_minor=-accrual.amount_minor,
            reason_code="refund",
            reason="The renewal's invoice was refunded inside the holdback window.",
            created_by_id=None,
        ):
            stats.reversed += 1


def _attribute(fact: Any, moment: datetime) -> tuple[UUID, str, str, str] | None:
    """Who earns on this customer at the time of the sale, and - for a
    product link - the product it promoted (ADR-054; empty otherwise).

    A managed partner relationship outranks a referral: a customer is served
    by one partner and pays one commission (Task 89). Both are evaluated
    *as of the sale*, so ending a relationship later does not retroactively
    unearn what was earned while it stood.
    """
    link = partner_selectors.link_at(fact.organization_id, moment)
    if link is not None:
        return link.partner_id, "managed", str(link.id), ""
    referral = selectors.referral_conversion_for(fact.organization_id, moment)
    if referral is not None and (
        moment - referral.converted_at <= timedelta(days=REFERRAL_EARNING_DAYS)
    ):
        return (
            referral.partner_id,
            "referral",
            str(referral.id),
            referral.code.product_code,
        )
    return None


@dataclass(slots=True)
class AccrualStats:
    created: int = 0
    duplicates: int = 0
    reversed: int = 0
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def accrue_for_organization(
    organization_id: UUID, *, now: datetime | None = None
) -> AccrualStats:
    """Derive and record every commission this customer's sales have earned.

    Idempotent and safe to call from anywhere, any number of times, at any
    concurrency. Also records the automatic **reversal** of a commission whose
    sale was cancelled with immediate effect inside its holdback window - the
    one clawback that needs no human judgement.
    """
    from workeasy360.commercial.commissions.referrals import record_trial
    from workeasy360.commercial.payments.selectors import paid_item_lines
    from workeasy360.commercial.subscriptions.selectors import sale_facts

    moment = now or timezone.now()
    stats = AccrualStats()
    record_trial(organization_id)
    facts = sale_facts(organization_id)
    if not facts:
        return stats
    rules = selectors.rule_views()
    margins = selectors.margin_views()
    lines_by_item = paid_item_lines(organization_id)
    first_paid = {item: lines[0] for item, lines in lines_by_item.items() if lines}

    for fact in facts:
        evaluation = evaluate_sale(
            fact,
            rules=rules,
            now=moment,
            margins=margins,
            payment=first_paid.get(str(fact.item_id)),
        )
        if not evaluation.accrues:
            stats.skip(evaluation.reason)
            continue
        created = _insert_accrual(fact, evaluation)
        if created:
            stats.created += 1
        else:
            stats.duplicates += 1

    stats.reversed += _reverse_cancelled_sales(organization_id, facts, moment)
    stats.reversed += _reverse_refunded_sales(organization_id, first_paid, moment)
    if _any_recurring(rules, margins):
        _accrue_renewals(
            organization_id,
            rules=rules,
            margins=margins,
            facts={str(f.item_id): f for f in facts},
            lines_by_item=lines_by_item,
            now=moment,
            stats=stats,
        )
    return stats


def _any_recurring(
    rules: list[domain.RuleView], margins: list[domain.MarginView]
) -> bool:
    return any(r.recurring for r in rules) or any(m.recurring for m in margins)


def _insert_accrual(
    fact: Any,
    evaluation: Evaluation,
    *,
    moment: datetime | None = None,
    plan_code: str | None = None,
) -> bool:
    """Insert one ledger row; ``False`` if it already exists. ``moment`` is
    the event's time when it is not a sale's (a renewal's payment)."""
    from workeasy360.commercial.commissions.models import CommissionAccrual

    key = domain.accrual_key(fact.source_ref)
    moment = moment or sale_moment(fact)
    if moment is None:  # pragma: no cover - evaluate_sale refuses these first
        return False
    try:
        # The savepoint keeps the unique violation from poisoning a caller's
        # transaction: PostgreSQL aborts the whole transaction on a violation.
        with transaction.atomic():
            row = CommissionAccrual.objects.create(
                partner_id=evaluation.partner_id,
                organization_id=fact.organization_id,
                kind=AccrualKind.ACCRUAL.value,
                amount_minor=evaluation.amount_minor,
                currency=evaluation.currency,
                source_ref=fact.source_ref,
                event_at=moment,
                clears_at=domain.clears_at(moment, holdback_days()),
                rule_id=evaluation.rule.id if evaluation.rule is not None else None,
                rule_version=evaluation.version,
                rate_bp=evaluation.rate_bp,
                basis_minor=evaluation.basis_minor,
                basis_currency=evaluation.currency,
                attribution_kind=evaluation.attribution_kind,
                attribution_ref=evaluation.attribution_ref,
                product_code=getattr(fact, "product_code", "") or "",
                plan_code=(
                    plan_code if plan_code is not None else getattr(fact, "plan_code", "")
                )
                or "",
                earning=evaluation.earning,
                margin_rule_id=evaluation.margin.id if evaluation.margin else None,
                cost_minor=evaluation.cost_minor,
                payment_ref=evaluation.payment_ref,
                idempotency_key=key,
            )
            _announce(row, "commission.accrued", schemas.COMMISSION_ACCRUED_V1)
    except IntegrityError:
        metrics.increment(COMMISSION_ACCRUALS, outcome="duplicate")
        return False
    metrics.increment(COMMISSION_ACCRUALS, outcome="created")
    return True


def _reverse_cancelled_sales(
    organization_id: UUID, facts: tuple[Any, ...], now: datetime
) -> int:
    """Reverse accruals whose subscription was cancelled at once, in the holdback."""
    from workeasy360.commercial.commissions.models import CommissionAccrual

    by_source = {fact.source_ref: fact for fact in facts}
    rows = list(
        CommissionAccrual.objects.filter(
            organization_id=organization_id, kind=AccrualKind.ACCRUAL.value
        )
    )
    reversed_count = 0
    for accrual in rows:
        fact = by_source.get(accrual.source_ref)
        if fact is None or fact.subscription_cancelled_at is None:
            continue
        cancelled_at, effective_at = (
            fact.subscription_cancelled_at,
            fact.subscription_cancel_at,
        )
        if (
            effective_at is None
            or effective_at - cancelled_at > _IMMEDIATE_CANCEL_TOLERANCE
        ):
            continue  # cancelled at period end: the customer used what it paid for
        if not accrual.event_at <= cancelled_at < accrual.clears_at:
            continue  # outside the holdback window
        if insert_reversal(
            accrual,
            amount_minor=-accrual.amount_minor,
            reason_code="cancellation",
            reason="Subscription cancelled with immediate effect inside the holdback window.",
            created_by_id=None,
        ):
            reversed_count += 1
    return reversed_count


def _reverse_refunded_sales(
    organization_id: UUID, first_paid: dict[str, dict[str, Any]], now: datetime
) -> int:
    """ADR-054: reverse the commission on a sale whose paying invoice was
    refunded while the commission is still in the holdback - in proportion to
    the refund (a half refund reverses half), topped up as further refunds
    land. Idempotent per refunded amount. After the holdback a refund is a
    staff decision (``ledger.reverse_accrual``), reported by ``reconcile``."""
    from django.db.models import Sum

    from workeasy360.commercial.commissions.models import CommissionAccrual

    refunded = {
        f"subscriptions.item:{item}": line
        for item, line in first_paid.items()
        if line.get("refunded_minor", 0) > 0 and line.get("invoice_paid_minor", 0) > 0
    }
    if not refunded:
        return 0
    count = 0
    for accrual in CommissionAccrual.objects.filter(
        organization_id=organization_id,
        kind=AccrualKind.ACCRUAL.value,
        source_ref__in=list(refunded),
        clears_at__gt=now,
    ):
        line = refunded[accrual.source_ref]
        target = -domain.refund_share(
            accrual.amount_minor,
            refunded_minor=line["refunded_minor"],
            paid_minor=line["invoice_paid_minor"],
        )
        already = (
            CommissionAccrual.objects.filter(
                reverses_id=accrual.id, reason_code="refund"
            ).aggregate(total=Sum("amount_minor"))["total"]
            or 0
        )
        if target < already and insert_reversal(
            accrual,
            amount_minor=target - already,
            reason_code="refund",
            reason="The sale's invoice was refunded inside the holdback window.",
            created_by_id=None,
            idempotency_key=(
                f"{domain.reversal_key(accrual.id, 'refund')}:{line['refunded_minor']}"
            ),
        ):
            count += 1
    return count


def insert_reversal(
    accrual: Any,
    *,
    amount_minor: int,
    reason_code: str,
    reason: str,
    created_by_id: UUID | None,
    idempotency_key: str | None = None,
    audited: bool = True,
) -> bool:
    """Append a reversal of ``accrual``. ``False`` if this exact one exists.

    The cumulative reversal of an entry may never exceed the entry, which is
    checked under a per-entry advisory lock: the ledger's application role has
    no UPDATE, so the entry cannot be row-locked, and two concurrent partial
    reversals must not each look at a half-spent balance.
    """
    from django.db import connection
    from django.db.models import Sum

    from workeasy360.commercial.commissions.models import CommissionAccrual

    key = idempotency_key or domain.reversal_key(accrual.id, reason_code)
    try:
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    [f"reversal:{accrual.id}"],
                )
            if CommissionAccrual.objects.filter(idempotency_key=key).exists():
                return False  # this exact reversal was already recorded
            already = (
                CommissionAccrual.objects.filter(reverses_id=accrual.id).aggregate(
                    total=Sum("amount_minor")
                )["total"]
                or 0
            )
            if already + amount_minor < -accrual.amount_minor:
                from workeasy360.contracts.errors import PartnerValidationError

                raise PartnerValidationError(
                    "Reversals cannot exceed the commission they reverse.",
                    field="amount_minor",
                )
            row = CommissionAccrual.objects.create(
                partner_id=accrual.partner_id,
                organization_id=accrual.organization_id,
                kind=AccrualKind.REVERSAL.value,
                amount_minor=amount_minor,
                currency=accrual.currency,
                source_ref=f"ledger.entry:{accrual.id}",
                event_at=timezone.now(),
                clears_at=timezone.now(),
                rule_id=accrual.rule_id,
                rule_version=accrual.rule_version,
                rate_bp=accrual.rate_bp,
                attribution_kind=accrual.attribution_kind,
                attribution_ref=accrual.attribution_ref,
                product_code=accrual.product_code,
                plan_code=accrual.plan_code,
                earning=accrual.earning,
                margin_rule_id=accrual.margin_rule_id,
                reverses_id=accrual.id,
                reason_code=reason_code,
                reason=reason[:200],
                created_by_id=created_by_id,
                idempotency_key=key,
            )
            _announce(
                row,
                "commission.reversed",
                schemas.COMMISSION_REVERSED_V1,
                reverses=accrual.id,
                audited=audited,
            )
    except IntegrityError:
        return False
    metrics.increment(COMMISSION_ACCRUALS, outcome="reversed")
    return True


def _announce(
    row: Any,
    action: str,
    schema: Any,
    *,
    reverses: UUID | None = None,
    audited: bool = True,
) -> None:
    """Audit and publish, in the ledger write's own transaction.

    Announced under the **platform organization**: the ledger is a
    control-plane fact and must not be tied to the customer's own audit log
    or webhooks, which is what keeps a customer from learning that a partner
    earns on it.
    """
    from workeasy360.contracts.enums import ActorKind
    from workeasy360.contracts.events.envelope import Actor, DomainEvent
    from workeasy360.integration.events import services as events
    from workeasy360.platform.tenancy.context import no_tenant
    from workeasy360.platform.tenancy.db import organization_db_scope

    payload: dict[str, Any] = {
        "partner_id": str(row.partner_id),
        "accrual_id": str(row.id),
    }
    if reverses is not None:
        payload["reverses_id"] = str(reverses)
        payload["reason_code"] = row.reason_code
    else:
        payload["source_ref"] = row.source_ref

    # No ambient tenant context: an accrual is usually written while the
    # caller is bound to the *customer's* organization (after a partner's
    # sale), and the platform-organization audit row must not be checked
    # against that binding. ``record_system`` is the no-actor audit path.
    with no_tenant(), organization_db_scope(PLATFORM_ORGANIZATION_ID):
        events.publish(
            DomainEvent(
                event_type=schema.event_type,
                event_version=1,
                organization_id=PLATFORM_ORGANIZATION_ID,
                aggregate_type=schema.aggregate_type,
                aggregate_id=str(row.id),
                payload=payload,
                actor=Actor(kind=ActorKind.SYSTEM, service_name="commercial.commissions"),
            )
        )
        if audited:
            audit.record_system(
                PLATFORM_ORGANIZATION_ID,
                action,
                resource_type="commissions.accrual",
                resource_id=row.id,
                changes={"partner_id": str(row.partner_id), "kind": row.kind},
                reason=(
                    "Commission derived deterministically from an immutable sale."
                    if reverses is None
                    else "Commission reversed: " + (row.reason_code or "reversal")
                ),
            )


# --- entry points ---------------------------------------------------------


def accrue_after_commercial_change(organization_id: UUID) -> None:
    """The ``on_commercial_change`` listener. **Never raises.**

    Registered with the same registry provisioning uses, so it fires after a
    sale commits. A failure is counted and logged and swallowed: a commission
    problem must not be reported to, or roll back, a customer's purchase - and
    the scheduled sweep and ``reconcile`` will catch what this missed.
    """
    try:
        with transaction.atomic():
            accrue_for_organization(organization_id)
    except Exception as exc:
        metrics.increment(COMMISSION_FAILURES)
        logger.error(
            "commission.accrual.listener_failed",
            organization_id=str(organization_id),
            error_type=type(exc).__name__,
        )


def candidate_organizations() -> list[UUID]:
    """Every customer that could have earned a commission for someone."""
    linked = partner_selectors.all_linked_organization_ids()
    return sorted({*linked, *selectors.converted_organization_ids()}, key=str)


def sweep(*, limit: int | None = None) -> dict[str, int]:
    """The catch-up pass: accrue for every candidate customer.

    Establishes its own trusted context - it enumerates from the control plane
    and each organization's read enters that organization's scope for itself,
    so nothing is inherited from a request or an eager-mode caller. One
    customer's failure is counted and does not stop the rest.
    """
    counts = {"organizations": 0, "created": 0, "reversed": 0, "failed": 0}
    targets = candidate_organizations()
    if limit:
        targets = targets[:limit]
    for organization_id in targets:
        counts["organizations"] += 1
        try:
            with transaction.atomic():
                stats = accrue_for_organization(organization_id)
            counts["created"] += stats.created
            counts["reversed"] += stats.reversed
        except SoftTimeLimitExceeded:
            raise
        except Exception as exc:
            counts["failed"] += 1
            metrics.increment(COMMISSION_FAILURES)
            logger.error(
                "commission.sweep.failed",
                organization_id=str(organization_id),
                error_type=type(exc).__name__,
            )
    return counts


# --- reconciliation -------------------------------------------------------


@dataclass(slots=True)
class ReconciliationReport:
    organizations: int = 0
    missing: list[dict[str, Any]] = field(default_factory=list)
    duplicates: list[dict[str, Any]] = field(default_factory=list)
    wrong: list[dict[str, Any]] = field(default_factory=list)
    unexpected: list[dict[str, Any]] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)

    @property
    def is_clean(self) -> bool:
        return not (self.missing or self.duplicates or self.wrong or self.unexpected)

    def as_dict(self) -> dict[str, Any]:
        return {
            "organizations": self.organizations,
            "is_clean": self.is_clean,
            "missing": self.missing,
            "duplicates": self.duplicates,
            "wrong": self.wrong,
            "unexpected": self.unexpected,
            "skipped": self.skipped,
        }


def reconcile(*, organization_ids: list[UUID] | None = None) -> ReconciliationReport:
    """Recompute what the ledger *should* hold and report every disagreement.

    **Read-only.** Nothing is created, changed or deleted; a discrepancy is
    reported for a person to decide. Repairs are explicit: ``accrue_missing``
    creates rows that are missing (and only those), and a wrongly computed
    row is corrected by the reversal/adjustment commands, never by rewriting.
    """
    from workeasy360.commercial.commissions.models import CommissionAccrual
    from workeasy360.commercial.payments.selectors import paid_item_lines
    from workeasy360.commercial.subscriptions.selectors import sale_facts

    report = ReconciliationReport()
    now = timezone.now()
    rules = selectors.rule_views()
    margins = selectors.margin_views()
    targets = (
        organization_ids if organization_ids is not None else candidate_organizations()
    )

    for organization_id in targets:
        report.organizations += 1
        rows: dict[str, list[Any]] = {}
        for row in CommissionAccrual.objects.filter(
            organization_id=organization_id, kind=AccrualKind.ACCRUAL.value
        ):
            rows.setdefault(row.source_ref, []).append(row)

        expected_refs: set[str] = set()
        facts = sale_facts(organization_id)
        lines_by_item = paid_item_lines(organization_id)
        evaluated = [
            (
                fact.source_ref,
                evaluate_sale(
                    fact,
                    rules=rules,
                    now=now,
                    margins=margins,
                    payment=(lines_by_item.get(str(fact.item_id)) or [None])[0],
                ),
            )
            for fact in facts
        ]
        if _any_recurring(rules, margins):
            by_item = {str(f.item_id): f for f in facts}
            evaluated += [
                (
                    renewal.source_ref,
                    evaluate_renewal(
                        renewal,
                        rules=rules,
                        now=now,
                        margins=margins,
                        fact=by_item.get(renewal.item_id),
                    ),
                )
                for renewal in renewals_of(organization_id, lines_by_item)
            ]
        for source_ref, evaluation in evaluated:
            existing = rows.get(source_ref, [])
            if not evaluation.accrues:
                report.skipped[evaluation.reason] = (
                    report.skipped.get(evaluation.reason, 0) + 1
                )
                # A refunded renewal's accrual is answered by its reversal.
                if existing and evaluation.reason != "refunded":
                    report.unexpected.append(
                        {"source_ref": source_ref, "why": evaluation.reason}
                    )
                continue
            expected_refs.add(source_ref)
            if not existing:
                report.missing.append(
                    {
                        "source_ref": source_ref,
                        "expected_minor": evaluation.amount_minor,
                    }
                )
            elif len(existing) > 1:
                report.duplicates.append(
                    {"source_ref": source_ref, "rows": [str(r.id) for r in existing]}
                )
            else:
                row = existing[0]
                if (
                    row.partner_id != evaluation.partner_id
                    or row.rule_version != evaluation.version
                    or row.rate_bp != evaluation.rate_bp
                    or row.amount_minor != evaluation.amount_minor
                ):
                    report.wrong.append(
                        {
                            "source_ref": source_ref,
                            "ledger_minor": row.amount_minor,
                            "expected_minor": evaluation.amount_minor,
                            "ledger_rule_version": row.rule_version,
                            "expected_rule_version": evaluation.version,
                        }
                    )
    return report


def accrue_missing(*, organization_ids: list[UUID]) -> dict[str, int]:
    """The explicit repair for *missing* rows: accrue for exactly these customers."""
    created = 0
    for organization_id in organization_ids:
        with transaction.atomic():
            created += accrue_for_organization(organization_id).created
    return {"created": created}

"""Commission rule administration: append a version, never edit one (Task 43).

**Assigning a rule to a partner (Phase 10).** The relationship between a
partner and its commission already exists in the schema: a rule row may name a
partner (``CommissionRule.partner``), ``domain.select_rule`` prefers the
partner's rule over the programme default, an accrual snapshots the rule, its
version and the rate it was computed under, and a database trigger freezes every
rule column but ``retired_at``. What was missing was the *use case* a person can
run: "this partner earns X% from this date". :func:`assign_partner_commission`
is that use case, and it adds nothing else - no table, no second engine, no
second way to compute a commission. It finds the partner's rule chain by
(partner, product), never by a naming convention, and appends the next version
through :func:`create_rule_version`, so everything that command guarantees
holds unchanged: effective-dated, never backdated, the predecessor retired at
the moment the successor starts, audited, dangerous, and old sales keep
the rule they were computed under because an accrual never re-reads its rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.db import connection, transaction
from django.utils import timezone

from workeasy360.commercial.partners import selectors as partner_selectors
from workeasy360.commercial.partners.domain import clean_reason
from workeasy360.contracts.enums import PartnerStatus
from workeasy360.contracts.errors import PartnerRecordNotFound, PartnerValidationError
from workeasy360.contracts.values.identifiers import ProductCode
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.observability.metrics import metrics

MANAGE = "platform.staff_commission.manage"

#: A new version may start slightly in the past to absorb clock skew between
#: the operator's request and the server, and no further: a rule that could be
#: backdated would silently change what an earlier sale is worth.
BACKDATE_TOLERANCE = timedelta(seconds=30)


@in_scope
def create_rule_version(
    ctx: TenantContext,
    *,
    code: Any,
    rate_bp: Any,
    reason: Any,
    product_code: Any = "",
    partner_id: UUID | None = None,
    effective_from: datetime | None = None,
    recurring: Any = False,
) -> Any:
    """Create the next version of a rule, retiring the one it replaces.

    Sales made before ``effective_from`` keep the version they were computed
    under; nothing already accrued is recalculated. ``rate_bp`` is integer
    basis points (1500 = 15%) - a float, a bool or a string is refused.
    ``recurring`` (ADR-052) also earns on each paid renewal of the sale.
    """
    from workeasy360.commercial.commissions.models import CommissionRule

    authz.require(ctx, MANAGE)
    why = clean_reason(reason)
    if not isinstance(recurring, bool):
        raise PartnerValidationError(
            "recurring must be true or false.", field="recurring"
        )
    clean_code = str(code or "").strip().lower()
    if (
        not clean_code
        or len(clean_code) > 48
        or not clean_code.replace("-", "").isalnum()
    ):
        raise PartnerValidationError(
            "code must be a short slug (letters, digits, hyphens).", field="code"
        )
    if (
        isinstance(rate_bp, bool)
        or not isinstance(rate_bp, int)
        or not 0 <= rate_bp <= 10000
    ):
        raise PartnerValidationError(
            "rate_bp must be a whole number of basis points between 0 and 10000.",
            field="rate_bp",
        )
    product = str(product_code or "").strip()
    if product:
        try:
            ProductCode(product)
        except ValueError as exc:
            raise PartnerValidationError(
                "product_code is not valid.", field="product_code"
            ) from exc
    if partner_id is not None and partner_selectors.partner_row(partner_id) is None:
        raise PartnerRecordNotFound("No such partner.", partner_id=str(partner_id))

    now = timezone.now()
    start = effective_from or now
    if start < now - BACKDATE_TOLERANCE:
        raise PartnerValidationError(
            "A commission rule cannot take effect in the past: that would "
            "change what earlier sales are worth.",
            field="effective_from",
        )

    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", [f"rule:{clean_code}"]
            )
        previous = list(
            CommissionRule.objects.filter(code=clean_code).order_by("-version")
        )
        live = next((r for r in previous if r.retired_at is None), None)
        if live is not None and start < live.effective_from:
            raise PartnerValidationError(
                "The new version must start no earlier than the version it replaces.",
                field="effective_from",
            )
        if live is not None and (
            (live.partner_id != partner_id) or (live.product_code != product)
        ):
            raise PartnerValidationError(
                "A rule's partner and product are fixed; create a new code instead.",
                field="code",
            )
        if live is not None:
            live.retired_at = start
            live.save(update_fields=["retired_at"])
        rule = CommissionRule.objects.create(
            code=clean_code,
            version=(previous[0].version + 1) if previous else 1,
            partner_id=partner_id,
            product_code=product,
            rate_bp=rate_bp,
            recurring=recurring,
            effective_from=start,
            reason=why,
            created_by_id=ctx.user_id,
        )
        audit.record(
            ctx,
            "commission.rule_created",
            resource_type="commissions.rule",
            resource_id=rule.id,
            changes={
                "code": rule.code,
                "version": rule.version,
                "rate_bp": rule.rate_bp,
                "recurring": rule.recurring,
                "partner_id": str(partner_id) if partner_id else None,
                "product_code": product,
                "effective_from": start.isoformat(),
            },
            reason=why,
        )
    metrics.increment("commission_rule_changes_total")
    return rule


# --- assigning a rule to one partner ----------------------------------------


@dataclass(frozen=True, slots=True)
class CommissionAssignment:
    rule: Any
    #: ``False`` when the rate asked for was already in force (a retry).
    created: bool
    #: The rate of the version this replaced, or ``None`` for a first rule.
    previous_rate_bp: int | None


def partner_rule_code(partner_id: UUID, product_code: str = "") -> str:
    """The code a partner's *first* rule for a scope is created under.

    Only used to name a new chain (a slug, at most 48 characters: ``pc-`` +
    12 hex + ``-`` + a product code of at most 32). Every later version of that
    chain is found by (partner, product) and reuses the code it already has.
    """
    suffix = f"-{product_code}" if product_code else ""
    return f"pc-{partner_id.hex[:12]}{suffix}"


def _validated_rate(rate_bp: Any) -> int:
    if (
        isinstance(rate_bp, bool)
        or not isinstance(rate_bp, int)
        or not 0 <= rate_bp <= 10000
    ):
        raise PartnerValidationError(
            "rate_bp must be a whole number of basis points between 0 and 10000.",
            field="rate_bp",
        )
    return rate_bp


@in_scope
def assign_partner_commission(
    ctx: TenantContext,
    partner_id: UUID,
    *,
    rate_bp: Any,
    reason: Any,
    expected_version: Any,
    product_code: Any = "",
    effective_from: datetime | None = None,
) -> CommissionAssignment:
    """Make ``rate_bp`` the commission this partner earns, from ``effective_from``.

    - **Version-safe.** ``expected_version`` is the version of the partner's
      live rule the caller read (``0`` when it had none). A stale token is
      refused, never merged - two staff members changing one partner's rate
      cannot silently overwrite each other - and a concurrent first assignment
      is serialised by an advisory lock on (partner, product), so the second
      sees the first's rule and is refused as stale.
    - **Effective-dated, never retroactive.** The new version starts at
      ``effective_from`` (default now; it may be later, never earlier than a
      moment's clock skew ago) and the previous one is retired at that same
      instant. Sales already accrued keep the rule, version and rate stamped on
      their ledger row.
    - **Idempotent.** Asking for the rate already in force (with the token from
      just before or just after it was set) changes nothing and reports
      ``created=False``, so a retry after a lost response is safe.
    - **Authorized and audited** by :func:`create_rule_version`: dangerous, and
      recorded with the reason; this adds the assignment's
      own audit entry naming the previous rate.
    """
    from workeasy360.commercial.commissions.models import CommissionRule
    from workeasy360.commercial.partners.services import ConcurrentPartnerModification

    authz.require(ctx, MANAGE)
    why = clean_reason(reason)
    rate = _validated_rate(rate_bp)
    product = str(product_code or "").strip()
    if product:
        try:
            ProductCode(product)
        except ValueError as exc:
            raise PartnerValidationError(
                "product_code is not valid.", field="product_code"
            ) from exc
    if (
        isinstance(expected_version, bool)
        or not isinstance(expected_version, int)
        or expected_version < 0
    ):
        raise PartnerValidationError(
            "expected_version is required: the version of the partner's live rule "
            "you read, or 0 if it had none.",
            field="expected_version",
        )
    partner = partner_selectors.partner_row(partner_id)
    if partner is None:
        raise PartnerRecordNotFound("No such partner.", partner_id=str(partner_id))
    if partner.status_enum is PartnerStatus.TERMINATED:
        raise PartnerValidationError(
            "A terminated partner cannot be given a commission rule.", field="partner_id"
        )
    now = timezone.now()
    if effective_from is not None and effective_from < now - BACKDATE_TOLERANCE:
        raise PartnerValidationError(
            "A commission rule cannot take effect in the past: that would "
            "change what earlier sales are worth.",
            field="effective_from",
        )

    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                [f"partner-commission:{partner_id}:{product}"],
            )
        # The clock is read again *after* the lock: a caller that waited behind
        # another assignment must compare against a moment that is not earlier
        # than the rule that assignment just wrote, or an identical retry would
        # look like a rule that "has not started yet".
        now = timezone.now()
        live = list(
            CommissionRule.objects.filter(
                partner_id=partner_id, product_code=product, retired_at__isnull=True
            ).order_by("version")
        )
        if len(live) > 1:
            raise PartnerValidationError(
                "This partner has more than one live commission rule for that "
                "scope. Resolve it in the rule list before assigning another.",
                field="product_code",
            )
        current = live[0] if live else None
        held = current.version if current is not None else 0

        if (
            current is not None
            and current.rate_bp == rate
            and (
                current.effective_from <= now
                if effective_from is None
                else current.effective_from == effective_from
            )
            and expected_version in (held, held - 1)
        ):
            return CommissionAssignment(
                rule=current, created=False, previous_rate_bp=current.rate_bp
            )
        if expected_version != held:
            raise ConcurrentPartnerModification(
                f"The partner's commission rule was at version {held}, not "
                f"{expected_version}. Re-read it and retry.",
                expected_version=expected_version,
                actual_version=held,
            )

        rule = create_rule_version(
            ctx,
            code=current.code
            if current is not None
            else partner_rule_code(partner_id, product),
            rate_bp=rate,
            reason=why,
            product_code=product,
            partner_id=partner_id,
            effective_from=effective_from,
            # A new rate keeps the rule's recurring choice (ADR-052).
            recurring=current.recurring if current is not None else False,
        )
        audit.record(
            ctx,
            "commission.partner_rule_assigned",
            resource_type="commissions.rule",
            resource_id=rule.id,
            changes={
                "partner_id": str(partner_id),
                "code": rule.code,
                "version": rule.version,
                "rate_bp": rule.rate_bp,
                "previous_rate_bp": current.rate_bp if current is not None else None,
                "product_code": product,
                "effective_from": rule.effective_from.isoformat(),
            },
            reason=why,
        )
    metrics.increment("commission_partner_assignments_total")
    return CommissionAssignment(
        rule=rule,
        created=True,
        previous_rate_bp=current.rate_bp if current is not None else None,
    )

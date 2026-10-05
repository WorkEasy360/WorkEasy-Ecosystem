"""What an organization's administrator sees of its AI (Task 89).

Usage and limits for the current month, per product, and the latest requests
as metadata: operation, skill, model, outcome, cost. Never a prompt, a
response or a retrieved passage - none is stored, and nothing here would show
it if it were (Task 91). Keyset-paginated, bounded (E8).
"""

from __future__ import annotations

from typing import Any, Final

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from workeasy360.contracts.values.cursor import (
    Page,
    clamp_limit,
    decode_cursor,
    encode_cursor,
)
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.intelligence.ai.budget import (
    ORGANIZATION_SCOPE,
    period_end,
    period_start,
)
from workeasy360.intelligence.ai.models import AIBudgetPeriod, AIExecution
from workeasy360.intelligence.ai.policy import VIEW, budget_feature, policy_for

MAX_PAGE: Final = 50


def usage(ctx: TenantContext) -> dict[str, Any]:
    """This month's spend against every limit that applies."""
    from workeasy360.contracts.ports.entitlements import UNLIMITED
    from workeasy360.platform.authz import services as authz
    from workeasy360.platform.entitlements.services import read as entitlements

    authz.require(ctx, VIEW)
    now = timezone.now()
    start = period_start(now)
    policy = policy_for(ctx)
    rows = {row.scope: row for row in AIBudgetPeriod.objects.filter(period_start=start)}
    org_row = rows.get(ORGANIZATION_SCOPE)

    products = []
    for code in sorted({*entitlements.products(ctx.organization_id), *rows} - {""}):
        limit = entitlements.quota(ctx.organization_id, budget_feature(str(code)))
        row = rows.get(str(code))
        products.append(
            {
                "product_code": str(code),
                "ai_entitled": limit != 0,
                "limit_micros": None if limit == UNLIMITED else max(limit, 0),
                **_spend(row),
            }
        )
    return {
        "currency": settings.AI_ACCOUNTING_CURRENCY,
        "period_start": start.isoformat(),
        "resets_at": period_end(start).isoformat(),
        "status": _status(policy),
        "organization": {
            "limit_micros": policy.organization_cap_micros,
            **_spend(org_row),
        },
        "products": products,
        "note": (
            "Estimated AI infrastructure cost, for spend control. It is not an "
            "invoice and is not billed."
        ),
    }


def recent_executions(
    ctx: TenantContext, *, limit: Any = None, cursor: Any = None
) -> dict[str, Any]:
    from workeasy360.platform.authz import services as authz

    authz.require(ctx, VIEW)
    size = clamp_limit(limit, default=20, maximum=MAX_PAGE)
    rows = AIExecution.objects.order_by("-created_at", "-id")
    position = decode_cursor(cursor if isinstance(cursor, str) else None)
    if position is not None:
        created, last = position
        rows = rows.filter(Q(created_at__lt=created) | Q(created_at=created, id__lt=last))
    page = list(rows[: size + 1])
    has_more = len(page) > size
    page = page[:size]
    return Page(
        items=[
            {
                "request_id": str(r.id),
                "product_code": r.product_code,
                "operation": r.operation,
                "skill": r.skill_key,
                "skill_version": r.skill_version,
                "status": r.status,
                "outcome": r.outcome,
                "model": r.model_key,
                "provider": r.provider_key,
                "fallback_used": r.fallback_used,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "embedding_tokens": r.embedding_tokens,
                "cost_micros": r.cost_micros,
                "created_at": r.created_at.isoformat(),
            }
            for r in page
        ],
        next_cursor=encode_cursor(page[-1].created_at, page[-1].id) if has_more else None,
        has_more=has_more,
    ).as_dict()


def _spend(row: AIBudgetPeriod | None) -> dict[str, int]:
    if row is None:
        return {
            "spent_micros": 0,
            "reserved_micros": 0,
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "embedding_tokens": 0,
        }
    return {
        "spent_micros": row.spent_micros,
        "reserved_micros": row.reserved_micros,
        "requests": row.requests,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "embedding_tokens": row.embedding_tokens,
    }


def _status(policy: Any) -> str:
    if policy.platform_suspended:
        return "suspended_by_platform"
    if not policy.ai_enabled:
        return "disabled"
    return "enabled"

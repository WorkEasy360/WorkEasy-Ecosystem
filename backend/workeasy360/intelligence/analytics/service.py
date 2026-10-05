"""Analytics use cases for the two callers: a product over its credential, and
an organization's administrator in the platform shell (Tasks 74-77, 90).

Both end in ``query.run`` over the organization's own rows. What differs is
who decides the metric is theirs to read: a product may read metrics it owns
or that were shared with it, for an organization it is entitled to; an
administrator may read metrics of products the organization holds.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from workeasy360.contracts.errors import AIInvalidRequest, MetricNotFound
from workeasy360.contracts.values import json_input
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.intelligence.analytics import query, registry
from workeasy360.intelligence.analytics.permissions import VIEW

_QUERY_FIELDS = frozenset(
    {"metric", "version", "start", "end", "granularity", "group_by"}
)


def register_metric(client: Any, raw: Any) -> tuple[dict[str, Any], bool]:
    from workeasy360.platform.ecosystem import ratelimit

    ratelimit.consume("ai_registry", client.client_id, endpoint="analytics_registry")
    metric, created = registry.register(
        client.product_code,
        client.client_id,
        _json(raw, "metric"),
        declares_event=client.declares_event,
    )
    return metric.public(), created


def product_metrics(client: Any) -> list[dict[str, Any]]:
    return [m.public() for m in registry.owned_or_shared(client.product_code)]


def product_query(client: Any, *, organization_id: Any, raw: Any) -> dict[str, Any]:
    """A product queries a metric for one organization. The organization must
    hold the product (read); a metric the product may not read, an unknown
    one, and an organization it has no relationship with all answer
    ``metric_not_found``."""
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.ecosystem import ratelimit, verification
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    organization = _uuid(organization_id)
    ratelimit.consume(
        "analytics_query_org",
        f"{client.client_id}:{organization}",
        endpoint="analytics_query",
    )
    params = _json(raw, "query")
    _known(params)
    metric = query.authorize_product(
        client.product_code, params.get("metric"), params.get("version")
    )
    ctx = routing_context(organization)
    if ctx is None:
        raise MetricNotFound("No such metric.")
    decision = verification.verify(
        organization_id=organization, product_code=client.product_code
    )
    if decision.decision != verification.ALLOW:
        raise MetricNotFound("No such metric.")
    with tenant_context(ctx), organization_db_scope(organization):
        return query.run(
            metric,
            organization_id=organization,
            start=params.get("start"),
            end=params.get("end"),
            granularity=params.get("granularity"),
            group_by=params.get("group_by"),
        )


def organization_metrics(ctx: TenantContext) -> list[dict[str, Any]]:
    """The metrics the organization's administrator may query: those of the
    products it holds (definitions only - no data)."""
    from workeasy360.platform.authz import services as authz
    from workeasy360.platform.entitlements.services import read as entitlements

    authz.require(ctx, VIEW)
    held = {str(code) for code in entitlements.products(ctx.organization_id)}
    return [m.public() for m in registry.for_owners(held)]


def organization_query(ctx: TenantContext, params: dict[str, Any]) -> dict[str, Any]:
    from workeasy360.platform.authz import services as authz

    authz.require(ctx, VIEW)
    _known(params)
    version = params.get("version")
    if isinstance(version, str) and version.isdigit():
        version = int(version)
    metric = query.authorize_organization(
        ctx.organization_id, params.get("metric"), version
    )
    return query.run(
        metric,
        organization_id=ctx.organization_id,
        start=params.get("start"),
        end=params.get("end"),
        granularity=params.get("granularity"),
        group_by=params.get("group_by"),
    )


def _known(params: dict[str, Any]) -> None:
    unknown = sorted(set(params) - _QUERY_FIELDS)
    if unknown:
        raise AIInvalidRequest(
            f"Unknown field(s): {', '.join(unknown)}.", field=unknown[0]
        )


def _json(raw: Any, field: str) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw or len(raw) > 65_536:
        raise AIInvalidRequest(
            f"A '{field}' field holding a JSON object is required.", field=field
        )
    try:
        parsed = json_input.loads(raw)
    except ValueError:
        raise AIInvalidRequest(f"The {field} is not valid JSON.", field=field) from None
    if not isinstance(parsed, dict):
        raise AIInvalidRequest(f"The {field} must be a JSON object.", field=field)
    return parsed


def _uuid(value: Any) -> UUID:
    try:
        return UUID(str(value)) if isinstance(value, str | UUID) else _bad()
    except ValueError:
        return _bad()


def _bad() -> UUID:
    raise AIInvalidRequest("organization_id must be a UUID.", field="organization_id")

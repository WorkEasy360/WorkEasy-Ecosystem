"""The organization console's dashboard.

Sections are included only when the caller holds the permission behind them,
so one endpoint serves an owner (everything), an administrator (members,
activity, apps) and anyone else (nothing sensitive):

============================  ============================
section                       permission
============================  ============================
``members``, ``invitations``  ``platform.member.view``
``activity`` (sign-ins)       ``platform.audit.view``
``apps``                      ``platform.entitlement.view``
============================  ============================

Commercial state (the subscription) is added by the HTTP adapter from
``commercial``, which ``platform`` may not import (F-4).

Counts only, and bounded: at most ``MAX_WINDOW_DAYS`` of daily buckets and the
top ten members. Every query runs in the caller's tenant scope.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Final

from django.utils import timezone

from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz

WINDOWS: Final = (7, 30, 90)
MAX_WINDOW_DAYS: Final = max(WINDOWS)


def summary(ctx: TenantContext, *, window_days: int = 7) -> dict[str, Any]:
    from workeasy360.platform.organizations import selectors

    authz.require(ctx, "platform.organization.view")
    window = window_days if window_days in WINDOWS else WINDOWS[0]
    now = timezone.now()
    since = now - timedelta(days=window)
    held = authz.effective_permissions(ctx)

    result: dict[str, Any] = {
        "window_days": window,
        "generated_at": now.isoformat(),
        "organization": selectors.organization_summary(ctx),
    }
    if "platform.member.view" in held:
        result["members"] = selectors.member_statistics(ctx, since=since, now=now)
    if "platform.audit.view" in held:
        series = {row["date"]: row for row in selectors.sign_in_series(ctx, since=since)}
        days = [(since + timedelta(days=i + 1)).date() for i in range(window)]
        result["activity"] = {
            "series": [
                {
                    "date": day.isoformat(),
                    "members": series.get(day.isoformat(), {}).get("members", 0),
                    "sign_ins": series.get(day.isoformat(), {}).get("sign_ins", 0),
                }
                for day in days
            ],
            "most_active": selectors.most_active_members(ctx, since=since, limit=10),
        }
    if "platform.entitlement.view" in held:
        result["apps"] = _apps(ctx)
    return result


def _apps(ctx: TenantContext) -> dict[str, int]:
    from workeasy360.platform.entitlements.services import read as entitlements

    return {"entitled": len(list(entitlements.products(ctx.organization_id)))}

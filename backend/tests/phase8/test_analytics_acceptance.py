"""Phase 8 final acceptance - analytics (the brief's 10 steps), end to end.

The product publishes ordinary Phase 6 events over its credential; the
platform routes them, records facts, projects aggregates on the due-work
timer, and answers bounded queries - for the product over its credential and
for the organization's administrator in the platform shell.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone

import pytest
from django.db import connection
from django.utils import timezone

from tests.factories.intelligence import order_metric, record_event
from tests.phase8.helpers import drain, in_org

pytestmark = [pytest.mark.django_db(transaction=True)]

KEY = "product.example.record_value"


def _midnight(days_ago: int = 0) -> datetime:
    today = (
        timezone.now()
        .astimezone(dt_timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0)
    )
    return today - timezone.timedelta(days=days_ago)


def _window(days: int = 5) -> dict[str, str]:
    return {
        "metric": KEY,
        "start": _midnight(days).isoformat(),
        "end": (_midnight() + timezone.timedelta(days=1)).isoformat(),
        "granularity": "day",
    }


def _facts(organization_id):
    from workeasy360.intelligence.analytics.models import AnalyticsFact

    with in_org(organization_id):
        return list(
            AnalyticsFact.unscoped.filter(organization_id=organization_id).values(
                "source", "metric_key", "value", "currency"
            )
        )


def test_phase_8_analytics_acceptance_flow(
    client,
    example_product,
    second_product,
    product_client,
    entitled_org,
    entitle_products,
    signed_in,
    grant_permissions,
):
    x = product_client(example_product)
    y = product_client(second_product)
    org_a = entitled_org("example")
    org_b = entitled_org("example")
    a, b = org_a["organization_id"], org_b["organization_id"]

    status, body = x.register_metric(order_metric())
    assert status == 201, body

    # 1-2. Product X publishes a versioned event; its credential authenticates
    # it and the live entitlement decision binds the organization.
    won = record_event(amount_minor=1500, status="won")
    status, body = x.publish_event(organization_id=a, event=won)
    assert (status, body["status"]) == (202, "accepted"), body

    # 3. The duplicate is idempotent: no second fact.
    status, body = x.publish_event(organization_id=a, event=won)
    assert (status, body["status"]) == (200, "duplicate")
    drain(org_a["org"].id)
    assert len(_facts(org_a["org"].id)) == 1

    # 4. The event updates the correct aggregate.
    status, result = x.query_metric(organization_id=a, query=_window())
    assert status == 200, result
    assert result["results"] == [
        {
            "bucket": _midnight().isoformat(),
            "dimensions": {},
            "count": 1,
            "amount_minor": 1500,
            "currency": "GBP",
        }
    ]
    assert result["freshness"]["up_to_date"] is True

    # 5. A late event lands in - and corrects - its historical bucket, once,
    # however many times it is delivered.
    late = record_event(
        occurred_at=timezone.now() - timezone.timedelta(days=3), amount_minor=700
    )
    for _ in range(3):
        x.publish_event(organization_id=a, event=late)
    drain(org_a["org"].id)
    status, result = x.query_metric(organization_id=a, query=_window())
    by_bucket = {row["bucket"]: row for row in result["results"]}
    assert by_bucket[_midnight(3).isoformat()]["amount_minor"] == 700
    assert by_bucket[_midnight(3).isoformat()]["count"] == 1
    assert by_bucket[_midnight().isoformat()]["amount_minor"] == 1500

    # 6. Organization A's administrator queries the approved metric in the
    # platform shell, through the session and the tenant path.
    for org in (org_a, org_b):
        grant_permissions(org["org"], org["membership"], ("platform.analytics.view",))
    signed_in(org_a["user"])
    response = client.get(
        f"/api/v1/orgs/{org_a['org'].slug}/analytics/query",
        {**_window(), "group_by": "status"},
    )
    assert response.status_code == 200, response.content
    grouped = {
        (row["bucket"], row["dimensions"]["status"]): row["amount_minor"]
        for row in response.json()["results"]
    }
    assert grouped == {
        (_midnight(3).isoformat(), "open"): 700,
        (_midnight().isoformat(), "won"): 1500,
    }
    listed = client.get(f"/api/v1/orgs/{org_a['org'].slug}/analytics/metrics").json()
    assert [m["key"] for m in listed["results"]] == [KEY]

    # 7. Organization B cannot query A's data - not through the product, not
    # through its own administrator, and not by naming A's organization.
    status, result_b = x.query_metric(organization_id=b, query=_window())
    assert status == 200 and result_b["results"] == []
    signed_in(org_b["user"])
    own = client.get(f"/api/v1/orgs/{org_b['org'].slug}/analytics/query", _window())
    assert own.status_code == 200 and own.json()["results"] == []
    foreign = client.get(f"/api/v1/orgs/{org_a['org'].slug}/analytics/query", _window())
    assert foreign.status_code in (403, 404)

    # 8. Product Y cannot query Product X's private metric - even for an
    # organization holding both - until X shares it explicitly.
    org_c = entitled_org("example")
    c = org_c["organization_id"]
    entitle_products(org_c["org"].id, {"example": {}, "second": {}})
    x.publish_event(organization_id=c, event=record_event(amount_minor=250))
    drain(org_c["org"].id)
    status, denied = y.query_metric(organization_id=c, query=_window())
    assert (status, denied["error"]) == (404, "metric_not_found"), denied
    status, body = x.register_metric(order_metric(version=2, shared_with=["second"]))
    assert status == 201, body
    x.publish_event(organization_id=c, event=record_event(amount_minor=400))
    drain(org_c["org"].id)
    status, shared = y.query_metric(organization_id=c, query={**_window(), "version": 2})
    assert status == 200, shared
    assert [r["amount_minor"] for r in shared["results"]] == [400]
    status, still_private = y.query_metric(
        organization_id=c, query={**_window(), "version": 1}
    )
    assert (status, still_private["error"]) == (404, "metric_not_found")

    # 9. A rebuild from facts reproduces the aggregates - after they have been
    # corrupted - and reconciliation first reports the drift.
    from workeasy360.intelligence.analytics import rebuild

    status, before = x.query_metric(organization_id=a, query=_window())
    with in_org(org_a["org"].id), connection.cursor() as cur:
        cur.execute(
            "UPDATE analytics_aggregate SET sum_value = sum_value + 99999 "
            "WHERE organization_id = %s",
            [org_a["org"].id],
        )
    assert rebuild.reconcile(org_a["org"].id), "drift must be reported"
    rebuild.rebuild(org_a["org"].id)
    assert rebuild.reconcile(org_a["org"].id) == []
    status, after = x.query_metric(organization_id=a, query=_window())
    assert after["results"] == before["results"]

    # 10. No product database was queried: every fact came from an event the
    # product published, attributed to the publishing product.
    facts = _facts(org_a["org"].id)
    assert {f["source"] for f in facts} == {"product.example"}
    assert sorted(f["value"] for f in facts) == [700, 1500]

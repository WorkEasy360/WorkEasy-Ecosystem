"""Unified analytics: the metric registry, facts, aggregates and queries
(Tasks 60-83), through the product endpoints and real event routing."""

from __future__ import annotations

from datetime import timezone as dt_timezone

import pytest
from django.utils import timezone

from tests.factories.intelligence import order_metric, record_event
from tests.phase8.helpers import drain, in_org

pytestmark = [pytest.mark.django_db(transaction=True)]

KEY = "product.example.record_value"


def _midnight(days_ago=0):
    return timezone.now().astimezone(dt_timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) - timezone.timedelta(days=days_ago)


def _daily(**extra):
    return {
        "metric": KEY,
        "start": _midnight(5).isoformat(),
        "end": (_midnight() + timezone.timedelta(days=1)).isoformat(),
        "granularity": "day",
        **extra,
    }


@pytest.fixture
def x(example_product, product_client):
    client = product_client(example_product)
    assert client.register_metric(order_metric())[0] == 201
    return client


# --- the registry -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"key": "product.second.value"}, "key"),
        ({"key": "platform.value"}, "key"),
        ({"event": {"type": "product.second.item.created", "version": 1}}, "event"),
        ({"event": {"type": "product.example.record.deleted", "version": 1}}, "event"),
        ({"measure": ""}, "measure"),
        ({"unit": "dollars"}, "unit"),
        (
            {"dimensions": [{"name": "email", "field": "email", "values": ["a"]}]},
            "dimensions",
        ),
        (
            {"dimensions": [{"name": "owner_id", "field": "owner", "values": ["a"]}]},
            "dimensions",
        ),
        (
            {"dimensions": [{"name": "stage", "field": "stage", "values": []}]},
            "dimensions",
        ),
        (
            {
                "dimensions": [
                    {"name": "stage", "field": "stage", "values": ["Bad Value"]}
                ]
            },
            "dimensions",
        ),
        (
            {
                "dimensions": [
                    {"name": f"d{i}", "field": "f", "values": ["a"]} for i in range(4)
                ]
            },
            "dimensions",
        ),
        ({"aggregation": "avg"}, "aggregation"),
        ({"value_type": "float"}, "value_type"),
        ({"shared_with": ["example"]}, "shared_with"),
        ({"sql": "SELECT 1"}, "sql"),
    ],
)
def test_a_definition_outside_the_contract_is_refused(
    example_product, product_client, overrides, field
):
    client = product_client(example_product)
    status, body = client.register_metric(order_metric(**overrides))
    assert (status, body["field"]) == (400, field), body


def test_a_count_metric_has_no_measure(example_product, product_client):
    client = product_client(example_product)
    status, body = client.register_metric(
        {
            "key": "product.example.records_created",
            "version": 1,
            "unit": "count",
            "aggregation": "count",
            "event": {"type": "product.example.record.created", "version": 1},
        }
    )
    assert status == 201, body


def test_a_registered_metric_version_is_immutable(x):
    assert x.register_metric(order_metric())[0] == 200
    status, body = x.register_metric(order_metric(description="changed"))
    assert (status, body["error"]) == (409, "ai_registry_conflict")


def test_a_product_lists_only_what_it_owns_or_was_shared(
    x, second_product, product_client
):
    y = product_client(second_product)
    status, body = y.intelligence("analytics_metrics")
    assert body["results"] == []
    x.register_metric(order_metric(version=2, shared_with=["second"]))
    status, body = y.intelligence("analytics_metrics")
    assert [(m["key"], m["version"]) for m in body["results"]] == [(KEY, 2)]


# --- facts and aggregates ---------------------------------------------------------------


def test_amounts_in_different_currencies_are_never_added_together(x, entitled_org):
    org = entitled_org("example")
    for currency, amount in (("GBP", 100), ("USD", 250), ("GBP", 50)):
        x.publish_event(
            organization_id=org["organization_id"],
            event=record_event(amount_minor=amount, currency=currency),
        )
    drain(org["org"].id)
    status, result = x.query_metric(
        organization_id=org["organization_id"], query=_daily()
    )
    assert sorted((r["currency"], r["amount_minor"]) for r in result["results"]) == [
        ("GBP", 150),
        ("USD", 250),
    ]


def test_bad_measurements_are_counted_and_skipped_never_stored(x, entitled_org):
    from workeasy360.intelligence.analytics.models import AnalyticsFact

    org = entitled_org("example")
    for bad in ({"amount_minor": 12.5}, {"amount_minor": 10, "currency": "XXX"}):
        status, _ = x.publish_event(
            organization_id=org["organization_id"], event=record_event(**bad)
        )
        assert status == 202  # routing is unaffected
    drain(org["org"].id)
    with in_org(org["org"].id):
        assert not AnalyticsFact.unscoped.filter(organization_id=org["org"].id).exists()


def test_dimension_values_outside_the_enumeration_cannot_grow_the_keyspace(
    x, entitled_org
):
    org = entitled_org("example")
    for status_value in (
        "won",
        "a@b.example",
        "d9b2c3a4-0000-4000-8000-000000000000",
        None,
    ):
        data = {"status": status_value} if status_value is not None else {"status": None}
        x.publish_event(
            organization_id=org["organization_id"], event=record_event(**data)
        )
    drain(org["org"].id)
    status, result = x.query_metric(
        organization_id=org["organization_id"], query=_daily(group_by="status")
    )
    assert sorted(r["dimensions"]["status"] for r in result["results"]) == [
        "other",
        "unknown",
        "won",
    ]


def test_freshness_says_when_aggregates_lag(x, entitled_org):
    org = entitled_org("example")
    x.publish_event(organization_id=org["organization_id"], event=record_event())
    status, result = x.query_metric(
        organization_id=org["organization_id"], query=_daily()
    )
    assert result["freshness"]["up_to_date"] is False
    assert result["freshness"]["pending_facts"] == 1
    assert result["results"] == []
    drain(org["org"].id)
    status, result = x.query_metric(
        organization_id=org["organization_id"], query=_daily()
    )
    assert result["freshness"]["up_to_date"] is True and len(result["results"]) == 1


def test_a_raw_fact_cannot_be_rewritten(x, entitled_org):
    from django.db import connection, transaction

    org = entitled_org("example")
    x.publish_event(organization_id=org["organization_id"], event=record_event())
    with pytest.raises(Exception, match="cannot be changed"), transaction.atomic():
        with in_org(org["org"].id), connection.cursor() as cur:
            cur.execute("UPDATE analytics_fact SET value = 999999")


def test_hourly_buckets_follow_occurrence_time_not_receipt(x, entitled_org):
    org = entitled_org("example")
    when = timezone.now().astimezone(dt_timezone.utc) - timezone.timedelta(hours=5)
    x.publish_event(
        organization_id=org["organization_id"],
        event=record_event(occurred_at=when, amount_minor=42),
    )
    drain(org["org"].id)
    hour = when.replace(minute=0, second=0, microsecond=0)
    status, result = x.query_metric(
        organization_id=org["organization_id"],
        query={
            "metric": KEY,
            "start": (hour - timezone.timedelta(hours=2)).isoformat(),
            "end": (hour + timezone.timedelta(hours=3)).isoformat(),
            "granularity": "hour",
        },
    )
    assert [(r["bucket"], r["amount_minor"]) for r in result["results"]] == [
        (hour.isoformat(), 42)
    ]


# --- queries ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "error", "field"),
    [
        ({"granularity": "minute"}, "analytics_query_invalid", "granularity"),
        (
            {"granularity": "hour", "start": "2020-01-01T00:00:00+00:00"},
            "analytics_query_invalid",
            "end",
        ),
        ({"start": "2026-01-01T13:00:00+00:00"}, "analytics_query_invalid", "start"),
        ({"start": "not-a-date"}, "analytics_query_invalid", "start"),
        ({"start": "2026-01-01T00:00:00"}, "analytics_query_invalid", "start"),
        ({"group_by": "amount_minor"}, "analytics_query_invalid", "group_by"),
        ({"group_by": ["status", "status"]}, "analytics_query_invalid", "group_by"),
        ({"expression": "sum(value)"}, "ai_invalid_request", "expression"),
    ],
)
def test_queries_outside_the_bounds_are_refused(x, entitled_org, query, error, field):
    org = entitled_org("example")
    status, body = x.query_metric(
        organization_id=org["organization_id"], query=_daily(**query)
    )
    assert (status, body["error"], body["field"]) == (400, error, field)


def test_a_query_that_could_return_too_many_rows_is_refused(x, entitled_org, settings):
    settings.ANALYTICS_MAX_ROWS = 10
    org = entitled_org("example")
    status, body = x.query_metric(
        organization_id=org["organization_id"], query=_daily(group_by="status")
    )
    assert (status, body["error"]) == (400, "analytics_query_invalid")


def test_a_product_not_entitled_to_the_organization_reads_nothing(
    x, entitled_org, organization_factory
):
    org, _membership, _user = organization_factory()
    status, body = x.query_metric(organization_id=str(org.id), query=_daily())
    assert (status, body["error"]) == (404, "metric_not_found")


def test_guessing_another_products_metric_key_learns_nothing(
    x, second_product, product_client, entitled_org, entitle_products
):
    y = product_client(second_product)
    org = entitled_org("example")
    entitle_products(org["org"].id, {"example": {}, "second": {}})
    responses = {
        key: y.query_metric(
            organization_id=org["organization_id"], query=_daily(metric=key)
        )
        for key in (KEY, "product.example.does_not_exist", "product.second.nothing")
    }
    assert {(s, b["error"]) for s, b in responses.values()} == {(404, "metric_not_found")}


def test_a_member_without_the_permission_cannot_use_the_shell(
    x, entitled_org, signed_in, client
):
    org = entitled_org("example")
    signed_in(org["user"])
    response = client.get(f"/api/v1/orgs/{org['org'].slug}/analytics/query", _daily())
    assert response.status_code == 403


# --- retention and rebuild ---------------------------------------------------------------


def test_retention_prunes_only_projected_facts_past_the_window(x, entitled_org, settings):
    from workeasy360.intelligence.analytics.models import AnalyticsFact
    from workeasy360.intelligence.gateway.tasks import prune

    org = entitled_org("example")
    x.publish_event(organization_id=org["organization_id"], event=record_event())
    x.publish_event(organization_id=org["organization_id"], event=record_event())
    drain(org["org"].id)
    with in_org(org["org"].id):
        first = AnalyticsFact.unscoped.filter(organization_id=org["org"].id).first()
    settings.ANALYTICS_FACT_RETENTION_DAYS = 0
    prune()
    with in_org(org["org"].id):
        assert not AnalyticsFact.unscoped.filter(id=first.id).exists()


def test_rebuild_after_a_lost_projection_restores_every_bucket(x, entitled_org):
    from workeasy360.intelligence.analytics import rebuild
    from workeasy360.intelligence.analytics.models import AnalyticsAggregate

    org = entitled_org("example")
    for amount in (10, 20, 30):
        x.publish_event(
            organization_id=org["organization_id"],
            event=record_event(amount_minor=amount),
        )
    drain(org["org"].id)
    status, before = x.query_metric(
        organization_id=org["organization_id"], query=_daily()
    )
    with in_org(org["org"].id):
        AnalyticsAggregate.unscoped.filter(organization_id=org["org"].id).delete()
    status, empty = x.query_metric(organization_id=org["organization_id"], query=_daily())
    assert empty["results"] == []
    rebuild.rebuild(org["org"].id)
    status, after = x.query_metric(organization_id=org["organization_id"], query=_daily())
    assert (
        after["results"] == before["results"]
        and after["results"][0]["amount_minor"] == 60
    )


def test_the_operator_command_reports_drift_then_repairs_it(x, entitled_org):
    from io import StringIO

    from django.core.management import call_command
    from django.core.management.base import CommandError

    from workeasy360.intelligence.analytics.models import AnalyticsAggregate

    org = entitled_org("example")
    x.publish_event(
        organization_id=org["organization_id"], event=record_event(amount_minor=25)
    )
    drain(org["org"].id)
    organization = str(org["org"].id)
    with in_org(org["org"].id):
        AnalyticsAggregate.unscoped.filter(organization_id=org["org"].id).update(
            sum_value=999
        )

    out = StringIO()
    with pytest.raises(CommandError):
        call_command(
            "w360_analytics_aggregates", organization, "--fail-on-drift", stdout=out
        )
    assert "differ from their facts" in out.getvalue()
    with in_org(org["org"].id):
        assert set(
            AnalyticsAggregate.unscoped.filter(organization_id=org["org"].id).values_list(
                "sum_value", flat=True
            )
        ) == {999}, "reporting must repair nothing"

    call_command(
        "w360_analytics_aggregates", organization, "--rebuild", stdout=StringIO()
    )
    out = StringIO()
    call_command("w360_analytics_aggregates", organization, stdout=out)
    assert "No drift detected." in out.getvalue()
    status, body = x.query_metric(organization_id=org["organization_id"], query=_daily())
    assert body["results"][0]["amount_minor"] == 25

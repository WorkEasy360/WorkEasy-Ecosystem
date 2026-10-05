"""Isolation Layers 3 and 4 for the Phase 6 integration tables.

Same three jobs, same helpers, as ``0001`` and ``0003``:

**Layer 3 - RLS** on every new ``TenantScopedModel`` table.

**Layer 4 - composite foreign keys** on every tenant-to-tenant reference, so a
delivery for organization A pointing at organization B's connection or event,
or an execution pinned to another tenant's rule version, is unrepresentable
(Tasks 57, 58). The parents expose ``UNIQUE (id, organization_id)`` as model
constraints.

Two foreign keys are ``NO ACTION`` rather than ``CASCADE``: a routed event is
referenced by deliveries and executions and must never be deleted out from
under them (retention prunes it only once nothing refers to it). ``NO
ACTION`` rather than ``RESTRICT`` because every composite key here is
``DEFERRABLE INITIALLY DEFERRED``, and ``RESTRICT`` cannot be deferred.
``replay_of`` is ``NO ACTION`` too: Django's collector nulls it (only that
column) before deleting an original, whereas a database-level composite
``SET NULL`` would also null ``organization_id``.

**Immutability** of the records whose whole value is that they cannot change
after the fact: a sealed secret version, the event a replay re-sends, a rule
version an execution is pinned to, and an attempt record (Tasks 26, 39). A
trigger refuses the ``UPDATE``; deleting (retention) is still allowed.

``scheduling_due_work`` is control-plane (ADR-024) and gets grants, no policy.
"""

from __future__ import annotations

from django.db import migrations

from workeasy360.platform.tenancy.migrations_helpers import (
    add_tenant_fk,
    enable_rls,
    freeze_columns,
    grant_app_role,
)

TENANT_TABLES = [
    "automation_execution",
    "automation_rule",
    "automation_rule_version",
    "connectors_connection",
    "connectors_secret",
    "events_routed",
    "webhooks_attempt",
    "webhooks_delivery",
    "webhooks_subscription",
]

CONTROL_PLANE_TABLES = [
    "scheduling_due_work",
]

COMPOSITE_FKS = [
    {
        "table": "connectors_secret",
        "column": "connection_id",
        "references_table": "connectors_connection",
    },
    {
        "table": "webhooks_subscription",
        "column": "connection_id",
        "references_table": "connectors_connection",
    },
    {
        "table": "webhooks_delivery",
        "column": "connection_id",
        "references_table": "connectors_connection",
    },
    {
        "table": "webhooks_delivery",
        "column": "event_id",
        "references_table": "events_routed",
        "on_delete": "NO ACTION",
    },
    {
        "table": "webhooks_delivery",
        "column": "replay_of_id",
        "references_table": "webhooks_delivery",
        "on_delete": "NO ACTION",
    },
    {
        "table": "webhooks_attempt",
        "column": "delivery_id",
        "references_table": "webhooks_delivery",
    },
    {
        "table": "automation_rule",
        "column": "current_version_id",
        "references_table": "automation_rule_version",
        "on_delete": "NO ACTION",
    },
    {
        "table": "automation_rule_version",
        "column": "rule_id",
        "references_table": "automation_rule",
    },
    {
        "table": "automation_execution",
        "column": "rule_id",
        "references_table": "automation_rule",
    },
    {
        "table": "automation_execution",
        "column": "rule_version_id",
        "references_table": "automation_rule_version",
    },
    {
        "table": "automation_execution",
        "column": "trigger_event_id",
        "references_table": "events_routed",
        "on_delete": "NO ACTION",
    },
]

FROZEN = [
    {
        "table": "connectors_secret",
        "columns": ["organization_id", "connection_id", "version", "ciphertext", "created_at"],
    },
    {
        "table": "events_routed",
        "columns": [
            "organization_id",
            "event_id",
            "source",
            "event_type",
            "event_version",
            "occurred_at",
            "correlation_id",
            "causation_id",
            "chain_depth",
            "payload",
            "payload_bytes",
            "fingerprint",
            "created_at",
        ],
    },
    {
        "table": "automation_rule_version",
        "columns": ["organization_id", "rule_id", "version", "definition", "created_at"],
    },
    {
        "table": "webhooks_attempt",
        "columns": [
            "organization_id",
            "delivery_id",
            "attempt_number",
            "outcome",
            "category",
            "http_status",
            "duration_ms",
            "started_at",
            "response_excerpt",
            "created_at",
        ],
    },
]


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("tenancy", "0004_commercial_history_immutable"),
        ("automation", "0002_initial"),
        ("connectors", "0001_initial"),
        ("events", "0002_routedevent"),
        ("scheduling", "0001_initial"),
        ("webhooks", "0001_initial"),
    ]

    operations = [
        *[enable_rls(table) for table in TENANT_TABLES],
        *[grant_app_role(table) for table in CONTROL_PLANE_TABLES],
        *[add_tenant_fk(**fk) for fk in COMPOSITE_FKS],
        *[freeze_columns(spec["table"], columns=spec["columns"]) for spec in FROZEN],
    ]

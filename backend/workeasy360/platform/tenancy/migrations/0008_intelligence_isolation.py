"""Isolation Layers 3 and 4, and immutability, for the Phase 8 tables.

Same jobs, same helpers, as ``0001``, ``0003`` and ``0005``:

**Layer 3 - RLS** on every new ``TenantScopedModel`` table: an organization's
AI policy, spend, executions, usage lines and short-lived content; its
knowledge sources, documents, versions and chunks (with their embeddings);
its analytics facts and aggregates.

**Layer 4 - composite foreign keys** on every tenant-to-tenant reference, so a
usage line pointing at another organization's execution, or a chunk pointing
at another organization's document or version, is unrepresentable (Task 31:
cross-tenant source mixing is P0 - the schema refuses to hold it).

**Immutability** of what must never change after the fact (Tasks 9, 13, 73):
a published price version, a registered skill version (its prompt and
contract) and metric version, a usage line, an accepted analytics fact (only
its projection mark moves), a chunk and its embedding, and the identity of an
execution and of a document version. A trigger refuses the ``UPDATE``;
retention may still delete.

The six registry tables are **control plane** (no organization, like the
catalog) and get grants, no policy.
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
    "ai_org_policy",
    "ai_budget_period",
    "ai_execution",
    "ai_usage_line",
    "ai_execution_content",
    "rag_source",
    "rag_document",
    "rag_document_version",
    "rag_chunk",
    "analytics_fact",
    "analytics_aggregate",
]

CONTROL_PLANE_TABLES = [
    "ai_provider",
    "ai_model",
    "ai_model_price",
    "ai_skill",
    "ai_skill_version",
    "analytics_metric",
]

COMPOSITE_FKS = [
    {"table": "ai_usage_line", "column": "execution_id", "references_table": "ai_execution"},
    {
        "table": "ai_execution_content",
        "column": "execution_id",
        "references_table": "ai_execution",
    },
    {"table": "rag_document", "column": "source_id", "references_table": "rag_source"},
    {
        "table": "rag_document_version",
        "column": "document_id",
        "references_table": "rag_document",
    },
    {"table": "rag_chunk", "column": "document_id", "references_table": "rag_document"},
    {
        "table": "rag_chunk",
        "column": "version_id",
        "references_table": "rag_document_version",
    },
]

FROZEN = [
    {
        "table": "ai_model_price",
        "columns": [
            "model_id",
            "version",
            "currency",
            "input_micros_per_mtok",
            "output_micros_per_mtok",
            "effective_from",
            "created_at",
        ],
    },
    {
        "table": "ai_skill_version",
        "columns": [
            "skill_id",
            "version",
            "description",
            "instructions",
            "input_schema",
            "output_schema",
            "capabilities",
            "tier",
            "pinned_model_key",
            "max_output_tokens",
            "retrieval_enabled",
            "retrieval_top_k",
            "shared_with",
            "definition_hash",
            "registered_by",
            "created_at",
        ],
    },
    {
        "table": "analytics_metric",
        "columns": [
            "key",
            "version",
            "owner",
            "description",
            "unit",
            "aggregation",
            "value_type",
            "source_event_type",
            "source_event_version",
            "measure_field",
            "currency_field",
            "dimensions",
            "shared_with",
            "definition_hash",
            "registered_by",
            "created_at",
        ],
    },
    {
        "table": "ai_execution",
        "columns": [
            "organization_id",
            "product_code",
            "client_id",
            "operation",
            "mode",
            "skill_key",
            "skill_version",
            "idempotency_key",
            "request_fingerprint",
            "currency",
            "reserved_micros",
            "created_at",
        ],
    },
    {
        "table": "ai_usage_line",
        "columns": [
            "organization_id",
            "execution_id",
            "kind",
            "model_key",
            "provider_key",
            "price_id",
            "attempt",
            "outcome",
            "input_tokens",
            "output_tokens",
            "cost_micros",
            "latency_ms",
            "created_at",
        ],
    },
    {
        "table": "rag_document_version",
        "columns": [
            "organization_id",
            "document_id",
            "version_label",
            "seq",
            "content_hash",
            "created_at",
        ],
    },
    {
        "table": "rag_chunk",
        "columns": [
            "organization_id",
            "document_id",
            "version_id",
            "source_id",
            "product_code",
            "access_tags",
            "ordinal",
            "text",
            "token_estimate",
            "embedding",
            "embedding_model",
            "embedding_dimensions",
            "created_at",
        ],
    },
    {
        "table": "analytics_fact",
        "columns": [
            "organization_id",
            "source",
            "event_id",
            "metric_key",
            "metric_version",
            "occurred_at",
            "received_at",
            "value",
            "currency",
            "dims",
            "dims_key",
            "created_at",
        ],
    },
]


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("tenancy", "0007_final_audit_controls"),
        ("ai", "0001_initial"),
        ("rag", "0001_initial"),
        ("analytics", "0001_initial"),
    ]

    operations = [
        *[enable_rls(table) for table in TENANT_TABLES],
        *[grant_app_role(table) for table in CONTROL_PLANE_TABLES],
        *[add_tenant_fk(**fk) for fk in COMPOSITE_FKS],
        *[freeze_columns(spec["table"], columns=spec["columns"]) for spec in FROZEN],
    ]

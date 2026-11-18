"""The Admin console's views across customers (2026-11-09).

**Control plane** - grants, no policy - for the projections of invoices, payments,
refunds and subscriptions, each kept in step by a trigger on its tenant table and
filled once from the rows that predate the trigger. The credit note is append-only
and has its own number sequence.

Reversible: dropping the triggers leaves the tenant tables exactly as they were, and
nothing reads a projection except the Admin console.
"""

from __future__ import annotations

from django.db import migrations

from workeasy360.platform.tenancy.migrations_helpers import (
    backfill_projection,
    grant_app_role,
    make_append_only,
    project_rows,
)


def _same(*names: str) -> dict[str, str]:
    return {name: name for name in names}


PROJECTIONS = (
    (
        "payments_invoice",
        "payments_invoice_summary",
        ("invoice_id", "id"),
        _same(
            "organization_id",
            "subscription_id",
            "order_id",
            "number",
            "status",
            "currency",
            "total_minor",
            "amount_paid_minor",
            "amount_refunded_minor",
            "collection_mode",
            "issued_at",
            "due_at",
            "paid_at",
            "voided_at",
            "dunning_stage",
        ),
    ),
    (
        "payments_invoice_line",
        "payments_invoice_line_summary",
        ("line_id", "id"),
        _same(
            "invoice_id",
            "organization_id",
            "kind",
            "product_code",
            "plan_code",
            "quantity",
            "amount_minor",
        ),
    ),
    (
        "payments_attempt",
        "payments_attempt_summary",
        ("attempt_id", "id"),
        _same(
            "organization_id",
            "invoice_id",
            "provider_key",
            "status",
            "amount_minor",
            "currency",
            "amount_refunded_minor",
            "failure_code",
            "created_at",
            "completed_at",
        ),
    ),
    (
        "payments_refund",
        "payments_refund_summary",
        ("refund_id", "id"),
        _same(
            "organization_id",
            "attempt_id",
            "amount_minor",
            "currency",
            "reason",
            "status",
            "failure_code",
            "requested_by_user_id",
            "created_at",
            "completed_at",
        ),
    ),
    (
        "subscriptions_subscription",
        "subscriptions_summary",
        ("subscription_id", "id"),
        _same(
            "organization_id",
            "status",
            "currency",
            "billing_interval",
            "renewal_policy",
            "current_period_start",
            "current_period_end",
            "trial_ends_at",
            "activated_at",
            "cancelled_at",
            "cancel_at",
            "ended_at",
            "created_at",
        ),
    ),
    (
        "subscriptions_item",
        "subscriptions_item_summary",
        ("item_id", "id"),
        _same(
            "subscription_id",
            "organization_id",
            "product_code",
            "plan_code",
            "bundle_code",
            "quantity",
            "total_amount_minor",
            "currency",
            "interval",
            "added_at",
            "removed_at",
        ),
    ),
)


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("tenancy", "0012_product_trial_isolation"),
        ("payments", "0007_admin_projections"),
        ("subscriptions", "0005_admin_projections"),
    ]

    operations = [
        *[grant_app_role(target) for _source, target, _key, _columns in PROJECTIONS],
        *[
            project_rows(source, target, key=key, columns=columns)
            for source, target, key, columns in PROJECTIONS
        ],
        *[
            backfill_projection(source, target, key=key, columns=columns)
            for source, target, key, columns in PROJECTIONS
        ],
        grant_app_role("payments_credit_note"),
        make_append_only("payments_credit_note"),
        migrations.RunSQL(
            sql="""
            CREATE SEQUENCE IF NOT EXISTS payments_credit_note_number_seq START 1;
            GRANT USAGE, SELECT ON SEQUENCE payments_credit_note_number_seq TO w360_app;
            """,
            reverse_sql="DROP SEQUENCE IF EXISTS payments_credit_note_number_seq;",
            elidable=False,
        ),
    ]

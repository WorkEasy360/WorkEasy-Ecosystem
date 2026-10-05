"""Phase 9 (owner decision, 2026-09-26): ``platform.billing.manage`` - paying an
invoice through the provider's hosted checkout - is no longer dangerous.

The registry is what ``authz.check`` reads; this keeps the stored copy of the
flag (shown when roles are listed) in step with it. Reversible.
"""

from __future__ import annotations

from django.db import migrations

KEY = "platform.billing.manage"


def relax(apps, schema_editor):
    Permission = apps.get_model("authz", "Permission")
    Permission.objects.filter(key=KEY).update(
        dangerous=False, label="Pay invoices and manage billing details"
    )


def restore(apps, schema_editor):
    Permission = apps.get_model("authz", "Permission")
    Permission.objects.filter(key=KEY).update(
        dangerous=True, label="Change payment methods and subscriptions"
    )


class Migration(migrations.Migration):
    dependencies = [("authz", "0008_console_permissions")]

    operations = [migrations.RunPython(relax, restore)]

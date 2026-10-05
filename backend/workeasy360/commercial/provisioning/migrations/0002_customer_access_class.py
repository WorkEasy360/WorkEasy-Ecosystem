"""Phase 9: an organization's commercial access class on the customer summary.

Back-filled from what each row already says - products granted and the open
subscription's status - so existing customers are classified from the moment
of deployment; the hourly reconciliation then rewrites every row from the
entitlement computation itself (``provisioning.services.access_class``).
Additive and reversible.
"""

from __future__ import annotations

from django.db import migrations, models

_CUSTOMER = {"active", "past_due", "grace", "cancelled", ""}


def backfill(apps, schema_editor):
    CustomerSummary = apps.get_model("provisioning", "CustomerSummary")
    for row in CustomerSummary.objects.exclude(product_codes=[]).iterator():
        if row.subscription_status == "trialing":
            value = "trial"
        elif row.subscription_status in _CUSTOMER:
            value = "customer"
        else:
            value = ""
        if value:
            CustomerSummary.objects.filter(pk=row.pk).update(access_class=value)


class Migration(migrations.Migration):
    dependencies = [("provisioning", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="customersummary",
            name="access_class",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
        migrations.AddIndex(
            model_name="customersummary",
            index=models.Index(fields=["access_class"], name="ix_custsummary_access"),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]

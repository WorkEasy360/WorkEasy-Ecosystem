# Generated for the User Admin Panel billing profile (2026-11-11).

from django.db import migrations, models


class Migration(migrations.Migration):
    # Seven new text columns, each with a database default (empty), so the previous
    # release's INSERTs keep working while both run: nothing old reads or writes them.

    dependencies = [
        ("billing", "0002_partner_wallet_collection"),
    ]

    operations = [
        migrations.AddField(
            model_name="billingaccount",
            name=name,
            field=models.CharField(
                blank=True, db_default="", default="", max_length=length
            ),
        )
        for name, length in (
            ("legal_name", 200),
            ("address_line1", 200),
            ("address_line2", 200),
            ("city", 100),
            ("state", 100),
            ("postal_code", 20),
            ("country", 2),
        )
    ]

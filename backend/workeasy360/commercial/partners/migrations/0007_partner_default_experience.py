from django.db import migrations, models


class Migration(migrations.Migration):
    # AddField is nullable-by-default (db_default="", so the previous release's
    # INSERTs, which never name the column, still succeed) and the CHECK admits
    # the empty string every existing row holds. Nothing is dropped, renamed or
    # rewritten, and the previous release never reads the column.
    overlap_safety = (
        "AddField with db_default and a CHECK every existing row satisfies: "
        "no change to an existing column, nothing the previous release reads"
    )

    dependencies = [
        ("partners", "0006_partner_portal_api_keys_translations_notifications"),
    ]

    operations = [
        migrations.AddField(
            model_name="partneraccount",
            name="default_experience",
            field=models.CharField(blank=True, db_default="", default="", max_length=16),
        ),
        migrations.AddConstraint(
            model_name="partneraccount",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("default_experience__in", ["", "reseller", "white_label", "affiliate"])
                ),
                name="ck_partner_default_experience_known",
            ),
        ),
    ]

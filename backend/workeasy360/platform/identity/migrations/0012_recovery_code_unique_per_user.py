"""ADR-075 review L-3: a recovery code is unique per person, not across
everyone. Two people drawing the same random 40-bit code made the second
person's new codes fail with an IntegrityError (a 500)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    # AlterField only drops the global unique index; the column stays NOT NULL
    # as it always was. The table is created by 0011 in the same unreleased
    # Accounts V3 release, so no previous release ever reads or writes it.
    overlap_safety = (
        "identity_two_step_recovery_code is new in this release (0011): no running "
        "release uses it; the column keeps its NOT NULL, only the unique key narrows"
    )

    dependencies = [
        ("identity", "0011_accounts_v3"),
    ]

    operations = [
        migrations.AlterField(
            model_name="twosteprecoverycode",
            name="code_hash",
            field=models.CharField(max_length=64),
        ),
        migrations.AddConstraint(
            model_name="twosteprecoverycode",
            constraint=models.UniqueConstraint(
                fields=("user", "code_hash"), name="uq_recovery_code_per_user"
            ),
        ),
    ]

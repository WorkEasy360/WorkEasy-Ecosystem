from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    # CONCURRENTLY cannot run inside a transaction; alone in its migration so a
    # failure leaves nothing half-applied (final audit).
    atomic = False

    dependencies = [
        ("identity", "0005_portal_handoff_and_sign_in_id"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="usersession",
            index=models.Index(fields=["user", "sign_in_id"], name="ix_session_sign_in"),
        ),
    ]

"""Isolation for Phase 11's one tenant table: a product trial.

**Layer 3 - RLS** on ``subscriptions_product_trial`` (FORCE, USING, WITH
CHECK), like every other tenant-owned commercial record. The catalog's trial
configuration (``catalog_product_trial_config``) is global reference data, like
the rest of the catalog, and needs no policy.
"""

from __future__ import annotations

from django.db import migrations

from workeasy360.platform.tenancy.migrations_helpers import enable_rls


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("tenancy", "0011_phase9_isolation"),
        ("subscriptions", "0004_product_trial"),
    ]

    operations = [enable_rls("subscriptions_product_trial")]

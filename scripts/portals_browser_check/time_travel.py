"""The browser check's controlled clock: make one organization's trial of one
product 7 days (and a minute) old, as if a week had passed.

    python scripts/portals_browser_check/time_travel.py <org-slug> <product-code>

The platform has no injectable clock, so the trial's own moments move back
instead - ``ProductTrial.started_at/ends_at`` and, for that trial's source,
the derived ``Entitlement``/``FeatureEntitlement`` windows and the snapshot
entry's ``ends_at`` - exactly what they would read a week later. Nothing marks
the trial expired and no sweeper runs: access must end by the time check alone
(ADR-043). Then this scratch backend's own cache database is flushed so no
stale snapshot is served.

Scratch database only (``w360_portals_fresh``), run with the same environment
as the scratch backend (prepare.py's backend_env).
"""

from __future__ import annotations

import sys
from datetime import timedelta

import django

django.setup()

from django.core.cache import cache
from django.db.models import F

from workeasy360.commercial.subscriptions.models import ProductTrial
from workeasy360.platform.entitlements.models import (
    Entitlement,
    EntitlementSnapshot,
    FeatureEntitlement,
)
from workeasy360.platform.organizations.models import Organization
from workeasy360.platform.tenancy.db import organization_db_scope
from workeasy360.platform.tenancy.unscoped import unscoped_access

SHIFT = timedelta(days=7, minutes=1)


def main(slug: str, product_code: str) -> None:
    with unscoped_access(reason="browser check controlled clock", module="time_travel"):
        org = Organization.objects.get(slug=slug)
        with organization_db_scope(org.id):
            trials = ProductTrial.unscoped.filter(organization_id=org.id, product_code=product_code)
            trial = trials.get()
            source = f"subscriptions.trial:{trial.id}"  # the DTO's source_ref
            trials.update(started_at=F("started_at") - SHIFT, ends_at=F("ends_at") - SHIFT)
            trial.refresh_from_db()
            moved = Entitlement.unscoped.filter(organization_id=org.id, source_ref=source).update(
                effective_from=F("effective_from") - SHIFT, effective_to=trial.ends_at
            )
            FeatureEntitlement.unscoped.filter(organization_id=org.id, source_ref=source).update(
                effective_from=F("effective_from") - SHIFT, effective_to=trial.ends_at
            )
            snapshot = EntitlementSnapshot.unscoped.get(organization_id=org.id)
            entry = dict(snapshot.products.get(product_code) or {})
            entry["ends_at"] = trial.ends_at.isoformat()
            snapshot.products = {**snapshot.products, product_code: entry}
            snapshot.save(update_fields=["products"])
    cache.clear()  # this backend's own Redis database (prepare.REDIS_URL)
    print(f"moved {product_code} trial of {slug} back {SHIFT}: ends_at {trial.ends_at.isoformat()} ({moved} entitlement rows)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

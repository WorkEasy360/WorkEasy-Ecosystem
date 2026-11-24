"""Scheduled Super Admin work: re-verifying product domain ownership (ADR-070)."""

from __future__ import annotations

from celery import shared_task

from workeasy360.platform.tenancy.tasks import CrossTenantTask


@shared_task(
    base=CrossTenantTask,
    # Heavy, matching its schedule entry (base.py): up to 200 DNS lookups of
    # several seconds each must never hold a default-queue worker.
    queue="heavy",
    soft_time_limit=300,
    time_limit=420,
    name="commercial.controlplane.recheck_product_domains",
)
def recheck_product_domains() -> dict[str, int]:
    """Re-look for the ownership proof of every DNS-proven product domain.

    Reads nothing from any request; audits as the system. A resolver outage
    is not evidence of lost control, so it changes nothing.
    """
    from workeasy360.commercial.controlplane.product_domains import (
        recheck_verified_domains,
    )

    return recheck_verified_domains()


@shared_task(
    base=CrossTenantTask,
    # Heavy: each check fetches a product's document over the network and
    # may look up a DNS record (ADR-074).
    queue="heavy",
    soft_time_limit=60,
    time_limit=90,
    name="commercial.controlplane.check_product_sign_in",
)
def check_product_sign_in(product_code: str) -> str:
    """The first automatic sign-in setup check of a product just added in
    Super Admin (queued after its transaction commits). Acts for the staff
    member who added it; reads nothing from any request."""
    from workeasy360.commercial.controlplane.sso_setup import check

    return str(check(product_code=product_code)["state"])


@shared_task(
    base=CrossTenantTask,
    queue="heavy",
    soft_time_limit=300,
    time_limit=420,
    name="commercial.controlplane.sweep_sign_in_setups",
)
def sweep_sign_in_setups() -> dict[str, int]:
    """Every automatic sign-in setup whose next check is due (ADR-074):
    retries with back-off until a product is active, then a daily re-check so
    keys and addresses follow the product's document."""
    from workeasy360.commercial.controlplane.sso_setup import sweep

    return sweep()

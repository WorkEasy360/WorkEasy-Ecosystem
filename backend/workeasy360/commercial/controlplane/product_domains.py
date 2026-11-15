"""Super Admin: proving a product's domains, and whether a product is ready for
Workeasy360 sign-in (ADR-068, ADR-067).

The DNS lookup is external I/O, so it happens **here**, outside any database
transaction, through the same resolver port partner custom domains use
(``commercial.partners.domains.txt_resolver``: one DNS configuration, one test
double). ``platform.ecosystem.domains`` records what was found and owns every
rule about what a proof unlocks; this module makes no decision of its own.

Nothing here names a product.
"""

from __future__ import annotations

from typing import Any, Final

from workeasy360.commercial.partners.domains import txt_resolver
from workeasy360.contracts.errors import ManifestRejected
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.ecosystem import domains
from workeasy360.platform.ecosystem import selectors as ecosystem_selectors
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

VIEW: Final = "platform.integration.view"
UPDATE: Final = "platform.integration.update"
#: One scheduled sweep looks up at most this many domains.
RECHECK_LIMIT: Final = 200


def _require(ctx: TenantContext, permission: str, operation: str) -> None:
    authz.require(ctx, permission)
    authz.require_platform_staff(ctx, operation=operation, permission=permission)


def _integration(product_code: str) -> Any:
    integration = domains.integration_by_code(product_code)
    if integration is None:
        raise ManifestRejected(
            "That product has no registered integration.", field="product_code"
        )
    return integration


@in_scope
def list_domains(ctx: TenantContext, *, product_code: str) -> dict[str, Any]:
    """Every hostname the product's registration uses, with its proof status
    and the TXT record to publish."""
    _require(ctx, VIEW, "ecosystem.integration.domains.list")
    return domains.domains_json(_integration(product_code))


@in_scope
def request_domain(
    ctx: TenantContext, *, product_code: str, hostname: str
) -> dict[str, Any]:
    """Ask for a proof of ``hostname`` - before pointing a live product at it,
    or to replace a carried-over hostname with a DNS proof."""
    _require(ctx, UPDATE, "ecosystem.integration.domains.request")
    integration = _integration(product_code)
    domains.request_domain(ctx, integration, hostname)
    return domains.domains_json(integration)


@in_scope
def verify_domain(
    ctx: TenantContext, *, product_code: str, hostname: str
) -> dict[str, Any]:
    """Look for the TXT record now and record the result.

    The row is read, then the lookup runs with **no transaction open** (a
    resolver can take seconds to time out), then the outcome is recorded under
    a row lock by ``domains.record_check``, which discards it if the token
    changed in the meantime.
    """
    _require(ctx, UPDATE, "ecosystem.integration.domains.verify")
    integration = _integration(product_code)
    row = domains.find_domain(integration, hostname)
    if row is None or not row.token:
        raise ManifestRejected(
            "Ask for a verification value for that hostname first.", field="hostname"
        )
    found = txt_resolver().txt_values(domains.record_name(row.hostname))
    result = domains.record_check(row.id, expected_token=row.token, found=found, ctx=ctx)
    body = domains.domains_json(integration)
    body["outcome"] = result.outcome if result else "superseded"
    return body


def recheck_verified_domains(*, limit: int = RECHECK_LIMIT) -> dict[str, int]:
    """Re-look for the proof of every DNS-proven domain of a launchable
    product (the scheduled task). A domain whose record is missing for
    ``domains.MAX_MISSED_CHECKS`` consecutive checks becomes ``lapsed``: shown
    to staff, audited, and required again before reactivation or before it is
    added to a registration - never a silent suspension of a live product,
    which would turn a DNS mistake into an outage. A failed lookup is not a
    miss.
    """
    from workeasy360.platform.authz.control_plane import ensure_platform_organization

    ensure_platform_organization()
    counts = {
        "checked": 0,
        "still_verified": 0,
        "missed": 0,
        "lapsed": 0,
        "lookup_failed": 0,
    }
    for domain_id, hostname, token in domains.due_for_recheck(limit=limit):
        found = txt_resolver().txt_values(domains.record_name(hostname))
        result = domains.record_check(domain_id, expected_token=token, found=found)
        counts["checked"] += 1
        if result is None:
            continue
        if result.outcome == "lookup_failed":
            counts["lookup_failed"] += 1
        elif result.outcome == "control_lost":
            counts["lapsed"] += 1
        elif result.outcome == "record_not_found":
            counts["missed"] += 1
        else:
            counts["still_verified"] += 1
    return counts


# --- SSO readiness -----------------------------------------------------------------

OK: Final = "ok"
ACTION: Final = "action"
WARNING: Final = "warning"
INFO: Final = "info"


def _check(key: str, label: str, state: str, detail: str) -> dict[str, str]:
    return {"key": key, "label": label, "state": state, "detail": detail}


@in_scope
def sso_readiness(ctx: TenantContext, *, product_code: str) -> dict[str, Any]:
    """Whether people can sign in to this product through Workeasy360, and
    what is missing - one answer staff can act on.

    ``action`` items stop sign-in working; ``warning`` items work but leave a
    gap (no back-channel logout, failing deliveries, an unhealthy address);
    ``info`` is context. The verdict is ``not_ready`` with any action item,
    ``attention`` with only warnings, otherwise ``ready``.

    Read-only and network-free: it reports the stored results of the last
    health probe, domain checks and logout deliveries; it asks no product.
    """
    _require(ctx, VIEW, "ecosystem.integration.readiness")
    current = ecosystem_selectors.integration_for_product(ctx, product_code=product_code)
    if current is None:
        return {
            "verdict": "not_ready",
            "checks": [
                _check(
                    "registered",
                    "Registered with WorkEasy360",
                    ACTION,
                    "This product has no integration. Register one to enable sign-in.",
                )
            ],
        }
    manifest = current.manifest or {}
    checks = [
        _check(
            "registered",
            "Registered with WorkEasy360",
            OK,
            f"Client {current.client_id} ({current.status}).",
        )
    ]

    mode = str(manifest.get("sign_in_mode") or "")
    redirect_uris = list(manifest.get("redirect_uris") or [])
    if mode == "local_only":
        checks.append(
            _check(
                "sign_in",
                "WorkEasy360 sign-in",
                ACTION,
                "The product signs people in itself only (local_only).",
            )
        )
    elif not redirect_uris:
        checks.append(
            _check(
                "sign_in",
                "WorkEasy360 sign-in",
                ACTION,
                "No sign-in callback URL is registered.",
            )
        )
    else:
        checks.append(
            _check(
                "sign_in",
                "WorkEasy360 sign-in",
                OK,
                f"{len(redirect_uris)} callback URL(s) registered.",
            )
        )

    credentials = ecosystem_selectors.credentials(ctx, client_id=current.client_id)
    live = [c for c in credentials if c.status == "active"]
    checks.append(
        _check(
            "credential",
            "Client credential",
            OK if live else ACTION,
            f"{len(live)} active credential(s)."
            if live
            else "No active credential: the product cannot authenticate to WorkEasy360.",
        )
    )

    owned = domains.domains_json(domains.integration_by_code(product_code))
    if owned["all_verified"]:
        checks.append(
            _check(
                "domains",
                "Domain ownership",
                OK,
                f"{len([d for d in owned['domains'] if d['in_use']])} hostname(s) proven.",
            )
        )
    else:
        checks.append(
            _check(
                "domains",
                "Domain ownership",
                ACTION,
                "Not proven: " + ", ".join(owned["unverified"]) + ".",
            )
        )

    if current.status == "active":
        checks.append(_check("status", "Integration status", OK, "Active."))
    elif current.status == "suspended":
        checks.append(
            _check(
                "status",
                "Integration status",
                ACTION,
                f"Suspended: {current.suspension_reason or 'no reason recorded'}.",
            )
        )
    else:
        checks.append(
            _check(
                "status",
                "Integration status",
                ACTION,
                f"{current.status.title()}: not yet active.",
            )
        )

    summary = ecosystem_selectors.backchannel_delivery_summary(
        ctx, client_id=current.client_id
    )
    if not manifest.get("backchannel_logout_uri"):
        checks.append(
            _check(
                "backchannel",
                "Back-channel logout",
                WARNING,
                "Not registered: someone who signs out of Workeasy360 stays signed in "
                "here until the product's next entitlement check.",
            )
        )
    else:
        counts = summary.get("counts") or {}
        retry = summary.get("retry") or {}
        problems = int(counts.get("failed", 0)) + int(counts.get("dead", 0))
        if problems:
            last = (summary.get("last_error") or {}).get("outcome", "")
            checks.append(
                _check(
                    "backchannel",
                    "Back-channel logout",
                    WARNING,
                    f"{problems} delivery(ies) failed in the last {summary.get('window_hours', 24)} h"
                    + (f" (last: {last})." if last else "."),
                )
            )
        elif int(retry.get("retrying", 0)):
            checks.append(
                _check(
                    "backchannel",
                    "Back-channel logout",
                    WARNING,
                    f"{retry['retrying']} delivery(ies) being retried.",
                )
            )
        else:
            checks.append(
                _check(
                    "backchannel",
                    "Back-channel logout",
                    OK,
                    "Registered; recent deliveries succeeded.",
                )
            )

    health = current.health_status
    if health == "healthy":
        checks.append(
            _check("health", "Address health", OK, "Answered at the last check.")
        )
    elif health == "unknown":
        checks.append(_check("health", "Address health", INFO, "Not checked yet."))
    else:
        checks.append(
            _check(
                "health",
                "Address health",
                WARNING,
                f"{health.title()} at the last check ({current.health_detail or 'no detail'}).",
            )
        )

    states = {c["state"] for c in checks}
    verdict = (
        "not_ready"
        if ACTION in states
        else ("attention" if WARNING in states else "ready")
    )
    return {"verdict": verdict, "checks": checks}

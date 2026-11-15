"""Product domain ownership (ADR-068): which hostnames a registration uses,
and whether its owner has proven control of each.

A manifest may *name* any public https hostname - ``contracts.values.manifest``
already refuses internal, loopback (in production), wildcard and credentialed
URLs. What it cannot know is whether the hostname belongs to whoever registered
it. A typo, a social-engineered "please update our callback", or a lapsed name
someone else re-registered would otherwise receive authorization codes, logout
tokens and signed-out browsers. So:

- **An integration cannot become active** until every hostname its manifest
  uses is ``verified``.
- **An active (or suspended) integration cannot be pointed at a new
  hostname** until that hostname is verified for it. Hostnames it already
  uses are not re-litigated by an unrelated edit.
- **A draft accepts any hostname** and simply gains a pending proof for it -
  the Super Admin "Add product" flow and the registry API keep working
  unchanged; activation is where the proof is required.

The proof is a DNS TXT record at ``_w360-product-verify.<hostname>`` carrying
a per-(integration, hostname) token - the same mechanism partner custom
domains use (``commercial/partners/domains.py``), under a different record
name so the two never read each other's proofs.

**No network here.** The ecosystem module makes no outbound call
(``test_the_ecosystem_module_makes_no_outbound_http_request``); the lookup is
done by ``commercial/controlplane/product_domains.py`` outside any
transaction and its *result* is recorded by :func:`record_check`.

Carried over: integrations registered before this existed have every hostname
of their manifest at that moment marked ``verified`` with method
``pre_existing`` (migration ``ecosystem.0008``), so live products are not
disturbed. Staff can still ask for a DNS proof of such a hostname at any time.
"""

from __future__ import annotations

import ipaddress
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from workeasy360.contracts.errors import ManifestRejected, ProductDomainUnverified
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.ecosystem.models import (
    ProductDomain,
    ProductDomainMethod,
    ProductDomainStatus,
    ProductIntegration,
)
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

RECORD_PREFIX: Final = "_w360-product-verify."
TOKEN_PREFIX: Final = "w360-product-verify="  # noqa: S105 - a public DNS proof label  # pragma: allowlist secret
#: A verified (DNS-proven) domain whose record is missing on this many
#: consecutive re-checks becomes ``lapsed``. A *failed lookup* is not a miss.
MAX_MISSED_CHECKS: Final = 3
DOMAIN_CHECKS_TOTAL: Final = "ecosystem_product_domain_checks_total"

#: Every manifest field that holds a URL a browser or token is sent to, or
#: that the platform calls. A new URL field must be added here - the manifest
#: test pins the list against ``ProductManifest``'s URL-typed fields.
SINGLE_URL_FIELDS: Final = (
    "launch_url",
    "health_url",
    "login_url",
    "backchannel_logout_uri",
)
MULTI_URL_FIELDS: Final = ("redirect_uris", "post_logout_redirect_uris")


# --- hostnames ---------------------------------------------------------------


def canonical_hostname(raw: str) -> str:
    """Lower-case, no trailing dot, IDNA (punycode). ``""`` if unusable.

    Manifest URLs are validated before they get here, so this only has to
    make two spellings of one name compare equal.
    """
    text = (raw or "").strip().strip("[]").lower().rstrip(".")
    if not text:
        return ""
    try:
        ipaddress.ip_address(text)
        return text
    except ValueError:
        pass
    try:
        return text.encode("idna").decode("ascii")
    except UnicodeError:
        return ""


def hostnames_of(manifest: Any) -> list[str]:
    """Every hostname a manifest (stored dict or ``ProductManifest``) uses,
    sorted and de-duplicated."""

    def read(field: str) -> Any:
        if isinstance(manifest, dict):
            return manifest.get(field)
        return getattr(manifest, field, None)

    urls: list[str] = []
    for field in SINGLE_URL_FIELDS:
        value = read(field)
        if value:
            urls.append(str(value))
    for field in MULTI_URL_FIELDS:
        urls.extend(str(v) for v in (read(field) or ()) if v)
    found = {canonical_hostname(urlsplit(url).hostname or "") for url in urls}
    found.discard("")
    return sorted(found)


_LABEL: Final = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def is_dns_name(hostname: str) -> bool:
    """A fully-qualified DNS name in canonical form: at least two labels of
    letters, digits and inner hyphens, at most 253 characters, not an IP."""
    labels = hostname.split(".")
    return (
        0 < len(hostname) <= 253
        and len(labels) >= 2
        and all(_LABEL.match(label) for label in labels)
        and not labels[-1].isdigit()
    )


def record_name(hostname: str) -> str:
    return f"{RECORD_PREFIX}{hostname}"


def new_token() -> str:
    return f"{TOKEN_PREFIX}{secrets.token_urlsafe(24)}"


def _loopback_accepted(hostname: str) -> bool:
    """Loopback hosts are proven by construction where - and only where - the
    manifest validator accepts plaintext loopback URLs at all (development and
    tests). In production such a URL never validates, so this never applies."""
    if not getattr(settings, "OIDC_ALLOW_INSECURE_REDIRECT_URIS", False):
        return False
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


# --- the gate ----------------------------------------------------------------


def verified_hostnames(integration: ProductIntegration) -> set[str]:
    return set(
        ProductDomain.objects.filter(
            integration=integration, status=ProductDomainStatus.VERIFIED.value
        ).values_list("hostname", flat=True)
    )


def ensure_domains(integration: ProductIntegration, hostnames: list[str]) -> None:
    """A row for each hostname this integration has none for: pending with a
    fresh token, or verified for an accepted loopback host. Idempotent; runs
    inside the caller's transaction so a refused registration leaves nothing.
    """
    existing = set(
        ProductDomain.objects.filter(integration=integration).values_list(
            "hostname", flat=True
        )
    )
    now = timezone.now()
    rows = []
    for hostname in hostnames:
        if hostname in existing:
            continue
        if _loopback_accepted(hostname):
            rows.append(
                ProductDomain(
                    integration=integration,
                    hostname=hostname,
                    status=ProductDomainStatus.VERIFIED.value,
                    method=ProductDomainMethod.LOOPBACK.value,
                    verified_at=now,
                    last_outcome="loopback",
                )
            )
        else:
            rows.append(
                ProductDomain(
                    integration=integration, hostname=hostname, token=new_token()
                )
            )
    if rows:
        ProductDomain.objects.bulk_create(rows, ignore_conflicts=True)


def require_verified(
    integration: ProductIntegration, hostnames: list[str], *, action: str
) -> None:
    """Refuse ``action`` unless every one of ``hostnames`` is verified for
    this integration. The message names each hostname and its record."""
    missing = tuple(sorted(set(hostnames) - verified_hostnames(integration)))
    if not missing:
        return
    records = ", ".join(f"TXT {record_name(h)}" for h in missing)
    metrics.increment(DOMAIN_CHECKS_TOTAL, outcome="gate_refused")
    raise ProductDomainUnverified(
        f"Cannot {action}: control of {', '.join(missing)} is not proven yet. "
        f"Publish the verification value shown under Domains as {records}, "
        f"then choose Verify.",
        hostnames=missing,
        product_code=integration.product_code,
    )


# --- staff actions (no network; the lookup happens in controlplane) -----------


def request_domain(
    ctx: TenantContext, integration: ProductIntegration, hostname: str
) -> ProductDomain:
    """Make sure ``hostname`` has a row with a token to publish.

    New: a pending row. Already carried over (``pre_existing``) or loopback
    without a token: a token is issued so staff can replace the carry-over
    with a DNS proof; it stays verified meanwhile. Pending or lapsed: the
    existing token is kept - re-publishing the same value is enough.
    """
    canonical = canonical_hostname(hostname)
    if not is_dns_name(canonical):
        # Bad input, not missing state: 400, naming the field.
        raise ManifestRejected(
            "That is not a hostname. Enter a domain name such as app.example.com.",
            field="hostname",
        )
    with transaction.atomic():
        row, created = ProductDomain.objects.select_for_update().get_or_create(
            integration=integration,
            hostname=canonical,
            defaults={"token": new_token()},
        )
        if not created and not row.token:
            row.token = new_token()
            row.save(update_fields=["token", "updated_at"])
        if created:
            audit.record(
                ctx,
                "ecosystem.integration.domain_requested",
                resource_type="ProductIntegration",
                resource_id=integration.id,
                changes={"product_code": integration.product_code, "hostname": canonical},
            )
    return row


def attest_domains(
    ctx: TenantContext, integration: ProductIntegration, *, reason: str
) -> list[str]:
    """Mark every hostname of the integration's manifest verified on an
    operator's word (``register_products --activate-integrations`` only - no
    API reaches this). Audited with the operator's reason."""
    hostnames = hostnames_of(integration.manifest or {})
    now = timezone.now()
    with transaction.atomic():
        ensure_domains(integration, hostnames)
        changed = list(
            ProductDomain.objects.select_for_update()
            .filter(integration=integration, hostname__in=hostnames)
            .exclude(status=ProductDomainStatus.VERIFIED.value)
            .values_list("hostname", flat=True)
        )
        ProductDomain.objects.filter(
            integration=integration, hostname__in=changed
        ).update(
            status=ProductDomainStatus.VERIFIED.value,
            method=ProductDomainMethod.ATTESTED.value,
            verified_at=now,
            last_outcome="attested",
            updated_at=now,
        )
        if changed:
            audit.record(
                ctx,
                "ecosystem.integration.domain_attested",
                resource_type="ProductIntegration",
                resource_id=integration.id,
                changes={
                    "product_code": integration.product_code,
                    "hostnames": sorted(changed),
                    "reason": reason,
                },
            )
    return sorted(changed)


@dataclass(frozen=True, slots=True)
class CheckResult:
    domain: ProductDomain
    #: ``verified``, ``record_not_found``, ``lookup_failed``, ``control_lost``,
    #: ``still_verified`` (a re-check that found the record).
    outcome: str


def record_check(
    domain_id: Any,
    *,
    expected_token: str,
    found: list[str] | None,
    ctx: TenantContext | None = None,
) -> CheckResult | None:
    """Record what a TXT lookup saw, under a row lock, re-reading the row so a
    concurrent check cannot be overwritten by a stale one.

    ``found`` is ``None`` when the lookup itself failed - not evidence of
    absence: nothing changes but the check time. ``expected_token`` is the
    token the lookup searched for; if the row's token changed meanwhile the
    result is discarded (``None``).

    ``ctx`` is the staff member who asked; ``None`` for the scheduled
    re-check, which audits as the system.
    """
    from contextlib import nullcontext

    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.platform.tenancy.db import organization_db_scope

    now = timezone.now()
    # The scheduled re-check has no request context: like the partner domain
    # re-check, it audits as the system inside the platform organization's
    # database scope (the audit table is under RLS).
    scope = (
        organization_db_scope(PLATFORM_ORGANIZATION_ID) if ctx is None else nullcontext()
    )
    with transaction.atomic(), scope:
        row = (
            # Lock the domain row only: a check must not hold the integration
            # row against a concurrent lifecycle transition.
            ProductDomain.objects.select_for_update(of=("self",))
            .select_related("integration")
            .filter(id=domain_id)
            .first()
        )
        if row is None or not expected_token or row.token != expected_token:
            return None
        row.last_checked_at = now
        row.check_count += 1
        proven = found is not None and expected_token in found
        was = row.status_enum
        dns_proven = row.method == ProductDomainMethod.DNS_TXT.value

        if found is None:
            outcome = "lookup_failed"
        elif proven:
            outcome = (
                "still_verified"
                if (was is ProductDomainStatus.VERIFIED and dns_proven)
                else "verified"
            )
            row.status = ProductDomainStatus.VERIFIED.value
            row.method = ProductDomainMethod.DNS_TXT.value
            row.missed_checks = 0
            if outcome == "verified":
                row.verified_at = now
        elif was is ProductDomainStatus.VERIFIED and dns_proven:
            row.missed_checks += 1
            outcome = "record_not_found"
            if row.missed_checks >= MAX_MISSED_CHECKS:
                row.status = ProductDomainStatus.LAPSED.value
                outcome = "control_lost"
        else:
            # Pending, lapsed, or a carried-over domain whose optional DNS
            # proof is not published yet: nothing proven, nothing lost.
            outcome = "record_not_found"
        row.last_outcome = outcome
        row.save()

        if outcome in ("verified", "control_lost"):
            action = (
                "ecosystem.integration.domain_verified"
                if outcome == "verified"
                else "ecosystem.integration.domain_lapsed"
            )
            changes = {
                "product_code": row.integration.product_code,
                "hostname": row.hostname,
                "method": row.method,
            }
            if ctx is not None:
                audit.record(
                    ctx,
                    action,
                    resource_type="ProductIntegration",
                    resource_id=row.integration_id,
                    changes=changes,
                )
            else:
                audit.record_system(
                    PLATFORM_ORGANIZATION_ID,
                    action,
                    resource_type="ProductIntegration",
                    resource_id=row.integration_id,
                    changes=changes,
                    reason=(
                        "Domain-control record missing on repeated re-verification."
                        if outcome == "control_lost"
                        else "Domain-control record found."
                    ),
                )
    if outcome == "control_lost":
        logger.warning(
            "ecosystem.integration.domain_lapsed",
            product_code=row.integration.product_code,
            hostname=row.hostname,
        )
    metrics.increment(DOMAIN_CHECKS_TOTAL, outcome=outcome)
    return CheckResult(domain=row, outcome=outcome)


def due_for_recheck(*, limit: int) -> list[tuple[Any, str, str]]:
    """(id, hostname, token) of DNS-proven domains of launchable integrations,
    least recently checked first."""
    from workeasy360.platform.ecosystem.models import IntegrationStatus

    return list(
        ProductDomain.objects.filter(
            status=ProductDomainStatus.VERIFIED.value,
            method=ProductDomainMethod.DNS_TXT.value,
            integration__status__in=[
                IntegrationStatus.ACTIVE.value,
                IntegrationStatus.SUSPENDED.value,
            ],
        )
        .exclude(token="")
        .order_by("last_checked_at", "id")
        .values_list("id", "hostname", "token")[:limit]
    )


# --- read side ----------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def domains_json(integration: ProductIntegration) -> dict[str, Any]:
    """What staff see: every hostname the manifest uses (and any older row no
    longer in use), its status, and the record to publish."""
    in_use = hostnames_of(integration.manifest or {})
    rows = {
        r.hostname: r
        for r in ProductDomain.objects.filter(integration=integration).order_by(
            "hostname"
        )
    }
    items = []
    for hostname in sorted(set(in_use) | set(rows)):
        row = rows.get(hostname)
        items.append(
            {
                "hostname": hostname,
                "in_use": hostname in in_use,
                "status": row.status if row else "missing",
                "method": row.method if row else "",
                "record_name": record_name(hostname),
                "record_type": "TXT",
                "record_value": row.token if row else "",
                "requested_at": _iso(row.requested_at) if row else None,
                "verified_at": _iso(row.verified_at) if row else None,
                "last_checked_at": _iso(row.last_checked_at) if row else None,
                "last_outcome": row.last_outcome if row else "",
            }
        )
    unverified = [
        i["hostname"] for i in items if i["in_use"] and i["status"] != "verified"
    ]
    return {"domains": items, "unverified": unverified, "all_verified": not unverified}


def integration_by_code(product_code: str) -> ProductIntegration | None:
    return ProductIntegration.objects.filter(product_code=str(product_code)).first()


def find_domain(integration: ProductIntegration, hostname: str) -> ProductDomain | None:
    return ProductDomain.objects.filter(
        integration=integration, hostname=canonical_hostname(hostname)
    ).first()

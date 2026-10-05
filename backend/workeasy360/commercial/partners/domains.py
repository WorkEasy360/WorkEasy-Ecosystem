"""Custom domains: ownership-verified metadata, never an identity (Phase 7, Task 32-35, 85-86).

A white-label partner may ask for a hostname to carry its branding. What this
module guarantees:

- **A domain is never active until the platform has itself seen proof of
  control.** The proof is a TXT record at ``_w360-verify.<hostname>`` carrying
  a per-request token, looked up by the platform's own resolver. A partner
  cannot claim a name by asserting it.
- **One proven owner per hostname**, decided by a partial unique index, so two
  partners racing to verify one name resolve in the database.
- **Control can lapse, and the platform notices.** Verified and active domains
  are re-checked; when the record disappears (an expired registration, a
  released name a stranger might re-register) the domain is disabled after
  repeated misses rather than trusted forever - the DNS-takeover case.
- **Hostnames are canonicalised** (lower-case, IDNA, no dot) so ``Example.COM``,
  ``example.com.`` and its punycode form are one name, and platform-owned or
  reserved names cannot be claimed.
- **A host is not an identity.** The public lookup returns presentation
  metadata for an *active* domain and nothing about who may do what; tenant
  resolution, sessions and authorization never read a Host header.

The DNS lookup is external I/O, so it happens **outside any database
transaction** (E2), through a small port that tests replace, with explicit
timeouts (E1).
"""

from __future__ import annotations

import re
import secrets
from datetime import timedelta
from typing import Any, Final, Protocol
from uuid import UUID

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from workeasy360.commercial.partners import access, selectors
from workeasy360.contracts.enums import DomainStatus, PrincipalKind
from workeasy360.contracts.errors import (
    DomainOwnershipUnproven,
    PartnerRecordNotFound,
    PartnerValidationError,
)
from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import ensure_scope, in_scope
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

MANAGE = "platform.partner_domain.manage"
VIEW = "platform.partner_domain.view"
STAFF_MANAGE = "platform.staff_partner.manage"

DOMAIN_OPERATIONS = "partner_domain_operations_total"
VERIFY_PREFIX: Final = "_w360-verify."
TOKEN_PREFIX: Final = "w360-verify="  # noqa: S105 - a public DNS proof label, not a credential

#: A request that has not been proven within this window is failed, so a
#: pending request is a bounded liability and not a permanent claim on a name.
VERIFICATION_WINDOW: Final = timedelta(days=7)
MAX_VERIFICATION_ATTEMPTS: Final = 30
#: A proven domain whose record has been missing for this many consecutive
#: re-checks is disabled.
MAX_MISSED_CHECKS: Final = 3
MAX_LIVE_DOMAINS_PER_PARTNER: Final = 10

_LABEL_RE: Final = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_RESERVED_SUFFIXES: Final = (
    ".local",
    ".localhost",
    ".internal",
    ".lan",
    ".invalid",
    ".test",
    ".example",
    ".arpa",
    ".onion",
)
_LIVE: Final = (
    DomainStatus.REQUESTED.value,
    DomainStatus.VERIFICATION_PENDING.value,
    DomainStatus.VERIFIED.value,
    DomainStatus.ACTIVE.value,
)


# --- hostname canonicalisation -------------------------------------------


def normalize_hostname(raw: Any) -> str:
    """The canonical form of a hostname, or ``PartnerValidationError``.

    Refuses anything that is not a bare, public, fully-qualified DNS name: no
    scheme, path, port, credentials, wildcard or IP literal, and none of the
    platform's own domains or reserved suffixes. IDNA is applied so that a
    Unicode name and its punycode spelling normalise to one string - the form
    the uniqueness index compares.
    """
    if not isinstance(raw, str):
        raise PartnerValidationError("hostname must be text.", field="hostname")
    text = raw.strip().lower().rstrip(".")
    if not text or len(text) > 253 or any(c in text for c in "/:@?#*\\ \t\r\n_"):
        raise PartnerValidationError(
            "hostname must be a bare domain name such as 'app.example.com'.",
            field="hostname",
        )
    try:
        ascii_name = text.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise PartnerValidationError(
            "hostname is not a valid domain.", field="hostname"
        ) from exc

    labels = ascii_name.split(".")
    if len(labels) < 2 or len(ascii_name) > 253:
        raise PartnerValidationError(
            "hostname must be a fully-qualified domain name.", field="hostname"
        )
    if not all(_LABEL_RE.match(label) for label in labels):
        raise PartnerValidationError("hostname is not a valid domain.", field="hostname")
    if labels[-1].isdigit():
        # An IP address (or something pretending to be one) is not a domain.
        raise PartnerValidationError(
            "hostname must not be an IP address.", field="hostname"
        )
    if ascii_name == "localhost" or ascii_name.endswith(_RESERVED_SUFFIXES):
        raise PartnerValidationError("That domain cannot be used.", field="hostname")
    for owned in getattr(settings, "W360_PLATFORM_DOMAINS", ()):
        owned = owned.lower().strip(".")
        if ascii_name == owned or ascii_name.endswith("." + owned):
            raise PartnerValidationError(
                "That domain belongs to the platform.", field="hostname"
            )
    return ascii_name


def verification_record_name(hostname: str) -> str:
    return f"{VERIFY_PREFIX}{hostname}"


def _new_token() -> str:
    return f"{TOKEN_PREFIX}{secrets.token_urlsafe(24)}"


# --- the DNS port ---------------------------------------------------------


class TxtResolver(Protocol):
    def txt_values(self, name: str) -> list[str] | None:
        """TXT strings at ``name``; ``[]`` if none; ``None`` if the lookup failed
        (timeout, server failure) - which is *not* evidence of absence."""
        ...


class DnsPythonResolver:
    """The production resolver: ``dnspython`` with hard timeouts."""

    def txt_values(self, name: str) -> list[str] | None:
        import dns.exception
        import dns.resolver

        resolver = dns.resolver.Resolver()
        resolver.timeout = float(getattr(settings, "W360_DNS_TIMEOUT_SECONDS", 3))
        resolver.lifetime = float(getattr(settings, "W360_DNS_LIFETIME_SECONDS", 5))
        nameservers = getattr(settings, "W360_DNS_RESOLVERS", ())
        if nameservers:
            resolver.nameservers = list(nameservers)
            # 53 unless told otherwise; set by the integration test that runs
            # a real DNS server on a local port.
            resolver.port = int(getattr(settings, "W360_DNS_PORT", 53))
        try:
            answer = resolver.resolve(name, "TXT")
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return []
        except (dns.exception.DNSException, OSError):
            return None
        return [
            b"".join(record.strings).decode("utf-8", errors="replace")
            for record in answer
        ]


_resolver: TxtResolver = DnsPythonResolver()


def set_txt_resolver(resolver: TxtResolver) -> TxtResolver:
    """Swap the resolver (tests). Returns the previous one."""
    global _resolver
    previous, _resolver = _resolver, resolver
    return previous


# --- authorisation --------------------------------------------------------


def _domain_for(ctx: TenantContext, domain_id: UUID, *, staff_ok: bool) -> Any:
    """The domain, scoped to the caller: a partner's own, or any for staff.

    A staff caller needs ``platform.staff_partner.manage``, which is dangerous:
    verifying, activating and disabling a hostname are second-factor actions
    whoever performs them."""
    from workeasy360.commercial.partners.models import PartnerDomain

    if ctx.principal is PrincipalKind.PARTNER:
        authz.require(ctx, MANAGE)
        access.require_transacting(ctx)
        row = selectors.domain_for(ctx.partner_id, domain_id)
    elif staff_ok:
        authz.require(ctx, STAFF_MANAGE)
        row = PartnerDomain.objects.filter(id=domain_id).first()
    else:
        authz.require(ctx, MANAGE)  # raises: a staff context does not hold it
        row = None
    if row is None:
        raise PartnerRecordNotFound("No such domain.", domain_id=str(domain_id))
    return row


# --- commands -------------------------------------------------------------


@in_scope
def request_domain(
    ctx: TenantContext, *, hostname: Any, partner_id: UUID | None = None
) -> Any:
    """Ask to serve branding on ``hostname``. Idempotent per partner and name.

    ``partner_id`` is for staff requesting on a partner's behalf (Phase 10d); a
    partner user never passes one. Either way the request is **only a request**:
    nothing is served until the platform itself has seen the DNS proof and the
    domain has been separately activated.
    """
    from workeasy360.commercial.partners.models import PartnerDomain

    subject = access.subject_partner(ctx, partner_id, partner_permission=MANAGE)
    name = normalize_hostname(hostname)

    with transaction.atomic():
        live = list(
            PartnerDomain.objects.select_for_update()
            .filter(partner_id=subject, status__in=_LIVE)
            .order_by("id")
        )
        for row in live:
            if row.hostname == name:
                return row
        if len(live) >= MAX_LIVE_DOMAINS_PER_PARTNER:
            raise PartnerValidationError(
                f"A partner may hold at most {MAX_LIVE_DOMAINS_PER_PARTNER} domains.",
                field="hostname",
            )
        try:
            with transaction.atomic():
                domain = PartnerDomain.objects.create(
                    partner_id=subject,
                    hostname=name,
                    verification_token=_new_token(),
                    requested_by_id=ctx.user_id,
                )
        except IntegrityError:
            existing = PartnerDomain.objects.filter(
                partner_id=subject, hostname=name, status__in=_LIVE
            ).first()
            if existing is None:
                raise
            return existing
        audit.record(
            ctx,
            "partner.domain_requested",
            resource_type="partners.domain",
            resource_id=domain.id,
            changes={"hostname": name},
        )
    metrics.increment(DOMAIN_OPERATIONS, operation="request", outcome="ok")
    return domain


def verify_domain(ctx: TenantContext, *, domain_id: UUID) -> Any:
    """Look for the proof in DNS and record what was found.

    The lookup runs **before** the database transaction opens: no connection is
    held while waiting on a resolver that may take seconds to time out. The
    outcome is then recorded under a row lock, re-reading the row, so a
    concurrent verification, disable or activation cannot be overwritten with a
    result computed against a state that has since moved on.
    """
    from workeasy360.commercial.partners.models import PartnerDomain

    domain = _domain_for(ctx, domain_id, staff_ok=True)
    if domain.status_enum in (DomainStatus.FAILED, DomainStatus.DISABLED):
        raise DomainOwnershipUnproven(
            "This request is closed. Request the domain again.",
            domain_status=domain.status,
        )
    if domain.status_enum in (DomainStatus.VERIFIED, DomainStatus.ACTIVE):
        return domain  # already proven; nothing to look up

    expected = domain.verification_token
    found = _resolver.txt_values(verification_record_name(domain.hostname))
    proven = found is not None and expected in found

    with ensure_scope(ctx), transaction.atomic():
        row = (
            PartnerDomain.objects.select_for_update()
            .filter(id=domain.id, partner_id=domain.partner_id)
            .first()
        )
        if row is None or row.status_enum not in (
            DomainStatus.REQUESTED,
            DomainStatus.VERIFICATION_PENDING,
        ):
            return row or domain
        now = timezone.now()
        row.last_checked_at = now
        row.check_count += 1

        if proven:
            row.status = DomainStatus.VERIFIED.value
            row.verified_at = now
            row.failure_code = ""
            try:
                with transaction.atomic():
                    row.version += 1
                    row.save()
            except IntegrityError:
                # Another partner proved this name first. Theirs stands; this
                # request ends rather than sharing the claim.
                row.refresh_from_db()
                row.status = DomainStatus.FAILED.value
                row.verified_at = None
                row.failure_code = "hostname_claimed"
                row.version += 1
                row.save()
                _audit_domain(ctx, row, "partner.domain_verification_failed")
                metrics.increment(
                    DOMAIN_OPERATIONS, operation="verify", outcome="claimed"
                )
                return row
            _audit_domain(ctx, row, "partner.domain_verified")
            metrics.increment(DOMAIN_OPERATIONS, operation="verify", outcome="verified")
            return row

        row.status = DomainStatus.VERIFICATION_PENDING.value
        row.failure_code = "lookup_failed" if found is None else "record_not_found"
        expired = (
            now - row.created_at > VERIFICATION_WINDOW
            or row.check_count >= MAX_VERIFICATION_ATTEMPTS
        )
        if expired:
            row.status = DomainStatus.FAILED.value
            row.failure_code = "verification_expired"
            row.verification_token = ""  # a spent proof: nothing left to check against
        row.version += 1
        row.save()
        if expired:
            _audit_domain(ctx, row, "partner.domain_verification_failed")
        metrics.increment(
            DOMAIN_OPERATIONS,
            operation="verify",
            outcome="expired" if expired else "pending",
        )
        return row


@in_scope
def activate_domain(ctx: TenantContext, *, domain_id: UUID) -> Any:
    """Make a **verified** domain serve branding. Never from any other state."""
    from workeasy360.commercial.partners.models import PartnerDomain

    domain = _domain_for(ctx, domain_id, staff_ok=True)
    if selectors.brand_for(domain.partner_id) is None:
        raise PartnerValidationError(
            "Configure branding before activating a domain.", field="domain_id"
        )
    with transaction.atomic():
        row = (
            PartnerDomain.objects.select_for_update()
            .filter(id=domain.id, partner_id=domain.partner_id)
            .first()
        )
        if row is None:
            raise PartnerRecordNotFound("No such domain.", domain_id=str(domain_id))
        if row.status_enum is DomainStatus.ACTIVE:
            return row
        if row.status_enum is not DomainStatus.VERIFIED:
            raise DomainOwnershipUnproven(
                "Ownership of this domain has not been verified.",
                domain_status=row.status,
            )
        row.status = DomainStatus.ACTIVE.value
        row.activated_at = timezone.now()
        row.version += 1
        row.save(update_fields=["status", "activated_at", "version", "updated_at"])
        _audit_domain(ctx, row, "partner.domain_activated")
    invalidate_host(row.hostname)
    metrics.increment(DOMAIN_OPERATIONS, operation="activate", outcome="ok")
    return row


@in_scope
def disable_domain(ctx: TenantContext, *, domain_id: UUID) -> Any:
    """Stop serving a domain. The partner or staff may; the name is released."""
    from workeasy360.commercial.partners.models import PartnerDomain

    domain = _domain_for(ctx, domain_id, staff_ok=True)
    with transaction.atomic():
        row = PartnerDomain.objects.select_for_update().filter(id=domain.id).first()
        if row is None:
            raise PartnerRecordNotFound("No such domain.", domain_id=str(domain_id))
        if row.status_enum in (DomainStatus.DISABLED, DomainStatus.FAILED):
            return row
        row.status = DomainStatus.DISABLED.value
        row.disabled_at = timezone.now()
        row.verification_token = ""  # retired with the claim (see the retention notes)
        row.version += 1
        row.save(
            update_fields=[
                "status",
                "disabled_at",
                "verification_token",
                "version",
                "updated_at",
            ]
        )
        _audit_domain(ctx, row, "partner.domain_disabled")
    invalidate_host(row.hostname)
    metrics.increment(DOMAIN_OPERATIONS, operation="disable", outcome="ok")
    return row


def _audit_domain(ctx: TenantContext, row: Any, action: str) -> None:
    audit.record(
        ctx,
        action,
        resource_type="partners.domain",
        resource_id=row.id,
        changes={
            "partner_id": str(row.partner_id),
            "hostname": row.hostname,
            "status": row.status,
            "failure_code": row.failure_code,
        },
    )


# --- re-verification (system) ----------------------------------------------


def recheck_proven_domains(*, limit: int = 200) -> dict[str, int]:
    """Re-look for the proof on every verified/active domain.

    Run by a scheduled task. A domain whose record is missing for
    ``MAX_MISSED_CHECKS`` consecutive checks is disabled: the name may have
    expired or been released, and continuing to serve a partner's branding on
    a name it no longer controls is how a takeover becomes a phishing site. A
    *failed lookup* (resolver down) is not a miss - an outage must not disable
    every partner's domain.

    Establishes its own trusted context: it writes the platform audit log as
    ``SYSTEM`` inside the platform organization's scope and inherits nothing
    from any request.
    """
    from workeasy360.commercial.partners.models import PartnerDomain
    from workeasy360.platform.authz.control_plane import ensure_platform_organization
    from workeasy360.platform.tenancy.db import organization_db_scope

    ensure_platform_organization()
    counts = {"checked": 0, "missed": 0, "disabled": 0, "lookup_failed": 0}
    candidates = list(
        PartnerDomain.objects.filter(
            status__in=[DomainStatus.VERIFIED.value, DomainStatus.ACTIVE.value]
        )
        .order_by("last_checked_at", "id")
        .values_list("id", "hostname", "verification_token")[:limit]
    )
    for domain_id, hostname, token in candidates:
        found = _resolver.txt_values(verification_record_name(hostname))
        counts["checked"] += 1
        if found is None:
            counts["lookup_failed"] += 1
            continue
        with transaction.atomic(), organization_db_scope(PLATFORM_ORGANIZATION_ID):
            row = PartnerDomain.objects.select_for_update().filter(id=domain_id).first()
            if row is None or row.status_enum not in (
                DomainStatus.VERIFIED,
                DomainStatus.ACTIVE,
            ):
                continue
            row.last_checked_at = timezone.now()
            if token in found:
                row.missed_checks = 0
                row.save(update_fields=["missed_checks", "last_checked_at", "updated_at"])
                continue
            counts["missed"] += 1
            row.missed_checks += 1
            if row.missed_checks >= MAX_MISSED_CHECKS:
                row.status = DomainStatus.DISABLED.value
                row.disabled_at = timezone.now()
                row.failure_code = "control_lost"
                row.verification_token = ""
                row.version += 1
                counts["disabled"] += 1
                row.save()
                audit.record_system(
                    PLATFORM_ORGANIZATION_ID,
                    "partner.domain_disabled",
                    resource_type="partners.domain",
                    resource_id=row.id,
                    changes={"hostname": row.hostname, "failure_code": "control_lost"},
                    reason="Domain-control record missing on repeated re-verification.",
                )
                invalidate_host(row.hostname)
            else:
                row.save(update_fields=["missed_checks", "last_checked_at", "updated_at"])
    return counts


# --- the public host -> branding lookup ------------------------------------

_HOST_CACHE_TTL: Final = 60
_NEGATIVE: Final = {"_none": True}


def _cache_key(hostname: str) -> str:
    return f"w360:partner:brand-host:{hostname}"


def invalidate_host(hostname: str) -> None:
    try:
        cache.delete(_cache_key(hostname))
    except Exception:  # noqa: S110 - cache down: the TTL bounds staleness
        pass


def invalidate_partner_hosts(partner_id: UUID) -> None:
    """Forget every host this partner may be serving (its proven domains)."""
    for hostname in selectors.proven_hostnames(partner_id):
        invalidate_host(hostname)


def public_brand_for_host(raw_host: Any) -> dict[str, Any] | None:
    """Presentation metadata for an ACTIVE custom domain, or ``None``.

    The cache key is the **canonical hostname** and the cached value is
    presentation only - never a partner id, a tenant or any authority - so a
    poisoned or stale entry can at worst show the wrong logo for up to a
    minute; it cannot grant anything. A malformed host is simply "no brand",
    not an error: the caller falls back to the default Workeasy360 branding,
    and a custom-domain failure never affects the canonical domain (Task 118,
    119).
    """
    try:
        host = normalize_hostname(raw_host)
    except PartnerValidationError:
        return None

    key = _cache_key(host)
    try:
        cached = cache.get(key)
    except Exception:
        cached = None
    if cached is not None:
        return None if cached == _NEGATIVE else cached

    brand = selectors.active_brand_for_host(host)
    try:
        cache.set(key, brand if brand is not None else _NEGATIVE, _HOST_CACHE_TTL)
    except Exception:  # noqa: S110
        pass
    return brand

"""Application services for the product integration control plane.

Every function is a use case: it owns its transaction, enforces authorization
twice, validates, writes, and audits. Views call these; nothing calls the
models directly.

**Authorization is two-factor for every mutation**, exactly as catalog
administration is, and for the same reason:

    authz.require(ctx, "platform.integration.<x>")   # role, and dangerous ->
                                                     # AAL 2, no impersonation
    authz.require_platform_staff(ctx, operation=..., permission=...)
                                                     # held via a StaffGrant

Either alone is one misconfiguration away from a customer administrator being
able to register an application against the platform, mint credentials for it
and read every organization's entitlement state.

**Every mutation is audited**, including the ones that fail closed. The audit
entry is written against the acting staff member's organization context - the
same shape Phase 2 established for catalog administration - because a
control-plane action still has an actor, and "who changed CRM's redirect URI"
is the first question anyone will ask.

What is deliberately *not* audited here: authorization denials and token
issuance failures at the OIDC endpoints. Those have no tenant context (there
is no session and no membership at the token endpoint) and ``AuditEvent`` is
tenant-scoped and customer-readable. An audit log entry with no tenant has no
customer to show it to. They go to structured logs and metrics instead, which
is where the security-event stream in 11-observability.md section 9 expects
them (docs/phase-3/11-security-threat-model.md section 7).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from workeasy360.contracts.enums import AuditOutcome
from workeasy360.contracts.errors import (
    ManifestRejected,
    ProductIntegrationError,
    ProductIntegrationNotFound,
)
from workeasy360.contracts.ports.catalog import get_product_directory
from workeasy360.contracts.values.manifest import ManifestError, parse_manifest
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.ecosystem import clientauth, keys
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

#: Bytes of entropy in a client id. Public, but random rather than derived:
#: a client id built from the product code would let anyone construct another
#: product's id, and it is also the `aud` claim, which should carry no
#: meaning at all.
CLIENT_ID_BYTES = 24

#: Default credential lifetime. A credential with no end date outlives the
#: person who created it and the reason it existed; a year is long enough not
#: to be operational noise and short enough to force the rotation procedure to
#: be exercised while anyone still remembers it.
DEFAULT_CREDENTIAL_DAYS = 365

#: The credential kind issued when a caller does not choose one. The stronger
#: of the two: a product that has not thought about it gets the mechanism
#: where no shared secret exists to be stolen.
DEFAULT_CREDENTIAL_KIND = "public_key"


def _enum(enum_cls: Any, value: Any, *, field: str) -> Any:
    """Parse a caller-supplied string into one of this module's enums.

    Lives here rather than in the view on purpose. The alternative - views
    importing ``ecosystem.models`` to build the enum themselves - is
    cross-module ORM access (F-6): it welds ``interfaces`` to this module's
    model file, so moving an enum out of ``models.py`` would break a view that
    is not in this module's test suite. Services take strings from the edge
    and own the validation.
    """
    try:
        return enum_cls(value)
    except ValueError:
        raise ProductIntegrationError(
            f"Unknown {field} {value!r}.",
            field=field,
            allowed=[member.value for member in enum_cls],
        ) from None


@dataclass(frozen=True, slots=True)
class IssuedCredential:
    """A newly minted credential. ``secret`` is present exactly once, ever."""

    credential_id: str
    kind: str
    expires_at: Any
    #: The plaintext secret, for ``client_secret`` credentials only. It is
    #: returned by this call and never again: nothing stores it, and no read
    #: path can produce it. A credential API that can re-read a secret leaks
    #: every secret the day it is compromised.
    secret: str = ""


def _authorise(ctx: TenantContext, permission: str, operation: str) -> None:
    authz.require(ctx, permission)
    authz.require_platform_staff(ctx, operation=operation, permission=permission)


# --- registration --------------------------------------------------------


def register_integration(ctx: TenantContext, *, manifest: Any) -> Any:
    """Register an external product. Starts in ``draft``.

    Never active on creation. A registration that could authenticate the
    moment it was written would mean a typo in a redirect URI is live before
    anyone has looked at it; the ``draft -> active`` transition is a separate,
    separately-audited decision.
    """
    from workeasy360.contracts.values.identifiers import ProductCode
    from workeasy360.platform.ecosystem.models import (
        IntegrationStatus,
        ProductIntegration,
    )

    _authorise(ctx, "platform.integration.register", "ecosystem.integration.register")

    parsed = _parse(manifest)
    _require_known_product(parsed.product_code)

    with transaction.atomic():
        try:
            integration = ProductIntegration.objects.create(
                product_code=str(ProductCode(parsed.product_code)),
                client_id=secrets.token_urlsafe(CLIENT_ID_BYTES),
                status=IntegrationStatus.DRAFT.value,
                manifest=parsed.as_dict(),
            )
        except IntegrityError:
            # The unique constraint on product_code, hit by two concurrent
            # registrations. Caught rather than pre-checked with an exists()
            # query, because that check-then-insert is a race: both callers
            # see "no row" and one of them still has to handle this.
            raise ProductIntegrationError(
                f"Product {parsed.product_code!r} already has an integration. "
                f"Update the existing registration rather than creating a "
                f"second client for the same product.",
                product_code=str(parsed.product_code),
            ) from None

        audit.record(
            ctx,
            "ecosystem.integration.registered",
            resource_type="ProductIntegration",
            resource_id=integration.id,
            changes={
                "product_code": str(parsed.product_code),
                "client_id": integration.client_id,
                "redirect_uris": list(parsed.redirect_uris),
                "scopes": list(parsed.scopes),
            },
        )

    logger.info(
        "ecosystem.integration.registered",
        product_code=str(parsed.product_code),
        manifest_version=parsed.manifest_version,
    )
    metrics.increment("ecosystem_integration_lifecycle_total", transition="registered")
    return integration


def update_manifest(ctx: TenantContext, *, client_id: str, manifest: Any) -> Any:
    """Replace a registration's manifest.

    The highest-risk write in this module: changing a redirect URI redirects
    every future authorization code for that product. It is ``dangerous``, it
    is staff-only, and the audit entry records the **old and new redirect URI
    sets** specifically so that an unauthorised change is visible in the trail
    rather than merely implied by it.

    The product code cannot change. A manifest naming a different product is
    a different integration; allowing the swap would let one client id mean
    CRM on Monday and Books on Tuesday, while every token ever issued under
    it still says CRM.
    """
    from workeasy360.platform.ecosystem.models import ProductIntegration

    _authorise(ctx, "platform.integration.update", "ecosystem.integration.update")
    parsed = _parse(manifest)

    with transaction.atomic():
        integration = _locked(ProductIntegration, client_id=client_id)
        if str(parsed.product_code) != integration.product_code:
            raise ProductIntegrationError(
                "A manifest update may not change the product code. Register "
                "a separate integration for a different product.",
                product_code=integration.product_code,
            )
        if integration.status_enum.is_terminal:
            raise ProductIntegrationError(
                "A retired integration cannot be modified.",
                product_code=integration.product_code,
            )

        previous = integration.manifest_obj
        integration.manifest = parsed.as_dict()
        integration.save(update_fields=["manifest", "updated_at"])

        audit.record(
            ctx,
            "ecosystem.integration.manifest_updated",
            resource_type="ProductIntegration",
            resource_id=integration.id,
            changes={
                "product_code": integration.product_code,
                "redirect_uris_before": list(previous.redirect_uris),
                "redirect_uris_after": list(parsed.redirect_uris),
                "scopes_before": list(previous.scopes),
                "scopes_after": list(parsed.scopes),
            },
        )

    logger.info(
        "ecosystem.integration.manifest_updated", product_code=integration.product_code
    )
    return integration


# --- lifecycle -----------------------------------------------------------


def transition(
    ctx: TenantContext, *, client_id: str, to_status: Any, reason: str = ""
) -> Any:
    """Move an integration through its lifecycle, or refuse.

    One function for all four transitions rather than four near-identical
    ones. The legality table lives in ``models.INTEGRATION_TRANSITIONS``, so
    the state machine is readable in one place and a new state cannot be added
    without deciding what reaches it.

    Row-locked, so two concurrent transitions serialise rather than both
    reading ``active`` and one silently overwriting the other's result.
    """
    from workeasy360.platform.ecosystem.models import (
        INTEGRATION_TRANSITIONS,
        IntegrationStatus,
        ProductIntegration,
    )

    _authorise(ctx, "platform.integration.lifecycle", "ecosystem.integration.lifecycle")
    target = _enum(IntegrationStatus, to_status, field="status")

    if target is IntegrationStatus.SUSPENDED and not reason:
        # A suspension stops every user of that product signing in. An
        # unexplained one is an outage nobody can attribute, so the reason is
        # required at the boundary rather than hoped for.
        raise ProductIntegrationError(
            "Suspending an integration requires a reason.", to_status=target.value
        )

    with transaction.atomic():
        integration = _locked(ProductIntegration, client_id=client_id)
        current = integration.status_enum

        if target not in INTEGRATION_TRANSITIONS[current]:
            # Refused rather than silently ignored. An activation that quietly
            # does nothing is indistinguishable from one that worked, and the
            # difference is whether a customer can sign in.
            raise ProductIntegrationError(
                f"{current.value} -> {target.value} is not a permitted "
                f"integration transition.",
                from_status=current.value,
                to_status=target.value,
            )

        now = timezone.now()
        fields = ["status", "updated_at"]
        integration.status = target.value

        if target is IntegrationStatus.ACTIVE:
            integration.activated_at = integration.activated_at or now
            integration.suspended_at = None
            integration.suspension_reason = ""
            fields += ["activated_at", "suspended_at", "suspension_reason"]
        elif target is IntegrationStatus.SUSPENDED:
            integration.suspended_at = now
            integration.suspension_reason = reason
            fields += ["suspended_at", "suspension_reason"]
        elif target is IntegrationStatus.RETIRED:
            integration.retired_at = now
            fields += ["retired_at"]

        integration.save(update_fields=fields)

        audit.record(
            ctx,
            f"ecosystem.integration.{target.value}",
            resource_type="ProductIntegration",
            resource_id=integration.id,
            changes={
                "product_code": integration.product_code,
                "from_status": current.value,
                "to_status": target.value,
            },
            reason=reason,
        )

    logger.info(
        "ecosystem.integration.transitioned",
        product_code=integration.product_code,
        from_status=current.value,
        to_status=target.value,
    )
    metrics.increment("ecosystem_integration_lifecycle_total", transition=target.value)
    return integration


# --- credentials ---------------------------------------------------------


def create_credential(
    ctx: TenantContext,
    *,
    client_id: str,
    kind: Any = DEFAULT_CREDENTIAL_KIND,
    public_key_pem: str = "",
    algorithm: str = "",
    label: str = "",
    valid_days: int = DEFAULT_CREDENTIAL_DAYS,
) -> IssuedCredential:
    """Issue a credential. Does **not** revoke any existing one.

    Overlap is the point. A product rotates by creating the new credential,
    deploying it, verifying it works, and only then revoking the old one. A
    rotation that required atomically swapping a secret across a fleet is a
    rotation nobody performs, and a credential nobody rotates is a credential
    that is eventually leaked.
    """
    from workeasy360.platform.ecosystem.models import (
        CredentialKind,
        IntegrationCredential,
        ProductIntegration,
    )

    _authorise(ctx, "platform.integration.credential", "ecosystem.credential.create")
    target_kind = _enum(CredentialKind, kind, field="kind")

    with transaction.atomic():
        integration = _locked(ProductIntegration, client_id=client_id)
        if integration.status_enum.is_terminal:
            raise ProductIntegrationError(
                "A retired integration cannot be issued credentials.",
                product_code=integration.product_code,
            )

        expires_at = timezone.now() + timezone.timedelta(days=max(1, valid_days))
        secret = ""

        if target_kind is CredentialKind.PUBLIC_KEY:
            if not public_key_pem.strip():
                raise ProductIntegrationError(
                    "A public_key credential requires public_key_pem."
                )
            if algorithm not in keys.SUPPORTED_ALGORITHMS:
                # The algorithm is pinned at registration and used at verify
                # time instead of the assertion's own header. Accepting an
                # arbitrary value here would put the choice back in the
                # attacker's hands, which is the algorithm-confusion attack.
                raise ProductIntegrationError(
                    f"Unsupported credential algorithm {algorithm!r}. "
                    f"Supported: {', '.join(keys.SUPPORTED_ALGORITHMS)}."
                )
            _assert_usable_public_key(public_key_pem, algorithm)
            credential_reference = keys.new_kid()
            credential = IntegrationCredential.objects.create(
                integration=integration,
                kind=target_kind.value,
                credential_id=credential_reference,
                public_key_pem=public_key_pem.strip(),
                algorithm=algorithm,
                expires_at=expires_at,
                label=label,
            )
        else:
            secret, prefix, digest = clientauth.generate_secret()
            credential = IntegrationCredential.objects.create(
                integration=integration,
                kind=target_kind.value,
                credential_id=prefix,
                secret_hash=digest,
                expires_at=expires_at,
                label=label,
            )

        audit.record(
            ctx,
            "ecosystem.credential.created",
            resource_type="IntegrationCredential",
            resource_id=credential.id,
            # The credential *reference* is recorded; the secret is not, and
            # `audit.redact` would strip it anyway if a future edit tried.
            changes={
                "product_code": integration.product_code,
                "kind": target_kind.value,
                "credential_id": credential.credential_id,
                "expires_at": expires_at.isoformat(),
            },
        )

    logger.info(
        "ecosystem.credential.created",
        product_code=integration.product_code,
        kind=target_kind.value,
    )
    return IssuedCredential(
        credential_id=credential.credential_id,
        kind=target_kind.value,
        expires_at=expires_at,
        secret=secret,
    )


def revoke_credential(
    ctx: TenantContext, *, client_id: str, credential_id: str, reason: str
) -> Any:
    """Revoke a credential immediately.

    Effective on the next request with no propagation delay, because client
    authentication reads the row every time - there is no cached credential
    state anywhere. That is the deliberate difference from token revocation,
    which is bounded by the token lifetime because tokens are verified
    offline (docs/phase-3/07-service-authentication.md section 6).

    Idempotent: revoking an already-revoked credential succeeds and does not
    move the timestamp. An operator responding to an incident must not be
    stopped by an error message saying the thing they wanted has happened.
    """
    from workeasy360.platform.ecosystem.models import (
        CredentialStatus,
        IntegrationCredential,
    )

    _authorise(ctx, "platform.integration.credential", "ecosystem.credential.revoke")
    if not reason:
        raise ProductIntegrationError("Revoking a credential requires a reason.")

    with transaction.atomic():
        credential = (
            IntegrationCredential.objects.select_for_update()
            .select_related("integration")
            .filter(integration__client_id=client_id, credential_id=credential_id)
            .first()
        )
        if credential is None:
            raise ProductIntegrationNotFound("No such credential.")

        if credential.status == CredentialStatus.REVOKED.value:
            return credential

        credential.status = CredentialStatus.REVOKED.value
        credential.revoked_at = timezone.now()
        credential.revocation_reason = reason
        credential.save(update_fields=["status", "revoked_at", "revocation_reason"])

        audit.record(
            ctx,
            "ecosystem.credential.revoked",
            resource_type="IntegrationCredential",
            resource_id=credential.id,
            changes={
                "product_code": credential.integration.product_code,
                "credential_id": credential.credential_id,
                "kind": credential.kind,
            },
            reason=reason,
            outcome=AuditOutcome.SUCCESS,
        )

    logger.info(
        "ecosystem.credential.revoked",
        product_code=credential.integration.product_code,
    )
    metrics.increment("ecosystem_credential_revocations_total")
    return credential


# --- signing keys --------------------------------------------------------
#
# There are deliberately **no service wrappers here**, and no HTTP surface.
#
# Signing-key rotation is an operator action performed with
# `manage.py w360_signing_key`, which calls `keys.py` directly. Three reasons,
# in order of weight:
#
# 1. The generate step produces private key material that has to reach the
#    secret manager. An API that returned the platform's private signing key
#    over the network would be the single worst endpoint in the system, and
#    one that did not return it could not complete a rotation.
# 2. There is no actor. `audit.record` needs a TenantContext - a user and a
#    membership - and a terminal session has neither. A synthetic one would
#    put an invented person's identifier against a platform event, which is
#    the objection `audit.record_system` already makes.
# 3. A rotation is not a tenant event. `AuditEvent` is tenant-scoped and
#    customer-readable; a platform key rotation belongs in the security-event
#    stream (11-observability.md section 9), which is where `keys.py` logs it
#    - asserted by test_key_rotation_is_recorded_in_the_security_log.
#
# The earlier draft of this module carried `rotate_signing_key`,
# `promote_signing_key` and `retire_signing_key` wrappers with a
# `platform.integration.key` permission. Nothing called them: the command
# bypassed them because it could not build a context. They were removed
# rather than wired up, because wiring them up meant building the endpoint in
# (1). See docs/phase-3/15-cleanup-review.md.


# --- internals -----------------------------------------------------------


def _parse(manifest: Any) -> Any:
    """Validate a submitted manifest into the platform's error model."""
    from django.conf import settings

    try:
        return parse_manifest(
            manifest,
            allow_insecure_urls=bool(settings.OIDC_ALLOW_INSECURE_REDIRECT_URIS),
        )
    except ManifestError as exc:
        raise ManifestRejected(exc.message, field=exc.field) from None


def _require_known_product(product_code: str) -> None:
    """Refuse a registration for a product the catalog does not know.

    Through the contracts port, so ``platform`` never imports ``commercial``
    (F-4). Catches the case that would otherwise produce an integration
    nobody can ever be entitled to - every entitlement check against a code
    no plan grants denies, and the resulting ticket reads "SSO is broken"
    rather than "the product code is misspelt".
    """
    from workeasy360.contracts.values.identifiers import ProductCode

    if get_product_directory().get(ProductCode(product_code)) is None:
        raise ManifestRejected(
            f"No product with code {product_code!r} exists in the catalog. "
            f"A product must be a registered SKU before an application can "
            f"be registered against it.",
            field="product_code",
        )


def _locked(model: Any, **lookup: Any) -> Any:
    """Fetch a row for update, or raise the 404-shaped not-found error."""
    row = model.objects.select_for_update().filter(**lookup).first()
    if row is None:
        raise ProductIntegrationNotFound("No such product integration.")
    return row


def _assert_usable_public_key(public_key_pem: str, algorithm: str) -> None:
    """Refuse a key that will not actually verify anything.

    Checked at registration rather than discovered at the first token
    exchange. A malformed or mismatched key stored now becomes an
    authentication outage later, at a moment nobody connects to this action -
    and the product's operator, who is not looking at this platform's logs,
    has no way to diagnose it.
    """
    import jwt

    try:
        algorithm_cls = jwt.algorithms.get_default_algorithms()[algorithm]
        algorithm_cls.prepare_key(public_key_pem.strip())
    except Exception as exc:
        raise ProductIntegrationError(
            f"The supplied public key is not a usable {algorithm} key "
            f"({type(exc).__name__})."
        ) from None

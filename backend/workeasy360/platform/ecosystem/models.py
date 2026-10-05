"""The ecosystem control plane: registered products, credentials, keys, codes.

Every table here is **control plane**, not tenant data. None carries an
``organization`` foreign key, none has an RLS policy, and none is a
``TenantScopedModel`` - for the same reason ``identity.User`` is not one, and
the same reason ``catalog.Product`` is not one. These rows describe *the
ecosystem*, not *a customer's business*.

``AuthorizationCode`` is the one that needs its exemption stated plainly,
because it does carry an ``organization_id`` column. It is exempt from
W360.E001 (see ``TENANT_SCOPE_EXEMPT``, ADR-020) because the token endpoint
has **no tenant context and cannot have one**: there is no session, no
membership and no organization until the code is found, and the code is what
establishes them. A tenant-scoped manager would require the answer in order
to ask the question. The row is protected instead by being reachable only
through the SHA-256 of a 256-bit random secret that is additionally bound to
one client, one redirect URI and one PKCE challenge.

**What is deliberately not stored anywhere in this module:**

- private signing keys - a *reference* only, resolved through
  ``contracts/ports/signing.py`` from the secret manager;
- authorization codes - only their SHA-256, so a database dump yields no
  redeemable credential;
- client secrets - only their SHA-256, for the same reason;
- issued tokens - they are verified offline by signature and are never
  looked up, so storing them would create a target with no purpose.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db import models
from django.utils import timezone

from workeasy360.contracts.enums import StrEnum
from workeasy360.contracts.values.manifest import ProductManifest, from_stored_manifest


class IntegrationStatus(StrEnum):
    """The registration lifecycle. Only ``active`` may authenticate a user.

    Four states because three are not enough and five would be ceremony:

    - ``draft``    registered, credentials issuable, cannot authenticate.
                   This is where a product lives while it is being built.
    - ``active``   fully operational.
    - ``suspended`` reversible stop. New authentication is refused; the
                   registration, its credentials and every commercial record
                   are untouched, so reactivating is one transition and not a
                   re-onboarding.
    - ``retired``  terminal. Deliberately not a delete: the audit trail, the
                   commercial history and any support question about "what
                   was this client id?" all outlive the integration.
    """

    DRAFT = "draft"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    RETIRED = "retired"

    @property
    def can_authenticate(self) -> bool:
        """Whether a user may be authenticated into this product.

        A property rather than a caller-side comparison so that every call
        site converges on one rule - branching on status names in business
        code is the same defect class as branching on plan names (C8).
        """
        return self is IntegrationStatus.ACTIVE

    @property
    def is_terminal(self) -> bool:
        return self is IntegrationStatus.RETIRED


class IntegrationHealth(StrEnum):
    """What the last bounded reachability check of a product saw (Phase 11).

    **Technical, and separate from everything commercial.** ``catalog.Product``
    says whether a product is *for sale*; ``IntegrationStatus`` says whether it
    may *authenticate*; this says whether its address answered when last asked.
    It gates nothing: an unhealthy product can still be launched, because a
    probe can be wrong and refusing a paying customer on a guess is worse than
    a slow page. It is only ever shown.

    ``unknown`` is the honest default and stays until the first check.
    """

    UNKNOWN = "unknown"
    #: The address answered with a success or a redirect.
    HEALTHY = "healthy"
    #: It answered, but with an error status.
    DEGRADED = "degraded"
    #: Nothing answered: DNS, connection, TLS or a timeout failed.
    UNREACHABLE = "unreachable"


#: Legal lifecycle transitions. A table rather than a chain of ifs, so the
#: whole state machine is visible at once and a new state cannot be added
#: without deciding what reaches it.
INTEGRATION_TRANSITIONS: dict[IntegrationStatus, frozenset[IntegrationStatus]] = {
    IntegrationStatus.DRAFT: frozenset(
        {IntegrationStatus.ACTIVE, IntegrationStatus.RETIRED}
    ),
    IntegrationStatus.ACTIVE: frozenset(
        {IntegrationStatus.SUSPENDED, IntegrationStatus.RETIRED}
    ),
    IntegrationStatus.SUSPENDED: frozenset(
        {IntegrationStatus.ACTIVE, IntegrationStatus.RETIRED}
    ),
    IntegrationStatus.RETIRED: frozenset(),
}


class CredentialKind(StrEnum):
    """How a product proves it is itself at the token endpoint."""

    #: RFC 7523. The product holds a private key and signs a short-lived
    #: assertion; the platform stores only the public half. No shared secret
    #: is ever transmitted, logged, or capable of being stolen from the
    #: platform's database. The default, and what the guide recommends.
    PUBLIC_KEY = "public_key"

    #: RFC 6749 section 2.3.1. A 256-bit random secret, stored as a SHA-256
    #: digest. Offered because a product that cannot manage a key pair is a
    #: real constraint, not a hypothetical one.
    CLIENT_SECRET = "client_secret"  # noqa: S105 - an enum member name, not a secret


class CredentialStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class SigningKeyStatus(StrEnum):
    """A signing key's place in the rotation.

    Three live states, which is what makes rotation possible without a
    synchronised redeploy of every product (docs/phase-3/08-key-management.md):

    - ``pending``   published in the JWKS, not yet signing. Products cache the
                    key set; publishing ahead of use is what stops the first
                    token signed by a new key from failing verification
                    against a stale cache.
    - ``active``    the one key currently signing. Exactly one at a time.
    - ``retiring``  no longer signing, still published and still verifying, so
                    tokens issued before the switch remain valid for their
                    remaining lifetime.
    - ``retired``   removed from the JWKS. Tokens signed by it no longer
                    verify anywhere.
    """

    PENDING = "pending"
    ACTIVE = "active"
    RETIRING = "retiring"
    RETIRED = "retired"


def _choices(enum_cls: Any) -> list[tuple[str, str]]:
    return [(member.value, member.name.replace("_", " ").title()) for member in enum_cls]


class ProductIntegration(models.Model):
    """One externally developed product's registration with the ecosystem.

    **Not** ``catalog.Product``. The two answer different questions and change
    for different reasons:

        catalog.Product      "What can we sell, and what does it cost?"
        ProductIntegration   "How does this application securely connect?"

    They are joined by ``product_code`` and by nothing else. A foreign key
    would make the platform's authentication path depend on a commercial
    table, and F-4 forbids ``platform -> commercial`` precisely so that a
    product never learns how it was purchased. Validation that the code names
    a real SKU happens once, at registration, through
    ``contracts/ports/catalog.py``.

    One integration per product code, enforced by a unique constraint: two
    registrations for ``crm`` would mean two client ids that both claim to be
    CRM, and an entitlement answer that depended on which one asked.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    #: The ecosystem identity of the product. Validated as a ``ProductCode``
    #: and checked against the catalog at registration.
    product_code = models.CharField(max_length=32, unique=True)

    #: The OAuth client identifier. A random 32-character token rather than a
    #: readable slug: a client id derived from the product code would let
    #: anyone construct another product's id and probe with it, and it is
    #: also the `aud` claim, so it should carry no meaning at all.
    client_id = models.CharField(max_length=64, unique=True)

    status = models.CharField(
        max_length=16,
        choices=_choices(IntegrationStatus),
        default=IntegrationStatus.DRAFT.value,
        db_index=True,
    )

    #: The validated manifest, exactly as accepted. The **single source** for
    #: launch URL, redirect URIs, scopes, capabilities and auth method - they
    #: are not also columns, because two copies of a redirect URI list is one
    #: copy too many and the one that drifts is always the one being checked.
    manifest = models.JSONField()

    #: Lifecycle as timestamps rather than inferred from ``status``, so "when
    #: did this go live?" survives a later suspension and is answerable
    #: without reading the audit log. Same shape as ``catalog.Product``.
    activated_at = models.DateTimeField(null=True, blank=True)
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspension_reason = models.TextField(blank=True, default="")
    retired_at = models.DateTimeField(null=True, blank=True)

    # --- Phase 11: technical health, written only by ``health.record_health``
    # from the bounded probe. Telemetry, never an access decision.
    health_status = models.CharField(
        max_length=16,
        choices=_choices(IntegrationHealth),
        default=IntegrationHealth.UNKNOWN.value,
    )
    health_checked_at = models.DateTimeField(null=True, blank=True)
    #: A short category such as ``http_503`` or ``tls_error`` - never a
    #: response body, a header or an address.
    health_detail = models.CharField(max_length=64, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ecosystem_product_integration"
        ordering = ["product_code"]
        indexes = [
            # The authorization endpoint's lookup: client id plus "may it
            # authenticate?" in one index, so the hot path is a single probe.
            models.Index(fields=["client_id", "status"], name="ix_integration_client"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.product_code} ({self.status})"

    @property
    def status_enum(self) -> IntegrationStatus:
        return IntegrationStatus(self.status)

    @property
    def manifest_obj(self) -> ProductManifest:
        """The manifest as a validated value object.

        Reconstructed without re-running validation. The rules are applied at
        write time by the registration service; re-applying them on every
        read would mean that tightening a rule silently breaks every existing
        integration at *runtime* - turning a deliberate policy change into an
        unannounced ecosystem outage. Tightened rules are applied by
        re-registration, which is an auditable act.
        """
        return from_stored_manifest(self.manifest)

    def permits_redirect(self, candidate: str) -> bool:
        """Exact-match a redirect URI. The only comparison ever performed."""
        return self.manifest_obj.permits_redirect(candidate)


class IntegrationCredential(models.Model):
    """A credential a product uses to authenticate itself to the platform.

    Both kinds live in one table with one lifecycle, because rotation,
    revocation, expiry and audit are identical for both and two tables would
    mean two implementations of each - one of which would eventually be
    missing revocation.

    **Overlap is supported and is the point.** Creating a second active
    credential does not retire the first, so a product rotates by adding the
    new one, deploying, and only then revoking the old one. A rotation that
    required atomically swapping a secret across a fleet is a rotation nobody
    performs (docs/phase-3/07-service-authentication.md section 5).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    integration = models.ForeignKey(
        ProductIntegration, on_delete=models.CASCADE, related_name="credentials"
    )

    kind = models.CharField(max_length=16, choices=_choices(CredentialKind))
    status = models.CharField(
        max_length=16,
        choices=_choices(CredentialStatus),
        default=CredentialStatus.ACTIVE.value,
        db_index=True,
    )

    #: Public, non-secret identifier.
    #:
    #: For ``public_key`` it is the JWK ``kid`` the product puts in its
    #: assertion header, which is how the platform selects the verifying key
    #: without trying every one of them.
    #:
    #: For ``client_secret`` it is the secret's first 8 characters. Indexing
    #: a prefix means the lookup is a single row rather than a scan of every
    #: credential the client holds - which matters because the alternative is
    #: hashing the candidate once per stored credential.
    credential_id = models.CharField(max_length=64)

    #: SHA-256 of the secret, hex. Empty for ``public_key``.
    #:
    #: SHA-256 and not Argon2, deliberately. Argon2's cost exists to make a
    #: *low-entropy human-chosen* password expensive to guess. These secrets
    #: are 256 bits from ``secrets.token_urlsafe``, so there is nothing to
    #: guess and the work factor buys no security - while costing 100ms on
    #: every token exchange and handing an attacker a CPU-exhaustion lever
    #: against the one endpoint that must stay available
    #: (docs/phase-3/07-service-authentication.md section 4).
    secret_hash = models.CharField(max_length=64, blank=True, default="")

    #: PEM-encoded public key. Empty for ``client_secret``. Public by
    #: definition, so it is safe at rest here.
    public_key_pem = models.TextField(blank=True, default="")
    #: The JWS algorithm this key verifies. Stored rather than inferred: an
    #: attacker who could choose the algorithm could present an RSA public
    #: key as an HMAC secret, which is the classic JWT confusion attack.
    algorithm = models.CharField(max_length=16, blank=True, default="")

    #: Credentials expire. A credential with no end date is a credential that
    #: outlives the person who created it and the reason it existed.
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.TextField(blank=True, default="")

    #: Last successful authentication, to the day. Deliberately coarse: this
    #: exists to answer "is this credential still in use?" before revoking
    #: it, and a per-second timestamp would mean a write on every token
    #: exchange - turning a read path into a write path for no added answer.
    last_used_on = models.DateField(null=True, blank=True)

    label = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ecosystem_integration_credential"
        constraints = [
            models.UniqueConstraint(
                fields=["integration", "kind", "credential_id"],
                name="uq_credential_integration_ref",
            ),
        ]
        indexes = [
            models.Index(
                fields=["integration", "status"], name="ix_credential_integration"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.kind}:{self.credential_id} for {self.integration_id}"

    @property
    def is_usable(self) -> bool:
        """Active, not revoked and not expired - all three, every time.

        A single predicate rather than three checks at the call site: a
        credential check that forgot one of them would accept a revoked
        credential, and the one most often forgotten is expiry.
        """
        if self.status != CredentialStatus.ACTIVE.value:
            return False
        if self.revoked_at is not None:
            return False
        return not (self.expires_at is not None and self.expires_at <= timezone.now())


class SigningKey(models.Model):
    """A platform token-signing key. The public half only.

    The private half never enters this database. ``private_key_ref`` is a
    name that ``contracts/ports/signing.py`` resolves against the secret
    manager - the same pattern ``identity.AuthFactor.secret_ref`` already
    uses for MFA secrets, and for a strictly larger reason: whoever holds
    this key can mint a token for any user in any organization for any
    product.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    #: The JWK ``kid``. Published, put in every token header, and used by a
    #: product to select the verifying key from the cached key set. Random,
    #: not sequential: a predictable kid tells an observer how many rotations
    #: have happened and when the next one is due.
    kid = models.CharField(max_length=64, unique=True)

    algorithm = models.CharField(max_length=16)
    public_key_pem = models.TextField()

    #: The secret manager reference for the private half. **Never the key.**
    private_key_ref = models.CharField(max_length=128)

    status = models.CharField(
        max_length=16,
        choices=_choices(SigningKeyStatus),
        default=SigningKeyStatus.PENDING.value,
        db_index=True,
    )

    activated_at = models.DateTimeField(null=True, blank=True)
    retiring_at = models.DateTimeField(null=True, blank=True)
    retired_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ecosystem_signing_key"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"], name="ix_signing_key_status"),
        ]
        constraints = [
            # Exactly one signing key at a time. Two would make "which key
            # signed this?" depend on query ordering, and a rotation that
            # left both active would be indistinguishable from a rotation
            # that had not finished.
            models.UniqueConstraint(
                fields=["status"],
                condition=models.Q(status=SigningKeyStatus.ACTIVE.value),
                name="uq_one_active_signing_key",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.kid} ({self.status})"

    @property
    def status_enum(self) -> SigningKeyStatus:
        return SigningKeyStatus(self.status)


class AuthorizationCode(models.Model):
    """A single-use, short-lived authorization code and everything it binds.

    The code itself is never stored - only its SHA-256. A database dump must
    not yield a redeemable credential, exactly as
    ``organizations.Invitation.token_hash`` already establishes for
    invitations.

    Every column below the hash exists to make the code *unusable in any
    context other than the one it was issued for*. That is the whole defence
    against the attacks in docs/phase-3/11-security-threat-model.md:

        integration      -> a code minted for CRM cannot be redeemed by Books
        redirect_uri     -> it can only be returned to the exact URI it named
        code_challenge   -> only the browser that started the flow can finish it
        organization_id  -> the tenant was fixed at issue time, not at exchange
        expires_at       -> a stolen code is worthless within a minute
        redeemed_at      -> and worthless immediately after its first use

    **Not tenant-scoped, by necessity.** See this module's docstring; the
    exemption is recorded in ``TENANT_SCOPE_EXEMPT`` and ADR-020.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    #: SHA-256 of the code, hex. Unique, so a hash collision or a duplicate
    #: issue is a database error rather than two codes that redeem each other.
    code_hash = models.CharField(max_length=64, unique=True)

    integration = models.ForeignKey(
        ProductIntegration, on_delete=models.CASCADE, related_name="authorization_codes"
    )

    #: The resolved context, carried as plain UUID columns.
    #:
    #: Not foreign keys, because a join is not the right shape for this. These
    #: three fix *which* subject the code speaks for, and that must never
    #: change between issue and redemption - a code issued for one tenant
    #: being completed against another is the defect the binding exists to
    #: prevent. What a foreign key would additionally give - a live read of
    #: the row's current state - is not skipped, it is done explicitly at
    #: redemption by ``authorization._revalidate_subject``, which re-runs the
    #: membership, organization-status and entitlement checks through the
    #: same functions the authorization endpoint uses. The identifiers are
    #: fixed; the *permission* is re-asked.
    user_id = models.UUIDField()
    organization_id = models.UUIDField()
    membership_id = models.UUIDField()

    #: Authenticator assurance level reached by the session that authorized.
    #: Carried into the token so a product can gate its own sensitive actions
    #: on step-up without inventing a second factor.
    aal = models.PositiveSmallIntegerField(default=1)

    #: Bound at issue, compared at redemption (RFC 6749 section 4.1.3).
    redirect_uri = models.CharField(max_length=512)

    #: Space-delimited, the granted subset - never what was requested.
    scope = models.CharField(max_length=128)

    #: RFC 7636. ``S256`` only: ``plain`` puts the verifier in the same
    #: request that carries the code and protects nothing.
    code_challenge = models.CharField(max_length=128)
    code_challenge_method = models.CharField(max_length=8)

    #: OIDC Core section 3.1.2.1. Echoed into the ID token so the client can
    #: detect a replayed or substituted token.
    nonce = models.CharField(max_length=255, blank=True, default="")

    #: What the authorizing browser session vouched for (sign-in time, second
    #: factor, client address) when the organization's access policy allowed
    #: it - re-judged against the live policy at redemption
    #: (``authorization._revalidate_subject``, Phase 12). Nullable, so the
    #: column is safe while the previous release is still writing codes; a
    #: code without it is judged as the least-trusted session. Lives exactly
    #: as long as the code (pruned with it).
    session_facts = models.JSONField(null=True, blank=True)

    expires_at = models.DateTimeField()
    #: Set by a conditional UPDATE, which is what makes single use hold under
    #: concurrent redemption. See ``authorization.redeem_code``.
    redeemed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ecosystem_authorization_code"
        indexes = [
            # The pruning job's query. Codes are tiny and live 60 seconds,
            # but without this the sweep is a full scan of everything ever
            # issued.
            models.Index(fields=["expires_at"], name="ix_authcode_expires"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return (
            f"code for {self.integration_id} ({'used' if self.redeemed_at else 'live'})"
        )

    # There is deliberately no `is_live` predicate here.
    #
    # It existed, it had no callers, and it was the more dangerous kind of
    # dead code: it read as the answer to "may this code be redeemed?" while
    # checking two of the seven things redemption actually checks. The real
    # answer involves the client, the redirect URI, the PKCE challenge, the
    # membership, the organization and the entitlement, it is in
    # `authorization.redeem_code`, and it is not expressible as a property of
    # a row. A second, weaker definition of the same question is how the
    # weaker one eventually gets called.


class PendingAuthorization(models.Model):
    """A verified authorization request, parked while the browser completes
    login or organization selection at the platform's own UI. Phase 4.

    **The gap this closes.** ADR-018 accepted, as a stated consequence, that
    an unauthenticated `/authorize` request had nowhere to send the browser
    except back to the product with a `login_required` error - because no
    platform UI existed yet to send it to instead. Now one does
    (docs/phase-4/05-organization-selection.md), and the standards-conformant
    behaviour is for the *provider* to interrupt the flow with its own login
    page and resume afterwards, not to make every product implement that
    round trip itself.

    **Why a row, not a signed, stateless token.** A self-contained signed
    token (a JWT-shaped blob naming the client, redirect URI, PKCE challenge
    and organization) would need no database write and would work identically
    across instances - the property the frozen Phase 3 rule "multi-instance-
    safe security state" cares about, and PostgreSQL already gives it for
    free here. What a stateless token cannot give is **revocation on first
    use**: `AuthorizationCode` is single-use by exactly the same conditional
    UPDATE this model reuses, and reusing that mechanism rather than
    inventing a stateless variant is what stops a resume link, once clicked,
    from being clickable a second time (Task 32: replay).

    **Why every field is copied rather than re-derived from the query
    string at resume time.** The request was already verified once, by
    `resolve_client` and `parse_request`, against a browser navigation that
    could not yet authenticate. Re-parsing untrusted query parameters a
    second time - after login, when the stakes of getting it wrong are
    higher - would mean the verified request and the resumed request are two
    different objects that merely resemble each other. Storing the verified
    one and replaying it unchanged is what makes "cross-client resume" and
    "wrong organization substitution" (Task 32) structurally unreachable:
    there is no field on this row a browser can influence except
    `organization_id`, and only when the original request left it unset (see
    `authorization.resume_pending`).

    Exempt from tenant scoping (W360.E001, `TENANT_SCOPE_EXEMPT`) for the
    identical reason `AuthorizationCode` is: reachable only through the
    SHA-256 of a 256-bit secret, and the organization it names - when it
    names one at all - has not yet been re-verified against a live
    membership. That re-verification happens at resume, through the same
    `resolve_subject` the authorization endpoint itself uses.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    #: SHA-256 of the resume token. The plaintext travels in a URL the
    #: browser is redirected to and is never itself stored, for the same
    #: reason `AuthorizationCode.code_hash` is not the plaintext code.
    token_hash = models.CharField(max_length=64, unique=True)

    integration = models.ForeignKey(
        ProductIntegration,
        on_delete=models.CASCADE,
        related_name="pending_authorizations",
    )

    redirect_uri = models.CharField(max_length=512)
    state = models.CharField(max_length=512, blank=True, default="")
    scope = models.CharField(max_length=128)
    code_challenge = models.CharField(max_length=128)
    code_challenge_method = models.CharField(max_length=8)
    nonce = models.CharField(max_length=255, blank=True, default="")

    #: NULL when the original request named no organization - the product's
    #: launch URL omitted it, or (rarer) a user reached `/authorize` directly.
    #: Resolved by the organization chooser at resume time. Never overwritten
    #: once set: see `authorization.resume_pending`.
    organization_id = models.UUIDField(null=True, blank=True)

    expires_at = models.DateTimeField()
    #: Set by a conditional UPDATE, exactly as `AuthorizationCode.redeemed_at`
    #: is - single use under concurrency needs no lock, only the WHERE clause.
    consumed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ecosystem_pending_authorization"
        indexes = [
            # The pruning job's query, identical in shape to
            # ix_authcode_expires: these are short-lived and the sweep must
            # not be a full scan of everything ever issued.
            models.Index(fields=["expires_at"], name="ix_pendingauth_expires"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        state = "consumed" if self.consumed_at else "pending"
        return f"pending authorization for {self.integration_id} ({state})"

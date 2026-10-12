"""Signing keys: generation, selection, JWKS publication and rotation.

Three things happen here and they are deliberately separate:

**Generation** produces a key pair and hands the private half to the operator
*once*, on stdout, for them to put into the secret manager. It is never
written to the database, never logged and never returned by an API.

**Selection** answers two questions with two different rules. "Which key signs
this token?" has exactly one answer - the single ``active`` key - and fails
loudly if there is none. "Which keys may verify a token?" has several, because
a token signed thirty seconds before a rotation must still verify after it.

**Publication** renders the public halves as a JWK Set. Products cache it, so
a key appears in the set *before* it signs anything and stays in the set
*after* it stops. Those two overlaps are the entire reason rotation does not
require every product to redeploy.

The rotation sequence, and why each step exists:

    1. generate        new key is `pending` - published, not signing.
                       Products' caches pick it up on their next refresh.
    2. promote         new key becomes `active`; the old becomes `retiring`.
                       New tokens use the new kid; every product already has
                       it. Old tokens still verify.
    3. retire          after the longest token lifetime has elapsed, the old
                       key leaves the set. Nothing it signed is still valid,
                       so nothing breaks.

Compressing steps 1 and 2 into one is the mistake that makes rotation an
outage: the first token signed by an unpublished key fails verification at
every product whose cache has not refreshed yet.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from workeasy360.contracts.ports.signing import (
    SigningKeyUnavailable,
    get_signing_key_provider,
)
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

#: RSA modulus size. 2048 is the floor every JWT library accepts and NIST
#: considers adequate past 2030; 4096 triples signing cost for a key that is
#: rotated on a schedule anyway.
RSA_KEY_SIZE = 2048

#: Algorithms this platform will sign with. An allowlist, checked on every
#: sign *and* every verify.
#:
#: The exclusion that matters is ``none`` and the whole HMAC family. A
#: verifier that accepts an HMAC algorithm can be handed a token signed with
#: the *public* key as the shared secret - the algorithm-confusion attack that
#: has broken more JWT deployments than every other flaw combined. Naming the
#: permitted algorithms explicitly at the verify call, rather than trusting
#: the token's own header, is what closes it.
SUPPORTED_ALGORITHMS = ("RS256", "ES256")


@dataclass(frozen=True, slots=True)
class VerificationKey:
    """A public key a product (or this platform) may verify a signature with."""

    kid: str
    algorithm: str
    public_key_pem: str


# --- generation -----------------------------------------------------------


def generate_key_pair(algorithm: str) -> tuple[str, str]:
    """A fresh key pair as ``(private_pem, public_pem)``.

    The private half is returned to the caller and goes no further. The one
    caller is the rotation management command, which prints it for an
    operator to store in the secret manager.
    """
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise ValueError(
            f"Unsupported signing algorithm {algorithm!r}. "
            f"Supported: {', '.join(SUPPORTED_ALGORITHMS)}."
        )

    private: Any
    if algorithm == "RS256":
        private = rsa.generate_private_key(public_exponent=65537, key_size=RSA_KEY_SIZE)
    else:
        private = ec.generate_private_key(ec.SECP256R1())

    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        # Unencrypted, because the secret manager is the thing providing
        # encryption at rest. A passphrase here would have to be stored
        # somewhere too, which moves the problem rather than solving it.
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")

    public_pem = (
        private.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    return private_pem, public_pem


def new_kid() -> str:
    """A random key identifier.

    Random rather than sequential or date-derived: a predictable ``kid``
    tells an observer how many rotations have happened and roughly when the
    next is due, which is free reconnaissance for no benefit.
    """
    return secrets.token_urlsafe(16)


# --- selection ------------------------------------------------------------


def active_signing_key() -> Any:
    """The one key currently signing, or raise.

    Raising rather than falling back to "the newest key" is the point. A
    fallback would quietly sign with a ``retiring`` key during a botched
    rotation and produce tokens that expire from the key set before they
    expire from their own ``exp``.
    """
    from workeasy360.platform.ecosystem.models import SigningKey, SigningKeyStatus

    key = SigningKey.objects.filter(status=SigningKeyStatus.ACTIVE.value).first()
    if key is None:
        logger.error("ecosystem.signing_key.none_active")
        raise SigningKeyUnavailable(
            "No active signing key. Token issuance is impossible until one is "
            "promoted - run `manage.py w360_signing_key promote --kid <kid>`."
        )
    return key


def verification_keys() -> list[VerificationKey]:
    """Every key whose signatures are still accepted, newest first.

    ``pending`` is included. A key that is published but not yet signing
    cannot have signed anything, so including it costs nothing - and
    excluding it would mean the moment of promotion is a moment when the
    platform cannot verify its own freshly issued token, which the
    conformance suite would then fail intermittently.
    """
    from workeasy360.platform.ecosystem.models import SigningKey, SigningKeyStatus

    rows = SigningKey.objects.exclude(status=SigningKeyStatus.RETIRED.value).values(
        "kid", "algorithm", "public_key_pem"
    )
    return [
        VerificationKey(
            kid=row["kid"],
            algorithm=row["algorithm"],
            public_key_pem=row["public_key_pem"],
        )
        for row in rows
    ]


def verification_key_for(kid: str) -> VerificationKey | None:
    """The key a token names, or None.

    None - not an exception - because an unknown ``kid`` is an ordinary
    untrusted-input case: a forged token, a token from another issuer, or a
    token signed by a key that has finished retiring. All three are "reject
    this token", none is "the platform is broken".
    """
    from workeasy360.platform.ecosystem.models import SigningKey, SigningKeyStatus

    row = (
        SigningKey.objects.filter(kid=kid)
        .exclude(status=SigningKeyStatus.RETIRED.value)
        .values("kid", "algorithm", "public_key_pem")
        .first()
    )
    if row is None:
        return None
    return VerificationKey(
        kid=row["kid"], algorithm=row["algorithm"], public_key_pem=row["public_key_pem"]
    )


def private_key_pem(key: Any) -> str:
    """Resolve a key row's private half through the signing-key port.

    The only path from a ``SigningKey`` row to key material, so there is
    exactly one place the secret manager is consulted and exactly one place
    to audit.
    """
    return get_signing_key_provider().private_key_pem(key.private_key_ref)


# --- publication ----------------------------------------------------------


def jwks() -> dict[str, Any]:
    """The JWK Set, RFC 7517.

    Public keys only, by construction: the private half is not in the
    database, so there is no code path by which one could appear here even
    if this function were wrong. That is the reason for the split - a
    "don't serialise the private field" rule is one refactor from being
    violated; not having the field is not.
    """
    import jwt

    keys: list[dict[str, Any]] = []
    for key in verification_keys():
        algorithm_cls = jwt.algorithms.get_default_algorithms()[key.algorithm]
        public_key = algorithm_cls.prepare_key(key.public_key_pem)
        jwk: dict[str, Any] = dict(algorithm_cls.to_jwk(public_key, as_dict=True))
        jwk["kid"] = key.kid
        jwk["alg"] = key.algorithm
        # "sig" rather than "enc": a product must not be able to use a
        # signing key for encryption, and stating the use is how a
        # conforming JOSE library enforces that.
        jwk["use"] = "sig"
        keys.append(jwk)
    return {"keys": keys}


# --- rotation -------------------------------------------------------------


def create_pending_key(*, algorithm: str, private_key_ref: str) -> tuple[Any, str]:
    """Generate and store a new ``pending`` key. Returns ``(row, private_pem)``.

    The private PEM is returned, not stored. The caller - the management
    command - is responsible for getting it to the secret manager under
    ``private_key_ref``, and the key cannot be promoted until it is there,
    because promotion verifies resolvability first.
    """
    from workeasy360.platform.ecosystem.models import SigningKey, SigningKeyStatus

    private_pem, public_pem = generate_key_pair(algorithm)
    key = SigningKey.objects.create(
        kid=new_kid(),
        algorithm=algorithm,
        public_key_pem=public_pem,
        private_key_ref=private_key_ref,
        status=SigningKeyStatus.PENDING.value,
    )
    logger.info(
        "ecosystem.signing_key.created",
        kid=key.kid,
        algorithm=algorithm,
        status=SigningKeyStatus.PENDING.value,
    )
    return key, private_pem


def promote_key(kid: str) -> Any:
    """Make ``kid`` the active signing key; move the incumbent to ``retiring``.

    Refuses if the private half cannot be resolved. Promoting a key whose
    secret has not reached the secret manager yet would stop token issuance
    dead the moment it took effect - the failure would surface as a platform
    outage minutes after an operation that appeared to succeed.

    One transaction, and the partial unique index on ``status='active'``
    means two concurrent promotions cannot both win: the loser hits a
    constraint violation rather than leaving two active keys.
    """
    from workeasy360.platform.ecosystem.models import SigningKey, SigningKeyStatus

    with transaction.atomic():
        key = SigningKey.objects.select_for_update().filter(kid=kid).first()
        if key is None:
            raise SigningKeyUnavailable(f"No signing key with kid {kid!r}.")
        if key.status_enum is SigningKeyStatus.RETIRED:
            raise SigningKeyUnavailable(
                f"Signing key {kid!r} is retired and cannot be promoted. "
                f"Retirement is terminal - generate a new key."
            )

        # Resolve before switching, never after.
        provider = get_signing_key_provider()
        material = provider.private_key_pem(key.private_key_ref)
        if not material:
            raise SigningKeyUnavailable(
                f"The private key behind reference {key.private_key_ref!r} is "
                f"empty. Refusing to promote a key that cannot sign."
            )

        now = timezone.now()
        incumbent = (
            SigningKey.objects.select_for_update()
            .filter(status=SigningKeyStatus.ACTIVE.value)
            .exclude(kid=kid)
            .first()
        )
        if incumbent is not None:
            incumbent.status = SigningKeyStatus.RETIRING.value
            incumbent.retiring_at = now
            incumbent.save(update_fields=["status", "retiring_at"])

        key.status = SigningKeyStatus.ACTIVE.value
        key.activated_at = now
        key.save(update_fields=["status", "activated_at"])

    logger.info(
        "ecosystem.signing_key.promoted",
        kid=kid,
        previous_kid=incumbent.kid if incumbent is not None else None,
    )
    return key


def retire_key(kid: str) -> Any:
    """Remove a key from the JWKS. Terminal.

    The active key cannot be retired: doing so would leave the platform with
    no way to sign, and the caller almost certainly meant to promote a
    replacement first.
    """
    from workeasy360.platform.ecosystem.models import SigningKey, SigningKeyStatus

    with transaction.atomic():
        key = SigningKey.objects.select_for_update().filter(kid=kid).first()
        if key is None:
            raise SigningKeyUnavailable(f"No signing key with kid {kid!r}.")
        if key.status_enum is SigningKeyStatus.ACTIVE:
            raise SigningKeyUnavailable(
                f"Signing key {kid!r} is the active signing key. Promote a "
                f"replacement first, then retire this one."
            )
        key.status = SigningKeyStatus.RETIRED.value
        key.retired_at = timezone.now()
        key.save(update_fields=["status", "retired_at"])

    logger.info("ecosystem.signing_key.retired", kid=kid)
    return key


def safe_retirement_after() -> int:
    """Seconds a retiring key must stay published before it may be retired.

    The longest-lived thing it could have signed, plus the accepted clock
    skew. Retiring sooner invalidates tokens that have not expired, which a
    product experiences as random authentication failures with no pattern.
    """
    return max(
        int(settings.OIDC_ID_TOKEN_TTL_S),
        int(settings.OIDC_ACCESS_TOKEN_TTL_S),
    ) + int(settings.OIDC_CLOCK_SKEW_S)


# --- the provider ---------------------------------------------------------


class EnvSigningKeyProvider:
    """Resolves a key reference from the process environment.

    The default implementation, and the one the local stack and CI use. In a
    deployed environment the same variables are populated by the secret
    manager, which is precisely the pattern ``config/env.py`` already
    describes for every other secret: the application reads the environment,
    and what fills the environment is the deployment's business.

    Swapping this for a KMS or Vault client is a registration change in
    ``apps.py`` - no caller moves, because no caller names this class
    (contracts/ports/signing.py).
    """

    def __init__(self, prefix: str | None = None) -> None:
        self._prefix = (
            prefix
            if prefix is not None
            else getattr(
                settings, "OIDC_SIGNING_KEY_ENV_PREFIX", "W360_OIDC_SIGNING_KEY_"
            )
        )

    def variable_name(self, reference: str) -> str:
        """``k1`` -> ``W360_OIDC_SIGNING_KEY_K1``.

        Uppercased and underscored so a reference is a readable, typeable
        name in a secret manager rather than an opaque id.
        """
        safe = "".join(c if c.isalnum() else "_" for c in reference).upper()
        return f"{self._prefix}{safe}"

    def private_key_pem(self, reference: str) -> str:
        import os

        name = self.variable_name(reference)
        # Read live rather than at construction: an operator adding a key to
        # the secret manager must not need a restart for rotation to proceed
        # (docs/phase-3/08-key-management.md section 5).
        value = os.environ.get(name, "")
        if not value:
            # The *variable name* is safe to log and is the one thing an
            # operator needs. The value never is.
            logger.error("ecosystem.signing_key.unresolved", variable=name)
            raise SigningKeyUnavailable(
                f"No private key material at {name}. The public half is "
                f"registered but the secret is missing from this "
                f"environment's secret store."
            )
        # PEM in an environment variable arrives with literal \n from most
        # secret managers' CLI tooling. Accepting both spellings removes a
        # class of "it works locally" rotation failures.
        return value.replace("\\n", "\n")

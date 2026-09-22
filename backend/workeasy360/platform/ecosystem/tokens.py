"""Minting and verifying the signed assertions products consume.

Two tokens are issued, and they are not interchangeable:

**The ID token** is the identity assertion. It answers "who is this, in which
organization, for which product" and it is the thing a product verifies
offline and turns into its own session. It is short-lived because its lifetime
*is* the revocation window (docs/phase-3/09-product-integration-guide.md s6).

**The access token** is a credential for this platform's ``/userinfo``
endpoint and nothing else. It exists because RFC 6749 section 5.1 requires the
token response to carry one, and rather than emit a decorative value that no
endpoint accepts, it is given the one job that is genuinely its own: fetching
the profile claims a product's granted scopes permit, without re-running the
authorization flow.

There are **no refresh tokens**. A refresh token is a long-lived credential
whose entire purpose is to extend access past the point where the platform
last checked anything, which is the opposite of what a bounded revocation
window means. A product that needs a longer session issues its own; a product
that needs to know the user is still valid re-runs the flow or asks the
entitlement endpoint (docs/phase-3/05-token-claims.md section 6).

**Claim minimalism.** Every claim below had to justify itself against "could
the product do its job without this?". ``membership_id`` did not - a product
keys its own data on ``(sub, organization_id)`` and has no use for the
platform's join row - so it is not issued. ``email`` and ``name`` are behind
scopes the integration must be registered for, because a product that only
needs to know *which* user should not be handed *who* the user is.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import jwt
from django.conf import settings

from workeasy360.platform.ecosystem import keys
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

#: Token classes, carried in the ``typ`` claim (not the JOSE header).
#:
#: An access token presented where an ID token is expected must be rejected
#: even though both verify against the same key with the same audience. OAuth
#: deployments that omitted this have had access tokens accepted as identity
#: assertions, which grants whatever the identity grants. One claim closes it.
TYPE_ID_TOKEN = "id_token"  # noqa: S105 - a claim value, not a credential
TYPE_ACCESS_TOKEN = "access_token"  # noqa: S105 - a claim value, not a credential

#: Claims every issued token carries, asserted by a test so the documented
#: contract in docs/phase-3/05-token-claims.md cannot drift from the code.
BASE_CLAIMS = ("iss", "sub", "aud", "iat", "nbf", "exp", "jti", "typ")

#: Claims required to be *present* at verification. Listing them makes a
#: token missing ``exp`` a rejection rather than a token that never expires -
#: PyJWT does not require a claim it is not told to require.
REQUIRED_ON_VERIFY = ("iss", "sub", "aud", "exp", "iat", "jti", "typ")


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """A signed token and the metadata the caller needs about it."""

    value: str
    jti: str
    expires_in: int


@dataclass(frozen=True, slots=True)
class TokenSubject:
    """Everything an issued token asserts. Assembled once, used by both mints.

    A single struct rather than a long parameter list, because the fields are
    three UUIDs and two strings: adjacent same-typed positional parameters are
    a swap waiting to happen, and swapping ``user_id`` with ``organization_id``
    here would be a cross-tenant defect delivered under signature.
    """

    user_id: UUID
    organization_id: UUID
    product_code: str
    client_id: str
    scope: str
    aal: int
    nonce: str = ""


def issuer() -> str:
    """The ``iss`` value.

    From settings, never from the request. Deriving the issuer from the
    ``Host`` header would let anyone who can reach the platform with a forged
    Host mint tokens claiming to come from a different issuer - and TB-1 says
    client headers are never truthful.
    """
    return str(settings.OIDC_ISSUER).rstrip("/")


def issue_id_token(
    subject: TokenSubject, *, profile: dict[str, Any] | None = None
) -> IssuedToken:
    """The identity assertion the product verifies and trusts.

    ``profile`` carries the scope-gated personal claims. The caller resolves
    them, because deciding *whether* a scope was granted is an authorization
    question and this module signs rather than decides.
    """
    ttl = int(settings.OIDC_ID_TOKEN_TTL_S)
    claims = _base_claims(subject, ttl=ttl, token_type=TYPE_ID_TOKEN)

    # The ecosystem's own claims. Three, each documented in
    # docs/phase-3/05-token-claims.md and each one a product cannot derive.
    claims["organization_id"] = str(subject.organization_id)
    claims["product_code"] = subject.product_code
    claims["aal"] = subject.aal

    if subject.nonce:
        # OIDC Core 3.1.3.7 step 11. Echoed verbatim so the client can prove
        # this token answers *its* request and is not one replayed from
        # another session.
        claims["nonce"] = subject.nonce

    if profile:
        claims.update(profile)

    return _sign(claims, ttl=ttl)


def issue_access_token(subject: TokenSubject) -> IssuedToken:
    """The credential for ``/userinfo``. Deliberately carries no org claim.

    Leaving ``organization_id`` out is not an oversight. The access token
    addresses a *user profile* resource, which is global; putting a tenant
    claim on it would suggest it conveys tenant access, and a product reading
    the org from the wrong token is exactly the confusion the ``typ`` claim
    exists to prevent.
    """
    ttl = int(settings.OIDC_ACCESS_TOKEN_TTL_S)
    claims = _base_claims(subject, ttl=ttl, token_type=TYPE_ACCESS_TOKEN)
    claims["scope"] = subject.scope
    return _sign(claims, ttl=ttl)


def verify(
    token: str, *, audience: str, expected_type: str, leeway: int | None = None
) -> dict[str, Any]:
    """Verify a token this platform issued, returning its claims.

    Used by ``/userinfo`` and by the conformance tests, and written to be the
    reference a product's own verification is checked against
    (docs/phase-3/10-product-certification-checklist.md).

    The order is the security-relevant part:

    1. Read the header **without verifying** to learn the ``kid``. This is
       untrusted data and is used for exactly one thing: selecting a
       candidate key. Nothing else in the header is believed.
    2. Look the ``kid`` up among non-retired keys. Unknown -> reject.
    3. Verify with the algorithm **from the key record**, not from the token.
       Trusting the token's ``alg`` is the algorithm-confusion attack.
    4. Verify issuer, audience, expiry and required claims.
    5. Verify ``typ`` last, because it is ours rather than the library's.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise jwt.InvalidTokenError(
            f"Unreadable token header: {type(exc).__name__}"
        ) from None

    kid = header.get("kid")
    if not kid:
        raise jwt.InvalidTokenError("Token header carries no kid.")

    key = keys.verification_key_for(str(kid))
    if key is None:
        raise jwt.InvalidTokenError("Token names an unknown or retired signing key.")

    claims = jwt.decode(
        token,
        key.public_key_pem,
        # A list of exactly one, taken from our own record. PyJWT will refuse
        # any token whose header says something else, which is what makes
        # "none" and HMAC confusion unreachable.
        algorithms=[key.algorithm],
        audience=audience,
        issuer=issuer(),
        leeway=leeway if leeway is not None else int(settings.OIDC_CLOCK_SKEW_S),
        options={
            "require": list(REQUIRED_ON_VERIFY),
            "verify_signature": True,
            "verify_exp": True,
            "verify_nbf": True,
            "verify_iat": True,
            "verify_aud": True,
            "verify_iss": True,
        },
    )

    if claims.get("typ") != expected_type:
        raise jwt.InvalidTokenError(
            f"Token is a {claims.get('typ')!r}, not a {expected_type!r}."
        )
    return claims


# --- internals ------------------------------------------------------------


def _base_claims(subject: TokenSubject, *, ttl: int, token_type: str) -> dict[str, Any]:
    # One clock reading for all three time claims. Calling time.time() three
    # times can straddle a second boundary and produce iat > nbf, which some
    # strict verifiers reject.
    now = int(time.time())
    return {
        "iss": issuer(),
        # The stable, opaque, platform-minted user id. Reused rather than
        # re-invented: identity.User.id is already a UUID, already stable for
        # the life of the account and already meaningless to an observer, so
        # a second external identifier would be one more thing to keep in
        # step for no property gained (docs/phase-3/05-token-claims.md s3).
        "sub": str(subject.user_id),
        # The client id, not the product code. A token minted for CRM names
        # CRM's client; Books verifies against its own and rejects it.
        "aud": subject.client_id,
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
        # Unique per token, so a product can detect and refuse a replay
        # within the token's lifetime.
        "jti": uuid.uuid4().hex,
        "typ": token_type,
    }


def _sign(claims: dict[str, Any], *, ttl: int) -> IssuedToken:
    key = keys.active_signing_key()
    private_pem = keys.private_key_pem(key)

    token = jwt.encode(
        claims,
        private_pem,
        algorithm=key.algorithm,
        # The only header addition. Products select the verifying key by it,
        # which is what lets a key rotate without a product redeploying.
        headers={"kid": key.kid},
    )
    # The token itself is never logged - it is a bearer credential. The jti
    # is, because correlating "this token was issued" with "this token was
    # used" is the whole point of having one.
    logger.info(
        "ecosystem.token.issued",
        token_type=claims["typ"],
        jti=claims["jti"],
        kid=key.kid,
        expires_in=ttl,
    )
    return IssuedToken(value=token, jti=str(claims["jti"]), expires_in=ttl)

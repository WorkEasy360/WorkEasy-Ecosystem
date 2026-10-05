"""How a product proves it is itself, on every server-to-server call.

Two mechanisms, one lifecycle, one failure response.

**private_key_jwt** (RFC 7523, the default and what the guide recommends).
The product signs a short-lived assertion with a private key it never sends;
the platform holds only the public half. Nothing secret crosses the network,
nothing secret is in this platform's database, and a compromise of the
platform's storage yields no credential an attacker can use as the product.

**client_secret_basic** (RFC 6749 section 2.3.1). A 256-bit random secret in
an HTTP Basic header, stored as a SHA-256 digest. Offered because "our
product cannot manage a key pair" is a real constraint, and refusing it would
push those products toward something worse - a static token in a query string.

**Why SHA-256 and not Argon2.** The rest of this platform hashes passwords
with Argon2id, and that is correct *there*: a human-chosen password has maybe
40 bits of entropy, so the work factor is what stands between a stolen hash
and the plaintext. These secrets are 256 bits from ``secrets.token_urlsafe``.
There is nothing to guess; a work factor buys no security whatsoever, while
costing ~100 ms on the token endpoint and the entitlement endpoint - handing
any authenticated client a CPU-exhaustion lever against the two endpoints
that must stay up. Fast hashing of a full-entropy secret is the documented
correct choice (docs/phase-3/07-service-authentication.md section 4).

**One failure response, always.** Unknown client id, wrong secret, bad
signature, revoked credential, expired credential, wrong assertion audience -
every one returns the identical ``invalid_client``. Distinguishing them turns
this endpoint into a client-enumeration oracle and tells an attacker when they
have found a real client id, which is most of the work.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Any

import jwt
from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest
from django.utils import timezone

from workeasy360.platform.ecosystem import protocol, ratelimit
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

#: Bytes of entropy in a generated client secret. 32 bytes = 256 bits, which
#: is beyond brute force by any margin that will ever matter.
SECRET_BYTES = 32

#: How much of a secret is stored in the clear as a lookup handle. Eight
#: url-safe characters is 48 bits - far too little to help an attacker who
#: has it, and enough to make the lookup a single indexed row rather than a
#: hash of every credential the client holds.
SECRET_PREFIX_LENGTH = 8

#: Assertion ``jti`` values already spent, so a captured assertion cannot be
#: replayed inside its own validity window. Held in the cache rather than a
#: table: the entries live for the assertion's maximum age (minutes), and a
#: database row per token exchange would be a write on the hot path to store
#: something that is garbage almost immediately.
#:
#: A cache outage therefore loses replay protection for the window. That is
#: the deliberate trade: the assertion is still signature-verified,
#: audience-bound and expiry-bound, so what is lost is one layer of a
#: defence in depth, not the defence (docs/phase-3/14-failure-mode-review.md).
_REPLAY_KEY_PREFIX = "w360:eco:assertion:"


@dataclass(frozen=True, slots=True)
class AuthenticatedClient:
    """The result of client authentication: which integration is calling.

    The whole security value of this type is that ``product_code`` comes from
    *here* and never from the request. A CRM client asking about Books cannot
    express the question, because the question's subject is determined by
    which credential signed it (docs/phase-3/06-entitlement-api.md section 4).
    """

    integration: Any
    credential: Any

    @property
    def client_id(self) -> str:
        return str(self.integration.client_id)

    @property
    def product_code(self) -> str:
        return str(self.integration.product_code)

    def declares_event(self, event_type: str, version: int) -> bool:
        """Whether this product registered ``event_type`` at ``version`` in
        its manifest (Phase 6). Read from the authenticated integration's own
        registration - the declaration, like the product code, comes from
        the credential and never from the request."""
        return self.integration.manifest_obj.declares_event(event_type, version)


def authenticate(request: HttpRequest, *, endpoint: str) -> AuthenticatedClient:
    """Authenticate the calling product, or raise ``invalid_client``.

    ``endpoint`` names the rate-limit bucket. Failures are counted against a
    much tighter budget than successes, because a caller failing
    authentication ten times a minute is not a caller with a configuration
    problem.
    """
    client_id = _declared_client_id(request)
    try:
        client = _authenticate(request, client_id)
    except protocol.OAuthError:
        # Count the failure, then re-raise unchanged. The limiter's own
        # rejection is a 429 and is raised from inside ``consume``.
        ratelimit.consume(
            "client_auth_failure", client_id or _client_ip(request), endpoint=endpoint
        )
        metrics.increment("ecosystem_client_auth_failures_total", endpoint=endpoint)
        raise

    # Suspension stops the product *operating*, not only signing users in.
    #
    # Checked here rather than per endpoint, because this is the one place
    # that knows which integration is calling - and the usual reason to
    # suspend an integration is that it is compromised. A suspended client
    # that could still redeem an in-flight authorization code, or read every
    # organization's entitlement state, would make suspension a half-measure
    # exactly when it is being used in earnest.
    #
    # Deliberately **not** collapsed into ``invalid_client``. The uniform
    # error exists so that an attacker cannot learn whether a client id or a
    # secret was right; this caller has already proved they hold a valid
    # credential, so telling them the integration is suspended discloses
    # nothing they could not determine anyway and is the one thing that lets
    # their operator fix it.
    if not client.integration.status_enum.can_authenticate:
        logger.warning(
            "ecosystem.client_auth.integration_not_active",
            product_code=client.product_code,
            status=client.integration.status,
            endpoint=endpoint,
        )
        metrics.increment("ecosystem_client_auth_failures_total", endpoint=endpoint)
        raise protocol.OAuthError(
            protocol.INTEGRATION_SUSPENDED,
            "This integration is not active.",
            http_status=403,
        )

    _touch(client.credential)
    metrics.increment("ecosystem_client_auth_total", endpoint=endpoint)
    return client


def _authenticate(request: HttpRequest, client_id: str) -> AuthenticatedClient:
    assertion = request.POST.get("client_assertion", "")
    assertion_type = request.POST.get("client_assertion_type", "")

    if assertion or assertion_type:
        if assertion_type != protocol.CLIENT_ASSERTION_TYPE_JWT_BEARER:
            raise protocol.invalid_client()
        return _authenticate_private_key_jwt(assertion)

    secret = _presented_secret(request)
    if client_id and secret:
        return _authenticate_client_secret(client_id, secret)

    raise protocol.invalid_client("Client authentication is required.")


# --- private_key_jwt ------------------------------------------------------


def _authenticate_private_key_jwt(assertion: str) -> AuthenticatedClient:
    """RFC 7523 section 3, with the token endpoint as the audience.

    The ordering mirrors ``tokens.verify`` and for the same reasons: the
    header is read unverified to select a key, the algorithm comes from our
    record rather than from the assertion, and every check that can fail
    produces the same error.
    """
    from workeasy360.platform.ecosystem.models import (
        CredentialKind,
        IntegrationCredential,
    )

    try:
        header = jwt.get_unverified_header(assertion)
        # A peek to find the credential by iss/kid; the assertion is then
        # verified with that credential's key and algorithm (below).
        unverified = jwt.decode(
            assertion,
            options={"verify_signature": False},  # nosemgrep
        )
    except jwt.PyJWTError:
        raise protocol.invalid_client() from None

    kid = str(header.get("kid") or "")
    issuer_claim = str(unverified.get("iss") or "")
    subject_claim = str(unverified.get("sub") or "")

    # RFC 7523 section 3: for client authentication both `iss` and `sub` are
    # the client id. Requiring them to agree costs nothing and refuses an
    # assertion that names one client and is signed as another.
    if not kid or not issuer_claim or issuer_claim != subject_claim:
        raise protocol.invalid_client()

    credential = (
        IntegrationCredential.objects.select_related("integration")
        .filter(
            integration__client_id=issuer_claim,
            kind=CredentialKind.PUBLIC_KEY.value,
            credential_id=kid,
        )
        .first()
    )
    if credential is None or not credential.is_usable:
        # One branch, one error - "no such client" and "revoked credential"
        # are indistinguishable to the caller by design.
        raise protocol.invalid_client()

    try:
        claims = jwt.decode(
            assertion,
            credential.public_key_pem,
            algorithms=[credential.algorithm],
            # The assertion must name *this* endpoint. Without it, an
            # assertion the product legitimately created for some other
            # service could be replayed here by whoever received it.
            audience=_assertion_audience(),
            issuer=issuer_claim,
            leeway=int(settings.OIDC_CLOCK_SKEW_S),
            # ``iat`` is required, not optional. Without it the maximum-age
            # check below has nothing to measure against and silently does
            # not run - which is how a documented 300-second bound becomes a
            # bound that applies only to assertions that volunteered to be
            # bounded.
            options={"require": ["iss", "sub", "aud", "exp", "iat", "jti"]},
        )
    except jwt.PyJWTError:
        raise protocol.invalid_client() from None

    _reject_replayed_assertion(claims)
    return AuthenticatedClient(integration=credential.integration, credential=credential)


def _assertion_audience() -> str:
    """What a client assertion must name as its audience.

    The token endpoint's URL, per RFC 7523 section 3. Derived from the
    configured issuer so it cannot be influenced by a request header.
    """
    from workeasy360.platform.ecosystem import discovery

    return discovery.endpoint("token")


def _reject_replayed_assertion(claims: dict[str, Any]) -> None:
    """Refuse a ``jti`` already seen inside its validity window.

    Bounded by the assertion's own maximum age, so the cache entry cannot
    outlive the credential it protects against.

    **Both ends of the window are bounded**, and the second one is the
    reason this is not merely an age check. The ``jti`` is remembered for
    ``max_age`` and no longer, because a cache that remembered every
    assertion forever would grow without limit. An assertion whose ``exp``
    reaches further than that outlives the memory of having been used - so a
    captured one becomes replayable again the moment the entry expires. A
    product is free to set a long ``exp``; the platform is not obliged to
    honour it, and refusing to is what makes "spent on first use" true for
    the whole life of every assertion it accepts.
    """
    jti = str(claims.get("jti") or "")
    max_age = int(settings.OIDC_CLIENT_ASSERTION_MAX_AGE_S)
    now = time.time()

    issued_at = claims.get("iat")
    if issued_at is None or now - float(issued_at) > max_age:
        raise protocol.invalid_client()

    expires_at = claims.get("exp")
    if expires_at is None or float(expires_at) - now > max_age:
        raise protocol.invalid_client()

    key = f"{_REPLAY_KEY_PREFIX}{hashlib.sha256(jti.encode()).hexdigest()}"
    try:
        # add() is atomic and returns False when the key already exists,
        # which makes "first use wins" a property of the cache rather than a
        # read-then-write race.
        first_use = cache.add(key, "1", max_age)
    except Exception:  # pragma: no cover - cache backend down
        logger.warning("ecosystem.assertion.replay_cache_unavailable")
        return
    if not first_use:
        logger.warning("ecosystem.assertion.replayed")
        raise protocol.invalid_client()


# --- client_secret_basic --------------------------------------------------


def _authenticate_client_secret(client_id: str, secret: str) -> AuthenticatedClient:
    from workeasy360.platform.ecosystem.models import (
        CredentialKind,
        IntegrationCredential,
    )

    candidate = (
        IntegrationCredential.objects.select_related("integration")
        .filter(
            integration__client_id=client_id,
            kind=CredentialKind.CLIENT_SECRET.value,
            credential_id=secret[:SECRET_PREFIX_LENGTH],
        )
        .first()
    )
    if candidate is None:
        # Hash anyway before failing. Returning early on a missing row makes
        # the "no such client" path measurably faster than the "wrong secret"
        # path, and that timing difference is the enumeration oracle this
        # module's docstring says must not exist.
        hash_secret(secret)
        raise protocol.invalid_client()

    if not hmac.compare_digest(hash_secret(secret), candidate.secret_hash):
        raise protocol.invalid_client()
    if not candidate.is_usable:
        raise protocol.invalid_client()

    return AuthenticatedClient(integration=candidate.integration, credential=candidate)


def generate_secret() -> tuple[str, str, str]:
    """A new client secret as ``(secret, prefix, digest)``.

    The plaintext is returned exactly once, to the service that will show it
    to the operator, and is never stored. A credential API that can re-read a
    secret is a credential API that leaks every secret the day it is
    compromised.
    """
    secret = secrets.token_urlsafe(SECRET_BYTES)
    return secret, secret[:SECRET_PREFIX_LENGTH], hash_secret(secret)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


# --- request parsing ------------------------------------------------------


def _declared_client_id(request: HttpRequest) -> str:
    """The client id the caller claims, for rate limiting only.

    Explicitly *not* trusted for authentication - it is read before anything
    is verified. It exists so that a brute-force run against one client's
    secret is throttled against that client rather than against every caller
    sharing an IP.
    """
    basic = _basic_credentials(request)
    if basic is not None:
        return basic[0]
    return str(request.POST.get("client_id", ""))[:64]


def _presented_secret(request: HttpRequest) -> str:
    basic = _basic_credentials(request)
    if basic is not None:
        return basic[1]
    # client_secret_post. Accepted because some HTTP clients cannot set an
    # Authorization header, and refusing it would push those products to put
    # the secret somewhere worse.
    return str(request.POST.get("client_secret", ""))


def _basic_credentials(request: HttpRequest) -> tuple[str, str] | None:
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if not header.startswith("Basic "):
        return None
    try:
        raw = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    if ":" not in raw:
        return None
    client_id, _, secret = raw.partition(":")
    # RFC 6749 section 2.3.1 requires form-urlencoding of both halves before
    # Basic encoding. Unquoting is what makes a secret containing ':' or '%'
    # round-trip, and not doing it is a bug that only appears for some
    # generated secrets.
    from urllib.parse import unquote

    return unquote(client_id), unquote(secret)


def _client_ip(request: HttpRequest) -> str:
    """The caller's address, as far as the deployment can vouch for it.

    Resolved through ``platform.observability.client_ip`` rather than read
    straight off ``REMOTE_ADDR``: behind a proxy that is the proxy's address,
    and every caller on earth would share one bucket (D-009).
    """
    from workeasy360.platform.observability.client_ip import (
        client_ip,
        rate_limit_identity,
    )

    # Used only as a rate-limit key; IPv6 bucketed by /64 (Phase 12, G-12).
    return rate_limit_identity(client_ip(request))


def _touch(credential: Any) -> None:
    """Record that a credential was used, at most once per day.

    Day granularity, deliberately. The question this answers is "is anything
    still using this credential?" before revoking it, and a per-request
    timestamp would turn every token exchange into a write for an answer
    nobody needs to the second.
    """
    today = timezone.now().date()
    if credential.last_used_on == today:
        return
    type(credential).objects.filter(pk=credential.pk).update(last_used_on=today)

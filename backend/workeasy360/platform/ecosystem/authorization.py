"""The authorization code flow: issue, then redeem exactly once.

This module is where the platform decides whether a person may enter a
product, and it is the only place that decision is made. The order is fixed
and every step denies:

    1. The client is registered and ``active``          -> invalid_client
    2. The redirect URI is *exactly* one it registered  -> invalid_request
    3. PKCE is present and uses S256                    -> invalid_request
    4. A user is authenticated                          -> login_required
    5. An organization was named                        -> organization_required
    6. That user has an *active* membership in it       -> membership_inactive
    7. The organization's status permits login          -> organization_inactive
    8. The organization is entitled to the product      -> product_not_entitled
    9. Only then: mint a code

Steps 1 and 2 come first because until both hold, **the platform does not
know where the browser is allowed to go**, and every later error must be
rendered rather than redirected (RFC 6749 section 4.1.2.1). Getting that
backwards is the open redirect.

Step 6 is the answer to "a product must never choose an arbitrary
organization and receive access merely because the user exists". The product
supplies an organization - it has to, since it learns it from the launch URL
and cannot enumerate the user's memberships - and the platform *verifies* it
against a real membership row. The claim is a hint; the membership is the
authority.

**Why the code is bound to so much.** A code is a bearer credential that
travels in a URL, through a browser, into history and possibly into a
referrer header. Everything bound into it at issue - client, redirect URI,
PKCE challenge, organization, expiry, single use - narrows the set of
circumstances in which a stolen one is worth anything. Taken together they
reduce it to "the attacker is the browser that started the flow, within sixty
seconds, and got there first".

**And why binding is not enough on its own.** Steps 6, 7 and 8 answer
questions about *live state*, and a code outlives the moment they were asked.
A membership revoked, an organization locked or a subscription cancelled in
the sixty seconds between the redirect and the exchange would otherwise still
produce a freshly minted five-minute identity token, because the code carries
the old answer and nothing re-asked. Redemption therefore re-runs exactly
those three checks, through exactly those two functions - see
``_revalidate_subject``. Without it the documented revocation window would
silently be the token lifetime *plus the code lifetime*, and an operator who
had just removed someone would be told the removal was effective when it was
not.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from workeasy360.contracts.enums import OrganizationStatus
from workeasy360.contracts.ports.entitlements import get_gate
from workeasy360.contracts.values.manifest import REQUIRED_SCOPE
from workeasy360.platform.ecosystem import protocol
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

#: Bytes of entropy in an authorization code. 32 bytes = 256 bits, so
#: guessing one inside its 60-second life is not a threat that needs
#: modelling.
CODE_BYTES = 32

#: RFC 7636 section 4.1. A verifier outside these bounds is malformed, and
#: accepting a short one would let a client weaken its own protection.
MIN_VERIFIER_LENGTH = 43
MAX_VERIFIER_LENGTH = 128

#: RFC 7636 section 4.2: the challenge is 43-128 characters drawn from the
#: unreserved set. Enforced at the *authorization* endpoint rather than
#: discovered at redemption, because a challenge the platform stores but can
#: never match is an authorization that is guaranteed to fail sixty seconds
#: later, at the product, with no way for its developer to see why.
_CODE_CHALLENGE_RE = re.compile(
    rf"^[A-Za-z0-9\-._~]{{{MIN_VERIFIER_LENGTH},{MAX_VERIFIER_LENGTH}}}$"
)

#: The widest ``nonce`` that survives a round trip. It is the column width,
#: and it is a *limit* rather than a truncation point: OIDC Core section
#: 3.1.3.7 step 11 has the client compare the echoed value with the one it
#: sent, so a silently shortened nonce is a token the client must reject -
#: an unexplained login failure the platform caused and does not report.
MAX_NONCE_LENGTH = 255


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    """A parsed, not-yet-authorized request from the authorization endpoint."""

    integration: Any
    redirect_uri: str
    state: str
    scope: tuple[str, ...]
    code_challenge: str
    code_challenge_method: str
    nonce: str
    organization_id: UUID | None


@dataclass(frozen=True, slots=True)
class GrantedCode:
    """A minted authorization code and the state needed to return it."""

    code: str
    state: str
    redirect_uri: str
    expires_in: int


@dataclass(frozen=True, slots=True)
class ClientResolution:
    """A verified client and a verified place to send the browser.

    The existence of this type *is* the RFC 6749 section 4.1.2.1 rule. Until
    the client is known and the redirect URI has matched the registered set
    exactly, the server does not know where the user agent is allowed to go,
    so nothing may be delivered by redirection. Once it holds, everything may
    be - which is why the two phases are separate functions returning
    separate types rather than one function with a comment in the middle.
    """

    integration: Any
    redirect_uri: str
    state: str


# --- phase 1: who is asking, and where may the answer go? -----------------


def resolve_client(params: Any) -> ClientResolution:
    """Verify the client and the redirect URI. Errors here are **rendered**.

    Every failure raised by this function is non-redirectable, because in
    each case the destination is exactly what could not be established.
    Getting this backwards is the open redirect.
    """
    from workeasy360.platform.ecosystem.models import ProductIntegration

    client_id = str(params.get("client_id") or "")
    if not client_id:
        raise protocol.OAuthError(protocol.INVALID_REQUEST, "client_id is required.")

    integration = ProductIntegration.objects.filter(client_id=client_id).first()
    if integration is None:
        # Same error and same shape as any other client-authentication
        # failure, so the endpoint cannot be used to discover which client
        # ids exist.
        raise protocol.invalid_client("Unknown or unusable client.")

    redirect_uri = str(params.get("redirect_uri") or "")
    if not redirect_uri:
        raise protocol.OAuthError(protocol.INVALID_REQUEST, "redirect_uri is required.")

    # THE check. Exact string equality against the registered set - see
    # ProductManifest.permits_redirect for why nothing looser is acceptable.
    if not integration.permits_redirect(redirect_uri):
        metrics.increment("ecosystem_invalid_redirect_total")
        logger.warning(
            "ecosystem.authorize.redirect_not_registered",
            client_id=client_id,
            product_code=integration.product_code,
        )
        raise protocol.OAuthError(
            protocol.INVALID_REQUEST,
            "redirect_uri is not registered for this client.",
        )

    return ClientResolution(
        integration=integration,
        redirect_uri=redirect_uri,
        # Captured here so that an error raised in phase 2 can still echo it.
        # A client that cannot correlate an error with the request it made
        # will surface it against the wrong user's session.
        state=str(params.get("state") or ""),
    )


# --- phase 2: is the request itself well formed? --------------------------


def parse_request(resolution: ClientResolution, params: Any) -> AuthorizationRequest:
    """Validate the rest of the request. Errors here are **redirected**.

    The client is known and the destination is verified, so a failure belongs
    at the product, in the protocol's own shape, where its client library can
    render the right call to action.
    """
    integration = resolution.integration

    response_type = str(params.get("response_type") or "")
    if response_type != protocol.RESPONSE_TYPE_CODE:
        raise protocol.OAuthError(
            protocol.INVALID_REQUEST,
            "Only the authorization code response type is supported.",
        )

    if not integration.status_enum.can_authenticate:
        raise protocol.OAuthError(
            protocol.INTEGRATION_SUSPENDED,
            "This integration is not accepting authentication.",
        )

    code_challenge = str(params.get("code_challenge") or "")
    method = str(params.get("code_challenge_method") or "")
    if not code_challenge:
        raise protocol.OAuthError(
            protocol.INVALID_REQUEST,
            "PKCE is required: code_challenge is missing.",
        )
    if method != protocol.PKCE_METHOD_S256:
        # `plain` is refused outright rather than defaulted to. RFC 7636
        # makes `plain` the default when the parameter is absent, which
        # means a silent default here would let a client downgrade its own
        # protection by omitting a field.
        raise protocol.OAuthError(
            protocol.INVALID_REQUEST,
            "code_challenge_method must be S256.",
        )
    if not _CODE_CHALLENGE_RE.match(code_challenge):
        # Shape-checked here, where the client can still act on the answer.
        # A malformed challenge is not a security hole - it can never match a
        # verifier, so the flow fails closed either way - but failing closed
        # at the *token* endpoint means the product sees `invalid_grant` for
        # a request it got wrong a minute earlier, which is an afternoon of
        # somebody's life.
        raise protocol.OAuthError(
            protocol.INVALID_REQUEST,
            "code_challenge must be 43-128 unreserved characters (RFC 7636).",
        )

    nonce = str(params.get("nonce") or "")
    if len(nonce) > MAX_NONCE_LENGTH:
        # Refused, never truncated. See MAX_NONCE_LENGTH.
        raise protocol.OAuthError(
            protocol.INVALID_REQUEST,
            f"nonce may be at most {MAX_NONCE_LENGTH} characters.",
        )

    scope = _granted_scope(integration, str(params.get("scope") or REQUIRED_SCOPE))

    organization = str(params.get("organization") or "")
    organization_id: UUID | None = None
    if organization:
        try:
            organization_id = UUID(organization)
        except ValueError:
            raise protocol.OAuthError(
                protocol.INVALID_REQUEST, "organization is not a valid identifier."
            ) from None

    return AuthorizationRequest(
        integration=integration,
        redirect_uri=resolution.redirect_uri,
        state=resolution.state,
        scope=scope,
        code_challenge=code_challenge,
        code_challenge_method=method,
        nonce=nonce,
        organization_id=organization_id,
    )


def _granted_scope(integration: Any, requested: str) -> tuple[str, ...]:
    """Intersect what was requested with what the integration may have.

    An unregistered scope is an error rather than a silent drop. Silently
    narrowing means a product believes it asked for ``email``, receives a
    token without one, and debugs its own parsing for an afternoon.
    """
    allowed = set(integration.manifest_obj.scopes)
    asked = [s for s in requested.split(" ") if s]

    if REQUIRED_SCOPE not in asked:
        raise protocol.OAuthError(
            protocol.INVALID_SCOPE, f"The {REQUIRED_SCOPE} scope is required."
        )
    unregistered = [s for s in asked if s not in allowed]
    if unregistered:
        raise protocol.OAuthError(
            protocol.INVALID_SCOPE,
            "One or more requested scopes are not registered for this client.",
        )
    # Deduplicate while keeping the manifest's declared order, so the granted
    # scope string is stable for a given request regardless of how the client
    # spelled it.
    return tuple(s for s in integration.manifest_obj.scopes if s in set(asked))


# --- steps 4-8: the person, the tenant and the entitlement ----------------


@dataclass(frozen=True, slots=True)
class ResolvedSubject:
    """A verified (user, organization, membership) triple. Never client-supplied."""

    user_id: UUID
    organization_id: UUID
    membership_id: UUID
    organization_status: OrganizationStatus
    aal: int

    def to_context(self) -> Any:
        """The verified tenant context this authorization acts in.

        Real, not synthetic: every field came from a membership row this
        module just read and checked. It is what lets the entitlement gate be
        called at all - the gate reads a tenant-scoped snapshot, so it needs
        both isolation layers bound, and binding them requires a context.
        """
        from workeasy360.contracts.values.tenant import TenantContext

        return TenantContext(
            organization_id=self.organization_id,
            membership_id=self.membership_id,
            user_id=self.user_id,
            organization_status=self.organization_status,
            aal=self.aal,
        )


def resolve_subject(
    *, user_id: UUID, organization_id: UUID | None, aal: int
) -> ResolvedSubject:
    """Verify membership and organization status. Raises on any failure.

    The membership lookup is delegated to ``platform/organizations``, which
    owns those tables. Not a queryset over ``Membership`` from here: reading
    another module's models is forbidden even where the import is legal by
    layer (F-6), and this is the one query in the system where getting the
    rules subtly different - forgetting soft deletion, say - hands out access.
    It is literally the same function the tenancy middleware resolves through,
    keyed by id instead of by slug.
    """
    from workeasy360.platform.organizations.resolver import resolve_by_organization_id

    if organization_id is None:
        # Deliberately not "pick the user's only organization". An implicit
        # choice here is a choice the user did not make and cannot see, and
        # the moment they join a second organization the behaviour silently
        # changes. The launcher knows which organization the user selected;
        # it passes it (docs/phase-3/03-sso-architecture.md section 5).
        raise protocol.OAuthError(
            protocol.ORGANIZATION_REQUIRED,
            "An organization must be specified for this authorization.",
        )

    resolution = resolve_by_organization_id(
        user_id=user_id, organization_id=organization_id
    )
    if resolution is None or not resolution.membership_status.grants_access:
        # One error for "no such organization", "not a member" and "membership
        # revoked". The three must be indistinguishable, or this endpoint
        # enumerates organizations for any authenticated user - and it is
        # reachable by every user of every product.
        logger.info("ecosystem.authorize.membership_denied")
        metrics.increment(
            "ecosystem_authorization_denied_total", reason=protocol.MEMBERSHIP_INACTIVE
        )
        raise protocol.OAuthError(
            protocol.MEMBERSHIP_INACTIVE,
            "You do not have access to this organization.",
        )

    status = resolution.organization_status
    if not status.allows_login:
        # Distinct from membership_inactive because the remedy is different
        # and the caller is a member, so it discloses nothing they do not
        # already know.
        metrics.increment(
            "ecosystem_authorization_denied_total",
            reason=protocol.ORGANIZATION_INACTIVE,
        )
        raise protocol.OAuthError(
            protocol.ORGANIZATION_INACTIVE,
            "This organization cannot be signed in to at present.",
        )

    return ResolvedSubject(
        user_id=user_id,
        organization_id=resolution.organization_id,
        membership_id=resolution.membership_id,
        organization_status=status,
        aal=aal,
    )


def require_entitlement(subject: ResolvedSubject, product_code: str) -> None:
    """Refuse the authorization unless the organization holds the product.

    Reached through ``contracts.ports.entitlements.get_gate()``, so this
    module never names the implementation. That is the property doc 16 s4.1
    says to preserve: because no caller names who answers, replacing the
    in-process gate with a remote client is a registration change rather than
    a caller change.

    ``EntitlementDenied`` is caught and translated rather than propagated. It
    carries ``http_status = 402``, which is the right answer for a platform
    API and the wrong one here: this failure has to become a redirectable
    OAuth error code so the product's client library can render a "purchase
    this product" screen, rather than a 402 JSON body appearing in the middle
    of a browser redirect.

    Read-level entitlement, not write. A suspended organization is read-only
    (R-10), and locking its users out of a product they still pay for would
    stop them exporting their data or looking at what they owe - which is the
    opposite of what suspension is for.
    """
    from workeasy360.contracts.errors import EntitlementDenied
    from workeasy360.contracts.values.identifiers import ProductCode
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import tenant_db_scope

    ctx = subject.to_context()
    try:
        # Both isolation layers, exactly as the tenancy middleware
        # establishes them for an ordinary request. The authorization
        # endpoint is not under the tenant URL shape, so the middleware
        # resolved nothing - but the entitlement snapshot is tenant-scoped,
        # and a read of it with only one layer bound does not fail, it
        # silently matches zero rows. Zero rows would read as "not entitled"
        # and lock every user out of every product.
        with tenant_context(ctx), tenant_db_scope(ctx):
            get_gate().require(subject.organization_id, ProductCode(product_code))
    except EntitlementDenied as denied:
        metrics.increment(
            "ecosystem_authorization_denied_total", reason=protocol.PRODUCT_NOT_ENTITLED
        )
        logger.info(
            "ecosystem.authorize.not_entitled",
            product_code=product_code,
            reason_code=str(denied.context.get("reason_code", "")),
        )
        raise protocol.OAuthError(
            protocol.PRODUCT_NOT_ENTITLED,
            "This organization is not entitled to use this product.",
        ) from None


# --- step 9: mint --------------------------------------------------------


def issue_code(request: AuthorizationRequest, subject: ResolvedSubject) -> GrantedCode:
    """Mint a single-use code bound to everything verified above.

    The plaintext code is returned to the caller and never stored; only its
    SHA-256 goes to the database, so a dump of this table yields nothing
    redeemable - the same rule ``organizations.Invitation`` already follows.
    """
    from workeasy360.platform.ecosystem.models import AuthorizationCode

    code = secrets.token_urlsafe(CODE_BYTES)
    ttl = int(settings.OIDC_AUTHORIZATION_CODE_TTL_S)

    AuthorizationCode.objects.create(
        code_hash=hash_code(code),
        integration=request.integration,
        user_id=subject.user_id,
        organization_id=subject.organization_id,
        membership_id=subject.membership_id,
        aal=subject.aal,
        redirect_uri=request.redirect_uri,
        scope=" ".join(request.scope),
        code_challenge=request.code_challenge,
        code_challenge_method=request.code_challenge_method,
        nonce=request.nonce,
        expires_at=timezone.now() + timezone.timedelta(seconds=ttl),
    )

    metrics.increment("ecosystem_authorization_codes_issued_total")
    logger.info(
        "ecosystem.authorize.granted",
        client_id=request.integration.client_id,
        product_code=request.integration.product_code,
        scope=" ".join(request.scope),
    )
    return GrantedCode(
        code=code,
        state=request.state,
        redirect_uri=request.redirect_uri,
        expires_in=ttl,
    )


# --- redemption ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RedeemedCode:
    """A code that has just been spent, and what it authorised."""

    integration: Any
    user_id: UUID
    organization_id: UUID
    membership_id: UUID
    aal: int
    scope: tuple[str, ...]
    nonce: str


def redeem_code(
    *, code: str, client: Any, redirect_uri: str, code_verifier: str
) -> RedeemedCode:
    """Spend a code exactly once, or raise ``invalid_grant``.

    **Single use under concurrency** is the whole point of the shape below.
    The spend is a conditional UPDATE:

        UPDATE ... SET redeemed_at = now WHERE id = %s AND redeemed_at IS NULL

    PostgreSQL applies that atomically, so of two concurrent redemptions of
    the same code exactly one sees ``rowcount == 1``. A read-then-write, even
    inside a transaction, would let both reads see ``redeemed_at IS NULL``
    under READ COMMITTED and issue two tokens for one authorization. A
    ``SELECT FOR UPDATE`` would also work and would additionally serialise
    every redemption on the row; the conditional update needs no lock at all.

    Every check produces the same ``invalid_grant``. Distinguishing "expired"
    from "already used" from "wrong client" would tell an attacker holding a
    stolen code which part of their attempt was wrong. That uniformity extends
    to the subject re-validation below: the product learns that the grant is
    no longer usable and restarts the flow, at which point the *authorization*
    endpoint tells it precisely why, redirectably, where its client library
    can render the right call to action.

    **Re-validation happens before the spend, not after.** If the entitlement
    read is momentarily unavailable the exception propagates, the endpoint
    answers 503, and the code is still live for the product's retry. Spending
    first would burn a legitimate user's authorization on a database blip and
    make them sign in again.
    """
    from workeasy360.platform.ecosystem.models import AuthorizationCode

    failure = _invalid_grant()

    row = (
        AuthorizationCode.objects.select_related("integration")
        .filter(code_hash=hash_code(code))
        .first()
    )
    if row is None:
        raise failure

    # Bound to the client that requested it. This is the check that stops
    # Books exchanging a code minted for CRM, even with a valid Books
    # credential and a stolen code.
    if row.integration_id != client.integration.id:
        logger.warning(
            "ecosystem.token.cross_client_redemption",
            presenting_product=client.product_code,
            issued_product=row.integration.product_code,
        )
        metrics.increment("ecosystem_cross_client_redemption_total")
        raise failure

    # Bound to the exact redirect URI it was issued against (RFC 6749
    # section 4.1.3). Without it, a code obtained via one registered callback
    # could be completed against another.
    if not _constant_time_equals(row.redirect_uri, redirect_uri):
        raise failure

    if not _verifier_matches(row.code_challenge, code_verifier):
        metrics.increment("ecosystem_pkce_failures_total")
        raise failure

    if row.expires_at <= timezone.now():
        raise failure

    # The three live-state checks, re-asked rather than inherited from the
    # code. See this module's docstring; the code is a record of a decision,
    # not a licence to skip making it again.
    _revalidate_subject(row)

    with transaction.atomic():
        spent = AuthorizationCode.objects.filter(
            pk=row.pk, redeemed_at__isnull=True
        ).update(redeemed_at=timezone.now())

    if spent != 1:
        # The code was already redeemed - possibly a replay, possibly the
        # loser of a genuine race. Either way it is a security-relevant
        # event: a legitimate client never redeems twice.
        logger.warning(
            "ecosystem.token.code_replayed",
            product_code=row.integration.product_code,
        )
        metrics.increment("ecosystem_code_replay_total")
        raise failure

    return RedeemedCode(
        integration=row.integration,
        user_id=row.user_id,
        organization_id=row.organization_id,
        membership_id=row.membership_id,
        aal=row.aal,
        scope=tuple(s for s in row.scope.split(" ") if s),
        nonce=row.nonce,
    )


def _invalid_grant() -> protocol.OAuthError:
    """The one failure every redemption path produces.

    A function rather than a constant because an ``OAuthError`` carries
    mutable-ish state (``headers``) and a shared instance raised from two
    places is one object appearing in two tracebacks. One description, one
    code, no way for two call sites to phrase it differently - which is the
    property that stops this endpoint becoming an oracle.
    """
    return protocol.OAuthError(
        protocol.INVALID_GRANT,
        "The authorization code is invalid, expired or already used.",
    )


def _revalidate_subject(row: Any) -> None:
    """Re-ask the three live-state questions the code only *recorded*.

    Membership, organization status and entitlement were all true when the
    code was minted. They are re-read here because the code outlives the
    answer: up to sixty seconds separate the redirect from the exchange, and
    an operator who revokes a membership inside that window is entitled to
    expect that no new token is issued afterwards.

    The same two functions the authorization endpoint calls, deliberately -
    a second implementation of "is this person still allowed in?" is a second
    place for a rule to be forgotten, and the rule that gets forgotten is
    always the one about soft deletion.

    Failures become ``invalid_grant``. The specific reason goes to the
    security log, not to the caller: it is disclosed properly at the
    authorization endpoint, redirectably, when the product restarts the flow.

    Anything that is *not* an ``OAuthError`` - the database is unreachable,
    the entitlement snapshot cannot be read - propagates untouched, so the
    endpoint answers 503 rather than telling a paying customer their grant
    was bad. A platform failure is never reported as a denial.
    """
    try:
        subject = resolve_subject(
            user_id=row.user_id,
            organization_id=row.organization_id,
            # Carried from the code, not re-read: the browser session that
            # reached AAL 2 is not present at the token endpoint, and
            # defaulting it down here would silently demote every step-up.
            aal=row.aal,
        )
        require_entitlement(subject, row.integration.product_code)
    except protocol.OAuthError as denial:
        logger.warning(
            "ecosystem.token.subject_no_longer_valid",
            product_code=row.integration.product_code,
            reason=denial.error,
        )
        metrics.increment(
            "ecosystem_code_revalidation_failures_total", reason=denial.error
        )
        raise _invalid_grant() from None


def _constant_time_equals(expected: str, candidate: str) -> bool:
    """Compare two strings in constant time, whatever bytes they contain.

    ``hmac.compare_digest`` raises ``TypeError`` on ``str`` arguments holding
    any non-ASCII character, and both operands here come off the wire: a
    ``redirect_uri`` in a form post and a ``code_challenge`` in a query
    string. Passing them straight in turns "this request is malformed" into
    an unhandled exception - a 503 with a stack trace in the error log and a
    token-issuance failure on the dashboard, for input the endpoint should
    simply refuse. Encoding first keeps the comparison constant time and
    makes every input representable.
    """
    return hmac.compare_digest(expected.encode("utf-8"), candidate.encode("utf-8"))


def _verifier_matches(challenge: str, verifier: str) -> bool:
    """RFC 7636 section 4.6: BASE64URL(SHA256(verifier)) == challenge.

    Length-checked first, because a client that sent a 4-character verifier
    has weakened its own protection to something guessable and should be told
    its request is invalid rather than have it silently accepted.
    """
    if not (MIN_VERIFIER_LENGTH <= len(verifier) <= MAX_VERIFIER_LENGTH):
        return False
    digest = hashlib.sha256(verifier.encode("ascii", errors="ignore")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    # Constant time. The comparison is against a value an attacker chose, and
    # a byte-by-byte early exit is measurable across enough requests.
    return _constant_time_equals(expected, challenge)


def hash_code(code: str) -> str:
    """SHA-256 of an authorization code, hex.

    Not a slow hash: the code is 256 bits of entropy with a 60-second life,
    so there is nothing to brute force and a work factor would only slow the
    token endpoint down. Hashed at all so that a database dump yields no
    redeemable credential.
    """
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def prune_expired(*, older_than_seconds: int = 3600) -> int:
    """Delete spent and expired codes. Returns how many went.

    Codes are worthless the moment they expire, and a table that only grows
    is a table that eventually makes the redemption lookup slow. Retained
    briefly past expiry so that a replay attempt still finds the row and is
    logged as a replay rather than as an unknown code.
    """
    from workeasy360.platform.ecosystem.models import AuthorizationCode

    cutoff = timezone.now() - timezone.timedelta(seconds=older_than_seconds)
    deleted, _ = AuthorizationCode.objects.filter(expires_at__lt=cutoff).delete()
    return int(deleted)

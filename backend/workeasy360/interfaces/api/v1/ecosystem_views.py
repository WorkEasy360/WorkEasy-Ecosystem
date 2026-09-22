"""HTTP adapters for the remote product integration surface.

Thin, as every view in this repository is: parse, call one application
service, shape the response (F-7, G4). The decisions live in
``platform/ecosystem``; what lives here is the wire.

Three things are genuinely this layer's job, and all three are security
properties rather than plumbing.

**CSRF.** The token, userinfo and entitlement endpoints are exempt, and the
exemption is safe *because of a second rule enforced alongside it*: those
endpoints authenticate the caller by client credential or bearer token and
**never** by session cookie. CSRF protects requests that carry ambient
credentials; a request with no ambient credentials has nothing to forge. An
exemption without that second rule would be a real hole, so
``test_credentialed_endpoints_ignore_session_authentication`` asserts it. The
global CSRF middleware is untouched - disabling it to make OAuth work would
expose every other endpoint in the platform (12-security.md, TASK 43).

**Where errors go.** A protocol failure is returned to the client's registered
redirect URI *only once that URI has been verified*. Before that, it is
rendered. This is RFC 6749 section 4.1.2.1 and it is the difference between an
authorization server and an open redirect.

**CORS.** Only the two public, credential-free documents - discovery and the
key set - carry ``Access-Control-Allow-Origin: *``. They are cacheable public
metadata containing no secret and no personal data, and browser-side
verification libraries fetch them. Every credentialed endpoint here is
server-to-server and sends no CORS header at all, so the combination
12-security.md section 8 forbids - a wildcard origin with credentials - is
unreachable.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from django.conf import settings
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    JsonResponse,
)
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from workeasy360.platform.ecosystem import (
    authorization,
    clientauth,
    discovery,
    keys,
    protocol,
    ratelimit,
    tokens,
    verification,
)
from workeasy360.platform.observability.correlation import current_correlation_id
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)


# --- public metadata ------------------------------------------------------


@require_GET
def openid_configuration(request: HttpRequest) -> JsonResponse:
    """OpenID Provider metadata. Public, cacheable, no credentials."""
    return _public_document(discovery.document())


@require_GET
def jwks_document(request: HttpRequest) -> JsonResponse:
    """The JSON Web Key Set. Public keys only - see keys.jwks()."""
    return _public_document(keys.jwks())


def _public_document(payload: dict[str, Any]) -> JsonResponse:
    response = JsonResponse(payload)
    # Safe wildcard: no credential is accepted on these two endpoints and no
    # cookie is read, so there is no ambient authority for a hostile origin
    # to borrow. See this module's docstring.
    response["Access-Control-Allow-Origin"] = "*"
    # Products cache the key set; five minutes bounds how long a newly
    # published key takes to become usable, and how long a retired one
    # lingers. Both windows are accounted for in the rotation procedure
    # (docs/phase-3/08-key-management.md).
    response["Cache-Control"] = "public, max-age=300"
    return response


# --- authorization endpoint ----------------------------------------------


@require_GET
@never_cache
def authorize(request: HttpRequest) -> HttpResponse:
    """Begin an authorization. Browser-facing, session-authenticated.

    GET only. The authorization request is a navigation, not a state change,
    so there is nothing for CSRF to protect.

    **Phase 4.** ``login_required`` and ``organization_required`` no longer
    bounce the browser back to the product with an error. A platform UI now
    exists (docs/phase-4/05-organization-selection.md), and standards-based
    OIDC behaviour is for the *provider* to interrupt the flow with its own
    login page or organization chooser and resume afterwards - see
    ``resume`` below, and ``PendingAuthorization``'s docstring for why a
    parked row rather than reconstructing the request from the browser a
    second time. Every other failure is still delivered to the product,
    unchanged.
    """
    # Phase 1: who is asking, and where may an answer go? Until both are
    # established nothing may be redirected anywhere (RFC 6749 4.1.2.1).
    try:
        resolution = authorization.resolve_client(request.GET)
    except protocol.OAuthError as exc:
        return _render_error(exc)

    # Rendered, not redirected, even though ``temporarily_unavailable`` is a
    # redirectable code. Being throttled is an infrastructure answer to the
    # *caller*, not a protocol outcome for the flow: a 302 would report 429
    # as a success to every monitor in the path and would discard the
    # Retry-After the client needs. It also declines to bounce a browser at a
    # registered URI once per rejected request.
    try:
        ratelimit.consume(
            "authorize",
            f"{resolution.integration.client_id}:{_client_ip(request)}",
            endpoint="authorize",
        )
    except protocol.OAuthError as exc:
        return _error_response(exc)

    # Phase 2. The client and its redirect URI are now verified, so every
    # failure below is delivered to the product in the protocol's own shape,
    # or parks the request for the platform's own UI - see the docstring.
    try:
        parsed = authorization.parse_request(resolution, request.GET)
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            raise protocol.OAuthError(
                protocol.LOGIN_REQUIRED,
                "No authenticated Workeasy360 session.",
            )

        subject = authorization.resolve_subject(
            user_id=user.pk,
            organization_id=parsed.organization_id,
            aal=_session_aal(request),
        )
        authorization.require_entitlement(subject, parsed.integration.product_code)
        authorization.require_seat(subject, parsed.integration.product_code)
        granted = authorization.issue_code(parsed, subject)
    except protocol.OAuthError as exc:
        if exc.error in (protocol.LOGIN_REQUIRED, protocol.ORGANIZATION_REQUIRED):
            return _park_and_redirect(parsed, exc)
        return _redirect_error(resolution, exc)

    metrics.increment("ecosystem_authorizations_total", result="granted")
    return HttpResponseRedirect(
        _with_query(granted.redirect_uri, {"code": granted.code, "state": granted.state})
    )


# --- resume: completing a parked request after login/org selection --------


@require_GET
@never_cache
def resume(request: HttpRequest) -> HttpResponse:
    """Complete a request the browser parked at ``/login`` or
    ``/organizations/select``. Reached by a full top-level navigation from
    the platform frontend, never by a background fetch, so the session
    cookie the login page just set is present.

    Single-use: a resume link works once, whether it succeeds or fails.
    ``authorization.resume_pending`` enforces that with the same conditional
    UPDATE ``redeem_code`` uses, so a token clicked twice - a doubled tab, a
    replayed URL - resumes at most once (Task 32).
    """
    token = request.GET.get("resume") or ""
    try:
        pending_request = authorization.resume_pending(token=token)
    except protocol.OAuthError:
        # No verified destination is known for an unrecognised token - see
        # ``resume_pending``'s docstring. Sent back to the platform's own
        # login page, never to any product-supplied URL.
        return HttpResponseRedirect(
            f"{settings.W360_FRONTEND_URL}/login?error=resume_invalid"
        )

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        # The token is already spent (single use). This is not a resumable
        # retry - the product's client library sees `login_required` via its
        # own redirect_uri and the browser restarts the flow from scratch.
        return _redirect_pending_error(
            pending_request, protocol.OAuthError(protocol.LOGIN_REQUIRED, "")
        )

    organization_id = pending_request.organization_id
    if organization_id is None:
        raw = request.GET.get("organization_id") or ""
        try:
            organization_id = UUID(raw)
        except ValueError:
            # The chooser did not supply one either. Re-park under a fresh
            # token rather than fail to the product - the user is signed in
            # and the only missing piece is which organization, which the
            # platform's own chooser is what asks (Task 5).
            requeued = authorization.create_pending(pending_request)
            return HttpResponseRedirect(
                f"{settings.W360_FRONTEND_URL}/organizations/select?resume={requeued.token}"
            )

    try:
        subject = authorization.resolve_subject(
            user_id=user.pk, organization_id=organization_id, aal=_session_aal(request)
        )
        authorization.require_entitlement(
            subject, pending_request.integration.product_code
        )
        authorization.require_seat(subject, pending_request.integration.product_code)
        granted = authorization.issue_code(pending_request, subject)
    except protocol.OAuthError as exc:
        return _redirect_pending_error(pending_request, exc)

    metrics.increment("ecosystem_authorizations_total", result="granted")
    return HttpResponseRedirect(
        _with_query(granted.redirect_uri, {"code": granted.code, "state": granted.state})
    )


def _park_and_redirect(request_obj: Any, exc: protocol.OAuthError) -> HttpResponse:
    """Park a verified request and send the browser to the platform's own
    login page or organization chooser. See ``authorize``'s docstring."""
    pending = authorization.create_pending(request_obj)
    path = "/login" if exc.error == protocol.LOGIN_REQUIRED else "/organizations/select"
    metrics.increment("ecosystem_authorization_parked_total", kind=exc.error)
    return HttpResponseRedirect(
        f"{settings.W360_FRONTEND_URL}{path}?resume={pending.token}"
    )


# --- token endpoint -------------------------------------------------------


@csrf_exempt
@require_POST
@never_cache
def token(request: HttpRequest) -> JsonResponse:
    """Exchange an authorization code for signed tokens.

    Server-to-server. Authenticated by client credential, never by cookie.
    """
    try:
        client = clientauth.authenticate(request, endpoint="token")
        ratelimit.consume("token", client.client_id, endpoint="token")

        grant_type = request.POST.get("grant_type", "")
        if grant_type != protocol.GRANT_TYPE_AUTHORIZATION_CODE:
            raise protocol.OAuthError(
                protocol.UNSUPPORTED_GRANT_TYPE,
                "Only the authorization_code grant is supported.",
            )

        redeemed = authorization.redeem_code(
            code=request.POST.get("code", ""),
            client=client,
            redirect_uri=request.POST.get("redirect_uri", ""),
            code_verifier=request.POST.get("code_verifier", ""),
        )
        payload = _token_response(redeemed)
    except protocol.OAuthError as exc:
        return _error_response(exc)
    except Exception:
        # Signing-key resolution failures land here. A 500 with no detail:
        # the caller did nothing wrong and learns nothing about why.
        logger.error("ecosystem.token.issuance_failed", exc_info=True)
        metrics.increment("ecosystem_token_issuance_failures_total")
        return _error_response(
            protocol.OAuthError(
                protocol.SERVER_ERROR,
                "The token could not be issued.",
                http_status=503,
            )
        )

    metrics.increment("ecosystem_tokens_issued_total")
    response = JsonResponse(payload)
    # RFC 6749 section 5.1: token responses must not be cached anywhere.
    response["Cache-Control"] = "no-store"
    response["Pragma"] = "no-cache"
    return response


def _token_response(redeemed: Any) -> dict[str, Any]:
    subject = tokens.TokenSubject(
        user_id=redeemed.user_id,
        organization_id=redeemed.organization_id,
        product_code=redeemed.integration.product_code,
        client_id=redeemed.integration.client_id,
        scope=" ".join(redeemed.scope),
        aal=redeemed.aal,
        nonce=redeemed.nonce,
    )
    id_token = tokens.issue_id_token(
        subject, profile=_profile_claims(redeemed.user_id, redeemed.scope)
    )
    access_token = tokens.issue_access_token(subject)
    return {
        "access_token": access_token.value,
        "token_type": "Bearer",
        "expires_in": access_token.expires_in,
        "id_token": id_token.value,
        "scope": " ".join(redeemed.scope),
    }


def _profile_claims(user_id: Any, scope: tuple[str, ...]) -> dict[str, Any]:
    """Personal claims, strictly gated by granted scope.

    Absent unless the integration registered for the scope *and* asked for it
    on this request. A product that only needs to know *which* user is not
    handed *who* the user is (docs/phase-3/05-token-claims.md section 4).
    """
    from workeasy360.platform.identity.selectors import profile_claims

    return profile_claims(user_id=user_id, scopes=scope)


# --- userinfo -------------------------------------------------------------


@csrf_exempt
@require_GET
@never_cache
def userinfo(request: HttpRequest) -> JsonResponse:
    """OIDC UserInfo. Authenticated by the access token, never by cookie."""
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if not header.startswith("Bearer "):
        return _error_response(
            protocol.OAuthError(
                protocol.INVALID_TOKEN,
                "A bearer access token is required.",
                http_status=401,
                headers={"WWW-Authenticate": 'Bearer realm="workeasy360"'},
            )
        )

    import jwt as pyjwt

    raw = header[7:].strip()
    try:
        # The audience is read from the *unverified* token only to select
        # which client's audience to verify against, then verified. Passing
        # it through without re-verification would defeat the check, so
        # tokens.verify is given it as the expected value and PyJWT rejects
        # any mismatch.
        unverified = pyjwt.decode(raw, options={"verify_signature": False})
        claims = tokens.verify(
            raw,
            audience=str(unverified.get("aud") or ""),
            expected_type=tokens.TYPE_ACCESS_TOKEN,
        )
    except Exception:
        return _error_response(
            protocol.OAuthError(
                protocol.INVALID_TOKEN,
                "The access token is invalid or expired.",
                http_status=401,
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )
        )

    from workeasy360.platform.identity.selectors import profile_claims

    scopes = tuple(s for s in str(claims.get("scope", "")).split(" ") if s)
    body: dict[str, Any] = {"sub": claims["sub"]}
    body.update(profile_claims(user_id=claims["sub"], scopes=scopes))
    response = JsonResponse(body)
    response["Cache-Control"] = "no-store"
    return response


# --- entitlement verification --------------------------------------------


@csrf_exempt
@require_POST
@never_cache
def verify_entitlement(request: HttpRequest) -> JsonResponse:
    """ "May this organization use me?" - answered for the *calling* product.

    ``product_code`` is not a parameter. It comes from the authenticated
    client's registration, which is what makes cross-product probing
    impossible rather than merely forbidden (verification.py).

    **Form-encoded request, JSON response** - exactly like the token endpoint,
    and for the same reason. Client authentication travels as form fields
    (``client_assertion`` per RFC 7523, or HTTP Basic per RFC 6749 section
    2.3.1), so accepting a JSON body here would mean a second way to
    authenticate a caller: ``private_key_jwt`` has no standard placement
    outside a form, and inventing a header for it would be a proprietary
    authentication mechanism in a phase whose whole point is not having one.
    One authentication path is worth more than a fashionable request body
    (docs/phase-3/06-entitlement-api.md section 3).
    """
    from uuid import UUID

    try:
        client = clientauth.authenticate(request, endpoint="entitlement_verify")
        ratelimit.consume("verify", client.client_id, endpoint="entitlement_verify")

        try:
            organization_id = UUID(str(request.POST.get("organization_id", "")))
        except (ValueError, AttributeError):
            raise protocol.OAuthError(
                protocol.INVALID_REQUEST, "organization_id must be a UUID."
            ) from None

        result = verification.verify(
            organization_id=organization_id,
            product_code=client.product_code,
            # Absent means the read question, which is the conservative one:
            # a product that did not ask about writes is told nothing that
            # would let it offer one.
            write=str(request.POST.get("write", "")).lower() in ("1", "true", "yes"),
        )
    except protocol.OAuthError as exc:
        return _error_response(exc)
    except verification.VerificationUnavailable:
        # 503, never a denial. Telling a product "not entitled" because this
        # platform could not reach its own database would have that product
        # lock out a paying customer over an outage that has nothing to do
        # with their licence (verification.py, module docstring).
        return _error_response(
            protocol.OAuthError(
                protocol.TEMPORARILY_UNAVAILABLE,
                "The entitlement decision is temporarily unavailable. Retry, "
                "and meanwhile continue to honour your last known answer.",
                http_status=503,
                headers={"Retry-After": "5"},
            )
        )

    response = JsonResponse(result.as_dict())
    response["Cache-Control"] = "no-store"
    return response


# --- shared response shaping ---------------------------------------------


def _error_response(exc: protocol.OAuthError) -> JsonResponse:
    response = JsonResponse(
        exc.as_dict(correlation_id=str(current_correlation_id() or "")),
        status=exc.http_status,
    )
    for header, value in exc.headers.items():
        response[header] = value
    response["Cache-Control"] = "no-store"
    return response


def _render_error(exc: protocol.OAuthError) -> HttpResponse:
    """A failure that must not be redirected. Rendered to the user agent."""
    metrics.increment("ecosystem_authorizations_total", result="rejected")
    return _error_response(exc)


def _redirect_error(resolution: Any, exc: protocol.OAuthError) -> HttpResponse:
    """Deliver a failure to the product's verified redirect URI.

    Reached only with a ``ClientResolution`` in hand, which by construction
    means ``resolve_client`` verified the client and matched the redirect URI
    against the registered set by exact equality. The destination is
    therefore one the product registered - never one the request supplied.
    """
    metrics.increment("ecosystem_authorizations_total", result="denied")
    return HttpResponseRedirect(
        _error_redirect_url(resolution.redirect_uri, resolution.state, exc)
    )


def _redirect_pending_error(
    pending_request: Any, exc: protocol.OAuthError
) -> HttpResponse:
    """Deliver a failure to a *resumed* request's verified redirect URI.

    Identical contract to ``_redirect_error``: ``pending_request.redirect_uri``
    was verified once, by ``resolve_client``, before it was ever parked - see
    ``PendingAuthorization``'s docstring. One helper, so the two paths cannot
    drift on what belongs in the query string.
    """
    metrics.increment("ecosystem_authorizations_total", result="denied")
    return HttpResponseRedirect(
        _error_redirect_url(pending_request.redirect_uri, pending_request.state, exc)
    )


def _error_redirect_url(redirect_uri: str, state: str, exc: protocol.OAuthError) -> str:
    params = {"error": exc.error}
    if exc.description:
        params["error_description"] = exc.description
    if state:
        # Echoed verbatim, including on the error path. A client that cannot
        # correlate an error with the request it made will surface it against
        # the wrong user's session.
        params["state"] = state
    return _with_query(redirect_uri, params)


def _with_query(url: str, params: dict[str, str]) -> str:
    """Append parameters to a registered redirect URI.

    Safe to use ``?`` unconditionally: the manifest validator refuses a
    redirect URI carrying a query string, precisely so that this function
    never has to guess whether to use ``?`` or ``&`` - and so that a product
    cannot register a URI whose existing parameters would be shadowed by
    ``code`` or ``state``.
    """
    encoded = urlencode({k: v for k, v in params.items() if v})
    return f"{url}?{encoded}" if encoded else url


def _client_ip(request: HttpRequest) -> str:
    return str(request.META.get("REMOTE_ADDR", "")) or "unknown"


def _session_aal(request: HttpRequest) -> int:
    """The assurance level this session reached.

    Read from the session, which the platform's own login flow writes.
    Defaults to 1 rather than raising: a session with no recorded level is a
    single-factor session, and defaulting *down* is the fail-closed direction.
    """
    try:
        return max(1, int(request.session.get("w360_aal", 1)))
    except (AttributeError, TypeError, ValueError):
        return 1


#: Views that authenticate by credential rather than by session cookie, and
#: are therefore CSRF-exempt. Enumerated so the security test can assert that
#: each one genuinely ignores session authentication, rather than trusting the
#: decorator to have been applied thoughtfully.
CREDENTIALED_VIEWS: tuple[Callable[..., Any], ...] = (token, userinfo, verify_entitlement)

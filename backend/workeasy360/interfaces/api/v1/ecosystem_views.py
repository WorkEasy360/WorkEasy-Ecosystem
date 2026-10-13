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

from workeasy360.contracts.values import json_input
from workeasy360.interfaces.api.v1 import intelligence_product_views
from workeasy360.platform.ecosystem import (
    authorization,
    chooser,
    clientauth,
    discovery,
    keys,
    protocol,
    ratelimit,
    sign_in,
    tokens,
    verification,
)
from workeasy360.platform.identity import portals as portal_links
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
        logger.info(
            "ecosystem.sso.started",
            product_code=parsed.integration.product_code,
            organization_id=str(parsed.organization_id or ""),
            prompt=" ".join(sorted(parsed.prompt)),
        )
        if parsed.prompt_login:
            # The product asked for a fresh sign-in (ADR-044) - whoever it
            # turns out to be. Parked before any check, because every check
            # then runs at resume for the person who actually signed in.
            return _park_and_redirect(
                parsed,
                protocol.OAuthError(protocol.LOGIN_REQUIRED, ""),
                reauthenticate=True,
            )
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            raise protocol.OAuthError(
                protocol.LOGIN_REQUIRED,
                "No authenticated Workeasy360 session.",
            )

        # The request itself keeps what the product sent: an implied
        # organization belongs to this person only, so a request parked for
        # the access chooser never carries it to whoever might resume it next
        # ("Use another Workeasy360 account") - they get their own.
        organization_id = authorization.organization_for(
            user_id=user.pk, named=parsed.organization_id
        )
        subject = authorization.resolve_subject(
            user_id=user.pk,
            organization_id=organization_id,
            aal=_session_aal(request),
            facts=_session_facts(request, organization_id),
        )
        authorization.require_entitlement(subject, parsed.integration.product_code)
        authorization.require_seat(subject, parsed.integration.product_code)

        # Step 10 (ADR-044): only now, with every refusal already delivered,
        # the person's own preference. A preference never reaches a request
        # that was going to be refused, and it can only *add* a question.
        decision, doors = sign_in.decision_at_authorize(user.pk, parsed.integration)
        if not doors.sso_available:
            # Active but not SSO-ready (no usable credential): a code would be
            # one the product can never redeem. Refused, never offered - the
            # chooser must not pretend Workeasy360 sign-in works here.
            raise protocol.OAuthError(
                protocol.UNAUTHORIZED_CLIENT,
                "This product is not ready for Workeasy360 sign-in.",
            )
        if parsed.wants_account_confirmation or not decision.silent:
            if parsed.prompt_none:
                raise protocol.OAuthError(
                    protocol.INTERACTION_REQUIRED,
                    "Workeasy360 needs to ask this person how to sign in.",
                )
            return _park_for_choice(parsed, user.pk)
        granted = authorization.issue_code(parsed, subject)
    except protocol.OAuthError as exc:
        if exc.error in (protocol.LOGIN_REQUIRED, protocol.ORGANIZATION_REQUIRED):
            if parsed.prompt_none:
                # prompt=none: no page may be shown, so the interaction the
                # request needs is reported instead of performed.
                return _redirect_error(
                    resolution,
                    protocol.OAuthError(
                        protocol.LOGIN_REQUIRED,
                        "No authenticated Workeasy360 session.",
                    )
                    if exc.error == protocol.LOGIN_REQUIRED
                    else protocol.OAuthError(
                        protocol.INTERACTION_REQUIRED,
                        "An organization must be chosen in Workeasy360.",
                    ),
                )
            return _park_and_redirect(parsed, exc)
        return _redirect_error(resolution, exc)

    _granted_log(
        parsed,
        subject.organization_id,
        path="silent",
        source="named" if parsed.organization_id else "implied",
    )
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
    try:
        # Keyed on the caller, not on a client: `resume` is reached before
        # any client is known (the token is what names one). Unthrottled it
        # is an online guessing budget against the resume token and, more
        # practically, free database and log load for anyone who wants it.
        ratelimit.consume("resume", _client_ip(request), endpoint="resume")
    except protocol.OAuthError as exc:
        return _error_response(exc)

    token = request.GET.get("resume") or ""
    invalid = HttpResponseRedirect(
        f"{portal_links.auth_url()}/login?error=resume_invalid"
    )
    parked = authorization.find_pending(token=token)
    if parked is None:
        # No verified destination is known for an unrecognised token - see
        # ``resume_pending``'s docstring. Sent back to the platform's own
        # login page, never to any product-supplied URL.
        return invalid

    user = getattr(request, "user", None)
    signed_in = user is not None and user.is_authenticated
    if parked.awaiting_choice:
        # Not answered yet (ADR-044): a resume link cannot skip the question
        # the person's preference asks. Back to the chooser - for its person.
        if signed_in and parked.user_id is not None and parked.bound_to(user.pk):
            return _to_chooser(token)
        return invalid
    if signed_in and not parked.bound_to(user.pk):
        # Answered by one person, resumed in another's session: spent, and
        # refused, rather than completed for whoever happens to hold it.
        authorization.claim_pending(parked)
        return invalid
    if signed_in and not parked.admits_session(_session_authenticated_at(request)):
        # prompt=login: this session predates the request. Sign in again;
        # the token stays live for the fresh session.
        return HttpResponseRedirect(
            f"{portal_links.auth_url()}/login?resume={token}&reauth=1"
        )
    if not authorization.claim_pending(parked):
        return invalid
    pending_request = parked.request

    if not signed_in:
        # The token is already spent (single use). This is not a resumable
        # retry - the product's client library sees `login_required` via its
        # own redirect_uri and the browser restarts the flow from scratch.
        return _redirect_pending_error(
            pending_request, protocol.OAuthError(protocol.LOGIN_REQUIRED, "")
        )

    organization_id = pending_request.organization_id
    source = "named"
    if organization_id is None:
        raw = request.GET.get("organization_id") or ""
        try:
            organization_id = UUID(raw)
            source = "chosen"
        except ValueError:
            organization_id = None
            source = "implied"
        # Nothing chosen at the chooser: the person's only organization, if
        # they have exactly one (the same rule ``authorize`` applies).
        organization_id = authorization.organization_for(
            user_id=user.pk, named=organization_id
        )
        if organization_id is None:
            # The chooser did not supply one either. Re-park under a fresh
            # token rather than fail to the product - the user is signed in
            # and the only missing piece is which organization, which the
            # platform's own chooser is what asks (Task 5).
            requeued = authorization.create_pending(pending_request)
            return HttpResponseRedirect(
                f"{portal_links.auth_url()}/organizations/select?resume={requeued.token}"
            )

    try:
        subject = authorization.resolve_subject(
            user_id=user.pk,
            organization_id=organization_id,
            aal=_session_aal(request),
            facts=_session_facts(request, organization_id),
        )
        authorization.require_entitlement(
            subject, pending_request.integration.product_code
        )
        authorization.require_seat(subject, pending_request.integration.product_code)
        if parked.user_id is None and parked.signed_in_before_parking(
            _session_authenticated_at(request)
        ):
            # Parked for sign-in or for the organization chooser by a person
            # who was ALREADY signed in: nothing was typed for this flow, so
            # their preference applies exactly as at ``authorize`` (step 10,
            # after every check). Without this a resume link opened in a
            # signed-in browser, or an organization chosen by someone set to
            # "Ask me each time", completed with no question.
            decision, doors = sign_in.decision_at_authorize(
                user.pk, pending_request.integration
            )
            if not doors.sso_available:
                raise protocol.OAuthError(
                    protocol.UNAUTHORIZED_CLIENT,
                    "This product is not ready for Workeasy360 sign-in.",
                )
            if not decision.silent:
                # Parked as the product sent it: a chosen or implied
                # organization is this person's, not the request's.
                return _park_for_choice(pending_request, user.pk)
        granted = authorization.issue_code(pending_request, subject)
    except protocol.OAuthError as exc:
        return _redirect_pending_error(pending_request, exc)

    _granted_log(pending_request, subject.organization_id, path="resumed", source=source)
    metrics.increment("ecosystem_authorizations_total", result="granted")
    return HttpResponseRedirect(
        _with_query(granted.redirect_uri, {"code": granted.code, "state": granted.state})
    )


def _park_and_redirect(
    request_obj: Any, exc: protocol.OAuthError, *, reauthenticate: bool = False
) -> HttpResponse:
    """Park a verified request and send the browser to the platform's own
    login page or organization chooser. See ``authorize``'s docstring.

    ``reauthenticate`` (``prompt=login``): only a session that signs in from
    now on may resume it, and the login page asks for the password even when
    a session exists."""
    from django.utils import timezone

    if reauthenticate:
        pending = authorization.create_pending(
            request_obj, authenticated_after=timezone.now()
        )
    else:
        pending = authorization.create_pending(request_obj)
    path = "/login" if exc.error == protocol.LOGIN_REQUIRED else "/organizations/select"
    suffix = "&reauth=1" if reauthenticate else ""
    metrics.increment(
        "ecosystem_authorization_parked_total",
        kind="reauthenticate" if reauthenticate else exc.error,
    )
    return HttpResponseRedirect(
        f"{portal_links.auth_url()}{path}?resume={pending.token}{suffix}"
    )


def _park_for_choice(request_obj: Any, user_id: Any) -> HttpResponse:
    """Park a request that passed every check, for the access chooser
    (ADR-044). Bound to this person: only they can answer or resume it."""
    pending = authorization.create_pending(
        request_obj, awaiting_choice=True, user_id=user_id
    )
    metrics.increment("ecosystem_authorization_parked_total", kind="choice")
    logger.info(
        "ecosystem.sso.chooser_shown",
        product_code=request_obj.integration.product_code,
        organization_id=str(request_obj.organization_id or ""),
    )
    return _to_chooser(pending.token)


def _to_chooser(token: str) -> HttpResponse:
    return HttpResponseRedirect(
        f"{portal_links.auth_url()}/product-access?resume={token}"
    )


def _granted_log(
    request_obj: Any, organization_id: Any, *, path: str, source: str
) -> None:
    """Observability (design section 9): which product, which organization,
    and how - never the code, the state, the nonce or any claim. ``source``
    is where the organization came from: ``named`` by the product (the
    launcher), ``chosen`` at the organization chooser, or ``implied`` - the
    person's only organization (ADR-044 item 16)."""
    logger.info(
        "ecosystem.sso.succeeded",
        product_code=request_obj.integration.product_code,
        organization_id=str(organization_id),
        path=path,
        organization_source=source,
    )


def _session_authenticated_at(request: HttpRequest) -> float | None:
    """When this session's password was entered (epoch seconds), or None."""
    from workeasy360.platform.tenancy.resolution import SESSION_AUTHENTICATED_AT_KEY

    raw = getattr(request, "session", {}).get(SESSION_AUTHENTICATED_AT_KEY)
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


# --- the access chooser (ADR-044) -------------------------------------------


@require_GET
@never_cache
def pending_view(request: HttpRequest) -> JsonResponse:
    """What the access chooser shows for a request parked for this person:
    the product, the organization, the signed-in account and which answers
    exist. Display facts only - never a redirect URI, state or PKCE value."""
    try:
        ratelimit.consume("chooser", _client_ip(request), endpoint="pending")
    except protocol.OAuthError as exc:
        return _error_response(exc)
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return _json_refusal("login_required", 401)
    facts = chooser.chooser_facts(token=request.GET.get("resume") or "", user_id=user.pk)
    if facts is None:
        return _json_refusal("pending_invalid", 404)

    from workeasy360.commercial.catalog import selectors as catalog
    from workeasy360.platform.organizations.selectors import organization_names

    shown = catalog.product_presentations([facts.product_code]).get(
        facts.product_code, {}
    )
    organization = ""
    if facts.organization_id is not None:
        organization = organization_names([facts.organization_id]).get(
            facts.organization_id, ""
        )
    response = JsonResponse(
        {
            "product": {
                "code": facts.product_code,
                "name": shown.get("name") or facts.display_name,
                "icon": shown.get("icon", ""),
            },
            "organization": {"name": organization},
            "account": {"email": user.email, "name": user.full_name},
            "choices": {
                "workeasy360": True,
                "product": facts.product_login_available,
            },
            "remembered": facts.override.value,
        }
    )
    response["Cache-Control"] = "no-store"
    return response


@require_POST
@never_cache
def pending_choose_view(request: HttpRequest) -> JsonResponse:
    """The person's answer. Session-authenticated and CSRF-protected (the
    global middleware, untouched). Answers ``{redirect_url}``: the page then
    navigates there itself - a cross-origin destination cannot be a redirect
    of a ``fetch``, and this way the URL is always one the server built from
    the registry (the resume endpoint, the product's registered sign-in page,
    the platform's own sign-in page, or the product's registered redirect URI
    with ``access_denied``)."""
    from workeasy360.interfaces.api.v1.auth_views import connection_facts

    try:
        ratelimit.consume("chooser", _client_ip(request), endpoint="pending_choose")
    except protocol.OAuthError as exc:
        return _error_response(exc)
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return _json_refusal("login_required", 401)
    try:
        body = json_input.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return _json_refusal("pending_invalid", 400)
    if not isinstance(body, dict):
        return _json_refusal("pending_invalid", 400)

    where = connection_facts(request)
    outcome = chooser.answer(
        token=str(body.get("resume") or ""),
        user_id=user.pk,
        choice=str(body.get("choice") or ""),
        remember=body.get("remember") is True,
        ip_prefix=where["ip_prefix"],
        device_label=where["device_label"],
    )
    if outcome is None:
        return _json_refusal("pending_invalid", 400)

    if outcome.choice is chooser.Choice.WORKEASY360:
        target = f"/api/v1/integrations/oauth/resume?resume={outcome.resume_token}"
    elif outcome.choice is chooser.Choice.PRODUCT:
        target = outcome.product_login_url
    elif outcome.choice is chooser.Choice.SWITCH_ACCOUNT:
        _sign_out(request)
        target = f"/login?resume={outcome.resume_token}"
    else:
        assert outcome.request is not None
        target = _error_redirect_url(
            outcome.request.redirect_uri,
            outcome.request.state,
            protocol.OAuthError(
                protocol.ACCESS_DENIED, "The person cancelled the sign-in."
            ),
        )
    response = JsonResponse({"redirect_url": target})
    response["Cache-Control"] = "no-store"
    return response


def _sign_out(request: HttpRequest) -> None:
    """End this sign-in everywhere (every portal session of it, ADR-043) -
    the platform's own sign-out, not a second copy of it."""
    from workeasy360.interfaces.api.v1.auth_views import sign_out

    sign_out(request)


def _json_refusal(code: str, status: int) -> JsonResponse:
    response = JsonResponse({"code": code}, status=status)
    response["Cache-Control"] = "no-store"
    return response


# --- RP-initiated sign-out (ADR-044) ----------------------------------------


@require_GET
@never_cache
def end_session(request: HttpRequest) -> HttpResponse:
    """OpenID Connect RP-Initiated Logout 1.0 ``end_session_endpoint``.

    A GET signs no one out (logout CSRF): a signed-in browser is sent to the
    platform's confirmation page; a signed-out one straight back to the
    product's registered post-logout URI. Until the client and that URI are
    verified, the failure is rendered - never redirected."""
    try:
        ratelimit.consume("end_session", _client_ip(request), endpoint="end_session")
    except protocol.OAuthError as exc:
        return _error_response(exc)
    try:
        verified = chooser.logout_request(request.GET)
    except protocol.OAuthError as exc:
        return _render_error(exc)
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return HttpResponseRedirect(verified.return_url)
    return HttpResponseRedirect(
        f"{portal_links.auth_url()}/sign-out?{urlencode({'request': chooser.seal(verified)})}"
    )


@require_GET
@never_cache
def end_session_request_view(request: HttpRequest) -> JsonResponse:
    """What the confirmation page names: the product and the account."""
    verified = chooser.unseal(request.GET.get("request") or "")
    if verified is None:
        return _json_refusal("logout_invalid", 400)
    user = getattr(request, "user", None)
    signed_in = user is not None and user.is_authenticated
    response = JsonResponse(
        {
            "product": {"name": verified.display_name},
            "signed_in": signed_in,
            "account": {"email": user.email} if signed_in else None,
        }
    )
    response["Cache-Control"] = "no-store"
    return response


@require_POST
@never_cache
def end_session_confirm_view(request: HttpRequest) -> JsonResponse:
    """The person's answer: sign out of Workeasy360 (``sign_out: true``) or
    not, then return to the product's registered post-logout URI either way.
    CSRF-protected like every session write."""
    try:
        body = json_input.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return _json_refusal("logout_invalid", 400)
    if not isinstance(body, dict):
        return _json_refusal("logout_invalid", 400)
    verified = chooser.unseal(str(body.get("request") or ""))
    if verified is None:
        return _json_refusal("logout_invalid", 400)
    if body.get("sign_out") is True:
        _sign_out(request)
        logger.info("ecosystem.sso.signed_out", product_code=verified.product_code)
    response = JsonResponse({"redirect_url": verified.return_url})
    response["Cache-Control"] = "no-store"
    return response


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
        auth_time=redeemed.auth_time,
    )
    id_token = tokens.issue_id_token(
        subject,
        profile={
            **_profile_claims(redeemed.user_id, redeemed.scope),
            **_organization_claims(redeemed.organization_id, redeemed.scope),
        },
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


def _organization_claims(organization_id: Any, scope: tuple[str, ...]) -> dict[str, Any]:
    """``organization_name``, for the ``organization`` scope only.

    A display string a product uses to label the tenant it provisions on a
    first sign-in. It is never an identity: the customer can rename the
    organization, so a product keys on ``organization_id`` and treats the name
    as a starting label (docs/phase-3/05-token-claims.md section 1).
    """
    if "organization" not in scope:
        return {}
    from workeasy360.platform.organizations.selectors import organization_names

    name = organization_names([organization_id]).get(organization_id, "")
    # Omitted when empty, as the profile claims are (OIDC Core section 5.3.2).
    return {"organization_name": name} if name else {}


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
        # A peek to choose the expected audience; tokens.verify below checks
        # the signature, issuer, expiry, type and that audience (semgrep
        # triage, Phase 12: docs/phase-12/11-security-scans.md).
        unverified = pyjwt.decode(
            raw,
            options={"verify_signature": False},  # nosemgrep
        )
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

        try:
            organization_id = UUID(str(request.POST.get("organization_id", "")))
        except (ValueError, AttributeError):
            # A malformed question still spends the product's budget.
            ratelimit.consume("verify", client.client_id, endpoint="entitlement_verify")
            raise protocol.OAuthError(
                protocol.INVALID_REQUEST, "organization_id must be a UUID."
            ) from None
        # Optional: the person the product's session belongs to (the ID
        # token's ``sub``). Bounded like every other caller-supplied string.
        raw_subject = request.POST.get("subject")
        subject = str(raw_subject)[:64] if raw_subject else None
        # The per-(organization, person) bucket FIRST: a loop stuck on one key
        # is refused there without spending the product's whole budget, which
        # its other customers' questions need. A conforming product asks about
        # each key at most once a minute, however many people an organization
        # has. Keyed on the canonical UUID, so spellings of one subject share a
        # count; every malformed subject shares one key.
        ratelimit.consume(
            "verify_org",
            f"{client.client_id}:{organization_id}:{_subject_key(subject)}",
            endpoint="entitlement_verify",
        )
        ratelimit.consume("verify", client.client_id, endpoint="entitlement_verify")

        result = verification.verify(
            organization_id=organization_id,
            product_code=client.product_code,
            # Absent means the read question, which is the conservative one:
            # a product that did not ask about writes is told nothing that
            # would let it offer one.
            write=str(request.POST.get("write", "")).lower() in ("1", "true", "yes"),
            subject=subject,
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


# --- event ingestion (Phase 6) --------------------------------------------

#: Headroom over the event itself for the client-authentication fields that
#: share the form body (a signed ``client_assertion`` is ~1 KB).
_INGEST_AUTH_OVERHEAD_BYTES = 16_384


@csrf_exempt
@require_POST
@never_cache
def publish_event(request: HttpRequest) -> JsonResponse:
    """A registered product publishes one event into the ecosystem.

    **Form-encoded, like the token and entitlement endpoints, and for the
    same reason** (see ``verify_entitlement``): client authentication travels
    as form fields (``client_assertion`` per RFC 7523, or HTTP Basic), so the
    event travels as one more - ``event``, a JSON string - rather than
    inventing a second way to authenticate a JSON body. Fields:
    ``organization_id`` and ``event``.

    The oversized-body check runs before anything is parsed: an attacker's
    megabyte costs a header read, not a JSON parse (Task 11).

    ``202 accepted`` for a new event, ``200 duplicate`` for one already
    recorded - a retry learns its first attempt landed, and nothing is
    processed twice (Task 10). Every refusal is an OAuth-shaped error a
    product's client library already parses.
    """
    from workeasy360.integration.routing import ingestion

    limit = int(settings.INTEGRATION_EVENT_MAX_BYTES) + _INGEST_AUTH_OVERHEAD_BYTES
    try:
        declared = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        declared = 0
    if declared > limit:
        return _error_response(
            protocol.OAuthError(
                protocol.INVALID_REQUEST, "The request is too large.", http_status=413
            )
        )

    try:
        client = clientauth.authenticate(request, endpoint="event_ingest")
        result = ingestion.ingest_product_event(
            client,
            organization_id=request.POST.get("organization_id"),
            raw_event=request.POST.get("event"),
        )
    except protocol.OAuthError as exc:
        return _error_response(exc)

    response = JsonResponse(
        {"status": result.status, "event_id": result.event_id},
        status=202 if result.status == "accepted" else 200,
    )
    response["Cache-Control"] = "no-store"
    return response


# --- shared response shaping ---------------------------------------------


def _subject_key(subject: str | None) -> str:
    """The rate-limit key part for an entitlement question's ``subject``: the
    canonical UUID (``UUID()`` accepts case, braces, ``urn:uuid:`` and
    missing dashes, which would otherwise be separate counts), ``""`` for none,
    and one shared ``"invalid"`` for anything that is not a UUID."""
    from uuid import UUID

    if not subject:
        return ""
    try:
        return str(UUID(subject))
    except ValueError:
        return "invalid"


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
    _failed_log(resolution.integration, exc)
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
    _failed_log(pending_request.integration, exc)
    return HttpResponseRedirect(
        _error_redirect_url(pending_request.redirect_uri, pending_request.state, exc)
    )


def _failed_log(integration: Any, exc: protocol.OAuthError) -> None:
    """The failure category is the OAuth error code - a closed set, never a
    value the caller sent (design section 9)."""
    logger.info(
        "ecosystem.sso.failed",
        product_code=integration.product_code,
        category=exc.error,
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
    """See ``platform.observability.client_ip`` - D-009, and why the obvious
    ``X-Forwarded-For`` read is worse than the bug it fixes."""
    from workeasy360.platform.observability.client_ip import (
        client_ip,
        rate_limit_identity,
    )

    # Rate-limit keys only; IPv6 bucketed by /64 (Phase 12, G-12).
    return rate_limit_identity(client_ip(request))


def _session_facts(request: HttpRequest, organization_id: UUID | None) -> Any:
    """The session's standing for the organization's access policy - read by
    the same function the tenancy middleware uses."""
    from workeasy360.platform.tenancy.resolution import session_facts

    if organization_id is None:
        return None
    return session_facts(request, organization_id)


def _session_aal(request: HttpRequest) -> int:
    """The assurance level this session reached.

    Read from the session, which the platform's own login flow writes.
    Defaults to 1 rather than raising: a session with no recorded level is a
    single-factor session, and defaulting *down* is the fail-closed direction.
    A step-up older than ``STEP_UP_MAX_AGE_SECONDS`` also reads as 1.
    """
    from workeasy360.platform.tenancy.resolution import session_aal

    return session_aal(getattr(request, "session", None))


#: Views that authenticate by credential rather than by session cookie, and
#: are therefore CSRF-exempt. Enumerated so the security test can assert that
#: each one genuinely ignores session authentication, rather than trusting the
#: decorator to have been applied thoughtfully.
CREDENTIALED_VIEWS: tuple[Callable[..., Any], ...] = (
    token,
    userinfo,
    verify_entitlement,
    publish_event,
    # Phase 8: the product-facing AI, retrieval and analytics endpoints share
    # this trust boundary exactly (intelligence_product_views).
    *intelligence_product_views.CREDENTIALED_VIEWS,
)

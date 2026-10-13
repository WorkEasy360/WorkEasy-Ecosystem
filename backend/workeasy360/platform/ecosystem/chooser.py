"""The access chooser and RP-initiated sign-out (ADR-044).

**The access chooser** is where Workeasy360 asks a signed-in person how to
enter a product, when their preference says to ask - or when the product asked
for it (``prompt=select_account`` / ``consent``). It answers a request the
authorization endpoint has already verified and parked
(``authorization.create_pending(awaiting_choice=True)``), and every answer
either releases that request for ``resume`` - which re-runs every check before
any code is minted - or spends it. Nothing here mints a code, and nothing here
reads a destination from the browser: the product's sign-in page and its
redirect URI both come from the registered manifest.

**RP-initiated sign-out** (OpenID Connect RP-Initiated Logout 1.0) lets a
product offer "sign out of Workeasy360 too". The product sends the browser to
``end_session_endpoint`` with its ``client_id`` and one of its registered
``post_logout_redirect_uris``; Workeasy360 asks the person to confirm (a GET
must never sign anyone out - that is logout CSRF), and returns the browser to
exactly that registered URI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from django.core import signing

from workeasy360.contracts.enums import SignInPreference, StrEnum
from workeasy360.platform.ecosystem import authorization, protocol, sign_in
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)


class Choice(StrEnum):
    """What a person can answer on the access chooser."""

    #: Continue with the Workeasy360 account already signed in.
    WORKEASY360 = "workeasy360"
    #: Use the product's own sign-in page instead (only where it has one).
    PRODUCT = "product"
    #: Sign out of Workeasy360 and sign in as someone else.
    SWITCH_ACCOUNT = "switch_account"
    #: Go back to the product without signing in (``access_denied``).
    CANCEL = "cancel"


@dataclass(frozen=True, slots=True)
class ChooserFacts:
    """What the chooser page may show: display facts only - no redirect URI,
    no state, no PKCE value, no client id."""

    product_code: str
    display_name: str
    organization_id: UUID | None
    product_login_available: bool
    #: The person's own per-product setting, so "Remember my choice" can show
    #: what is already remembered.
    override: SignInPreference


def chooser_facts(*, token: str, user_id: Any) -> ChooserFacts | None:
    """The facts for a parked request still awaiting this person's answer,
    or ``None`` - unknown, expired, spent, already answered or someone else's
    are one answer."""
    parked = authorization.find_pending(token=token)
    if parked is None or not parked.awaiting_choice or parked.user_id is None:
        return None
    if not parked.bound_to(user_id):
        return None
    integration = parked.request.integration
    _decision, offered = sign_in.decision_at_authorize(user_id, integration)
    _default, overrides = sign_in.preferences_of(user_id)
    manifest = integration.manifest_obj
    return ChooserFacts(
        product_code=integration.product_code,
        display_name=manifest.display_name or integration.product_code,
        # What the product named, else this person's only organization - shown
        # to them, never stored on the request (ADR-044 item 16).
        organization_id=authorization.organization_for(
            user_id=user_id, named=parked.request.organization_id
        ),
        product_login_available=offered.product_login_available,
        override=overrides.get(integration.product_code, SignInPreference.INHERIT),
    )


@dataclass(frozen=True, slots=True)
class ChoiceOutcome:
    """Where the browser goes next. Exactly one destination is set."""

    choice: Choice
    #: ``workeasy360``: this token is now resumable. ``switch_account``: a
    #: fresh, unbound token to resume after the next sign-in.
    resume_token: str = ""
    #: ``product``: the product's registered sign-in page.
    product_login_url: str = ""
    #: ``cancel``: the verified request, for the ``access_denied`` redirect.
    request: authorization.AuthorizationRequest | None = None


def answer(
    *,
    token: str,
    user_id: Any,
    choice: str,
    remember: bool,
    ip_prefix: str = "",
    device_label: str = "",
) -> ChoiceOutcome | None:
    """Apply the person's answer. ``None`` is the one failure answer (unknown
    or spent token, someone else's, an unknown choice, or a choice this
    product does not offer)."""
    try:
        picked = Choice(str(choice))
    except ValueError:
        return None
    parked = authorization.find_pending(token=token)
    if (
        parked is None
        or not parked.awaiting_choice
        or parked.user_id is None
        or not parked.bound_to(user_id)
    ):
        return None
    integration = parked.request.integration
    product_code = integration.product_code
    where = {"ip_prefix": ip_prefix, "device_label": device_label}

    if picked is Choice.WORKEASY360:
        # The product may have lost its credential since the request was parked:
        # never release a request for a code it could not redeem.
        _decision, offered = sign_in.decision_at_authorize(user_id, integration)
        if not offered.sso_available:
            return None
        if not authorization.release_choice(parked):
            return None
        if remember:
            sign_in.remember_choice(
                user_id, product_code, SignInPreference.AUTO_SSO, **where
            )
        outcome = ChoiceOutcome(choice=picked, resume_token=token)
    elif picked is Choice.PRODUCT:
        login_url = integration.manifest_obj.product_login_url
        _decision, offered = sign_in.decision_at_authorize(user_id, integration)
        if not offered.product_login_available or not login_url:
            return None
        if not authorization.claim_pending(parked):
            return None
        if remember:
            sign_in.remember_choice(
                user_id, product_code, SignInPreference.PRODUCT_LOGIN, **where
            )
        outcome = ChoiceOutcome(choice=picked, product_login_url=login_url)
    elif picked is Choice.SWITCH_ACCOUNT:
        if not authorization.claim_pending(parked):
            return None
        # The same verified request, parked again unbound: whoever signs in
        # next resumes it, and ``resume`` runs every check for *them*.
        fresh = authorization.create_pending(parked.request)
        outcome = ChoiceOutcome(choice=picked, resume_token=fresh.token)
    else:
        if not authorization.claim_pending(parked):
            return None
        outcome = ChoiceOutcome(choice=picked, request=parked.request)

    logger.info(
        "ecosystem.sso.choice",
        product_code=product_code,
        choice=picked.value,
        remember=bool(remember) and picked in (Choice.WORKEASY360, Choice.PRODUCT),
    )
    metrics.increment("ecosystem_sso_choice_total", choice=picked.value)
    return outcome


# --- RP-initiated sign-out ---------------------------------------------------

#: The sealed request survives one confirmation page, no longer.
LOGOUT_REQUEST_MAX_AGE_S: Final = 600
_LOGOUT_SALT: Final = "workeasy360.ecosystem.end_session"


@dataclass(frozen=True, slots=True)
class LogoutRequest:
    """A verified sign-out request: a registered client and one of its
    registered post-logout URIs, matched by exact string equality."""

    client_id: str
    product_code: str
    display_name: str
    post_logout_redirect_uri: str
    state: str

    @property
    def return_url(self) -> str:
        """The registered URI, with ``state`` echoed when the product sent
        one. Safe to append ``?``: a registered URI never carries a query
        (the manifest validator refuses one)."""
        from urllib.parse import urlencode

        if not self.state:
            return self.post_logout_redirect_uri
        return f"{self.post_logout_redirect_uri}?{urlencode({'state': self.state})}"


def logout_request(params: Any) -> LogoutRequest:
    """Verify an ``end_session_endpoint`` request. Every failure raises an
    ``invalid_request`` that the caller **renders**: until the client and the
    URI are both verified there is nowhere safe to send the browser - the
    same rule as the authorization endpoint's first phase."""
    from workeasy360.platform.ecosystem.models import ProductIntegration

    client_id = str(params.get("client_id") or "")
    uri = str(params.get("post_logout_redirect_uri") or "")
    state = str(params.get("state") or "")
    if not client_id or not uri:
        raise protocol.OAuthError(
            protocol.INVALID_REQUEST,
            "client_id and post_logout_redirect_uri are required.",
        )
    if len(state) > 512:
        raise protocol.OAuthError(protocol.INVALID_REQUEST, "state is too long.")
    integration = ProductIntegration.objects.filter(client_id=client_id).first()
    if integration is None or not integration.status_enum.can_authenticate:
        raise protocol.OAuthError(protocol.INVALID_REQUEST, "Unknown or unusable client.")
    manifest = integration.manifest_obj
    if not manifest.permits_post_logout_redirect(uri):
        metrics.increment("ecosystem_invalid_post_logout_redirect_total")
        raise protocol.OAuthError(
            protocol.INVALID_REQUEST,
            "post_logout_redirect_uri is not registered for this client.",
        )
    return LogoutRequest(
        client_id=client_id,
        product_code=integration.product_code,
        display_name=manifest.display_name or integration.product_code,
        post_logout_redirect_uri=uri,
        state=state,
    )


def seal(request: LogoutRequest) -> str:
    """An opaque, signed, short-lived handle for the confirmation page. Not
    a credential: it can only ever say "return to this registered URI"."""
    return signing.dumps(
        {
            "c": request.client_id,
            "u": request.post_logout_redirect_uri,
            "s": request.state,
        },
        salt=_LOGOUT_SALT,
        compress=True,
    )


def unseal(handle: str) -> LogoutRequest | None:
    """Open a sealed request and re-verify it against the *live* registry
    (a URI unregistered since is refused). ``None`` for anything invalid."""
    try:
        raw = signing.loads(handle, salt=_LOGOUT_SALT, max_age=LOGOUT_REQUEST_MAX_AGE_S)
    except signing.BadSignature:
        return None
    if not isinstance(raw, dict):
        return None
    try:
        return logout_request(
            {
                "client_id": raw.get("c"),
                "post_logout_redirect_uri": raw.get("u"),
                "state": raw.get("s"),
            }
        )
    except protocol.OAuthError:
        return None

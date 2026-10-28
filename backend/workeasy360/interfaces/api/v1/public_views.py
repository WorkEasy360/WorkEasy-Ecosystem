"""Public and account-level endpoints of the partner channel (Phase 7).

Three kinds, kept apart on purpose:

- **Public and unauthenticated**: host branding lookup and referral capture.
  Both are rate-limited by client address, accept a tightly bounded input and
  return nothing that identifies a customer, a partner id or any authority.
  Neither is CSRF-exempt: there is no reason to be, and the frontend obtains
  its cookie from ``/auth/session`` like every other write.
- **Account-level, authenticated**: accepting an owner invitation and
  converting a referral. The subject is always the *session's* user, never a
  value from the request.
"""

from __future__ import annotations

import secrets
from urllib.parse import urlencode
from uuid import UUID

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from workeasy360.commercial.commissions import referrals
from workeasy360.commercial.partners import activation, branding, customers, domains
from workeasy360.interfaces.api.v1 import commercial_views as legacy
from workeasy360.interfaces.api.v1.auth_views import establish_session
from workeasy360.interfaces.api.v1.control_plane_http import authenticated_user, limited

_body = legacy._body


@require_http_methods(["GET"])
def branding_view(request: HttpRequest) -> JsonResponse:
    """Presentation metadata for a host, or the default.

    The host arrives as an explicit query parameter from the frontend server
    and is treated as **untrusted text**: it is canonicalised, looked up among
    *active, ownership-verified* domains only, and any failure - malformed
    host, unknown host, no brand, cache down - is the same "use the default"
    answer with a 200. It never decides who anyone is: the response carries no
    partner, tenant or capability, and nothing that reads it authorizes on it
    (Task 35, 118, 119).
    """
    limited_response = limited(request, "public_branding")
    if limited_response is not None:
        return limited_response
    brand = domains.public_brand_for_host(request.GET.get("host"))
    if brand is None:
        return JsonResponse({"brand": None})
    payload = dict(brand)
    has_logo = payload.pop("logo_file_id", None) is not None
    payload["logo_url"] = (
        "/api/v1/public/branding/logo?" + urlencode({"host": request.GET.get("host", "")})
        if has_logo
        else None
    )
    response = JsonResponse({"brand": payload})
    # Never shared between hosts or users by an intermediary cache.
    response["Cache-Control"] = "no-store"
    response["Vary"] = "Host"
    return response


@require_http_methods(["GET"])
@never_cache
def edge_tls_allowed_view(request: HttpRequest) -> JsonResponse:
    """May the edge obtain a TLS certificate for ``?domain=``? (Phase 12, ADR-039.)

    The edge (``deploy/edge/Caddyfile``) issues certificates on demand for
    partner custom domains, and asks this first. Yes for the platform's own
    canonical host and for a partner domain that would serve a brand *right
    now* - active, ownership-verified, partner active, both white-label
    capabilities held - and no (404) for everything else. So a certificate is
    never issued for an arbitrary name pointed at the edge, a domain whose
    partner is terminated or whose control has lapsed stops getting renewals,
    and a stale DNS record left pointing at the edge serves nothing.

    Answers the same thing the public branding lookup already discloses; the
    edge refuses to proxy this path from outside, so in production only the
    edge itself reaches it.
    """
    from workeasy360.platform.identity import portals as portal_links

    raw = request.GET.get("domain", "")
    # The platform's own hosts: the portals and the API (ADR-043), or the one
    # frontend host in single-host mode. Hosts, compared without a port.
    own = {host.split(":")[0] for host in portal_links.platform_hosts()}
    if raw and raw.strip().lower().rstrip(".") in own:
        return JsonResponse({"allowed": True})
    if domains.public_brand_for_host(raw) is not None:
        return JsonResponse({"allowed": True})
    return JsonResponse({"allowed": False}, status=404)


@require_http_methods(["GET"])
def branding_logo_view(request: HttpRequest) -> HttpResponse:
    """The logo of the brand active on ``host``, as an image, or 404.

    Same-origin so the CSP's ``img-src 'self'`` covers it. Resolved exactly as
    the brand itself is - active, ownership-verified domains only - so it can
    never be used to fetch an arbitrary file: there is no file id in the
    request, only a host.
    """
    limited_response = limited(request, "public_branding")
    if limited_response is not None:
        return limited_response
    brand = domains.public_brand_for_host(request.GET.get("host"))
    logo = branding.public_logo(brand.get("logo_file_id") if brand else None)
    if logo is None:
        return HttpResponse(status=404)
    data, content_type = logo
    response = HttpResponse(data, content_type=content_type)
    # ``private``: a signed-in browser's request also refreshes its session
    # cookie (the sliding idle window), so this response can carry a
    # Set-Cookie - never something a shared cache may store and replay. The
    # browser still caches the image for five minutes.
    response["Cache-Control"] = "private, max-age=300"
    response["Vary"] = "Host"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Content-Disposition"] = "inline"
    return response


@require_http_methods(["POST"])
def referral_capture_view(request: HttpRequest) -> JsonResponse:
    """Record that a visitor followed a referral code. Returns an opaque token."""
    limited_response = limited(request, "public_referral")
    if limited_response is not None:
        return limited_response
    visitor = request.COOKIES.get(settings.REFERRAL_VISITOR_COOKIE_NAME, "")[:64]
    if not 16 <= len(visitor) <= 64:
        visitor = secrets.token_urlsafe(24)
    held = request.COOKIES.get(settings.REFERRAL_COOKIE_NAME, "")[:200]
    captured = referrals.capture_visit(
        _body(request).get("code"), visitor_id=visitor, held_token=held
    )
    if captured is None:
        # One shape for unknown, disabled and ineligible codes.
        return JsonResponse({"code": "not_found", "detail": "Not found."}, status=404)
    token, destination = captured
    # ``destination`` is a path the server derived (ADR-053): never a URL.
    response = JsonResponse({"token": token, "destination": destination}, status=201)
    response.set_cookie(
        settings.REFERRAL_VISITOR_COOKIE_NAME,
        visitor,
        max_age=settings.REFERRAL_VISITOR_COOKIE_MAX_AGE,
        path="/",
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="Lax",
    )
    if token == held:
        # ADR-054 first touch: the browser keeps the referral it already holds;
        # this click was counted for its link, and the cookie is left alone.
        return response
    # ADR-052: the same opaque token as an HttpOnly cookie, so the referral is
    # still there when the verification link opens in another tab. It lives as
    # long as the programme's attribution window (ADR-054).
    response.set_cookie(
        settings.REFERRAL_COOKIE_NAME,
        token,
        max_age=int(referrals.capture_window().total_seconds()),
        path="/",
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="Lax",
    )
    return response


@require_http_methods(["POST"])
def referral_convert_view(request: HttpRequest) -> JsonResponse:
    """Attribute the caller's new organization to the referral they present.

    Best-effort by design: the signup flow calls it after the organization
    exists and ignores the outcome, so a referral-subsystem outage can never
    block signup (Task 120). ``converted: false`` covers every reason a
    conversion is not made, without saying which.
    """
    user = authenticated_user(request)
    limited_response = limited(request, "referral_convert", user=user)
    if limited_response is not None:
        return limited_response
    body = _body(request)
    try:
        organization_id = UUID(str(body.get("organization_id")))
    except ValueError:
        return JsonResponse({"converted": False})
    # The token the page kept, else the cookie the referral link set (ADR-052).
    token = _token(body) or request.COOKIES.get(settings.REFERRAL_COOKIE_NAME, "")[:200]
    converted = referrals.convert_referral(
        user_id=user.id, token=token, organization_id=organization_id
    )
    response = JsonResponse({"converted": converted})
    if settings.REFERRAL_COOKIE_NAME in request.COOKIES:
        # Used once: converted, or no longer convertible for this sign-up.
        response.delete_cookie(settings.REFERRAL_COOKIE_NAME, path="/", samesite="Lax")
    return response


def _token(body: dict) -> str:
    token = body.get("token")
    return token if isinstance(token, str) and len(token) <= 200 else ""


def _visitor(request: HttpRequest):
    user = getattr(request, "user", None)
    return user if user is not None and user.is_authenticated else None


@require_http_methods(["POST"])
@never_cache
def owner_invitation_preview_view(request: HttpRequest) -> JsonResponse:
    """What an owner-activation link is for. POST, not GET, so the token is in
    the body and never in a URL - and so never in an access log."""
    blocked = limited(request, "owner_invitation_public")
    if blocked is not None:
        return blocked
    return JsonResponse(
        activation.preview(_token(_body(request)), signed_in_user=_visitor(request))
    )


@require_http_methods(["POST"])
@never_cache
def owner_invitation_accept_view(request: HttpRequest) -> JsonResponse:
    """Claim the account: as the signed-in invitee, or by choosing a name and a
    password (which proves the mailbox and signs the person in here). An address
    that already has a password must sign in first - the answer then is
    ``sign_in_required``, never a password change."""
    blocked = limited(request, "owner_invitation_public")
    if blocked is not None:
        return blocked
    body = _body(request)
    visitor = _visitor(request)
    password = body.get("password")
    name = body.get("full_name")
    result = activation.claim(
        _token(body),
        signed_in_user=visitor,
        password=password if isinstance(password, str) else None,
        full_name=name if isinstance(name, str) else None,
    )
    if visitor is None:
        establish_session(request, result.user, password=str(password or ""))
    return JsonResponse(
        {
            "organization_slug": result.organization_slug,
            "organization_id": str(result.organization_id),
            "credentials_set": result.credentials_set,
        }
    )


@require_http_methods(["GET"])
def invitations_view(request: HttpRequest) -> JsonResponse:
    user = authenticated_user(request)
    return JsonResponse({"results": customers.pending_invites_for_user(user.id)})


@require_http_methods(["POST"])
def invitation_accept_view(request: HttpRequest, organization_id: UUID) -> JsonResponse:
    user = authenticated_user(request)
    customers.accept_owner_invite(user_id=user.id, organization_id=organization_id)
    return JsonResponse({"organization_id": str(organization_id), "accepted": True})


@require_http_methods(["GET"])
def storefront_view(request: HttpRequest, slug: str) -> JsonResponse:
    """A white-label partner's branded Store (ADR-053): its brand and the
    products it may sell, at its prices. Presentation only - the response
    carries no partner id or capability, and checkout re-verifies everything."""
    from workeasy360.commercial.partners import storefront

    limited_response = limited(request, "public_branding")
    if limited_response is not None:
        return limited_response
    payload = storefront.public_storefront(slug)
    if payload is None:
        return JsonResponse({"code": "not_found", "detail": "Not found."}, status=404)
    brand = payload["brand"]
    brand["logo_url"] = (
        f"/api/v1/public/storefront/{payload['slug']}/logo"
        if brand.pop("has_logo")
        else None
    )
    response = JsonResponse(payload)
    response["Cache-Control"] = "no-store"
    return response


@require_http_methods(["GET"])
def storefront_logo_view(request: HttpRequest, slug: str) -> HttpResponse:
    """The storefront's brand logo, same-origin (the CSP allows only 'self')."""
    from workeasy360.commercial.partners import storefront

    found = branding.public_logo(storefront.logo_file_id(slug))
    if found is None:
        return HttpResponse(status=404)
    data, content_type = found
    response = HttpResponse(data, content_type=content_type)
    response["Cache-Control"] = "public, max-age=300"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Content-Disposition"] = "inline"
    return response

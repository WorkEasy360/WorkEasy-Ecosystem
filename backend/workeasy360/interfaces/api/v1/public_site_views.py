"""What the public site calls (Phase 9), all anonymous: its configuration,
the product catalog, signup, email verification and password reset.

Reads return nothing that identifies a customer, a user or an authority, and
are rate-limited by client address. Every write is rate-limited by client address and, where it is aimed at an
inbox, by a hash of the address. None is CSRF-exempt: the frontend obtains its
cookie from ``/auth/session`` like every other write. Tokens travel in the
request **body**, never in a URL, so they never reach an access log.

The answers are deliberately uniform - see ``platform.administration.self_service``.
"""

from __future__ import annotations

from uuid import UUID

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from workeasy360.commercial.commissions import referrals
from workeasy360.commercial.controlplane.product_manager import (
    MEDIA_MAX_BYTES as PRODUCT_MEDIA_MAX_BYTES,
)
from workeasy360.interfaces.api.v1 import commercial_views as legacy
from workeasy360.interfaces.api.v1.auth_views import establish_session
from workeasy360.interfaces.api.v1.control_plane_http import limited
from workeasy360.platform.administration import self_service, site

_body = legacy._body

_ACCEPTED = {
    "status": "accepted",
    "detail": "If the address can receive it, an email is on its way.",
}


def _text(body: dict, key: str) -> str:
    value = body.get(key)
    return value if isinstance(value, str) else ""


@require_http_methods(["GET"])
def site_view(request: HttpRequest) -> JsonResponse:
    """What the public site may know: whether signup is open, where to write,
    the catalog's default currency. Nothing that identifies anyone."""
    blocked = limited(request, "public_catalog")
    if blocked is not None:
        return blocked
    return JsonResponse(site.public_site())


@require_http_methods(["POST"])
@never_cache
def signup_view(request: HttpRequest) -> JsonResponse:
    body = _body(request)
    email = _text(body, "email")
    blocked = limited(request, "signup", account_email=email or None)
    if blocked is not None:
        return blocked
    self_service.sign_up(
        first_name=body.get("first_name"),
        last_name=body.get("last_name"),
        email=email,
        password=body.get("password"),
        company_name=body.get("company_name"),
    )
    return JsonResponse(_ACCEPTED, status=202)


@require_http_methods(["POST"])
@never_cache
def verify_email_view(request: HttpRequest) -> JsonResponse:
    """Finish a signup: the emailed token and the password, together. Signs
    the person in on success."""
    blocked = limited(request, "verify_email")
    if blocked is not None:
        return blocked
    body = _body(request)
    password = _text(body, "password")
    result = self_service.verify_email(_text(body, "token"), password)
    establish_session(request, result.user, password=password)
    # ADR-053: the Registrations step of a referral - the visitor who followed
    # a link now has an account. Best-effort; it never affects the sign-up.
    referral_token = request.COOKIES.get(settings.REFERRAL_COOKIE_NAME, "")[:200]
    if referral_token:
        referrals.record_registration(referral_token)
    return JsonResponse(
        {
            "organization_slug": result.organization_slug,
            "organization_id": result.organization_id,
        }
    )


@require_http_methods(["POST"])
@never_cache
def resend_verification_view(request: HttpRequest) -> JsonResponse:
    body = _body(request)
    email = _text(body, "email")
    blocked = limited(request, "verify_resend", account_email=email or None)
    if blocked is not None:
        return blocked
    self_service.resend_verification(email)
    return JsonResponse(_ACCEPTED, status=202)


@require_http_methods(["POST"])
@never_cache
def forgot_password_view(request: HttpRequest) -> JsonResponse:
    body = _body(request)
    email = _text(body, "email")
    blocked = limited(request, "password_reset", account_email=email or None)
    if blocked is not None:
        return blocked
    self_service.request_password_reset(email)
    return JsonResponse(_ACCEPTED, status=202)


@require_http_methods(["POST"])
@never_cache
def reset_password_view(request: HttpRequest) -> JsonResponse:
    """Set a new password from the emailed link. Every session ends; the
    person signs in again with the new password."""
    blocked = limited(request, "password_reset_confirm")
    if blocked is not None:
        return blocked
    body = _body(request)
    self_service.reset_password(_text(body, "token"), _text(body, "password"))
    return JsonResponse({"status": "password_reset"})


# --- the public catalog -------------------------------------------------------------


@require_http_methods(["GET"])
def catalog_view(request: HttpRequest) -> JsonResponse:
    """Every public product with its plans, bundles and list prices, in the
    requested currency if it has prices, else the site default."""
    from workeasy360.commercial.subscriptions.marketplace import public_catalog

    blocked = limited(request, "public_catalog")
    if blocked is not None:
        return blocked
    return JsonResponse(public_catalog(currency=request.GET.get("currency")))


@require_http_methods(["GET"])
def catalog_product_view(request: HttpRequest, slug: str) -> JsonResponse:
    from workeasy360.commercial.subscriptions.marketplace import public_product

    blocked = limited(request, "public_catalog")
    if blocked is not None:
        return blocked
    found = public_product(slug, currency=request.GET.get("currency"))
    if found is None:
        return JsonResponse({"code": "not_found", "detail": "Not found."}, status=404)
    return JsonResponse(found)


@require_http_methods(["GET"])
def product_media_view(request: HttpRequest, slug: str, media_id: UUID) -> HttpResponse:
    """One listing image of a Store product (ADR-053): a published product's
    logo, banner or screenshot, for the Store, product pages and the partners'
    Marketing Materials. Only a product on the customer Store, only an image
    the files platform scanned clean; anything else is a 404."""
    from workeasy360.commercial.catalog import selectors as catalog_selectors
    from workeasy360.platform.authz.control_plane import acting_scope, platform_context
    from workeasy360.platform.files import selectors as file_selectors

    file_id = catalog_selectors.public_media_file_id(slug, media_id)
    found = None
    if file_id is not None:
        # Listing images are the platform's own files (uploaded by staff).
        with acting_scope(platform_context(user_id=UUID(int=0))):
            found = file_selectors.servable_content(
                file_id, max_bytes=PRODUCT_MEDIA_MAX_BYTES
            )
    if found is None:
        return HttpResponse(status=404)
    data, content_type = found
    response = HttpResponse(data, content_type=content_type)
    response["Cache-Control"] = "public, max-age=300"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    download = request.GET.get("download") == "1"
    response["Content-Disposition"] = "attachment" if download else "inline"
    return response

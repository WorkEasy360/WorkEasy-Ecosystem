"""A partner-portal session opened by Login as Guest can only read (ADR-062).

Login as Guest opens a session on the partner portal's host for a staff member
(``commercial.partners.guest``). That session carries the guest view's id
(``SESSION_GUEST_SESSION_KEY``) and nothing of the staff member's Admin powers:

- **It ends with the view.** Its guest view is read on every request; once the
  view is ended, revoked or expired, the session is flushed and the request is
  anonymous. (``SessionStore.load`` also refuses it after the view's end time.)
- **It never writes.** Every unsafe method is refused here, on every endpoint -
  partner, account, sign-in - before CSRF, the view or any service runs. The
  only writes it may make end it: "Exit Guest Mode" and signing out.
- **It reaches one partner.** ``control_plane_http.partner_view`` answers it
  with the guest context of exactly the viewed partner and refuses every other
  partner, even one the staff member belongs to.
- **It is not a staff session.** ``control_plane_http.staff_context`` refuses
  it, so no staff endpoint answers it on any host.

Defence in depth: the portal adapter already refuses a guest's writes, the
resolver already gives a guest the viewer tier only, and the admin API is not
served on the partner host at all (``portal_surface``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest, HttpResponse, JsonResponse

from workeasy360.platform.identity.sessions import SESSION_GUEST_SESSION_KEY

_SAFE_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS"})

#: The writes a guest-only session may make: the two that end it.
ENDING_WRITES: Final = frozenset(
    {"/api/v1/partner/guest-view/end", "/api/v1/auth/logout"}
)


def guest_session_id(request: HttpRequest) -> str | None:
    """The guest view this request's session exists for, if it is one."""
    session = getattr(request, "session", None)
    if session is None:
        return None
    marker = session.get(SESSION_GUEST_SESSION_KEY)
    return str(marker) if marker else None


class GuestSessionMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        marker = guest_session_id(request)
        if marker is None:
            return self.get_response(request)
        from workeasy360.commercial.partners import guest

        user = getattr(request, "user", None)
        view = (
            guest.live(marker, user.id)
            if user is not None and user.is_authenticated and user.is_staff
            else None
        )
        if view is None:
            # The view is over (ended, revoked, expired) or the account changed:
            # nothing of it stays signed in.
            request.session.flush()
            request.user = AnonymousUser()
            return self.get_response(request)
        request.w360_guest_view = view  # type: ignore[attr-defined]
        if request.method not in _SAFE_METHODS and request.path not in ENDING_WRITES:
            from workeasy360.contracts.errors import PermissionDenied

            # The same answer the portal adapter gives a guest's write.
            denied = PermissionDenied(
                "Guest Mode is read-only.",
                permission="partner.guest",
                reason="guest_read_only",
            )
            return JsonResponse(denied.as_dict(), status=denied.http_status)
        return self.get_response(request)

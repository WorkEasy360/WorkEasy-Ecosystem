"""Immediate session revocation via ``User.session_epoch``.

``User.revoke_sessions()`` has existed since Phase 1, documented as the
mechanism behind "sessions are server-side rather than stateless JWTs" - but
nothing read the value back until now. A revoked epoch that nothing checks
is not a control, it is a column. This is the check.

Placed immediately after ``AuthenticationMiddleware`` (which resolves
``request.user`` from the session) so that every view downstream - platform
and product-facing alike - sees the corrected, logged-out state rather than
having to know to check the epoch itself.

Also here: ``SlidingSessionMiddleware``, Django's session middleware with the
idle window refreshed at most once per ``SESSION_REFRESH_INTERVAL_S``.
"""

from __future__ import annotations

from collections.abc import Callable

from django.contrib.auth import logout as django_logout
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpRequest, HttpResponse

#: The session key the login view writes the epoch under, at the moment it
#: establishes the session - see platform.identity views.
SESSION_EPOCH_KEY = "w360_session_epoch"


class SessionEpochMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            recorded = request.session.get(SESSION_EPOCH_KEY)
            live = getattr(user, "session_epoch", None)
            # A session with *no* recorded epoch predates this control - a
            # session already live when this shipped, or one established by
            # something other than the login view (Django's test client
            # `force_login`, chief among them). Treated as current rather
            # than revoked: the alternative is every such session logging
            # itself out on its very next request, which is a self-inflicted
            # mass sign-out on deploy day, not a security improvement. A
            # session the login view itself established always carries a
            # recorded epoch, so real revocation is enforced from the moment
            # each session is actually created under this control.
            if recorded is not None and recorded != live:
                # A mismatch means this session was issued before the most
                # recent revoke_sessions() call (a password reset, a forced
                # sign-out, an account disable). django.contrib.auth.logout
                # flushes the session outright - not merely clearing the
                # auth keys - so a stolen session cookie is fully dead, not
                # merely logged out of identity while still carrying
                # whatever else had been written into it.
                django_logout(request)
        return self.get_response(request)


class SlidingSessionMiddleware(SessionMiddleware):
    """Django's ``SessionMiddleware``, sliding the idle window at most once per
    ``SESSION_REFRESH_INTERVAL_S`` instead of on every request (2026-10 audit).

    ``SESSION_SAVE_EVERY_REQUEST`` made every request - reads, the public
    catalog, a launch - end in an ``UPDATE identity_session`` and a commit:
    measured on staging at ~3 ms and one pooler transaction per request. A
    session that changed still saves exactly as before (Django's own
    ``modified`` rule); an unchanged one saves only when its row's last save
    is older than the interval. Nothing else reads the per-request write:
    revocation is enforced when the row is loaded, the epoch and the absolute
    lifetime are checked on every request, and ``last_activity_at`` only feeds
    the "where am I signed in" list.
    """

    def process_response(
        self, request: HttpRequest, response: HttpResponse
    ) -> HttpResponse:
        session = getattr(request, "session", None)
        # Another engine (a test swapping SESSION_ENGINE) has no refresh_due
        # and keeps Django's behaviour: saved when modified.
        refresh_due = getattr(session, "refresh_due", None)
        if session is not None and not session.modified and refresh_due and refresh_due():
            session.modified = True
        return super().process_response(request, response)

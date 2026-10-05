"""Per-request overhead removed by the 2026-10 architecture and performance
audit, pinned so it cannot creep back.

Measured on the staging stack before the change, every request - reads, the
public catalog, a launch - ended in ``UPDATE identity_session`` and a commit
(``SESSION_SAVE_EVERY_REQUEST``): ~3 ms and one pooler transaction each. The
sliding idle window now moves at most once per ``SESSION_REFRESH_INTERVAL_S``
(``platform.identity.middleware.SlidingSessionMiddleware``). These tests pin
both halves: a read writes nothing, and the window still slides.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tests.extension import conftest as _extension

pytestmark = pytest.mark.django_db

# Re-exported by assignment (not import) so that using the fixture as a test
# argument is not a redefinition of an imported name.
login = _extension.login
PASSWORD = _extension.PASSWORD

SESSION = "/api/v1/auth/session"


def _writes(queries: CaptureQueriesContext) -> list[str]:
    return [
        q["sql"]
        for q in queries.captured_queries
        if q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]


def _age_session(session_key: str, seconds: int) -> None:
    """Make the row look as if it was last saved ``seconds`` ago."""
    from django.conf import settings

    from workeasy360.platform.identity.models import UserSession

    then = timezone.now() - timedelta(seconds=seconds)
    UserSession.objects.filter(session_key=session_key).update(
        last_activity_at=then,
        expire_date=then + timedelta(seconds=settings.SESSION_COOKIE_AGE),
    )


def test_a_read_on_a_recently_saved_session_writes_nothing(user_factory, login):
    client = login(user_factory(password=PASSWORD))

    with CaptureQueriesContext(connection) as queries:
        response = client.get(SESSION)

    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert _writes(queries) == []
    # Nothing saved, so no refreshed cookie either.
    assert "w360_session" not in response.cookies


def test_the_idle_window_slides_once_the_interval_has_passed(
    user_factory, login, settings
):
    from workeasy360.platform.identity.models import UserSession

    client = login(user_factory(password=PASSWORD))
    key = client.session.session_key
    _age_session(key, settings.SESSION_REFRESH_INTERVAL_S + 5)
    before = timezone.now()

    response = client.get(SESSION)

    assert response.json()["authenticated"] is True
    row = UserSession.objects.get(session_key=key)
    assert row.last_activity_at >= before
    assert row.expire_date >= before + timedelta(seconds=settings.SESSION_COOKIE_AGE)
    # The browser's cookie slides with the row.
    assert response.cookies["w360_session"]["max-age"] == settings.SESSION_COOKIE_AGE

    # ...and the very next request, inside the interval, writes nothing again.
    with CaptureQueriesContext(connection) as queries:
        client.get(SESSION)
    assert _writes(queries) == []


def test_a_session_never_outlives_its_idle_window(user_factory, login, settings):
    """The throttle can only end a session early, never late: a read inside
    the interval does not push the expiry forward, and a row past its expiry
    no longer loads."""
    from workeasy360.platform.identity.models import UserSession

    client = login(user_factory(password=PASSWORD))
    key = client.session.session_key
    expiry = UserSession.objects.get(session_key=key).expire_date

    client.get(SESSION)
    assert UserSession.objects.get(session_key=key).expire_date == expiry

    UserSession.objects.filter(session_key=key).update(
        expire_date=timezone.now() - timedelta(seconds=1)
    )
    assert client.get(SESSION).json()["authenticated"] is False


def test_a_changed_session_still_saves_at_once(user_factory, login):
    from django.http import HttpResponse
    from django.test import RequestFactory

    from workeasy360.platform.identity.middleware import SlidingSessionMiddleware
    from workeasy360.platform.identity.sessions import SessionStore

    client = login(user_factory(password=PASSWORD))
    key = client.session.session_key

    request = RequestFactory().get(SESSION)
    request.session = SessionStore(key)
    request.session["w360_probe"] = "changed"
    middleware = SlidingSessionMiddleware(lambda r: HttpResponse())
    response = middleware.process_response(request, HttpResponse())

    assert SessionStore(key)["w360_probe"] == "changed"
    assert "w360_session" in response.cookies


def test_an_interval_of_zero_saves_every_request(user_factory, login, settings):
    """The knob's lower bound is the old behaviour, exactly."""
    settings.SESSION_REFRESH_INTERVAL_S = 0
    client = login(user_factory(password=PASSWORD))

    with CaptureQueriesContext(connection) as queries:
        client.get(SESSION)

    assert any('"identity_session"' in sql for sql in _writes(queries))


def test_an_anonymous_visitor_with_a_dead_cookie_creates_no_session_row(client):
    """No refresh for a row that never loaded: nothing is written, and the
    dead cookie is cleared as before."""
    from workeasy360.platform.identity.models import UserSession

    client.cookies["w360_session"] = "x" * 32
    count = UserSession.objects.count()

    response = client.get(SESSION)

    assert response.json()["authenticated"] is False
    assert UserSession.objects.count() == count
    assert response.cookies["w360_session"].value == ""


def test_a_reused_connection_carries_no_tenant_between_requests(
    organization_factory, user_factory, client
):
    """Persistent connections (``CONN_MAX_AGE`` > 0, 2026-10 audit) make one
    connection serving request after request the normal case. Each request's
    tenant scope must leave nothing behind on it: alternate two organizations
    of the same member on one connection and read the variable in between."""
    from workeasy360.platform.tenancy.db import current_tenant_variable

    user = user_factory()
    org_a, _membership, _owner = organization_factory(owner=user)
    org_b, _membership, _owner = organization_factory(owner=user)
    client.force_login(user)

    for org in (org_a, org_b, org_a):
        response = client.get(f"/api/v1/orgs/{org.slug}/access")
        assert response.status_code == 200
        assert current_tenant_variable() is None

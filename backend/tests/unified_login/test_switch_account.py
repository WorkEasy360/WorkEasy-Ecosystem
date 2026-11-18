"""Switch Account (the unified-login brief 5.3 and 16C, ADR-066).

A portal's sign-out is that portal's (ADR-059), so Switch Account also ends
the central sign-in host's session - the one products are signed in with -
through a signed hand-over that can end only the switching person's own
session there."""

from __future__ import annotations

import pytest
from django.test import Client

from tests.unified_login.conftest import HostClient, log_in, signed_in_as

pytestmark = pytest.mark.django_db

SWITCH = "/api/v1/auth/switch-account"
FINISH = "/api/v1/auth/switch-account/finish"


def _handle(redirect_url: str) -> str:
    prefix = "https://auth.workeasy360.com/switch-account#handle="
    assert redirect_url.startswith(prefix), redirect_url
    from urllib.parse import unquote

    return unquote(redirect_url.removeprefix(prefix))


# --- one host (development, and any deployment without portal hosts) ---------------------------


def test_switching_on_one_host_signs_out_and_opens_the_sign_in_page(person):
    rahul = person()
    browser = Client()
    assert log_in(browser, rahul.email).status_code == 200
    answer = browser.post(SWITCH, content_type="application/json")
    assert answer.status_code == 200
    assert answer.json()["redirect_url"].endswith("/login?switched=1")
    assert signed_in_as(browser) is None


def test_switching_while_signed_out_just_opens_the_sign_in_page(db):
    answer = Client().post(SWITCH, content_type="application/json")
    assert answer.status_code == 200
    assert answer.json()["redirect_url"].endswith("/login?switched=1")


def test_switching_needs_the_csrf_token(person):
    """A link from another site cannot sign anyone out with it."""
    rahul = person()
    browser = Client(enforce_csrf_checks=True)
    browser.force_login(rahul)
    assert browser.post(SWITCH, content_type="application/json").status_code == 403
    assert signed_in_as(browser) == rahul.email


# --- portal hosts ----------------------------------------------------------------------------------


def test_switching_on_the_customer_portal_also_ends_the_central_sign_in(
    portals_on, person
):
    rahul = person()
    app, auth = HostClient("app"), HostClient("auth")
    assert log_in(app, rahul.email).status_code == 200
    assert log_in(auth, rahul.email).status_code == 200

    started = app.post(SWITCH)
    assert started.status_code == 200
    handle = _handle(started.json()["redirect_url"])
    assert signed_in_as(app) is None
    assert signed_in_as(auth) == rahul.email  # until the auth page finishes

    finished = auth.post(FINISH, {"handle": handle})
    assert finished.status_code == 200
    assert (
        finished.json()["redirect_url"] == "https://app.workeasy360.com/login?switched=1"
    )
    assert signed_in_as(auth) is None


def test_the_hand_over_never_signs_out_somebody_else(portals_on, person):
    rahul, priya = person(), person()
    app, auth = HostClient("app"), HostClient("auth")
    assert log_in(app, rahul.email).status_code == 200
    assert log_in(auth, priya.email).status_code == 200
    handle = _handle(app.post(SWITCH).json()["redirect_url"])
    finished = auth.post(FINISH, {"handle": handle})
    assert finished.status_code == 200
    assert signed_in_as(auth) == priya.email


@pytest.mark.parametrize("handle", ["", "forged", "x" * 600, None, 7])
def test_a_forged_hand_over_changes_nothing_and_answers_the_same(
    portals_on, person, handle
):
    rahul = person()
    auth = HostClient("auth")
    assert log_in(auth, rahul.email).status_code == 200
    finished = auth.post(FINISH, {"handle": handle})
    assert finished.status_code == 200
    assert (
        finished.json()["redirect_url"] == "https://app.workeasy360.com/login?switched=1"
    )
    assert signed_in_as(auth) == rahul.email


def test_an_expired_hand_over_changes_nothing(portals_on, person, monkeypatch):
    from workeasy360.interfaces.api.v1 import auth_views

    rahul = person()
    app, auth = HostClient("app"), HostClient("auth")
    assert log_in(app, rahul.email).status_code == 200
    assert log_in(auth, rahul.email).status_code == 200
    handle = _handle(app.post(SWITCH).json()["redirect_url"])
    monkeypatch.setattr(auth_views, "SWITCH_HANDLE_MAX_AGE_S", -1)
    auth.post(FINISH, {"handle": handle})
    assert signed_in_as(auth) == rahul.email


def test_each_half_is_served_only_where_it_belongs(portals_on, person):
    assert HostClient("auth").post(SWITCH).status_code == 404
    assert HostClient("app").post(FINISH, {"handle": "x"}).status_code == 404


def test_a_switch_started_on_the_store_returns_to_the_stores_sign_in(settings, person):
    from tests.portals.conftest import HOST, ORIGINS

    origins = {**ORIGINS, "store": "https://store.workeasy360.com"}
    settings.PORTALS_ENABLED = True
    settings.W360_PORTAL_ORIGINS = origins
    settings.ALLOWED_HOSTS = [*HOST.values(), "store.workeasy360.com"]
    settings.CSRF_TRUSTED_ORIGINS = [
        origins[n] for n in ("admin", "partner", "app", "auth", "store")
    ]
    settings.W360_FRONTEND_URL = origins["app"]
    rahul = person()
    store = Client()
    store.get("/api/v1/auth/session", HTTP_HOST="store.workeasy360.com", secure=True)
    signed = store.post(
        "/api/v1/auth/login",
        {"email": rahul.email, "password": "correct horse battery staple 42"},
        content_type="application/json",
        HTTP_HOST="store.workeasy360.com",
        secure=True,
    )
    assert signed.status_code == 200, signed.content
    started = store.post(
        SWITCH,
        content_type="application/json",
        HTTP_HOST="store.workeasy360.com",
        secure=True,
    )
    handle = _handle(started.json()["redirect_url"])
    finished = HostClient("auth").post(FINISH, {"handle": handle})
    assert (
        finished.json()["redirect_url"]
        == "https://store.workeasy360.com/login?switched=1"
    )

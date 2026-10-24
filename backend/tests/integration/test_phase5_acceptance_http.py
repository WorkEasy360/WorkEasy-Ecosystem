"""Phase 5 acceptance, chained over real HTTP (Task 66).

A brand-new customer, from sign-in to a launcher, through a real socket:
``live_server`` runs the full WSGI stack in a server thread, and the
"browser" here is ``urllib`` with a cookie jar that submits the CSRF token
the way the frontend does (read the ``w360_csrf`` cookie, echo it in
``X-CSRFToken``). No application service is called to *drive* the flow;
direct database reads appear only as after-the-fact assertions about what
the HTTP calls produced.

The one exception is marked where it happens: no customer-facing endpoint is
gated by a ``dangerous`` permission yet, so "a dangerous action needs only the
grant" is proven by resolving a tenant context from the *live session this
test's HTTP calls produced* and asking ``authz`` about
``platform.organization.delete``.

``transaction=True``: the server thread has its own connection, so the data
must genuinely commit for it to see anything.
"""

from __future__ import annotations

import http.cookiejar
import json
import urllib.error
import urllib.request
import uuid

import pytest

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.slow]

PASSWORD = "a long enough passphrase for tests"


class Browser:
    """A cookie-keeping HTTP client that behaves like the frontend."""

    def __init__(self, base_url: str) -> None:
        # 127.0.0.1, not "localhost": on Windows the latter resolves to ::1
        # first, and the IPv4-only live server makes every request wait out
        # an IPv6 connection attempt.
        self.base_url = base_url.replace("://localhost", "://127.0.0.1")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def cookie(self, name: str) -> str | None:
        return next((c.value for c in self.jar if c.name == name), None)

    def call(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        token = self.cookie("w360_csrf")
        if token:
            request.add_header("X-CSRFToken", token)
        try:
            with self.opener.open(request, timeout=30) as response:
                return response.status, _json(response.read())
        except urllib.error.HTTPError as error:
            return error.code, _json(error.read())

    def get(self, path: str):
        return self.call("GET", path)

    def post(self, path: str, body: dict | None = None):
        return self.call("POST", path, body or {})

    def delete(self, path: str):
        return self.call("DELETE", path)

    def sign_in(self, email: str) -> None:
        status, body = self.get("/api/v1/auth/session")
        assert status == 200
        assert self.cookie("w360_csrf"), "session endpoint must issue the CSRF cookie"
        status, body = self.post(
            "/api/v1/auth/login", {"email": email, "password": PASSWORD}
        )
        assert status == 200, body


def _json(raw: bytes):
    """JSON, or the raw text for the few non-API responses (Django's CSRF
    failure page)."""
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return raw.decode("utf-8", "replace")


def _permission_check(browser: Browser, slug: str, permission: str):
    """authz's verdict for the tenant context the *live* session resolves to.

    Loads the real session row the HTTP calls produced (by its cookie) and
    runs the production resolver over it - the same two calls the tenancy
    middleware makes - then asks authz. Returns ``None`` if allowed, else
    the exception.
    """
    from importlib import import_module

    from django.conf import settings
    from django.test import RequestFactory

    from workeasy360.platform.authz import services as authz
    from workeasy360.platform.identity.models import User
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import tenant_db_scope
    from workeasy360.platform.tenancy.resolution import resolve_tenant_from_path

    # The configured engine (the platform session registry since the Phase 8
    # extension), exactly as SessionMiddleware loads it.
    SessionStore = import_module(settings.SESSION_ENGINE).SessionStore
    session = SessionStore(session_key=browser.cookie("w360_session"))
    request = RequestFactory().get(f"/api/v1/orgs/{slug}/context")
    request.session = session
    request.user = User.objects.get(pk=session["_auth_user_id"])
    ctx = resolve_tenant_from_path(request).to_context()
    try:
        with tenant_context(ctx), tenant_db_scope(ctx):
            authz.require(ctx, permission)
    except Exception as exc:  # returned for the caller to assert on
        return exc
    return None


def test_a_new_customer_goes_from_sign_in_to_launcher_over_http(
    live_server, user_factory
):
    from workeasy360.platform.authz.models import RoleAssignment
    from workeasy360.platform.organizations.models import (
        Membership,
        Organization,
        UserOrganizationAccess,
    )
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    user = user_factory(password=PASSWORD)
    someone_else = user_factory(password=PASSWORD)
    browser = Browser(live_server.url)

    # --- sign in: zero organizations, so the UI routes to onboarding --------
    browser.sign_in(user.email)
    status, body = browser.get("/api/v1/organizations")
    assert status == 200
    assert body == {"organizations": [], "next_cursor": None, "has_more": False}

    # --- create an organization, attempting mass assignment on the way ------
    forged_id = str(uuid.uuid4())
    idempotency_key = uuid.uuid4().hex
    status, created = browser.post(
        "/api/v1/organizations",
        {
            "name": "Acme Trading Ltd",
            "idempotency_key": idempotency_key,
            "organization_id": forged_id,
            "owner_id": str(someone_else.id),
            "status": "suspended",
            "role": "admin",
            "slug": "chosen-by-client",
        },
    )
    assert status == 201, created
    assert created["display_name"] == "Acme Trading Ltd"
    assert created["organization_id"] != forged_id
    assert created["slug"] == "acme-trading-ltd"
    slug = created["slug"]

    # The retry a flaky network produces: same key, same answer, no second org.
    status, replay = browser.post(
        "/api/v1/organizations",
        {"name": "Acme Trading Ltd", "idempotency_key": idempotency_key},
    )
    assert status == 200
    assert replay["organization_id"] == created["organization_id"]

    # --- what the HTTP calls produced, read back from the source of truth ---
    with unscoped_access(reason="test assertion", module="tests"):
        org = Organization.objects.get(id=created["organization_id"])
        assert org.status == "active"
        assert org.created_by_id == user.id
        assert Organization.objects.filter(display_name="Acme Trading Ltd").count() == 1
        from workeasy360.platform.tenancy.db import organization_db_scope

        with organization_db_scope(org.id):
            members = list(Membership.unscoped.filter(organization_id=org.id))
            assert [(m.user_id, m.is_owner, m.status) for m in members] == [
                (user.id, True, "active")
            ]
            roles = list(
                RoleAssignment.unscoped.filter(organization_id=org.id).values_list(
                    "membership_id", "role__key"
                )
            )
            assert roles == [(members[0].id, "owner")]
    assert UserOrganizationAccess.objects.filter(user_id=someone_else.id).count() == 0

    # --- the chooser lists it; selecting it resolves a real tenant context --
    status, body = browser.get("/api/v1/organizations")
    assert status == 200
    assert [(o["slug"], o["is_owner"], o["status"]) for o in body["organizations"]] == [
        (slug, True, "active")
    ]

    status, context = browser.get(f"/api/v1/orgs/{slug}/context")
    assert status == 200
    assert context["organization_id"] == created["organization_id"]
    assert context["write_allowed"] is True
    assert "aal" not in context

    # --- owner permissions work --------------------------------------------
    status, members_page = browser.get(f"/api/v1/orgs/{slug}/memberships")
    assert status == 200
    assert [m["is_owner"] for m in members_page["results"]] == [True]
    status, organization = browser.get(f"/api/v1/orgs/{slug}/organization")
    assert status == 200
    assert organization["display_name"] == "Acme Trading Ltd"

    # --- the launcher opens, and nothing is fabricated as entitled ----------
    status, apps = browser.get(f"/api/v1/orgs/{slug}/apps")
    assert status == 200
    assert all(app["can_launch"] is False for app in apps["apps"])
    status, entitlements = browser.get(f"/api/v1/orgs/{slug}/entitlements")
    assert status == 200
    assert entitlements == {"products": []}

    # --- a dangerous permission is allowed to the owner who holds it: a
    # password sign-in is enough, no second factor stands in the way ---------
    assert _permission_check(browser, slug, "platform.organization.delete") is None

    # --- a new sign-in works the same way -----------------------------------
    status, _ = browser.post("/api/v1/auth/logout")
    assert status == 200
    status, body = browser.get(f"/api/v1/orgs/{slug}/context")
    assert status in (401, 403, 404)

    browser.sign_in(user.email)
    status, context = browser.get(f"/api/v1/orgs/{slug}/context")
    assert status == 200 and "aal" not in context


def test_an_existing_customer_is_not_sent_to_onboarding(
    live_server, organization_factory
):
    """The chooser answers with the organization, so the frontend's
    zero-organization branch (``app/page.tsx`` -> ``/onboarding``) never
    applies. The browser-level redirect itself is exercised against the
    production frontend build (docs/phase-5/phase-5-status.md)."""
    org, _membership, user = organization_factory()
    user.set_password(PASSWORD)
    user.save(update_fields=["password"])

    browser = Browser(live_server.url)
    browser.sign_in(user.email)

    status, body = browser.get("/api/v1/organizations")
    assert status == 200
    assert [o["slug"] for o in body["organizations"]] == [org.slug]
    status, context = browser.get(f"/api/v1/orgs/{org.slug}/context")
    assert status == 200


def test_csrf_is_enforced_on_organization_creation(live_server, user_factory):
    """The real middleware, over a real socket: a signed-in POST without the
    double-submit token is refused before the view runs."""
    user = user_factory(password=PASSWORD)
    browser = Browser(live_server.url)
    browser.sign_in(user.email)

    for cookie in list(browser.jar):
        if cookie.name == "w360_csrf":
            browser.jar.clear(cookie.domain, cookie.path, cookie.name)

    status, _ = browser.post("/api/v1/organizations", {"name": "Forged"})
    assert status == 403

    from workeasy360.platform.organizations.models import UserOrganizationAccess

    assert not UserOrganizationAccess.objects.filter(user_id=user.id).exists()

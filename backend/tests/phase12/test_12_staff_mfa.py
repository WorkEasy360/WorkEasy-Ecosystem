"""Staff sign-in without a code (owner decision 2026-10-24, withdrawing the
sign-in half of Phase 12's D-037).

A password sign-in opens the control plane. What stays: every ``dangerous``
staff permission still needs a recent step-up (AAL 2) with a second factor,
and no staff route ever answers ``mfa_required`` any more - the admin shell no
longer has a "verify a code" gate to show.
"""

from __future__ import annotations

import re
import uuid

import pytest
from django.urls import URLPattern, URLResolver, get_resolver

from tests.phase7 import conftest as _phase7

PASSWORD = _phase7.PASSWORD

pytestmark = pytest.mark.django_db

VIEW = "platform.staff_customer.view"


def test_a_password_only_staff_session_opens_the_control_plane(staff_client):
    client, _ = staff_client(VIEW, aal2=False, mfa_verified=False)

    session = client.get("/api/v1/admin/session")
    assert session.status_code == 200, session.content
    assert session.json()["aal"] == 1
    assert VIEW in session.json()["permissions"]
    assert client.get("/api/v1/admin/customers").status_code == 200


def test_dangerous_actions_still_need_a_fresh_step_up(staff_client):
    client, _ = staff_client(
        VIEW, "platform.staff_partner.manage", aal2=False, mfa_verified=False
    )

    created = client.post(
        "/api/v1/admin/partners",
        {
            "code": "d037-check",
            "display_name": "D037",
            "legal_name": "D037 Ltd",
            "contact_name": "Dee",
            "contact_email": "dee@d037.example.net",
            "payout_currency": "GBP",
        },
        content_type="application/json",
    )
    assert created.status_code == 401
    assert created.json()["code"] == "step_up_required"


def test_a_non_staff_user_still_gets_the_plain_refusal(http_login, user_factory):
    client = http_login(user_factory(password=PASSWORD))
    response = client.get("/api/v1/admin/session")
    assert response.status_code == 403
    assert response.json()["code"] != "mfa_required"


# --- every staff endpoint ------------------------------------------------------


def _staff_wrapper_code():
    """The code object every ``staff_view`` wrapper shares."""
    from workeasy360.interfaces.api.v1.control_plane_http import staff_view

    fn = staff_view(["GET"])(lambda request, ctx: None)
    while True:
        inner = getattr(fn, "__wrapped__", None)
        if inner is None or fn.__code__.co_filename.endswith("control_plane_http.py"):
            return fn.__code__
        fn = inner


def _is_staff_view(callback, code) -> bool:
    fn = callback
    for _ in range(10):
        if getattr(fn, "__code__", None) is code:
            return True
        fn = getattr(fn, "__wrapped__", None)
        if fn is None:
            return False
    return False


_CONVERTER = re.compile(r"<(?:(\w+):)?(\w+)>")


def _example(route: str) -> str:
    def value(match: re.Match[str]) -> str:
        kind = match.group(1) or "str"
        return {"uuid": str(uuid.uuid4()), "int": "1"}.get(kind, "x")

    return _CONVERTER.sub(value, route)


def _staff_routes() -> list[str]:
    code = _staff_wrapper_code()
    found: list[str] = []

    def walk(patterns, prefix: str) -> None:
        for p in patterns:
            if isinstance(p, URLResolver):
                walk(p.url_patterns, prefix + str(p.pattern))
            elif isinstance(p, URLPattern) and _is_staff_view(p.callback, code):
                found.append("/" + _example(prefix + str(p.pattern)))

    walk(get_resolver().url_patterns, "")
    return found


def test_no_staff_endpoint_asks_a_password_only_session_for_a_code(staff_client):
    """Structural: each view decorated with ``staff_view`` - found by the
    wrapper it runs, not by URL prefix - judges the request on grants, never
    on a sign-in code."""
    client, _ = staff_client(aal2=False, mfa_verified=False)

    routes = _staff_routes()
    assert len(routes) > 40, routes
    from workeasy360.platform.observability.metrics import metrics

    for path in routes:
        # Hundreds of routes x five methods is more HTTP series than the
        # strict (test) ceiling allows in one process; this test is not about
        # metrics (test_12_metric_cardinality.py is).
        metrics.reset()
        for method in ("get", "post", "patch", "put", "delete"):
            response = getattr(client, method)(path, content_type="application/json")
            if response.status_code == 405 or not response.content:
                continue
            body = (
                response.json()
                if response["Content-Type"].startswith("application/json")
                else {}
            )
            assert body.get("code") != "mfa_required", (path, method)


# --- staff routes outside ``staff_view`` (final audit) --------------------------


def test_the_org_scoped_integration_registry_asks_no_sign_in_code(
    http_login, user_factory, organization_factory
):
    staff = user_factory(is_staff=True, password=PASSWORD)
    org, _membership, _owner = organization_factory(owner=staff)
    client = http_login(staff, aal2=False, mfa_verified=False)

    response = client.get(f"/api/v1/orgs/{org.slug}/integrations")
    assert response.json().get("code") != "mfa_required"


def test_the_deep_health_view_opens_to_a_password_only_staff_session(
    http_login, user_factory
):
    staff = user_factory(is_staff=True, password=PASSWORD)
    unverified = http_login(staff, aal2=False, mfa_verified=False)
    assert unverified.get("/healthz/deep").status_code in (200, 503)

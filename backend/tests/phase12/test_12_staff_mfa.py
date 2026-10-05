"""Phase 12, D-037: a password alone never opens the control plane.

Staff *view* permissions are not ``dangerous`` (they need no recent step-up),
yet the customer directory, orders and payment events they open read across
every tenant. So every staff request also needs a session that has verified a
second factor - once per sign-in; ``dangerous`` permissions still need a
recent step-up on top.
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


def _enroll(user) -> list[str]:
    """A confirmed TOTP factor for ``user``; its recovery codes."""
    from workeasy360.platform.identity import mfa
    from workeasy360.platform.identity.models import AuthFactor
    from workeasy360.platform.identity.services import (
        begin_totp_enrollment,
        confirm_totp_enrollment,
    )

    start = begin_totp_enrollment(user.id)
    secret = mfa.decrypt_totp_secret(
        AuthFactor.objects.get(id=start.factor_id).secret_ref
    )
    return confirm_totp_enrollment(user.id, start.factor_id, mfa.current_totp(secret))


def test_a_password_only_staff_session_is_refused_with_mfa_required(staff_client):
    client, _ = staff_client(VIEW, aal2=False, mfa_verified=False)

    for path in ("/api/v1/admin/session", "/api/v1/admin/customers"):
        response = client.get(path)
        assert response.status_code == 403, (path, response.content)
        assert response.json()["code"] == "mfa_required"


def test_verifying_a_code_opens_the_control_plane_for_the_session(staff_client):
    client, user = staff_client(VIEW, aal2=False, mfa_verified=False)
    recovery = _enroll(user)
    assert client.get("/api/v1/admin/session").status_code == 403

    verified = client.post(
        "/api/v1/auth/mfa/step-up", {"code": recovery[0]}, content_type="application/json"
    )
    assert verified.status_code == 200, verified.content

    session = client.get("/api/v1/admin/session")
    assert session.status_code == 200
    assert VIEW in session.json()["permissions"]
    assert client.get("/api/v1/admin/customers").status_code == 200


def test_the_verification_does_not_lapse_but_dangerous_actions_still_need_a_fresh_step_up(
    staff_client,
):
    """Verified earlier, step-up window over: reads work, and a dangerous
    permission is still refused with ``step_up_required`` (unchanged)."""
    client, _ = staff_client(VIEW, "platform.staff_partner.manage", aal2=False)

    session = client.get("/api/v1/admin/session")
    assert session.status_code == 200
    assert session.json()["aal"] == 1
    assert client.get("/api/v1/admin/customers").status_code == 200

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


def test_a_new_sign_in_needs_a_new_verification(staff_client):
    client, user = staff_client(VIEW)  # verified (AAL 2)
    assert client.get("/api/v1/admin/session").status_code == 200

    again = client.post(
        "/api/v1/auth/login",
        {"email": user.email, "password": PASSWORD},
        content_type="application/json",
    )
    assert again.status_code == 200
    refused = client.get("/api/v1/admin/session")
    assert refused.status_code == 403
    assert refused.json()["code"] == "mfa_required"


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


def test_every_staff_endpoint_refuses_a_password_only_session(staff_client):
    """Structural: each view decorated with ``staff_view`` - found by the
    wrapper it runs, not by URL prefix - refuses before doing anything."""
    # No grants needed: the refusal comes before any permission is judged.
    client, _ = staff_client(aal2=False, mfa_verified=False)

    routes = _staff_routes()
    assert len(routes) > 40, routes
    from workeasy360.platform.observability.metrics import metrics

    for path in routes:
        # Hundreds of routes x five methods is more HTTP series than the
        # strict (test) ceiling allows in one process; this test is not about
        # metrics (test_12_metric_cardinality.py is).
        metrics.reset()
        answers = {}
        for method in ("get", "post", "patch", "put", "delete"):
            response = getattr(client, method)(path, content_type="application/json")
            answers[method] = response.status_code
            if response.status_code == 405:
                continue
            assert response.status_code == 403, (path, method, response.content)
            assert response.json()["code"] == "mfa_required", (path, method)
        assert any(status == 403 for status in answers.values()), (path, answers)


# --- staff routes outside ``staff_view`` (final audit) --------------------------


def test_the_org_scoped_integration_registry_needs_the_second_factor_too(
    http_login, user_factory, organization_factory
):
    """Control-plane only (``require_platform_staff``) but routed under an
    organization, so not a ``staff_view``: a staff member who also owns an
    organization must not reach it with a password alone."""
    staff = user_factory(is_staff=True, password=PASSWORD)
    org, _membership, _owner = organization_factory(owner=staff)
    client = http_login(staff, aal2=False, mfa_verified=False)

    response = client.get(f"/api/v1/orgs/{org.slug}/integrations")
    assert response.status_code == 403
    assert response.json()["code"] == "mfa_required"

    verified = http_login(staff, aal2=False)  # verified earlier in the sign-in
    assert verified.get(f"/api/v1/orgs/{org.slug}/integrations").json().get("code") != (
        "mfa_required"
    )


def test_the_deep_health_view_is_not_found_without_the_second_factor(
    http_login, user_factory
):
    staff = user_factory(is_staff=True, password=PASSWORD)
    unverified = http_login(staff, aal2=False, mfa_verified=False)
    assert unverified.get("/healthz/deep").status_code == 404

    verified = http_login(staff, aal2=False)
    assert verified.get("/healthz/deep").status_code in (200, 503)

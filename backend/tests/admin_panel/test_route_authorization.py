"""Every route this programme added refuses people it should, on the server.

Walks the URL configuration rather than a list, so a route added later is covered without
anyone remembering to add it. The frontend hides what a person may not do, but that is a
convenience: what is tested here is what happens when the request is sent anyway.
"""

from __future__ import annotations

import re
import uuid

import pytest
from django.test import Client

pytestmark = pytest.mark.django_db

#: Filtered by the caller's grants rather than refused: a member of staff with no grants gets
#: an answer with nothing in it. ``test_a_member_of_staff_with_no_grants_sees_nothing`` proves it.
FILTERED_BY_GRANT = {"admin/search"}

MODULES = ("admin_panel_views",)


def _routes():
    from workeasy360.interfaces.api.v1 import urls

    out = []
    for pattern in urls.urlpatterns:
        callback = getattr(pattern, "callback", None)
        module = getattr(callback, "__module__", "")
        route = str(pattern.pattern)
        if (
            callback is not None
            and module.rsplit(".", 1)[-1] in MODULES
            and route.startswith("admin/")
        ):
            out.append(route)
    return sorted(set(out))


def _concrete(route: str) -> str:
    path = re.sub(r"<uuid:[a-z_]+>", str(uuid.uuid4()), route)
    path = re.sub(r"<(?:str|slug):[a-z_]+>", "sales", path)
    return f"/api/v1/{path}"


def _calls(client, route):
    url = _concrete(route)
    return [
        (
            method,
            getattr(client, method)(url, {}, content_type="application/json").status_code,
        )
        for method in ("get", "post", "put")
    ]


def test_the_walk_finds_the_routes():
    routes = _routes()
    assert len(routes) > 40
    assert "admin/wallets/<uuid:wallet_id>/adjust" in routes
    assert "admin/kyc/applications/<uuid:partner_id>/decision" in routes


def test_nobody_signed_in_gets_anything():
    client = Client()
    client.get("/api/v1/auth/session")
    for route in _routes():
        for method, status in _calls(client, route):
            assert status in (401, 403, 405), f"{method} {route} answered {status}"


def test_a_customer_gets_nothing(console_org, login):
    client = login(console_org()["owner"])
    for route in _routes():
        for method, status in _calls(client, route):
            assert status in (401, 403, 404, 405), f"{method} {route} answered {status}"


def test_a_member_of_staff_with_no_grants_gets_nothing(staff_login):
    client, _ = staff_login()
    for route in _routes():
        if route in FILTERED_BY_GRANT:
            continue
        for method, status in _calls(client, route):
            assert status in (403, 404, 405), f"{method} {route} answered {status}"


def test_a_member_of_staff_with_no_grants_sees_nothing_in_search(
    staff_login, console_org
):
    console_org()
    client, _ = staff_login()
    answer = client.get("/api/v1/admin/search", {"q": "a"})
    assert answer.status_code == 200
    body = answer.json()
    assert not any(body.get("results") or [])


def test_a_viewer_cannot_use_a_route_that_changes_something(staff_login):
    """A grant to look does not move money or decide an application."""
    client, _ = staff_login(
        "platform.staff_wallet.view",
        "platform.staff_kyc.view",
        "platform.staff_payment.view",
        "platform.staff_support.view",
        "platform.staff_report.view",
    )
    missing = str(uuid.uuid4())
    for url in (
        f"/api/v1/admin/wallets/{missing}/adjust",
        f"/api/v1/admin/wallets/{missing}/top-up",
        f"/api/v1/admin/wallets/entries/{missing}/reverse",
        f"/api/v1/admin/kyc/applications/{missing}/decision",
        f"/api/v1/admin/kyc/applications/{missing}/review",
        f"/api/v1/admin/orders/{missing}/retry-activation",
        f"/api/v1/admin/tickets/{missing}/resolve",
        "/api/v1/admin/usage-rates",
        "/api/v1/admin/credit-packs",
    ):
        status = client.post(url, {}, content_type="application/json").status_code
        assert status == 403, f"{url} answered {status}"


def test_a_change_needs_the_csrf_token(staff_login):
    client, user = staff_login("platform.staff_wallet.manage")
    strict = Client(enforce_csrf_checks=True)
    strict.cookies = client.cookies
    answer = strict.post(
        "/api/v1/admin/usage-rates",
        {
            "meter": "sms",
            "currency": "INR",
            "unit_amount": 1,
            "per_units": 1,
            "reason": "No token here",
        },
        content_type="application/json",
    )
    assert answer.status_code == 403

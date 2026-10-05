"""Phase 12: a CSRF refusal speaks the API's error shape (it was Django's HTML
page - found by the real-browser run), and never says *why* it refused."""

from __future__ import annotations

import pytest
from django.test import Client

pytestmark = pytest.mark.django_db


def test_a_write_without_the_token_is_a_json_403_that_names_no_reason():
    client = Client(enforce_csrf_checks=True)
    client.get("/api/v1/auth/session")  # the cookie exists; the header is missing
    response = client.post(
        "/api/v1/auth/login",
        {"email": "someone@example.net", "password": "irrelevant"},
        content_type="application/json",
    )
    assert response.status_code == 403
    assert response["Content-Type"].startswith("application/json")
    body = response.json()
    assert body["code"] == "csrf_failed"
    assert set(body) == {"code", "detail", "correlation_id"}
    lowered = body["detail"].lower()
    for leak in ("cookie", "token", "referer", "origin"):
        assert leak not in lowered


def test_with_the_token_the_same_write_reaches_the_view():
    client = Client(enforce_csrf_checks=True)
    client.get("/api/v1/auth/session")
    response = client.post(
        "/api/v1/auth/login",
        {"email": "someone@example.net", "password": "irrelevant"},
        content_type="application/json",
        HTTP_X_CSRFTOKEN=client.cookies["w360_csrf"].value,
    )
    assert response.status_code != 403

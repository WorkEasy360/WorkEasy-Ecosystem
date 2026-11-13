"""Fixtures for the unified-login suites (ADR-066): a person with a real
password, signed in through the real login endpoint, and the multi-portal
hosts reused from ``tests/portals``."""

from __future__ import annotations

import pytest
from django.test import Client

from tests.phase9.conftest import deliver_account_mail, token_from  # noqa: F401
from tests.portals.conftest import (  # noqa: F401 - re-exported fixtures
    HOST,
    ORIGINS,
    HostClient,
    host_client,
    portals_on,
)

PASSWORD = "correct horse battery staple 42"


@pytest.fixture
def person(user_factory):
    """An active person with a real password."""

    def make(email: str | None = None, **kwargs):
        user = user_factory(email=email, password=PASSWORD, **kwargs)
        user.full_name = user.full_name or "Rahul Sharma"
        user.save(update_fields=["full_name"])
        return user

    return make


def log_in(client, email: str, password: str = PASSWORD):
    """Sign in on ``client`` (a Django ``Client`` or a ``HostClient``) as the
    sign-in page does: session first (CSRF cookie), then the login POST."""
    client.get("/api/v1/auth/session")
    if isinstance(client, Client):
        return client.post(
            "/api/v1/auth/login",
            {"email": email, "password": password},
            content_type="application/json",
        )
    return client.post("/api/v1/auth/login", {"email": email, "password": password})


def signed_in_as(client) -> str | None:
    body = client.get("/api/v1/auth/session").json()
    if not body.get("authenticated"):
        return None
    return body.get("email")

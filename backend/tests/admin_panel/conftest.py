"""The Admin Panel suites reuse the extension suites' real-login fixtures."""

from __future__ import annotations

import pytest

from tests.extension.conftest import (  # noqa: F401
    console_org,
    fake_payments,
    login,
    staff_login,
    url,
)


@pytest.fixture
def shop(console_org, login, catalog, fake_payments):  # noqa: F811
    """A customer with a billing account, signed in, able to buy."""
    org = console_org()
    owner = login(org["owner"])
    opened = owner.post(
        url(org["slug"], "billing-account"),
        {"currency": "GBP", "billing_email": "b@example.com"},
        content_type="application/json",
    )
    assert opened.status_code == 200, opened.content
    return {**org, "client": owner, "catalog": catalog, "id": org["org"].id}

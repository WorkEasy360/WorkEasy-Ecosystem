"""The Admin Panel suites reuse the extension suites' real-login fixtures."""

from __future__ import annotations

from tests.extension.conftest import (  # noqa: F401
    console_org,
    fake_payments,
    login,
    staff_login,
)

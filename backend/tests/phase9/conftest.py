"""Fixtures for the Phase 9 suites (public site, self-service accounts, orders,
lifecycle, Super Admin). The Phase 8 extension's fixtures are reused as they
are: an organization with real system roles, members signed in through the real
login endpoint, staff clients."""

from __future__ import annotations

import re

import pytest
from django.core import mail

from tests.extension.conftest import (  # noqa: F401 - re-exported fixtures
    PASSWORD,
    console_org,
    fake_payments,
    login,
    staff_login,
    url,
)


def deliver_account_mail():
    """Run every pending account email now (the worker's job) and return the
    outbox. Account emails are queued under the platform organization."""
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID

    return deliver(PLATFORM_ORGANIZATION_ID)


def deliver(organization_id):
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.notifications.models import NotificationDelivery
    from workeasy360.platform.notifications.services import attempt_delivery
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    ctx = routing_context(organization_id)
    if ctx is None:  # nothing was ever queued there (the row is created lazily)
        return mail.outbox
    with tenant_context(ctx), organization_db_scope(organization_id):
        pending = list(
            NotificationDelivery.objects.filter(status="pending").values_list(
                "id", flat=True
            )
        )
        for delivery_id in pending:
            attempt_delivery(delivery_id)
    return mail.outbox


def token_from(message) -> str:
    match = re.search(r"#token=([A-Za-z0-9._-]+)", message.body)
    assert match, message.body
    return match.group(1)


@pytest.fixture
def anonymous():
    """A browser with its CSRF cookie, as the public pages have after their
    first ``/auth/session`` call - and CSRF enforced, as in production."""
    from django.test import Client

    from tests.extension.conftest import BROWSER

    def make(ip: str = "127.0.0.1"):
        client = Client(enforce_csrf_checks=True, HTTP_USER_AGENT=BROWSER, REMOTE_ADDR=ip)
        client.get("/api/v1/auth/session")
        token = client.cookies["w360_csrf"].value
        client.defaults["HTTP_X_CSRFTOKEN"] = token
        return client

    return make

"""Accounts V3 (ADR-075): shared fixtures. The Control Center's own fixtures
(hosts, the identity switch, a person with a product) are reused unchanged."""

from __future__ import annotations

from typing import Any

import pytest

from tests.accounts_center.conftest import (  # noqa: F401 - fixtures
    ACCOUNT_API,
    BROWSER_AGENT,
    OnHost,
    accounts_on,
    bcl_manifest,
    no_wait,
    on_accounts,
    on_admin,
    on_app,
    person,
    sign_in_to_product,
)
from tests.universal_sso.conftest import (  # noqa: F401 - fixtures
    AUTHORIZE,
    PASSWORD,
    authorize_query,
    password_login,
    prefer,
    query_of,
    sso_product,
    with_password,
)

LOGIN = "/api/v1/auth/login"
LOGIN_CODE = "/api/v1/auth/login/two-step"
TWO_STEP = "/api/v1/auth/two-step"


def post(client: Any, path: str, body: dict | None = None, **extra: Any):
    return client.post(path, body or {}, content_type="application/json", **extra)


@pytest.fixture
def enroll():
    """Turn two-step on for ``user`` through the real endpoints, on a client
    signed in as them. Returns (secret bytes, recovery codes)."""
    import base64

    from django.test import Client

    def make(user: Any, client: Any = None):
        from workeasy360.platform.identity import two_step

        browser = client or Client()
        if client is None:
            browser.force_login(user)
        started = post(browser, f"{TWO_STEP}/setup", {"password": PASSWORD})
        assert started.status_code == 200, started.content
        secret_b32 = started.json()["secret"]
        raw = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
        done = post(
            browser, f"{TWO_STEP}/setup/confirm", {"code": two_step.totp_now(raw)}
        )
        assert done.status_code == 200, done.content
        return raw, done.json()["recovery_codes"]

    return make


def next_code(raw: bytes) -> str:
    """A code for the next time step: the current one was already spent by
    the setup, and a step is accepted once (RFC 6238 section 5.2)."""
    import time

    from workeasy360.platform.identity import two_step

    return two_step.totp_now(raw, at=time.time() + two_step.STEP_SECONDS)


def delivered() -> list:
    """Every queued account email, delivered now (the worker's job)."""
    from tests.phase9.conftest import deliver_account_mail

    return list(deliver_account_mail())


def add_member(organization_id, user) -> None:
    """Make ``user`` an active (non-owner) member of the organization, as the
    chooser suites do: the membership row and its access projection."""
    from workeasy360.platform.organizations import projection
    from workeasy360.platform.organizations.models import Membership, Organization
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="Test adds a member to an organization", module="tests"),
        organization_db_scope(organization_id),
    ):
        organization = Organization.objects.get(id=organization_id)
        membership = Membership(
            organization_id=organization_id,
            user_id=user.id,
            status="active",
            member_type="internal",
        )
        membership.save()
    projection.sync_access_for_membership(
        user_id=user.id, membership=membership, organization=organization
    )

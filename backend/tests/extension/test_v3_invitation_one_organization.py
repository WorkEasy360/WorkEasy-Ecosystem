"""Accounts V3 5.1 / release evidence: an invited employee joins the inviting
organization; accepting never creates an organization of their own."""

from __future__ import annotations

import pytest

from tests.extension.test_invitations import (
    ACCEPT,
    NEW_PASSWORD,
    _deliver_all,
    _invite,
    _token_from,
)

pytestmark = pytest.mark.django_db


def test_accepting_an_invitation_creates_no_organization(console_org, login, client):
    from workeasy360.platform.organizations.models import (
        Organization,
        UserOrganizationAccess,
    )

    org = console_org()
    _invite(login(org["owner"]), org["slug"], "Employee.One@Example.com", role="readonly")
    token = _token_from(_deliver_all(org["org"].id)[0])
    before = Organization.objects.count()

    accepted = client.post(
        ACCEPT,
        {"token": token, "full_name": "Employee One", "password": NEW_PASSWORD},
        content_type="application/json",
    )
    assert accepted.status_code == 200, accepted.content
    assert accepted.json()["organization_slug"] == org["slug"]
    assert Organization.objects.count() == before
    joined = list(
        UserOrganizationAccess.objects.filter(user__email="employee.one@example.com")
        .values_list("organization_id", "is_owner")
    )
    assert joined == [(org["org"].id, False)]

"""Phase 5 final (Opus) audit: regression tests for the defects it found and
the adversarial cases it added.

Each test names the audit item it covers. The ones marked "Reproduces" failed
on the code as it stood before the audit's fix; the rest pin behaviour the
audit verified and that no earlier test asserted.
"""

from __future__ import annotations

import base64
import json
import uuid

import pytest

pytestmark = [pytest.mark.django_db, pytest.mark.security]

LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
ORGANIZATIONS = "/api/v1/organizations"
PASSWORD = "a long enough passphrase for tests"


def _post(client, path, body=None):
    return client.post(path, body or {}, content_type="application/json")


def _login(client, user):
    response = _post(client, LOGIN, {"email": user.email, "password": PASSWORD})
    assert response.status_code == 200
    return client


@pytest.fixture
def owner(client, user_factory):
    user = user_factory(password=PASSWORD)
    _login(client, user)
    created = _post(client, ORGANIZATIONS, {"name": "Audit Co"}).json()
    return {
        "user": user,
        "slug": created["slug"],
        "org_id": created["organization_id"],
        "membership_id": created["membership_id"],
    }


# --- Audit 36: CSRF on every Phase 5 write ----------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", ORGANIZATIONS),
    ],
)
def test_phase5_writes_require_the_csrf_token(user_factory, method, path):
    from django.test import Client

    browser = Client(enforce_csrf_checks=True)
    user = user_factory(password=PASSWORD)
    browser.force_login(user)

    response = getattr(browser, method)(path, "{}", content_type="application/json")

    assert response.status_code == 403


def test_member_removal_requires_the_csrf_token(owner, user_factory):
    from django.test import Client

    browser = Client(enforce_csrf_checks=True)
    browser.force_login(owner["user"])

    response = browser.delete(f"/api/v1/orgs/{owner['slug']}/memberships/{uuid.uuid4()}")

    assert response.status_code == 403


# --- Audit 15/16: every generated slug is routable ---------------------------


def test_an_organization_named_with_an_underscore_is_usable_by_its_owner(
    client, user_factory
):
    """Reproduces P1-3. ``slugify`` keeps underscores, the tenancy router does
    not accept them: "Acme_Corp" became "acme_corp", created fine (201), and
    then every /orgs/acme_corp/... request ran with no tenant at all - 403
    or 500 - so the owner could never use the organization."""
    user = user_factory(password=PASSWORD)
    _login(client, user)

    created = _post(client, ORGANIZATIONS, {"name": "Acme_Corp"})

    assert created.status_code == 201
    slug = created.json()["slug"]
    assert "_" not in slug
    for path in ("context", "memberships", "organization"):
        assert client.get(f"/api/v1/orgs/{slug}/{path}").status_code == 200, path


@pytest.mark.parametrize(
    "name",
    [
        "Acme_Corp",
        "__init__",
        "a_b-c__d",
        "-_-",
        "Ünïcödé GmbH",
        "株式会社",
        "émoji 🚀 Co",
        "O'Reilly & Sons",
        "  Leading and trailing  ",
        "tab\tseparated\nlines",
        "API",
        "123",
        "x" * 255,
        "a" * 49 + "_" + "b" * 10,
        "a" * 49 + " -b",
    ],
)
def test_every_generated_slug_is_one_the_tenancy_router_resolves(user_factory, name):
    """The slug generator and the tenancy router must agree on the alphabet,
    for the first organization with a name and for a colliding second one
    (which takes a random suffix)."""
    from workeasy360.platform.onboarding.services import create_organization
    from workeasy360.platform.tenancy.resolution import organization_slug_from_path

    user = user_factory()
    for _ in range(2):
        slug = create_organization(user_id=user.id, name=name).slug
        assert organization_slug_from_path(f"/api/v1/orgs/{slug}/context") == slug, slug
        assert len(slug) <= 63


# --- Audit 59/61 (member removal): seats -------------------------------------


def test_removing_a_seated_member_frees_the_seat(owner, client, user_factory):
    """Reproduces P1-2. Removal left the member's seat assigned. The seat
    screen lists active members only, so the seat could never be freed from
    the UI: each removal of a seated member permanently shrank the
    organization's usable (and paid-for) seats, and the next member was
    refused with 402."""
    from uuid import UUID

    from workeasy360.commercial.provisioning.services import grant_product
    from workeasy360.contracts.enums import MembershipStatus
    from workeasy360.contracts.values.tenant import TenantContext
    from workeasy360.platform.entitlements.services import seats
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import tenant_db_scope

    org_id = UUID(owner["org_id"])
    ctx = TenantContext(
        organization_id=org_id,
        membership_id=UUID(owner["membership_id"]),
        user_id=owner["user"].id,
    )
    grant_product(org_id, "crm", seats=1)
    with tenant_context(ctx), tenant_db_scope(ctx):
        leaving, joining = (
            Membership(
                organization_id=org_id,
                user=user_factory(),
                status=MembershipStatus.ACTIVE.value,
            )
            for _ in range(2)
        )
        leaving.save()
        joining.save()
        seats.assign_seat(org_id, pool_key="crm", membership_id=leaving.id)

    removed = client.delete(f"/api/v1/orgs/{owner['slug']}/memberships/{leaving.id}")
    assert removed.status_code == 200

    with tenant_context(ctx), tenant_db_scope(ctx):
        assert seats.assigned_count(org_id, pool_key="crm") == 0
        seats.assign_seat(org_id, pool_key="crm", membership_id=joining.id)
        assert seats.held_by(org_id, pool_key="crm") == {joining.id}


# --- Audit 7/13: chooser cursor ----------------------------------------------


def _cursor(payload) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


@pytest.mark.parametrize(
    "payload",
    [
        {"t": "2026-01-01T00:00:00+00:00", "i": 123},
        {"t": "2026-01-01T00:00:00+00:00", "i": ["x"]},
        {"t": "2026-01-01T00:00:00+00:00", "i": {"a": 1}},
        {"t": 5, "i": str(uuid.uuid4())},
        ["t", "i"],
        "just a string",
    ],
)
def test_a_crafted_cursor_is_the_first_page_not_a_500(owner, client, payload):
    """Reproduces P3-1: a cursor whose id was a JSON number/list/object made
    ``UUID()`` raise AttributeError, which the parser did not catch - a 500
    on the chooser (and the member list) for any signed-in user."""
    cursor = _cursor(payload)

    chooser = client.get(ORGANIZATIONS, {"cursor": cursor})
    members = client.get(f"/api/v1/orgs/{owner['slug']}/memberships", {"cursor": cursor})

    assert chooser.status_code == 200
    assert [o["slug"] for o in chooser.json()["organizations"]] == [owner["slug"]]
    assert members.status_code == 200


def test_a_cursor_from_another_users_chooser_reveals_nothing(owner, client, user_factory):
    """Audit 7: a cursor only positions the caller's own keyset; it cannot
    widen the user filter."""
    from django.test import Client

    other = _login(Client(), user_factory(password=PASSWORD))
    for index in range(3):
        _post(other, ORGANIZATIONS, {"name": f"Other {index}"})
    foreign_cursor = other.get(ORGANIZATIONS, {"limit": 1}).json()["next_cursor"]
    assert foreign_cursor

    mine = client.get(ORGANIZATIONS, {"cursor": foreign_cursor, "limit": 100}).json()

    slugs = {o["slug"] for o in mine["organizations"]}
    assert slugs <= {owner["slug"]}

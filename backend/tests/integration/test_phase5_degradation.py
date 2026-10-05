"""Phase 5 flows with Redis unreachable, and with the broker unreachable.

Same method as test_cache_degradation.py: the cache is pointed at a port
nothing listens on (what an outage looks like to this process), not mocked.
The line drawn is 09-failure-resilience.md's - Redis and the broker are
Tier 1: an outage may cost capacity (a rate limit becomes per-process) or
latency; it may never cost correctness (authorization, MFA, tenant
isolation) and must never hang a request.

The broker cases prove there is no synchronous broker dependency on these
paths at all: any attempt to enqueue a Celery task fails loudly, and the
flows complete anyway, leaving their events durable in the outbox.
"""

from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.django_db

UNREACHABLE_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": "redis://127.0.0.1:6399/0",
        "KEY_PREFIX": "w360",
        "OPTIONS": {"socket_connect_timeout": 0.05, "socket_timeout": 0.1},
    }
}
PASSWORD = "a long enough passphrase for tests"
ORGANIZATIONS = "/api/v1/organizations"
STEP_UP = "/api/v1/auth/mfa/step-up"
#: Generous for a local test run; the point is "bounded", not a latency SLO.
REQUEST_BUDGET_S = 2.0


def _timed(call, *args, **kwargs):
    start = time.perf_counter()
    response = call(*args, **kwargs)
    elapsed = time.perf_counter() - start
    assert elapsed < REQUEST_BUDGET_S, f"request took {elapsed:.2f}s"
    return response


def _post(client, path, body=None):
    return _timed(client.post, path, body or {}, content_type="application/json")


def _sign_in(client, user):
    response = _post(
        client, "/api/v1/auth/login", {"email": user.email, "password": PASSWORD}
    )
    assert response.status_code == 200


def _totp(factor_id, *, steps_ahead=0):
    from workeasy360.platform.identity import mfa
    from workeasy360.platform.identity.models import AuthFactor

    raw = mfa.decrypt_totp_secret(AuthFactor.objects.get(id=factor_id).secret_ref)
    return mfa.current_totp(raw, at=time.time() + 30 * steps_ahead)


# --- Redis unreachable ------------------------------------------------------


def test_onboarding_chooser_and_selection_work_with_an_unreachable_cache(
    client, user_factory, settings
):
    settings.CACHES = UNREACHABLE_CACHE
    user = user_factory(password=PASSWORD)
    _sign_in(client, user)

    created = _post(client, ORGANIZATIONS, {"name": "Outage Co", "idempotency_key": "k1"})
    assert created.status_code == 201
    replay = _post(client, ORGANIZATIONS, {"name": "Outage Co", "idempotency_key": "k1"})
    assert replay.json()["organization_id"] == created.json()["organization_id"]

    slug = created.json()["slug"]
    listed = _timed(client.get, ORGANIZATIONS).json()["organizations"]
    assert [o["slug"] for o in listed] == [slug]
    assert _timed(client.get, f"/api/v1/orgs/{slug}/context").status_code == 200
    # authz recomputes the permission set from the database: slower, correct.
    assert _timed(client.get, f"/api/v1/orgs/{slug}/memberships").status_code == 200


def test_authorization_never_fails_open_with_an_unreachable_cache(
    client, user_factory, organization_factory, settings
):
    """The three decisions that must not loosen: a dangerous permission at
    AAL1, a stale projection row, another tenant."""
    from workeasy360.contracts.enums import OrganizationStatus
    from workeasy360.contracts.errors import StepUpRequired
    from workeasy360.contracts.values.tenant import TenantContext
    from workeasy360.platform.authz import services as authz
    from workeasy360.platform.onboarding.services import create_organization
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope, tenant_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    settings.CACHES = UNREACHABLE_CACHE

    owner = user_factory(password=PASSWORD)
    created = create_organization(user_id=owner.id, name="Owner Co")
    ctx = TenantContext(
        organization_id=created.organization_id,
        membership_id=created.membership_id,
        user_id=owner.id,
        organization_status=OrganizationStatus.ACTIVE,
    )
    with tenant_context(ctx), tenant_db_scope(ctx):
        assert authz.check(ctx, "platform.member.view")
        with pytest.raises(StepUpRequired):
            authz.require(ctx, "platform.organization.delete")

    other_org, other_membership, member = organization_factory()
    _sign_in(client, owner)
    assert _timed(client.get, f"/api/v1/orgs/{other_org.slug}/context").status_code == 404

    # A stale projection row (membership removed behind its back) still
    # cannot select the organization while the cache is down.
    member.set_password(PASSWORD)
    member.save(update_fields=["password"])
    with (
        unscoped_access(reason="test makes the projection stale", module="tests"),
        organization_db_scope(other_org.id),
    ):
        Membership.unscoped.filter(id=other_membership.id).update(status="removed")
    _sign_in(client, member)
    assert [o["slug"] for o in client.get(ORGANIZATIONS).json()["organizations"]] == [
        other_org.slug
    ]
    assert _timed(client.get, f"/api/v1/orgs/{other_org.slug}/context").status_code == 404


def test_mfa_stays_correct_with_an_unreachable_cache(
    client, user_factory, settings, frozen_throttle_window
):
    """Enrollment, step-up and replay protection live in PostgreSQL; the
    code throttle degrades to a per-process counter - approximate, never
    absent."""
    settings.CACHES = UNREACHABLE_CACHE
    settings.MFA_STEP_UP_RATE_LIMITS = {
        "step_up_account": (4, 60),
        "step_up_ip": (100, 60),
    }
    user = user_factory(password=PASSWORD)
    _sign_in(client, user)
    slug = _post(client, ORGANIZATIONS, {"name": "MFA Co"}).json()["slug"]

    started = _post(client, "/api/v1/auth/mfa/totp/enroll").json()
    first = _totp(started["factor_id"])
    confirmed = _post(
        client,
        "/api/v1/auth/mfa/totp/confirm",
        {"factor_id": started["factor_id"], "code": first},
    )
    assert confirmed.status_code == 200

    assert _post(client, STEP_UP, {"code": first}).status_code == 400  # replay
    assert (
        _post(
            client, STEP_UP, {"code": _totp(started["factor_id"], steps_ahead=1)}
        ).status_code
        == 200
    )
    assert client.get(f"/api/v1/orgs/{slug}/context").json()["aal"] == 2

    # Three attempts so far (confirm, replay, step-up: one shared budget);
    # one more wrong code exhausts the limit of 4.
    assert _post(client, STEP_UP, {"code": "000000"}).status_code == 400
    assert _post(client, STEP_UP, {"code": "000000"}).status_code == 429


def test_organization_creation_throttle_degrades_but_does_not_vanish(
    client, user_factory, settings, frozen_throttle_window
):
    settings.CACHES = UNREACHABLE_CACHE
    settings.ORGANIZATION_CREATION_RATE_LIMITS = {
        "org_create_account": (2, 60),
        "org_create_ip": (100, 60),
    }
    user = user_factory(password=PASSWORD)
    _sign_in(client, user)

    codes = [
        _post(client, ORGANIZATIONS, {"name": f"Co {i}"}).status_code for i in range(3)
    ]
    assert codes == [201, 201, 429]


# --- broker unreachable -----------------------------------------------------


@pytest.fixture
def broker_down(monkeypatch):
    """Every way this process could hand work to the broker now fails - the
    enqueue path Celery itself uses. A flow that still completes has no
    synchronous broker dependency."""
    from celery.app.task import Task

    attempts: list[str] = []

    def unreachable(self, *args, **kwargs):
        attempts.append(self.name)
        raise ConnectionRefusedError("broker unreachable (test)")

    monkeypatch.setattr(Task, "apply_async", unreachable)
    return attempts


def test_onboarding_completes_without_the_broker_and_its_event_is_durable(
    client, user_factory, broker_down
):
    from workeasy360.integration.events.models import OutboxEvent
    from workeasy360.integration.events.tasks import relay_outbox
    from workeasy360.platform.organizations.models import UserOrganizationAccess

    user = user_factory(password=PASSWORD)
    _sign_in(client, user)

    created = _post(client, ORGANIZATIONS, {"name": "Brokerless Co"})

    assert created.status_code == 201
    assert broker_down == []  # nothing on this path even tried
    organization_id = created.json()["organization_id"]
    event = OutboxEvent.objects.get(
        organization_id=organization_id, event_type="organizations.organization.created"
    )
    assert event.published_at is None  # waiting for the relay, not lost
    assert UserOrganizationAccess.objects.filter(
        user_id=user.id, organization_id=organization_id
    ).exists()

    # When the relay next runs (the broker back, beat scheduling again) the
    # backlog drains; the relay itself dispatches in-process in this phase.
    relay_outbox.run()
    event.refresh_from_db()
    assert event.published_at is not None


def test_mfa_and_selection_need_no_broker(client, user_factory, broker_down):
    user = user_factory(password=PASSWORD)
    _sign_in(client, user)
    slug = _post(client, ORGANIZATIONS, {"name": "Brokerless MFA"}).json()["slug"]

    started = _post(client, "/api/v1/auth/mfa/totp/enroll").json()
    confirmed = _post(
        client,
        "/api/v1/auth/mfa/totp/confirm",
        {"factor_id": started["factor_id"], "code": _totp(started["factor_id"])},
    )
    assert confirmed.status_code == 200
    step = _post(client, STEP_UP, {"code": _totp(started["factor_id"], steps_ahead=1)})
    assert step.status_code == 200
    assert _timed(client.get, f"/api/v1/orgs/{slug}/context").json()["aal"] == 2
    assert broker_down == []

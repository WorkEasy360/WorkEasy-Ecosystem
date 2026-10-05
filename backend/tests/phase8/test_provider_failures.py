"""Provider failure injection (Tasks 49-53, 99): timeout, 429, 500, invalid
credential, unavailable provider - each a bounded AI failure, each leaving
the rest of the platform untouched."""

from __future__ import annotations

import socket
import time

import pytest

from tests.factories.intelligence import summary_skill
from tests.phase8.helpers import executions, spend

pytestmark = [pytest.mark.django_db(transaction=True)]

PLAIN = summary_skill(
    key="product.example.plain", retrieval={"enabled": False, "top_k": 3}
)


@pytest.fixture
def invoke(fake_ai, ai_registry, ai_org, ai_client):
    ai_client.register_skill(PLAIN)
    org = ai_org()

    def call(**extra):
        return ai_client.invoke(
            organization_id=org["organization_id"],
            request={"skill": "product.example.plain", "input": {"record": "x"}, **extra},
        )

    call.org = org  # type: ignore[attr-defined]
    return call


def _generation_outcomes(org):
    from tests.phase8.helpers import in_org
    from workeasy360.intelligence.ai.models import AIUsageLine

    with in_org(org["org"].id):
        return list(
            AIUsageLine.unscoped.filter(organization_id=org["org"].id, kind="generation")
            .order_by("created_at", "attempt")
            .values_list("outcome", flat=True)
        )


def test_a_timeout_is_bounded_by_the_deadline_and_not_retried(invoke, fake_ai, settings):
    settings.AI_SYNC_DEADLINE_S = 1.5
    fake_ai.fail("/v1/messages", 200, delay=5.0)
    started = time.monotonic()
    status, body = invoke()
    elapsed = time.monotonic() - started
    assert (status, body["error"]) == (503, "ai_provider_unavailable")
    assert elapsed < 4.0, "no indefinite wait"
    assert _generation_outcomes(invoke.org) == ["timeout"]
    assert spend(invoke.org["org"].id)["reserved_micros"] == 0


def test_a_rate_limit_is_retried_with_backoff_and_then_succeeds(invoke, fake_ai):
    fake_ai.fail("/v1/messages", 429, times=2)
    status, body = invoke()
    assert status == 200, body
    assert _generation_outcomes(invoke.org) == ["rate_limited", "rate_limited", "ok"]


def test_a_server_error_that_persists_is_a_retryable_answer_to_the_product(
    invoke, fake_ai
):
    fake_ai.fail("/v1/messages", 500, times=10)
    status, body = invoke()
    assert (status, body["error"]) == (503, "ai_provider_unavailable")
    assert _generation_outcomes(invoke.org) == ["server_error"] * 3


def test_an_invalid_credential_is_not_retried_and_trips_the_breaker(invoke, fake_ai):
    fake_ai.fail("/v1/messages", 401, times=10)
    status, body = invoke()
    assert (status, body["error"]) == (503, "ai_provider_unavailable")
    assert "sk-ant" not in str(body) and "x-api-key" not in str(body)
    assert _generation_outcomes(invoke.org) == ["auth_failed"]


def test_a_missing_credential_fails_without_a_network_call(invoke, fake_ai, monkeypatch):
    monkeypatch.delenv("W360_AI_CREDENTIAL_TEST_ANTHROPIC")
    status, body = invoke()
    assert (status, body["error"]) == (503, "ai_provider_unavailable")
    assert fake_ai.calls("/v1/messages") == []
    assert _generation_outcomes(invoke.org) == ["unavailable"]


def test_an_unreachable_provider_is_retried_as_a_connect_failure(
    invoke, fake_ai, settings
):
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    closed_port = probe.getsockname()[1]
    probe.close()
    settings.AI_PROVIDER_ENDPOINTS = {
        "anthropic_messages": f"http://127.0.0.1:{closed_port}"
    }
    status, body = invoke()
    assert (status, body["error"]) == (503, "ai_provider_unavailable")
    assert _generation_outcomes(invoke.org) == ["connect_failed"] * 3


def test_the_breaker_opens_then_fails_fast_then_probes(invoke, fake_ai, settings):
    from workeasy360.intelligence.ai.models import AIModel

    settings.AI_BREAKER_THRESHOLD = 2
    fake_ai.fail("/v1/messages", 500, times=100)
    invoke()
    invoke()
    model = AIModel.objects.get(key="test.primary")
    assert model.circuit_open_until is not None

    calls = len(fake_ai.calls("/v1/messages"))
    status, body = invoke()
    assert (status, body["error"]) == (503, "ai_provider_unavailable")
    assert len(fake_ai.calls("/v1/messages")) == calls, "an open circuit sends nothing"

    AIModel.objects.filter(key="test.primary").update(circuit_open_until=None)
    fake_ai._failures.clear()
    status, body = invoke()
    assert status == 200
    model.refresh_from_db()
    assert (model.consecutive_failures, model.circuit_open_until) == (0, None)


def test_a_provider_outage_leaves_the_platform_serving(
    invoke, fake_ai, client, signed_in, ai_client
):
    fake_ai.fail("/v1/messages", 529, times=100)
    assert invoke()[0] == 503
    assert client.get("/healthz/live").status_code == 200
    assert client.get("/healthz/ready").status_code == 200
    ready = client.get("/healthz/ready").json()
    assert all("ai" not in check["name"] for check in ready["checks"])
    org = invoke.org
    assert (
        ai_client.verify_entitlement(organization_id=org["organization_id"])["decision"]
        == "allow"
    )
    signed_in(org["user"])
    assert client.get("/api/v1/auth/session").status_code == 200
    assert client.get(f"/api/v1/orgs/{org['org'].slug}/apps").status_code == 200


def test_every_failed_request_releases_its_reservation(invoke, fake_ai):
    fake_ai.fail("/v1/messages", 500, times=100)
    for _ in range(3):
        invoke()
    month = spend(invoke.org["org"].id)
    assert month["reserved_micros"] == 0
    assert {e["status"] for e in executions(invoke.org["org"].id)} == {"failed"}


# --- Redis --------------------------------------------------------------------------------


def test_a_cache_outage_degrades_rate_limits_never_spend_control(
    fake_ai, ai_registry, ai_org, ai_client, settings, monkeypatch, frozen_throttle_window
):
    """Task 100: with every cache call failing, AI keeps working, the rate
    limit becomes a per-process window (D-012) instead of disappearing, and
    the quota - which never touches the cache - stays exact."""
    from django.core.cache import cache

    ai_client.register_skill(PLAIN)
    probe = ai_org()
    request = {"skill": "product.example.plain", "input": {"record": "x"}}
    assert (
        ai_client.invoke(organization_id=probe["organization_id"], request=request)[0]
        == 200
    )
    one_request = executions(probe["org"].id)[0]["reserved_micros"]

    def down(*_a, **_k):
        raise ConnectionError("redis unreachable")

    for name in ("get", "set", "add", "incr", "delete", "get_many", "set_many"):
        monkeypatch.setattr(cache, name, down)

    org = ai_org(budget=one_request)
    first = ai_client.invoke(organization_id=org["organization_id"], request=request)
    second = ai_client.invoke(organization_id=org["organization_id"], request=request)
    assert first[0] == 200, first
    assert (second[0], second[1]["error"]) == (429, "ai_quota_exceeded")
    month = spend(org["org"].id, "example")
    assert month["reserved_micros"] == 0 and 0 < month["spent_micros"] <= one_request

    settings.ECOSYSTEM_RATE_LIMITS = {
        **settings.ECOSYSTEM_RATE_LIMITS,
        "ai_invoke_org": (2, 60),
    }
    limited = ai_org()
    codes = [
        ai_client.invoke(organization_id=limited["organization_id"], request=request)[0]
        for _ in range(4)
    ]
    assert codes == [200, 200, 429, 429], codes

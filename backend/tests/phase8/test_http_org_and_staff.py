"""The organization's AI settings and usage, and the Super Admin AI control
plane, over HTTP (Tasks 87-91), and async AI jobs (Task 39)."""

from __future__ import annotations

import json

import pytest

from tests.factories.intelligence import summary_skill
from tests.phase8.helpers import drain, executions

pytestmark = [pytest.mark.django_db(transaction=True)]

AI_PERMISSIONS = ("platform.ai.view", "platform.ai.manage")
PLAIN = summary_skill(
    key="product.example.plain", retrieval={"enabled": False, "top_k": 3}
)


@pytest.fixture
def admin(ai_org, grant_permissions, signed_in, client):
    """An AI-entitled organization whose owner holds the AI permissions."""

    def make(*, permissions=AI_PERMISSIONS):
        org = ai_org()
        if permissions:
            grant_permissions(org["org"], org["membership"], permissions)
        signed_in(org["user"])
        org["api"] = f"/api/v1/orgs/{org['org'].slug}"
        return org

    return make


def _patch(client, url, body):
    return client.patch(url, data=json.dumps(body), content_type="application/json")


def _post(client, url, body):
    return client.post(url, data=json.dumps(body), content_type="application/json")


# --- the organization --------------------------------------------------------------------


def test_an_administrator_sees_limits_usage_and_providers_but_no_secret(
    admin, client, ai_registry, fake_ai, ai_client
):
    org = admin()
    ai_client.register_skill(PLAIN)
    ai_client.invoke(
        organization_id=org["organization_id"],
        request={
            "skill": "product.example.plain",
            "input": {"record": "confidential text"},
        },
    )
    usage = client.get(f"{org['api']}/ai/usage").json()
    assert usage["currency"] == "USD" and usage["status"] == "enabled"
    [example] = [p for p in usage["products"] if p["product_code"] == "example"]
    assert example["ai_entitled"] is True and example["limit_micros"] == 5_000_000
    assert example["requests"] == 1 and example["spent_micros"] > 0
    assert "not an invoice" in usage["note"].lower()

    requests = client.get(f"{org['api']}/ai/requests").json()["results"]
    assert [r["skill"] for r in requests] == ["product.example.plain"]
    settings_body = client.get(f"{org['api']}/ai/settings").json()
    rendered = json.dumps([usage, requests, settings_body])
    for forbidden in (
        "confidential text",
        "sk-ant",
        "sk-test",
        "credential_ref",
        "W360_AI",
    ):
        assert forbidden not in rendered


def test_an_administrator_can_switch_ai_off_and_cap_it(
    admin, client, ai_registry, fake_ai, ai_client
):
    org = admin()
    ai_client.register_skill(PLAIN)
    current = client.get(f"{org['api']}/ai/settings").json()["policy"]
    response = _patch(
        client,
        f"{org['api']}/ai/settings",
        {
            "revision": current["revision"],
            "ai_enabled": False,
            "monthly_budget_micros": 1000,
        },
    )
    assert response.status_code == 200, response.content
    assert response.json()["policy"]["organization_cap_micros"] == 1000
    status, body = ai_client.invoke(
        organization_id=org["organization_id"],
        request={"skill": "product.example.plain", "input": {"record": "x"}},
    )
    assert (status, body["error"]) == (403, "ai_disabled")
    stale = _patch(
        client,
        f"{org['api']}/ai/settings",
        {"revision": current["revision"], "ai_enabled": True},
    )
    assert stale.status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"ai_enabled": "yes"},
        {"monthly_budget_micros": -1},
        {"monthly_budget_micros": 1.5},
        {"allowed_providers": ["evil-vendor"]},
        {"allowed_providers": "anthropic"},
        {"allow_provider_fallback": 1},
    ],
)
def test_invalid_settings_are_refused_before_anything_is_written(
    admin, client, ai_registry, body
):
    from tests.phase8.helpers import in_org
    from workeasy360.intelligence.ai.models import OrganizationAIPolicy

    org = admin()
    response = _patch(client, f"{org['api']}/ai/settings", {"revision": 1, **body})
    assert response.status_code == 400
    with in_org(org["org"].id):
        assert not OrganizationAIPolicy.unscoped.filter(
            organization_id=org["org"].id
        ).exists()


def test_changing_ai_settings_takes_effect(admin, client, ai_registry):
    org = admin()
    response = _patch(
        client, f"{org['api']}/ai/settings", {"revision": 1, "ai_enabled": False}
    )
    assert response.status_code == 200, response.content
    settings = client.get(f"{org['api']}/ai/settings")
    assert (
        settings.status_code == 200 and settings.json()["policy"]["ai_enabled"] is False
    )


def test_a_member_without_the_permission_sees_nothing(admin, client):
    org = admin(permissions=())
    for path in ("ai/settings", "ai/usage", "ai/requests"):
        assert client.get(f"{org['api']}/{path}").status_code == 403, path


def test_the_organization_cannot_set_the_platforms_fields(admin, client, ai_registry):
    from tests.phase8.helpers import in_org
    from workeasy360.intelligence.ai.models import OrganizationAIPolicy

    org = admin()
    response = _patch(
        client,
        f"{org['api']}/ai/settings",
        {
            "revision": 1,
            "ai_enabled": True,
            "platform_cap_micros": 10**12,
            "premium_models_allowed": True,
            "platform_suspended": False,
        },
    )
    assert response.status_code == 200
    with in_org(org["org"].id):
        row = OrganizationAIPolicy.unscoped.get(organization_id=org["org"].id)
    assert row.platform_cap_micros is None and row.premium_models_allowed is False


# --- the Super Admin ---------------------------------------------------------------------


@pytest.fixture
def staff(staff_factory, signed_in):
    def make(*permissions):
        user, ctx = staff_factory(*permissions)
        signed_in(user)
        return user, ctx

    return make


def test_the_registry_shows_whether_a_credential_is_configured_never_its_value(
    staff, client, ai_registry, fake_ai
):
    staff("platform.staff_ai.view")
    body = client.get("/api/v1/admin/ai/registry").json()
    anthropic = next(p for p in body["providers"] if p["key"] == "anthropic")
    assert anthropic["credential_configured"] is True
    rendered = json.dumps(body)
    assert "sk-ant-test" not in rendered and "credential_ref" not in rendered
    assert {m["key"] for m in body["models"]} >= {"test.primary", "test.embed"}


def test_the_registry_needs_a_live_staff_grant(
    staff, client, ai_registry, user_factory, signed_in
):
    signed_in(user_factory())
    assert client.get("/api/v1/admin/ai/registry").status_code == 403
    staff()  # staff, but no AI grant
    assert client.get("/api/v1/admin/ai/registry").status_code == 403


def test_switching_a_provider_off_stops_its_use(
    staff, client, ai_registry, fake_ai, ai_client, ai_org
):
    staff("platform.staff_ai.manage")
    url = "/api/v1/admin/ai/providers/anthropic/status"
    body = {"status": "disabled", "reason": "Vendor incident INC-42"}
    assert _post(client, url, body).status_code == 200
    ai_client.register_skill(PLAIN)
    status, answer = ai_client.invoke(
        organization_id=ai_org()["organization_id"],
        request={"skill": "product.example.plain", "input": {"record": "x"}},
    )
    assert (status, answer["error"]) == (503, "ai_provider_unavailable")
    assert fake_ai.calls("/v1/messages") == []


def test_a_new_price_is_a_new_version_and_history_is_never_repriced(
    staff, client, ai_registry
):
    from django.db import connection, transaction

    from workeasy360.intelligence.ai.models import AIModelPrice

    staff("platform.staff_ai.manage")
    response = _post(
        client,
        "/api/v1/admin/ai/models/test.primary/prices",
        {
            "input_micros_per_mtok": 1,
            "output_micros_per_mtok": 2,
            "reason": "Vendor price change 2026-10",
        },
    )
    assert response.status_code == 201, response.content
    versions = list(
        AIModelPrice.objects.filter(model__key="test.primary")
        .order_by("version")
        .values_list("version", "input_micros_per_mtok")
    )
    assert versions == [(1, 3_000_000), (2, 1)]
    with pytest.raises(Exception, match="cannot be changed"), transaction.atomic():
        with connection.cursor() as cur:
            cur.execute("UPDATE ai_model_price SET input_micros_per_mtok = 0")


def test_a_new_model_is_created_disabled(staff, client, ai_registry):
    staff("platform.staff_ai.manage")
    response = _post(
        client,
        "/api/v1/admin/ai/models",
        {
            "key": "anthropic.claude-new",
            "provider": "anthropic",
            "model_id": "claude-new",
            "display_name": "New",
            "capabilities": ["text"],
            "context_window_tokens": 100_000,
            "max_output_tokens": 1_000,
            "input_micros_per_mtok": 1,
            "output_micros_per_mtok": 1,
            "reason": "Evaluation of a new model",
            "endpoint": "https://evil.example/v1",
        },
    )
    assert response.status_code == 201, response.content
    model = next(
        m for m in response.json()["models"] if m["key"] == "anthropic.claude-new"
    )
    assert model["status"] == "disabled"
    assert "endpoint" not in model


def test_staff_can_suspend_an_organizations_ai_with_an_audited_reason(
    staff, client, ai_registry, fake_ai, ai_client, ai_org
):
    from tests.phase8.helpers import in_org
    from workeasy360.platform.audit.models import AuditEvent

    org = ai_org()
    staff("platform.staff_ai.manage")
    url = f"/api/v1/admin/ai/organizations/{org['org'].id}/policy"
    assert (
        _post(client, url, {"platform_suspended": True, "reason": "x"}).status_code == 400
    )
    response = _post(
        client, url, {"platform_suspended": True, "reason": "Abuse report AB-7"}
    )
    assert response.status_code == 200, response.content
    assert response.json()["policy"]["platform_suspended"] is True
    ai_client.register_skill(PLAIN)
    status, answer = ai_client.invoke(
        organization_id=org["organization_id"],
        request={"skill": "product.example.plain", "input": {"record": "x"}},
    )
    assert (status, answer["error"]) == (403, "ai_disabled")
    with in_org(org["org"].id):
        assert AuditEvent.unscoped.filter(
            organization_id=org["org"].id, action="ai.policy.changed_by_staff"
        ).exists()
    detail = client.get(f"/api/v1/admin/ai/organizations/{org['org'].id}").json()
    assert detail["policy"]["platform_suspended"] is True


# --- async jobs ---------------------------------------------------------------------------


def test_an_async_job_runs_on_the_queue_and_is_polled_by_its_product_only(
    fake_ai, ai_registry, ai_org, ai_client, second_product, product_client
):
    ai_client.register_skill(PLAIN)
    org = ai_org()
    status, queued = ai_client.invoke(
        organization_id=org["organization_id"],
        request={
            "skill": "product.example.plain",
            "input": {"record": "x"},
            "mode": "async",
        },
    )
    assert (status, queued["status"]) == (202, "queued")
    status, polled = ai_client.request_status(
        organization_id=org["organization_id"], request_id=queued["request_id"]
    )
    assert status == 200 and polled["status"] == "succeeded", polled
    assert polled["output"] == "Here is the answer."
    other = product_client(second_product)
    status, body = other.request_status(
        organization_id=org["organization_id"], request_id=queued["request_id"]
    )
    assert (status, body["error"]) == (404, "ai_request_not_found")


def test_a_job_queued_during_a_broker_outage_runs_from_its_timer(
    fake_ai, ai_registry, ai_org, ai_client, monkeypatch
):
    from kombu.exceptions import OperationalError

    from workeasy360.intelligence.gateway import tasks

    ai_client.register_skill(PLAIN)
    org = ai_org()
    real = tasks.run_execution.apply_async

    def broker_down(*args, **kwargs):
        raise OperationalError("broker unreachable")

    monkeypatch.setattr(tasks.run_execution, "apply_async", broker_down)
    status, queued = ai_client.invoke(
        organization_id=org["organization_id"],
        request={
            "skill": "product.example.plain",
            "input": {"record": "x"},
            "mode": "async",
        },
    )
    assert status == 202
    assert executions(org["org"].id)[0]["status"] == "queued"
    monkeypatch.setattr(tasks.run_execution, "apply_async", real)
    drain(org["org"].id)
    assert executions(org["org"].id)[0]["status"] == "succeeded"


def test_a_job_whose_entitlement_lapsed_while_queued_spends_nothing(
    fake_ai, ai_registry, ai_org, ai_client, monkeypatch, entitled
):
    from workeasy360.intelligence.gateway import tasks

    ai_client.register_skill(PLAIN)
    org = ai_org()
    monkeypatch.setattr(tasks.run_execution, "apply_async", lambda *a, **k: None)
    ai_client.invoke(
        organization_id=org["organization_id"],
        request={
            "skill": "product.example.plain",
            "input": {"record": "x"},
            "mode": "async",
        },
    )
    monkeypatch.undo()
    entitled(org["org"].id, "example")  # the plan no longer grants AI
    drain(org["org"].id)
    [job] = executions(org["org"].id)
    assert (job["status"], job["outcome"], job["cost_micros"]) == (
        "failed",
        "ai_not_entitled",
        0,
    )
    assert fake_ai.calls("/v1/messages") == []

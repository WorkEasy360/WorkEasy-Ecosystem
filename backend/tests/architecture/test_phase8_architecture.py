"""Phase 8 fitness rules: the AI, retrieval and analytics platform.

Each rule is one sentence of the Phase 8 decision record made executable, so
it cannot be weakened by a refactor that nobody reviews against the ADRs.
"""

from __future__ import annotations

import ast
import configparser
from pathlib import Path

import pytest

pytestmark = [pytest.mark.architecture]

PACKAGE = Path(__file__).resolve().parents[2] / "workeasy360"
REPO_ROOT = PACKAGE.parents[1]
INTELLIGENCE = PACKAGE / "intelligence"


def _sources(root: Path):
    for path in sorted(root.rglob("*.py")):
        if "migrations" in path.parts or "tests" in path.parts:
            continue
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def test_ai_health_never_gates_readiness():
    """Task 53: AI is Tier 3. Its probe reports on /health/deep and nowhere
    that orchestration reads."""
    from django.test import Client

    from workeasy360.platform.observability.health import registered_probe_names

    assert "ai_providers" in registered_probe_names("deep")
    for group in ("startup", "registries"):
        assert "ai_providers" not in registered_probe_names(group)
    ready = Client().get("/healthz/ready").json()
    assert all("ai" not in check["name"] for check in ready["checks"])


def test_intelligence_work_never_runs_on_the_critical_queue():
    """Task 40: AI, indexing and projection are heavy work; sign-in and the
    outbox relay must never wait behind them."""
    from celery import current_app

    tasks = {
        name: task
        for name, task in current_app.tasks.items()
        if name.startswith("intelligence.")
    }
    assert set(tasks) >= {
        "intelligence.gateway.run_execution",
        "intelligence.rag.index_version",
        "intelligence.rag.purge_source",
        "intelligence.analytics.project",
        "intelligence.prune",
    }
    for name, task in tasks.items():
        assert task.queue == "heavy", name
        assert task.soft_time_limit and task.time_limit, name


def test_no_model_is_ever_offered_a_tool():
    """Task 16: the platform gives models no actions, so no provider request
    may carry a tool, a function or a tool choice."""
    forbidden = {"tools", "tool_choice", "functions", "function_call"}
    offenders = []
    for path, tree in _sources(INTELLIGENCE / "ai" / "providers"):
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in forbidden:
                offenders.append(f"{path.name}:{node.lineno} {node.value!r}")
    assert not offenders, offenders


def test_the_ai_authority_contract_cannot_be_removed_quietly():
    """Task 20 is enforced by import-linter; this pins the contract itself."""
    config = configparser.ConfigParser()
    config.read(REPO_ROOT / ".importlinter", encoding="utf-8")
    section = config["importlinter:contract:ai-is-never-an-authority"]
    forbidden = {
        line.strip() for line in section["forbidden_modules"].split("\n") if line.strip()
    }
    assert forbidden >= {
        "workeasy360.platform.entitlements.services.write",
        "workeasy360.platform.entitlements.services.seats",
        "workeasy360.platform.authz.staff",
        "workeasy360.platform.identity.services",
        "workeasy360.platform.organizations.services",
        "workeasy360.commercial",
    }
    assert (
        "workeasy360.intelligence"
        in config["importlinter:contract:top-level-layers"]["layers"]
    )


def test_no_provider_secret_has_a_column():
    """Task 6: a provider row names its secret; nothing stores one."""
    from django.apps import apps

    suspicious = {
        "api_key",
        "key_value",
        "secret",
        "secret_key",
        "access_token",
        "token",
        "password",
        "credential",
        "credential_value",
        "authorization",
    }
    offenders = [
        f"{model._meta.label}.{field.name}"
        for app in ("ai", "rag", "analytics")
        for model in apps.get_app_config(app).get_models()
        for field in model._meta.get_fields()
        if field.name in suspicious
    ]
    assert offenders == []
    from workeasy360.intelligence.ai.models import AIProvider

    # The reference, and only the reference.
    assert "credential_ref" in {f.name for f in AIProvider._meta.get_fields()}


def test_intelligence_defines_no_product_business_meaning():
    """Tasks 64, 90: no product code is compared against, no product-named
    skill or metric is built into the platform."""
    from workeasy360.intelligence.ai.skills import PLATFORM_SKILLS

    products = {
        "crm",
        "books",
        "inventory",
        "projects",
        "marketing",
        "support",
        "hr",
        "payroll",
        "commerce",
    }
    offenders = []
    for path, tree in _sources(INTELLIGENCE):
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for side in [node.left, *node.comparators]:
                    if isinstance(side, ast.Constant) and side.value in products:
                        offenders.append(f"{path.relative_to(PACKAGE)}:{node.lineno}")
    assert offenders == []
    assert all(skill["key"].startswith("platform.") for skill in PLATFORM_SKILLS)
    for skill in PLATFORM_SKILLS:
        text = skill["instructions"].lower()
        assert not any(f" {p} " in f" {text} " for p in products), skill["key"]


def test_intelligence_metric_labels_are_bounded():
    """Task 95: labels come from a small, known vocabulary - never an
    organization, a user, a document, a skill or a prompt."""
    allowed = {
        "operation",
        "outcome",
        "provider",
        "kind",
        "reason",
        "result",
        "category",
        "where",
        "charged",
    }
    offenders = []
    for path, tree in _sources(INTELLIGENCE):
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"increment", "observe", "gauge"}
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "metrics"
            ):
                offenders.extend(
                    f"{path.relative_to(PACKAGE)}:{node.lineno} {kw.arg}"
                    for kw in node.keywords
                    if kw.arg not in allowed
                )
    assert offenders == []


def test_nothing_in_intelligence_logs_content():
    """Task 96: log lines carry identifiers and categories - never a prompt,
    an input, an output, a passage or a key."""
    forbidden = {
        "prompt",
        "system",
        "user",
        "input",
        "output",
        "text",
        "content",
        "api_key",
        "query",
        "passage",
    }
    offenders = []
    for path, tree in _sources(INTELLIGENCE):
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr
                in {"debug", "info", "warning", "error", "critical", "exception"}
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"logger", "level"}
            ):
                offenders.extend(
                    f"{path.relative_to(PACKAGE)}:{node.lineno} {kw.arg}"
                    for kw in node.keywords
                    if kw.arg in forbidden
                )
    assert offenders == []


def test_the_frontend_never_renders_html_from_the_server():
    """Task 105: model output, citations and metric labels render as text."""
    frontend = REPO_ROOT / "frontend" / "src"
    if not frontend.exists():  # pragma: no cover - frontend is optional
        pytest.skip("no frontend")
    offenders = [
        str(path.relative_to(frontend))
        for path in frontend.rglob("*.tsx")
        if "dangerouslySetInnerHTML" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_every_phase8_product_endpoint_is_advertised_and_credentialed():
    """A product discovers every Phase 8 endpoint, and every one of them is
    credential-authenticated (A23)."""
    from django.urls import resolve

    from workeasy360.interfaces.api.v1.ecosystem_views import CREDENTIALED_VIEWS
    from workeasy360.platform.ecosystem.discovery import INTELLIGENCE_ENDPOINTS, PATHS

    credentialed = set(CREDENTIALED_VIEWS)
    for name in INTELLIGENCE_ENDPOINTS:
        view = resolve(PATHS[name]).func
        assert view in credentialed, name


def test_every_path_the_ai_client_requests_is_a_real_route():
    """H8 for Phase 8: the frontend's AI and analytics client calls only
    routes that exist, with every template segment filled in."""
    import re
    import uuid

    from django.urls import Resolver404, resolve

    source = (
        REPO_ROOT / "frontend" / "src" / "lib" / "api" / "intelligence.ts"
    ).read_text(encoding="utf-8")
    sample = str(uuid.uuid4())
    paths = []
    for match in re.finditer(r"`([^`]*)`", source):
        raw = match.group(1)
        if raw == "/api/v1/orgs/${encodeURIComponent(orgSlug)}":
            continue  # the org() base-path helper itself, not a request
        raw = raw.replace("${org(orgSlug)}", "/api/v1/orgs/acme").replace(
            "${admin}", "/api/v1/admin/ai"
        )
        raw = re.sub(r"\$\{encodeURIComponent\(key\)\}", "test.primary", raw)
        raw = re.sub(r"\$\{[^}]*\}", sample, raw)
        if raw.startswith("/api/v1/"):
            paths.append(raw)
    assert len(paths) >= 11, paths
    missing = []
    for path in paths:
        try:
            resolve(path)
        except Resolver404:
            missing.append(path)
    assert missing == []

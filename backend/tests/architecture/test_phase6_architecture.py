"""Phase 6 fitness rules: the properties the integration platform is only
safe because of, checked structurally so they cannot erode quietly."""

from __future__ import annotations

import ast
import pathlib

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
PACKAGE = REPO_ROOT / "backend" / "workeasy360"

PRODUCT_CODES = (
    "crm",
    "books",
    "inventory",
    "projects",
    "marketing",
    "support",
    "hr",
    "payroll",
    "commerce",
)


def _runtime_sources():
    for path in PACKAGE.rglob("*.py"):
        rel = path.relative_to(PACKAGE).as_posix()
        if "migrations/" in rel or "/tests/" in rel:
            continue
        yield rel, path


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
        elif isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
    return found


def test_outbound_http_is_confined_to_the_egress_boundary():
    """Task 87: one outbound HTTP abstraction. A socket, TLS or HTTP client
    import anywhere else is a request that skips the SSRF policy, the
    timeouts and the byte limits."""
    clients = {
        "socket",
        "ssl",
        "http.client",
        "urllib.request",
        "urllib3",
        "requests",
        "httpx",
        "aiohttp",
        "pycurl",
    }
    offenders = []
    for rel, path in _runtime_sources():
        if rel.startswith("integration/egress/"):
            continue
        for module in _imports(path):
            if module in clients or any(module.startswith(f"{c}.") for c in clients):
                offenders.append(f"{rel} imports {module}")
    assert not offenders, "Outbound I/O outside integration/egress:\n" + "\n".join(
        offenders
    )


def test_only_the_delivery_pipeline_can_send():
    """Actions and every other module reach the network only by writing a
    durable record the delivery pipeline carries out (Task 40). The layering
    contract lets ``automation`` import ``egress`` (a lower layer) and the
    test above only looks for socket libraries, so an action calling
    ``egress.client.send`` directly would pass both: this closes that. The
    connector contract and providers may import the request/response
    *types*; only the pipeline may import the client module or ``send``.

    Phase 8 adds exactly one more sender: ``intelligence/ai/transport.py``,
    the single module every AI provider call goes through (ADR-030). It sends
    only to an adapter's built-in vendor endpoint - never a customer- or
    product-supplied URL, and production pins the override map empty - so it
    carries none of the customer-endpoint risk this rule exists for, and it
    still gets every egress guarantee.

    The Phase 8 extension adds one more of the same kind:
    ``commercial/payments/transport.py``, the single module every payment
    provider call goes through - built-in vendor endpoints only, the override
    map pinned empty in production.

    Phase 11 adds ``integration/health/probe.py``: a bounded, read-only
    liveness GET against a *registered* product's own manifest URL (never a
    customer- or request-supplied address), called only by the scheduled
    sweep or a staff "check now" action - never while a customer's page
    renders. It carries every egress guarantee already; it is a sender, not
    a delivery pipeline, because a health check has no retry/backoff
    workflow to hand off - the result is either recorded now or the next
    sweep tries again."""
    allowed = {
        "integration/webhooks/pipeline.py",
        "intelligence/ai/transport.py",
        "commercial/payments/transport.py",
        "integration/health/probe.py",
    }
    offenders = []
    for rel, path in _runtime_sources():
        if rel.startswith("integration/egress/") or rel in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                hits = [
                    a.name
                    for a in node.names
                    if a.name == "workeasy360.integration.egress.client"
                ]
            elif isinstance(node, ast.ImportFrom):
                names = {a.name for a in node.names}
                hits = []
                if node.module == "workeasy360.integration.egress" and "client" in names:
                    hits.append("egress.client")
                if node.module == "workeasy360.integration.egress.client" and (
                    "send" in names or "*" in names
                ):
                    hits.append("egress.client.send")
            else:
                continue
            offenders.extend(f"{rel} imports {hit}" for hit in hits)
    assert not offenders, "Outbound sends outside the delivery pipeline:\n" + "\n".join(
        offenders
    )


def test_tls_verification_cannot_be_disabled():
    """Task 89: no switch, no shortcut, no developer override in runtime code."""
    forbidden = (
        "CERT_NONE",
        "check_hostname = False",
        "check_hostname=False",
        "_create_unverified_context",
        "verify=False",
    )
    offenders = [
        f"{rel}: {needle}"
        for rel, path in _runtime_sources()
        for needle in forbidden
        if needle in path.read_text(encoding="utf-8")
    ]
    assert not offenders, "\n".join(offenders)


def test_the_reference_receiver_imports_no_platform_module():
    """Like the reference product client: a receiver has only the documented
    contract, so the one the suite uses must too."""
    path = REPO_ROOT / "backend" / "tests" / "factories" / "webhook_receiver.py"
    offenders = [
        m for m in _imports(path) if m.split(".")[0] in ("workeasy360", "config")
    ]
    assert not offenders


def test_automation_evaluates_no_code():
    """Task 33: nothing in automation reaches eval/exec/compile, a template
    engine or raw SQL."""
    offenders = []
    for rel, path in _runtime_sources():
        if not rel.startswith("integration/automation/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"eval", "exec", "compile", "__import__"}
            ):
                offenders.append(f"{rel}:{node.lineno} calls {node.func.id}")
        for module in _imports(path):
            if module.split(".")[0] in {"jinja2", "mako"} or module == "django.template":
                offenders.append(f"{rel} imports {module}")
        if "RawSQL" in path.read_text(encoding="utf-8") or ".raw(" in path.read_text(
            encoding="utf-8"
        ):
            offenders.append(f"{rel} builds SQL")
    assert not offenders, "\n".join(offenders)


def test_no_registered_action_or_connector_is_product_specific(db):
    """Tasks 29, 35: platform/integration capabilities only. A product's
    domain action is the product's contract, never a platform built-in."""
    from workeasy360.integration.automation.actions import catalog as actions
    from workeasy360.integration.connectors.registry import catalog as connectors

    action_keys = {a["type"] for a in actions()}
    assert action_keys == {"connector.invoke", "notification.send"}
    for entry in connectors():
        assert entry["key"].split(".")[0] not in PRODUCT_CODES
        for capability in entry["capabilities"]:
            assert capability["key"] not in {
                "execute",
                "execute_action",
                "call",
                "invoke",
            }


def test_integration_health_never_gates_readiness():
    """Tasks 70/71: customer endpoints and connectors are Tier 3."""
    from workeasy360.platform.observability import health

    for group in ("startup", "registries"):
        assert "integration" not in health.registered_probe_names(group)
    assert "integration" in health.registered_probe_names("deep")


def test_integration_work_never_runs_on_the_critical_queue():
    """Task 47: an event storm cannot starve the outbox relay or anything
    else on ``critical``. The relay itself is the one Phase 1 exception."""
    from config.celery import app

    offenders = [
        name
        for name, task in app.tasks.items()
        if name.startswith("integration.")
        and name != "integration.events.relay_outbox"
        and getattr(task, "queue", None) == "critical"
    ]
    assert not offenders
    assert app.tasks["integration.webhooks.deliver"].queue == "heavy"


def test_the_due_work_timer_holds_no_tenant_data():
    """ADR-024: the one RLS-exempt Phase 6 table holds identifiers and a
    time, nothing a tenant owns. A payload column here would be a
    cross-tenant data store with no policy."""
    from workeasy360.integration.scheduling.models import DueWork

    columns = {f.name for f in DueWork._meta.get_fields()}
    assert columns == {
        "id",
        "kind",
        "organization_id",
        "target_id",
        "due_at",
        "claims",
        "created_at",
    }


@pytest.mark.django_db
def test_history_that_must_not_change_is_frozen_by_trigger():
    """Tasks 26/39: a replay re-sends the original event, an execution runs
    its pinned version, an attempt record is history - each protected in the
    database, not only by the application."""
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE t.tgname LIKE %s AND NOT t.tgisinternal",
            ["%reject_history_change"],
        )
        frozen = {row[0] for row in cur.fetchall()}
    assert {
        "events_routed",
        "connectors_secret",
        "automation_rule_version",
        "webhooks_attempt",
    } <= frozen


def test_webhook_delivery_uses_the_documented_signature_scheme():
    """The receiver-facing contract (Task 13) is the one the docs state."""
    import hashlib
    import hmac

    from workeasy360.integration.connectors.providers.webhook import signature_header

    header = signature_header(["k"], 10, b"body")
    expected = hmac.new(b"k", b"10.body", hashlib.sha256).hexdigest()
    assert header == f"t=10,v1={expected}"

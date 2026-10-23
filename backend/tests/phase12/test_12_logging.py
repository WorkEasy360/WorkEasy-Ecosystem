"""One log stream, one redaction policy (Phase 12, G-14 and Step 34).

Before Phase 12 a stdlib record - Django's, Celery's, or this codebase's own
``logging.getLogger(...).info("event", extra={...})`` (tenancy's RLS-bypass
audit lines, ``task.failed``) - was printed as its bare message: no JSON, no
fields, and none of the redaction structlog events get.
"""

from __future__ import annotations

import io
import json
import logging

import pytest

from workeasy360.platform.observability.logging import configure_logging, get_logger


@pytest.fixture
def json_logs(settings):
    """Everything the production handler would write, as parsed JSON lines.

    The exact formatter ``configure_logging()`` installs on the root logger,
    attached to an in-memory stream - independent of stdout capture.
    """
    settings.LOG_FORMAT = "json"
    settings.LOG_LEVEL = "INFO"
    root = logging.getLogger()
    saved = list(root.handlers), root.level
    configure_logging()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(root.handlers[0].formatter)
    root.addHandler(handler)
    yield lambda: [
        json.loads(line)
        for line in stream.getvalue().splitlines()
        if line.startswith("{")
    ]
    root.handlers, level = saved
    root.setLevel(level)


@pytest.fixture
def console_logs(settings):
    """What the development handler would write, as one string."""
    settings.LOG_FORMAT = "console"
    settings.LOG_LEVEL = "INFO"
    root = logging.getLogger()
    saved = list(root.handlers), root.level
    configure_logging()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(root.handlers[0].formatter)
    root.addHandler(handler)
    yield stream.getvalue
    root.handlers, level = saved
    root.setLevel(level)


def test_the_console_format_prints_where_an_error_was_never_what_was_in_it(console_logs):
    """The development format used to hand exceptions to Rich, which prints every
    frame's local variables - a password argument included. It prints a plain
    traceback now, as production does."""
    typed = "Violet Harbor Lantern 2049"

    def check_password(password):
        copy = password.upper()
        raise ValueError(f"refused {len(copy)}")

    try:
        check_password(typed)
    except ValueError:
        get_logger("workeasy360.test").exception("password.check.failed")
    output = console_logs()
    assert "password.check.failed" in output and "ValueError" in output
    assert typed not in output and typed.upper() not in output


def test_a_stdlib_record_is_json_with_its_extra_fields_and_redacted(json_logs):
    logging.getLogger("workeasy360.platform.tenancy.unscoped").warning(
        "tenancy.unscoped_access.opened",
        extra={
            "reason": "nightly sweep",
            "scope_module": "commercial.subscriptions",
            "authorization": "Bearer abc.def.ghi",
            "session_id": "s3cr3t-session",
        },
    )
    (line,) = [
        e for e in json_logs() if e.get("event") == "tenancy.unscoped_access.opened"
    ]
    assert line["reason"] == "nightly sweep"
    assert line["scope_module"] == "commercial.subscriptions"
    assert line["authorization"] == "[redacted]"
    assert line["session_id"] == "[redacted]"
    assert line["level"] == "warning"
    assert line["logger"] == "workeasy360.platform.tenancy.unscoped"
    assert {"environment", "release", "timestamp"} <= set(line)


def test_a_structlog_event_is_unchanged(json_logs):
    get_logger("workeasy360.test").info(
        "sso.token.issued", client_id="abc", code_verifier="must-not-appear"
    )
    (line,) = [e for e in json_logs() if e.get("event") == "sso.token.issued"]
    assert line["client_id"] == "abc"
    assert line["code_verifier"] == "[redacted]"


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "Authorization",
        "session_key",
        "csrftoken",
        "mfa_code",
        "otp",
        "db_password",
        "smtp_password",
        "authorization_code",
        "client_assertion",
        "api_key",
        "card_number",
        "private_key",
    ],
)
def test_every_secret_shaped_field_is_redacted_on_the_stdlib_path(json_logs, key):
    logging.getLogger("django.request").error(
        "request.failed", extra={key: "SECRET-VALUE"}
    )
    lines = json_logs()
    assert lines and all("SECRET-VALUE" not in json.dumps(e) for e in lines)


def test_metric_observations_are_bounded_but_totals_exact():
    """Phase 12 (G-34): every request and entitlement check used to append to
    an unbounded per-process list."""
    from workeasy360.platform.observability.metrics import (
        OBSERVATION_WINDOW,
        MetricRegistry,
    )

    registry = MetricRegistry()
    for i in range(OBSERVATION_WINDOW * 5):
        registry.observe("http_request_duration_seconds", 0.001 * (i % 7), route="x")
    assert len(registry.observations("http_request_duration_seconds", route="x")) == (
        OBSERVATION_WINDOW
    )
    snap = registry.snapshot()["http_request_duration_seconds"]
    assert snap["route=x|count"] == OBSERVATION_WINDOW * 5

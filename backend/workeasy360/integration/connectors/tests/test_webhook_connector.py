"""The reference connector: configuration, redaction and the signature
(Tasks 13, 14, 28)."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from workeasy360.integration.connectors.contract import (
    ConnectorConfigError,
    Invocation,
    SecretMaterial,
    outcome_for_response,
)
from workeasy360.integration.connectors.providers.webhook import (
    WEBHOOK_CONNECTOR,
    sign,
    signature_header,
)
from workeasy360.integration.egress.client import EgressResponse
from workeasy360.integration.egress.policy import EgressPolicy

STRICT = EgressPolicy()


def _invocation(**overrides):
    fields = {
        "capability": "deliver_event",
        "delivery_id": "d-1",
        "attempt": 1,
        "timestamp": 1_790_000_000,
        "event_id": "e-1",
        "event_type": "product.example.record.created",
        "correlation_id": "c-1",
        "body": {"schema": "workeasy360.webhook.v1", "event": {"id": "e-1"}},
    }
    fields.update(overrides)
    return Invocation(**fields)


def test_the_signature_is_hmac_sha256_over_timestamp_dot_body():
    """The documented construction, recomputed independently."""
    body = b'{"a":1}'
    expected = hmac.new(b"whsec_test", b"1790000000." + body, hashlib.sha256).hexdigest()
    assert sign("whsec_test", 1_790_000_000, body) == expected


def test_a_rotation_overlap_signs_with_every_valid_secret():
    header = signature_header(["new", "old"], 1_790_000_000, b"{}")
    parts = header.split(",")
    assert parts[0] == "t=1790000000"
    assert [p.split("=", 1)[0] for p in parts[1:]] == ["v1", "v1"]


def test_the_request_is_the_exact_signed_bytes():
    request = WEBHOOK_CONNECTOR.build_request(
        {"url": "https://hooks.example.com/in"},
        _invocation(),
        SecretMaterial(("whsec_one",)),
    )
    headers = dict(request.headers)
    assert request.url == "https://hooks.example.com/in"
    assert json.loads(request.body)["event"]["id"] == "e-1"
    assert headers["X-W360-Signature"] == signature_header(
        ["whsec_one"], 1_790_000_000, request.body
    )
    assert headers["X-W360-Event-Id"] == "e-1"
    assert headers["X-W360-Delivery-Attempt"] == "1"
    # The secret itself is never sent.
    assert "whsec_one" not in request.body.decode() + repr(request.headers)


def test_there_is_no_unsigned_request():
    with pytest.raises(ConnectorConfigError):
        WEBHOOK_CONNECTOR.build_request(
            {"url": "https://hooks.example.com/in"}, _invocation(), SecretMaterial(())
        )


def test_undeclared_capabilities_cannot_be_invoked():
    with pytest.raises(ConnectorConfigError):
        WEBHOOK_CONNECTOR.build_request(
            {"url": "https://hooks.example.com/in"},
            _invocation(capability="execute_anything"),
            SecretMaterial(("s",)),
        )


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"url": ""},
        {"url": "http://hooks.example.com/"},
        {"url": "https://169.254.169.254/"},
        {"url": "https://hooks.example.com/", "headers": {"Authorization": "x"}},
    ],
)
def test_configuration_is_validated_statically(config):
    with pytest.raises(ConnectorConfigError):
        WEBHOOK_CONNECTOR.validate_config(config, policy=STRICT)


def test_the_described_configuration_hides_the_path():
    described = WEBHOOK_CONNECTOR.describe_config(
        {"url": "https://hooks.example.com/services/T0/B0/SECRETTOKEN"}
    )
    assert described == {"destination": "https://hooks.example.com/…"}


@pytest.mark.parametrize(
    ("status", "result", "category"),
    [
        (200, "succeeded", "http_2xx"),
        (204, "succeeded", "http_2xx"),
        (301, "dead", "redirect_refused"),
        (400, "dead", "http_4xx"),
        (401, "dead", "http_4xx"),
        (404, "dead", "http_4xx"),
        (408, "retry", "http_408"),
        (429, "retry", "http_429"),
        (500, "retry", "http_5xx"),
        (503, "retry", "http_5xx"),
    ],
)
def test_http_classification(status, result, category):
    outcome = outcome_for_response(
        EgressResponse(
            status=status, headers={}, body=b"", truncated=False, duration_s=0.1
        )
    )
    assert (outcome.result, outcome.category) == (result, category)


def test_retry_after_is_parsed_in_both_forms():
    seconds = outcome_for_response(
        EgressResponse(429, {"retry-after": "120"}, b"", False, 0.1)
    )
    assert seconds.retry_after_s == 120
    dated = outcome_for_response(
        EgressResponse(
            503, {"retry-after": "Wed, 21 Oct 2099 07:28:00 GMT"}, b"", False, 0.1
        )
    )
    # A date decades away is capped at the sanity bound; the pipeline then
    # clamps it again to WEBHOOK_RETRY_AFTER_MAX_S.
    assert dated.retry_after_s == 7 * 24 * 3600.0
    garbage = outcome_for_response(
        EgressResponse(429, {"retry-after": "soon"}, b"", False, 0.1)
    )
    assert garbage.retry_after_s is None


def test_a_response_excerpt_is_short_and_printable():
    outcome = outcome_for_response(
        EgressResponse(400, {}, b"\x00\x01bad\nsignature" + b"x" * 1000, False, 0.1)
    )
    assert outcome.excerpt.startswith("bad signature")
    assert len(outcome.excerpt) <= 200

"""The reference connector: an HTTPS POST, signed with HMAC-SHA256.

**The signature (Task 13), exactly.** For each delivery attempt::

    signed_payload = f"{t}." + raw_request_body          (bytes)
    v1             = hex(HMAC-SHA256(key=secret, msg=signed_payload))
    X-W360-Signature: t=<t>,v1=<hex>[,v1=<hex>]

- ``t`` is the Unix time (seconds) at which the attempt was signed. It is
  inside the MAC, so it cannot be altered without invalidating the
  signature - which is what makes a timestamp check meaningful to the
  receiver.
- ``secret`` is the connection's signing secret exactly as it was shown to
  the administrator (``whsec_...``), used as UTF-8 bytes.
- The body is the exact bytes sent. Receivers must verify **before**
  parsing JSON: re-serialising a parsed body will not reproduce the bytes.
- During a secret rotation's overlap window there are two ``v1`` entries,
  one per valid secret, so a receiver can switch secrets without downtime.
  A receiver accepts the request if **any** ``v1`` matches.
- The scheme is versioned by its key (``v1``); a future scheme is added as
  ``v2`` alongside, never by changing what ``v1`` means.

**Replay protection (Task 14).** A receiver should (1) reject a ``t`` more
than five minutes from its own clock, (2) compare signatures in constant
time, and (3) remember the ``event.id`` of processed deliveries for at least
that window and ignore repeats - delivery is at-least-once, so repeats
happen legitimately too. The full procedure, with reference code in several
languages that imports nothing from Workeasy360, is in
docs/phase-6/03-webhooks.md.

HMAC and SHA-256 come from the standard library. Nothing here is a custom
cryptographic construction.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from typing import Any, Final
from urllib.parse import urlsplit

from workeasy360.integration.connectors.contract import (
    CapabilitySpec,
    ConnectorConfigError,
    Invocation,
    Outcome,
    SecretMaterial,
    outcome_for_response,
)
from workeasy360.integration.egress.client import EgressResponse, OutboundRequest
from workeasy360.integration.egress.policy import (
    EgressPolicy,
    EgressRejected,
    parse_target,
)

SIGNATURE_HEADER: Final = "X-W360-Signature"
DELIVER_EVENT: Final = "deliver_event"

_CONFIG_FIELDS: Final[frozenset[str]] = frozenset({"url"})


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """``hex(HMAC-SHA256(secret, f"{timestamp}." + body))``."""
    message = str(int(timestamp)).encode("ascii") + b"." + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def signature_header(secrets: Sequence[str], timestamp: int, body: bytes) -> str:
    """``t=<timestamp>,v1=<sig>[,v1=<sig>]`` - one ``v1`` per valid secret."""
    parts = [f"t={int(timestamp)}"]
    parts.extend(f"v1={sign(secret, timestamp, body)}" for secret in secrets)
    return ",".join(parts)


class WebhookConnector:
    key = "webhook"
    version = 1
    label = "Webhook"
    description = (
        "Deliver events to an HTTPS endpoint you control, signed with a secret "
        "only you and Workeasy360 hold."
    )
    capabilities: Mapping[str, CapabilitySpec] = {
        DELIVER_EVENT: CapabilitySpec(
            key=DELIVER_EVENT,
            label="Deliver event",
            description="POST the event as JSON, signed with HMAC-SHA256.",
        )
    }
    generates_secret = True

    def validate_config(
        self, raw: Mapping[str, Any], *, policy: EgressPolicy
    ) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise ConnectorConfigError("Configuration must be an object.")
        unknown = sorted(set(raw) - _CONFIG_FIELDS)
        if unknown:
            raise ConnectorConfigError(
                f"Unknown configuration field(s): {', '.join(unknown)}.",
                field=unknown[0],
            )
        url = raw.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ConnectorConfigError("An endpoint URL is required.", field="url")
        url = url.strip()
        try:
            parse_target(url, policy)
        except EgressRejected as exc:
            raise ConnectorConfigError(exc.message, field="url") from None
        return {"url": url}

    def describe_config(self, config: Mapping[str, Any]) -> dict[str, Any]:
        """The destination's origin, and no more.

        Never the path or query: chat-service webhook URLs carry their token
        there, so the full URL is treated as a credential once saved - shown
        to nobody, audited as its origin only.
        """
        url = str(config.get("url", ""))
        try:
            parts = urlsplit(url)
            origin = f"{parts.scheme}://{parts.netloc.rsplit('@', 1)[-1]}"
        except ValueError:
            origin = ""
        has_path = bool(urlsplit(url).path.strip("/")) if origin else False
        return {"destination": f"{origin}/…" if has_path else origin}

    def build_request(
        self,
        config: Mapping[str, Any],
        invocation: Invocation,
        secrets: SecretMaterial,
    ) -> OutboundRequest:
        if invocation.capability != DELIVER_EVENT:
            raise ConnectorConfigError(
                f"The webhook connector has no capability {invocation.capability!r}."
            )
        if not secrets.secrets:
            raise ConnectorConfigError("The connection has no usable signing secret.")

        body = json.dumps(
            invocation.body, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        headers = (
            ("Content-Type", "application/json"),
            ("X-W360-Event-Id", invocation.event_id),
            ("X-W360-Event-Type", invocation.event_type),
            ("X-W360-Delivery-Id", invocation.delivery_id),
            ("X-W360-Delivery-Attempt", str(invocation.attempt)),
            ("X-W360-Correlation-Id", invocation.correlation_id),
            (
                SIGNATURE_HEADER,
                signature_header(secrets.secrets, invocation.timestamp, body),
            ),
        )
        return OutboundRequest(url=str(config["url"]), body=body, headers=headers)

    def interpret(self, capability: str, response: EgressResponse) -> Outcome:
        return outcome_for_response(response)


WEBHOOK_CONNECTOR: Final = WebhookConnector()

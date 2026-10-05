"""A customer's webhook receiver, written as an outsider would write it.

The counterpart of ``reference_product_client.py``: that file is a product
sending events *in*; this is a customer's system receiving them *out*. Like
it, this file **imports nothing from ``workeasy360``** (asserted by
``test_the_reference_receiver_imports_no_platform_module``) - a receiver in
Go or TypeScript has only the documented contract, and so does this one.

``verify_signature`` is the executable form of the verification procedure in
docs/phase-6/03-webhooks.md: parse ``t`` and every ``v1``, reject a stale
``t``, recompute HMAC-SHA256 over ``f"{t}." + raw_body`` with the secret,
compare in constant time, accept if any ``v1`` matches. Receivers
deduplicate on ``event.id`` because delivery is at-least-once.

The server is a real HTTP server on loopback, so the platform's delivery
pipeline exercises its real egress client - sockets, timeouts, response
bounds - rather than a mock of it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

#: Accept a signature timestamp at most this far from our own clock.
TOLERANCE_S = 300


def verify_signature(
    *, secret: str, header: str, body: bytes, now: float | None = None
) -> bool:
    """The receiver-side check. Constant-time, timestamp-bounded, and
    tolerant of the two ``v1`` values a secret rotation produces."""
    pairs = [item.split("=", 1) for item in header.split(",") if "=" in item]
    timestamps = [value for key, value in pairs if key.strip() == "t"]
    signatures = [value.strip() for key, value in pairs if key.strip() == "v1"]
    if len(timestamps) != 1 or not signatures:
        return False
    try:
        timestamp = int(timestamps[0])
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - timestamp) > TOLERANCE_S:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), f"{timestamp}.".encode("ascii") + body, hashlib.sha256
    ).hexdigest()
    return any(hmac.compare_digest(expected, candidate) for candidate in signatures)


@dataclass
class Received:
    path: str
    headers: dict[str, str]
    body: bytes

    @property
    def json(self) -> dict[str, Any]:
        return json.loads(self.body)


@dataclass
class _Reply:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    delay: float = 0.0


class WebhookReceiver:
    """A loopback HTTP server that records every request it receives.

    Replies are scripted with :meth:`reply` (consumed in order, then the
    default), so a test can make it fail, throttle, redirect or hang.
    """

    def __init__(self, *, default_status: int = 200) -> None:
        self.received: list[Received] = []
        self._replies: list[_Reply] = []
        self._default = _Reply(default_status)
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> str:
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                with receiver._lock:
                    receiver.received.append(
                        Received(
                            path=self.path,
                            headers={k.lower(): v for k, v in self.headers.items()},
                            body=body,
                        )
                    )
                    reply = (
                        receiver._replies.pop(0)
                        if receiver._replies
                        else receiver._default
                    )
                if reply.delay:
                    time.sleep(reply.delay)
                self.send_response(reply.status)
                for name, value in reply.headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(reply.body)))
                self.end_headers()
                self.wfile.write(reply.body)

            def log_message(self, *_args: Any) -> None:  # silence test output
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    # --- scripting ------------------------------------------------------------

    def reply(
        self,
        status: int,
        *,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
        delay: float = 0.0,
        times: int = 1,
    ) -> None:
        with self._lock:
            self._replies.extend(
                _Reply(status, dict(headers or {}), body, delay) for _ in range(times)
            )

    # --- what a real receiver does --------------------------------------------

    def verified(self, secret: str) -> list[Received]:
        """Requests whose signature verifies with ``secret``."""
        return [
            r
            for r in self.received
            if verify_signature(
                secret=secret, header=r.headers.get("x-w360-signature", ""), body=r.body
            )
        ]

    def unique_events(self, secret: str) -> dict[str, Received]:
        """Verified requests, deduplicated on ``event.id`` - which is how a
        receiver makes at-least-once delivery harmless."""
        seen: dict[str, Received] = {}
        for request in self.verified(secret):
            seen.setdefault(request.json["event"]["id"], request)
        return seen

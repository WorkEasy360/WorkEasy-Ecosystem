"""A stand-in payment provider on loopback, written as an outsider would.

Speaks just enough of two providers' public wire formats for the platform's
real adapters to reach it through the real egress client:

- Stripe: ``POST /v1/checkout/sessions`` (form body) and ``POST /v1/refunds``
- Razorpay: ``POST /v1/payment_links`` (JSON) and
  ``POST /v1/payments/<id>/refund``

It records what it received (``requests``: path, headers, raw body) so a test
can assert exactly what left the platform - and can be told to fail
(``fail(path, status, times)``). Imports nothing from ``workeasy360``.
"""

from __future__ import annotations

import json
import threading
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class Seen:
    path: str
    headers: dict[str, str]
    body: bytes

    def form(self) -> dict[str, str]:
        return dict(urllib.parse.parse_qsl(self.body.decode("ascii")))

    def json(self) -> dict[str, Any]:
        return json.loads(self.body.decode("utf-8"))


@dataclass
class FakePaymentProvider:
    requests: list[Seen] = field(default_factory=list)
    failures: dict[str, list[int]] = field(default_factory=dict)
    refund_status: str = "succeeded"
    _server: ThreadingHTTPServer | None = None
    counter: int = 0

    def fail(self, path: str, status: int, times: int = 1) -> None:
        self.failures.setdefault(path, []).extend([status] * times)

    def start(self) -> str:
        provider = self

        class Handler(BaseHTTPRequestHandler):
            disable_nagle_algorithm = True

            def log_message(self, *args: Any) -> None:  # silence
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                seen = Seen(
                    path=self.path,
                    headers={k.lower(): v for k, v in self.headers.items()},
                    body=body,
                )
                provider.requests.append(seen)
                queued = provider.failures.get(self.path)
                if queued:
                    status = queued.pop(0)
                    self._answer(status, {"error": {"message": "failure injected"}})
                    return
                provider.counter += 1
                n = provider.counter
                if self.path == "/v1/checkout/sessions":
                    self._answer(
                        200,
                        {
                            "id": f"cs_test_{n}",
                            "url": f"https://checkout.stripe.com/c/pay/cs_test_{n}",
                        },
                    )
                elif self.path == "/v1/refunds":
                    self._answer(200, {"id": f"re_{n}", "status": provider.refund_status})
                elif self.path == "/v1/payment_links":
                    self._answer(
                        200, {"id": f"plink_{n}", "short_url": f"https://rzp.io/i/{n}"}
                    )
                elif self.path.startswith("/v1/payments/") and self.path.endswith(
                    "/refund"
                ):
                    status = (
                        "processed"
                        if provider.refund_status == "succeeded"
                        else "pending"
                    )
                    self._answer(200, {"id": f"rfnd_{n}", "status": status})
                else:
                    self._answer(404, {"error": "unknown path"})

            def _answer(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

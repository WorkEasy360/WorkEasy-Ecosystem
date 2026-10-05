"""A stand-in AI vendor on loopback, written as an outsider would.

Speaks just enough of two vendors' public wire formats for the gateway's
real adapters to talk to it through the real egress client - sockets,
timeouts, byte caps and all - rather than a mock of either:

- ``POST /v1/messages``          the Anthropic Messages shape
- ``POST /v1/chat/completions``  the OpenAI chat shape
- ``POST /v1/embeddings``        the OpenAI embeddings shape

Like ``webhook_receiver.py`` it imports nothing from ``workeasy360``.

**Answers.** Generation answers with ``text`` (default: a sentence citing
``[S1]`` when the prompt carried a source, else a plain sentence), or with
``json_output`` serialised when set. Embeddings are a deterministic hashed
bag of words (``DIMENSIONS`` components, L2-normalised), so texts that share
words are near each other and the nearest-neighbour behaviour of retrieval is
real, not stubbed.

**Failures.** ``fail(path, status, times=n, delay=s)`` makes the next ``n``
requests to ``path`` answer ``status`` (after ``delay`` seconds): 429, 500,
529, 401, or a delay past the caller's deadline for a timeout.

**What it saw.** ``requests`` records every request's path, headers and JSON
body, so a test can assert what left the platform - the API key header, the
absence of tools, that a secret was redacted before it was sent.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DIMENSIONS = 16
_WORD = re.compile(r"[a-z0-9]+")


def embed(text: str) -> list[float]:
    vector = [0.0] * DIMENSIONS
    for word in _WORD.findall(text.lower()):
        digest = hashlib.sha256(word.encode("utf-8")).digest()
        index = digest[0] % DIMENSIONS
        vector[index] += 1.0 if digest[1] % 2 == 0 else -1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [round(v / norm, 6) for v in vector]


@dataclass
class Seen:
    path: str
    headers: dict[str, str]
    body: dict[str, Any]


@dataclass
class _Failure:
    status: int
    delay: float
    body: bytes


@dataclass
class FakeAIProvider:
    text: str = ""
    json_output: Any = None
    #: Seconds every successful answer takes - for concurrency tests.
    latency: float = 0.0
    #: The most requests it was ever answering at once.
    peak_in_flight: int = 0
    _in_flight: int = 0
    requests: list[Seen] = field(default_factory=list)
    _failures: dict[str, list[_Failure]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _server: ThreadingHTTPServer | None = None
    url: str = ""

    def start(self) -> str:
        provider = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            # A vendor's server does not stall its own reply on Nagle; this
            # one writes headers and body separately, so it must not either.
            disable_nagle_algorithm = True

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    body = {}
                with provider._lock:
                    provider.requests.append(
                        Seen(
                            path=self.path,
                            headers={k.lower(): v for k, v in self.headers.items()},
                            body=body,
                        )
                    )
                    queue = provider._failures.get(self.path) or []
                    failure = queue.pop(0) if queue else None
                    provider._in_flight += 1
                    provider.peak_in_flight = max(
                        provider.peak_in_flight, provider._in_flight
                    )
                try:
                    if failure is not None:
                        if failure.delay:
                            time.sleep(failure.delay)
                        self._reply(failure.status, failure.body)
                        return
                    if provider.latency:
                        time.sleep(provider.latency)
                    self._reply(
                        200, json.dumps(provider._answer(self.path, body)).encode()
                    )
                finally:
                    with provider._lock:
                        provider._in_flight -= 1

            def _reply(self, status: int, payload: bytes) -> None:
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        return self.url

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    # --- scripting ---------------------------------------------------------------

    def fail(
        self,
        path: str,
        status: int,
        *,
        times: int = 1,
        delay: float = 0.0,
        body: Any = None,
    ) -> None:
        payload = json.dumps(
            body
            or {"type": "error", "error": {"type": "api_error", "message": "scripted"}}
        ).encode()
        with self._lock:
            self._failures.setdefault(path, []).extend(
                _Failure(status, delay, payload) for _ in range(times)
            )

    def calls(self, path: str) -> list[Seen]:
        with self._lock:
            return [r for r in self.requests if r.path == path]

    # --- answers -------------------------------------------------------------------

    def _generated(self, prompt: str) -> str:
        if self.json_output is not None:
            return json.dumps(self.json_output)
        if self.text:
            return self.text
        if '<source id="S1"' in prompt:
            return "According to the knowledge base, the answer is documented [S1]."
        return "Here is the answer."

    def _answer(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if path == "/v1/messages":
            prompt = str(body.get("system", "")) + "".join(
                str(m.get("content", "")) for m in body.get("messages", [])
            )
            text = self._generated(prompt)
            return {
                "id": "msg_fake",
                "type": "message",
                "role": "assistant",
                "model": body.get("model"),
                "content": [
                    {"type": "thinking", "thinking": ""},
                    {"type": "text", "text": text},
                ],
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": len(prompt) // 4 + 1,
                    "output_tokens": len(text) // 4 + 1,
                },
            }
        if path == "/v1/chat/completions":
            prompt = "".join(str(m.get("content", "")) for m in body.get("messages", []))
            text = self._generated(prompt)
            return {
                "id": "chatcmpl_fake",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": text},
                    }
                ],
                "usage": {
                    "prompt_tokens": len(prompt) // 4 + 1,
                    "completion_tokens": len(text) // 4 + 1,
                },
            }
        if path == "/v1/embeddings":
            texts = body.get("input") or []
            return {
                "data": [
                    {"object": "embedding", "index": i, "embedding": embed(str(t))}
                    for i, t in enumerate(texts)
                ],
                "usage": {"prompt_tokens": sum(len(str(t)) // 4 + 1 for t in texts)},
            }
        return {"error": "unknown path"}

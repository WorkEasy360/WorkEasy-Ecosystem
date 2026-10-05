"""A small browser-like HTTPS client for the Phase 12 staging rehearsal.

Talks to the real edge (Caddy -> Next.js / Django) over verified TLS: the
staging CA is trusted explicitly (never "verify=False"), the connection goes to
127.0.0.1:<port> with the canonical host as SNI, and cookies/CSRF behave as a
browser's do (Origin + X-CSRFToken from the w360_csrf cookie).

Standard library only, so it runs from the repository's virtualenv without
adding a dependency.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import socket
import ssl
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any

HOST = os.environ.get("W360_STG_HOST", "app.staging.workeasy360.com")
PORT = int(os.environ.get("W360_STG_PORT", "58443"))
CAFILE = os.environ.get("W360_STG_CA", "")
MAILPIT = os.environ.get("W360_STG_MAILPIT", "http://127.0.0.1:58026")


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: Any
    elapsed_ms: float

    def json(self) -> Any:
        return self.body


class Browser:
    def __init__(self, host: str = HOST, port: int = PORT, cafile: str = CAFILE) -> None:
        self.host, self.port = host, port
        self.ctx = ssl.create_default_context(cafile=cafile or None)
        self.cookies: dict[str, str] = {}

    def _conn(self) -> http.client.HTTPSConnection:
        host, port, ctx = self.host, self.port, self.ctx

        class Conn(http.client.HTTPSConnection):
            def connect(inner) -> None:  # noqa: N805
                sock = socket.create_connection(("127.0.0.1", port), inner.timeout)
                inner.sock = ctx.wrap_socket(sock, server_hostname=host)

        return Conn(host, port, context=ctx, timeout=60)

    def request(
        self, method: str, path: str, body: Any = None, *, headers: dict | None = None
    ) -> Response:
        if method not in ("GET", "HEAD") and "w360_csrf" not in self.cookies:
            # A real browser has loaded a page (and so received the CSRF
            # cookie) before it can submit anything.
            self.request("GET", "/api/v1/auth/session")
        h = {
            "Host": self.host,
            "User-Agent": "Mozilla/5.0 (phase12-harness)",
            "Accept": "application/json",
        }
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        data = None
        if method not in ("GET", "HEAD"):
            h["Origin"] = f"https://{self.host}"
            h["Referer"] = f"https://{self.host}/"
            if "w360_csrf" in self.cookies:
                h["X-CSRFToken"] = self.cookies["w360_csrf"]
            h["Idempotency-Key"] = uuid.uuid4().hex
            if body is not None:
                data = json.dumps(body).encode()
                h["Content-Type"] = "application/json"
        h.update(headers or {})
        started = time.perf_counter()
        conn = self._conn()
        try:
            conn.request(method, path, body=data, headers=h)
            resp = conn.getresponse()
            raw = resp.read()
            for name, value in resp.getheaders():
                if name.lower() == "set-cookie":
                    cookie = value.split(";", 1)[0]
                    k, _, v = cookie.partition("=")
                    if "max-age=0" in value.lower() or not v:
                        self.cookies.pop(k.strip(), None)
                    else:
                        self.cookies[k.strip()] = v.strip()
            try:
                parsed: Any = json.loads(raw) if raw else None
            except ValueError:
                parsed = raw.decode(errors="replace")
            return Response(
                resp.status,
                {k.lower(): v for k, v in resp.getheaders()},
                parsed,
                (time.perf_counter() - started) * 1000,
            )
        finally:
            conn.close()

    def get(self, path: str, **kw: Any) -> Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, body: Any = None, **kw: Any) -> Response:
        return self.request("POST", path, body if body is not None else {}, **kw)

    def login(self, email: str, password: str) -> Response:
        self.get("/api/v1/auth/session")
        return self.post("/api/v1/auth/login", {"email": email, "password": password})


# --- Mailpit (the staging SMTP relay) -------------------------------------------------------


def mail_messages(query: str = "") -> list[dict[str, Any]]:
    url = f"{MAILPIT}/api/v1/search?query={urllib.parse.quote(query)}" if query else f"{MAILPIT}/api/v1/messages"
    with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310 - local Mailpit
        return json.load(r).get("messages", [])


def mail_body(message_id: str) -> str:
    with urllib.request.urlopen(f"{MAILPIT}/api/v1/message/{message_id}", timeout=10) as r:  # noqa: S310
        doc = json.load(r)
    return (doc.get("Text") or "") + "\n" + (doc.get("HTML") or "")


def wait_for_mail(to: str, subject_contains: str = "", timeout: float = 60.0) -> dict[str, Any] | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for m in mail_messages(f"to:{to}"):
            if subject_contains.lower() in (m.get("Subject") or "").lower():
                return m
        time.sleep(1)
    return None


def link_in(message_id: str, needle: str) -> str | None:
    for url in re.findall(r"https://[^\s\"'<>]+", mail_body(message_id)):
        if needle in url:
            return url.replace("&amp;", "&")
    return None

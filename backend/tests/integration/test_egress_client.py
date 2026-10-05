"""The outbound HTTP boundary against real sockets (Tasks 15-19, 87-89).

A real HTTP server on loopback - and, for TLS, a real HTTPS server with a
certificate nobody trusts - so every assertion is about bytes on a socket,
not about a mock's idea of them. DNS is the one thing substituted: the
resolver is patched to answer as an attacker's DNS server would, which is
exactly the input DNS rebinding controls.
"""

from __future__ import annotations

import datetime
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from workeasy360.integration.egress import client as egress
from workeasy360.integration.egress.client import EgressFailure, OutboundRequest, Timeouts
from workeasy360.integration.egress.policy import EgressPolicy, EgressRejected

pytestmark = pytest.mark.security

LOCAL = EgressPolicy(allow_http=True, allow_loopback=True)
FAST = Timeouts(connect=1.0, read=1.0, total=3.0, dns=1.0)


class _Recorder:
    def __init__(self):
        self.requests = []
        self.status = 200
        self.headers = {}
        self.body = b"ok"
        self.delay = 0.0


@pytest.fixture
def server():
    recorder = _Recorder()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            recorder.requests.append(
                {
                    "host": self.headers.get("Host"),
                    "body": self.rfile.read(length),
                    "path": self.path,
                    "ua": self.headers.get("User-Agent"),
                }
            )
            if recorder.delay:
                time.sleep(recorder.delay)
            self.send_response(recorder.status)
            for name, value in recorder.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(recorder.body)))
            self.end_headers()
            self.wfile.write(recorder.body)

        def log_message(self, *_args):
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    recorder.port = httpd.server_address[1]
    yield recorder
    httpd.shutdown()
    httpd.server_close()


def _send(url, *, policy=LOCAL, **kwargs):
    return egress.send(
        OutboundRequest(url=url, body=b'{"hello":"world"}'),
        policy=policy,
        timeouts=kwargs.pop("timeouts", FAST),
        **kwargs,
    )


def test_a_request_reaches_the_receiver_with_a_platform_user_agent(server):
    response = _send(f"http://127.0.0.1:{server.port}/hook?x=1")
    assert response.status == 200
    assert server.requests[0]["body"] == b'{"hello":"world"}'
    assert server.requests[0]["path"] == "/hook?x=1"
    assert server.requests[0]["ua"].startswith("Workeasy360")


def test_redirects_are_returned_never_followed(server):
    """A 302 to the metadata service is the classic SSRF bypass (Task 17)."""
    server.status = 302
    server.headers = {"Location": "http://169.254.169.254/latest/meta-data/"}
    response = _send(f"http://127.0.0.1:{server.port}/hook")
    assert response.status == 302
    assert len(server.requests) == 1


def test_an_oversized_response_is_cut_off_not_buffered(server):
    """Task 88: at most max_response_bytes enter memory."""
    server.body = b"x" * 500_000
    response = _send(f"http://127.0.0.1:{server.port}/hook", max_response_bytes=1024)
    assert response.truncated is True
    assert len(response.body) == 1024


def test_a_slow_endpoint_costs_at_most_the_read_timeout(server):
    server.delay = 2.5
    started = time.monotonic()
    with pytest.raises(EgressFailure) as exc_info:
        _send(f"http://127.0.0.1:{server.port}/hook")
    assert exc_info.value.category in {"read_timeout", "deadline_exceeded"}
    assert exc_info.value.retryable
    assert time.monotonic() - started < 3.5


def test_a_refused_connection_is_a_retryable_failure():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(EgressFailure) as exc_info:
        _send(f"http://127.0.0.1:{port}/hook")
    assert exc_info.value.category in {"connect_failed", "connect_timeout"}
    assert exc_info.value.retryable


def test_an_unresolvable_host_is_a_dns_failure():
    policy = EgressPolicy()
    with pytest.raises(EgressFailure) as exc_info:
        _send("https://does-not-exist.invalid/hook", policy=policy)
    assert exc_info.value.category in {"dns_failure", "dns_timeout"}


def test_the_connection_is_pinned_to_the_validated_address(server, monkeypatch):
    """The name is resolved once, validated, and the socket opened to that
    address - while the Host header still names the host. There is no
    second lookup for a rebinding resolver to answer differently."""
    lookups = []

    def resolver(host, port, *args, **kwargs):
        lookups.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr(egress.socket, "getaddrinfo", resolver)
    policy = EgressPolicy(
        allow_http=True, allow_loopback=True, allowed_ports=frozenset({server.port})
    )
    response = _send(f"http://hooks.example.com:{server.port}/hook", policy=policy)
    assert response.status == 200
    # The *name* is resolved exactly once. (``create_connection`` also calls
    # getaddrinfo, with the pinned numeric address - which resolves to itself
    # and is not a lookup of the host.)
    assert [h for h in lookups if not h[:1].isdigit()] == ["hooks.example.com"]
    assert set(lookups) <= {"hooks.example.com", "127.0.0.1"}
    assert server.requests[0]["host"] == f"hooks.example.com:{server.port}"


@pytest.mark.parametrize(
    "answers",
    [
        ["169.254.169.254"],
        ["10.0.0.7"],
        ["93.184.216.34", "10.0.0.7"],  # one public, one private: refused whole
        ["::ffff:127.0.0.1"],
    ],
)
def test_dns_rebinding_to_a_private_address_is_refused(monkeypatch, answers):
    """A hostname that passed save-time validation resolves, at send time, to
    an internal address. The strict production policy refuses it before a
    socket exists (Tasks 15/16)."""

    def resolver(host, port, *args, **kwargs):
        family = {True: socket.AF_INET6, False: socket.AF_INET}
        return [(family[":" in a], socket.SOCK_STREAM, 6, "", (a, port)) for a in answers]

    monkeypatch.setattr(egress.socket, "getaddrinfo", resolver)
    connected = []
    monkeypatch.setattr(
        egress.socket,
        "create_connection",
        lambda *a, **k: connected.append(a) or pytest.fail("must not connect"),
    )
    with pytest.raises(EgressRejected) as exc_info:
        _send("https://hooks.example.com/hook", policy=EgressPolicy())
    assert exc_info.value.reason == "address_blocked"
    assert connected == []


def test_an_untrusted_certificate_is_refused(tmp_path):
    """Task 89: verification is on and cannot be switched off."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "c.pem", tmp_path / "k.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        port = httpd.server_address[1]
        policy = EgressPolicy(allow_loopback=True)
        with pytest.raises(EgressFailure) as exc_info:
            _send(f"https://localhost:{port}/hook", policy=policy)
        assert exc_info.value.category == "tls_error"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_an_oversized_request_is_refused_before_sending(server):
    with pytest.raises(EgressFailure) as exc_info:
        egress.send(
            OutboundRequest(url=f"http://127.0.0.1:{server.port}/", body=b"x" * 2048),
            policy=LOCAL,
            timeouts=FAST,
            max_request_bytes=1024,
        )
    assert exc_info.value.category == "request_too_large"
    assert not exc_info.value.retryable
    assert server.requests == []


# --- Phase 6 audit: the overall deadline is real ---------------------------------


def _raw_server(script):
    """A loopback TCP server that runs ``script(conn)`` per connection: the
    peer misbehaves at the byte level, below anything an HTTP server does."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)

    def serve():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return

            def handle(conn=conn):
                try:
                    conn.recv(65536)
                    script(conn)
                except OSError:
                    pass
                finally:
                    conn.close()

            threading.Thread(target=handle, daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    return listener


def _trickle(prefix, unit, *, every=0.3, times=100):
    def script(conn):
        if prefix:
            conn.sendall(prefix)
        for _ in range(times):
            conn.sendall(unit)
            time.sleep(every)

    return script


def _hang_after(prefix, seconds=30):
    def script(conn):
        conn.sendall(prefix)
        time.sleep(seconds)

    return script


@pytest.mark.parametrize(
    "script",
    [
        # Each unit arrives well inside the 1 s read timeout, so no single
        # read ever times out; only the overall deadline can end these.
        _trickle(b"HTTP/1.1 200 OK\r\n", b"X-Slow: 1\r\n"),
        _trickle(b"", b"HTTP/1.1 100 Continue\r\n\r\n"),
        _trickle(b"HTTP/1.1 2", b"0"),
    ],
    ids=["headers", "interim-responses", "status-line"],
)
def test_a_trickling_endpoint_is_cut_off_at_the_overall_deadline(script):
    listener = _raw_server(script)
    try:
        port = listener.getsockname()[1]
        started = time.monotonic()
        with pytest.raises(EgressFailure) as exc_info:
            _send(f"http://127.0.0.1:{port}/hook")
        elapsed = time.monotonic() - started
    finally:
        listener.close()
    assert exc_info.value.category == "deadline_exceeded"
    assert exc_info.value.retryable
    assert elapsed < FAST.total + 1.0, f"held for {elapsed:.1f}s"


def test_a_trickled_tls_handshake_is_cut_off_at_the_overall_deadline():
    # A TLS record header announcing 16 KiB, then one byte at a time.
    listener = _raw_server(_trickle(b"\x16\x03\x03\x40\x00", b"\x02", every=0.2))
    try:
        port = listener.getsockname()[1]
        started = time.monotonic()
        with pytest.raises(EgressFailure) as exc_info:
            _send(
                f"https://127.0.0.1:{port}/hook",
                policy=EgressPolicy(allow_loopback=True, allowed_ports=frozenset({port})),
            )
        elapsed = time.monotonic() - started
    finally:
        listener.close()
    assert exc_info.value.category in {"tls_error", "deadline_exceeded"}
    assert elapsed < FAST.total + 1.0


@pytest.mark.parametrize(
    "script",
    [
        _trickle(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n", b"x"),
        # No Content-Length and the connection never closes.
        _hang_after(b"HTTP/1.1 200 OK\r\n\r\nstarted"),
    ],
    ids=["trickled-body", "endless-body"],
)
def test_a_received_status_survives_a_body_that_never_finishes(script):
    """Once the status line is in, it is the receiver's answer. A body that
    trickles or never ends is cut off (``truncated``), not turned into a
    failure - which would re-send a delivery the receiver already accepted."""
    listener = _raw_server(script)
    try:
        port = listener.getsockname()[1]
        started = time.monotonic()
        response = _send(f"http://127.0.0.1:{port}/hook")
        elapsed = time.monotonic() - started
    finally:
        listener.close()
    assert response.status == 200
    assert response.truncated is True
    assert elapsed < FAST.total + 1.0


# --- Phase 6 audit: TLS is verified against the hostname, not the pinned IP -------


@pytest.fixture
def pki(tmp_path):
    """A private CA the client is made to trust, and a factory for leaf
    certificates it signs - so a failure below can only be the hostname or
    validity check, never "unknown issuer"."""
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "W360 audit CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = tmp_path / "ca.pem"
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))

    def leaf(name, sans, *, expired=False):
        key = ec.generate_private_key(ec.SECP256R1())
        if expired:
            start, end = (
                now - datetime.timedelta(days=10),
                now - datetime.timedelta(days=1),
            )
        else:
            start, end = (
                now - datetime.timedelta(days=1),
                now + datetime.timedelta(days=1),
            )
        entries = [
            x509.IPAddress(ipaddress.ip_address(s)) if s[0].isdigit() else x509.DNSName(s)
            for s in sans
        ]
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, sans[0])]))
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(start)
            .not_valid_after(end)
            .add_extension(x509.SubjectAlternativeName(entries), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        cert_path, key_path = tmp_path / f"{name}.pem", tmp_path / f"{name}.key"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        return cert_path, key_path

    return ca_path, leaf


@pytest.fixture
def tls_receiver(pki, monkeypatch):
    """An HTTPS server on loopback presenting a chosen certificate. Every
    lookup is pinned to it, and the client trusts the private CA - with
    hostname checking exactly as in production."""
    ca_path, leaf = pki
    context = ssl.create_default_context(cafile=str(ca_path))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    monkeypatch.setattr(egress, "_TLS_CONTEXT", context)
    started = []

    def start(sans, *, expired=False):
        cert_path, key_path = leaf(f"leaf{len(started)}", sans, expired=expired)
        seen = {"sni": None, "host": None}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                seen["host"] = self.headers.get("Host")
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *_args):
                return

        def remember_sni(_sslobj, server_name, _ctx):
            seen["sni"] = server_name

        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(cert_path, key_path)
        server_context.sni_callback = remember_sni
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        httpd.daemon_threads = True
        httpd.socket = server_context.wrap_socket(httpd.socket, server_side=True)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        port = httpd.server_address[1]

        def resolver(host, rport, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", rport))]

        monkeypatch.setattr(egress.socket, "getaddrinfo", resolver)
        policy = EgressPolicy(allow_loopback=True, allowed_ports=frozenset({port}))
        return port, policy, seen

    yield start
    for httpd in started:
        httpd.shutdown()
        httpd.server_close()


def test_tls_to_a_pinned_address_verifies_the_hostname_and_sends_it_as_sni(
    tls_receiver,
):
    port, policy, seen = tls_receiver(["hooks.example.com"])
    response = _send(f"https://hooks.example.com:{port}/hook", policy=policy)
    assert response.status == 200
    # Connected to 127.0.0.1, yet the handshake and the request both name
    # the host the administrator configured.
    assert seen["sni"] == "hooks.example.com"
    assert seen["host"] == f"hooks.example.com:{port}"


@pytest.mark.parametrize(
    ("sans", "expired"),
    [
        (["other.example.com"], False),  # valid, but for another host
        (["127.0.0.1"], False),  # valid for the pinned IP, not for the host
        (["hooks.example.com"], True),  # the right host, expired
    ],
    ids=["other-host", "ip-only", "expired"],
)
def test_tls_to_a_pinned_address_refuses_a_certificate_not_valid_for_the_host(
    tls_receiver, sans, expired
):
    port, policy, _seen = tls_receiver(sans, expired=expired)
    with pytest.raises(EgressFailure) as exc_info:
        _send(f"https://hooks.example.com:{port}/hook", policy=policy)
    assert exc_info.value.category == "tls_error"

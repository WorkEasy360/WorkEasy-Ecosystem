"""The outbound HTTP client: resolve, validate every address, pin, send.

Built on the standard library (``socket``, ``ssl``, ``http.client``) rather
than on ``requests``/``httpx``. Not from a dislike of either - they are both
excellent - but because the property this module exists for, "connect only
to an address validated in this same call", is one neither exposes as a
supported API: both resolve the host themselves inside their connection
pools, which is precisely the second lookup DNS rebinding exploits. The
standard library lets the socket be opened to a chosen address and then
handed to ``http.client`` untouched, with TLS verified against the hostname.
No HTTP protocol is implemented here; framing, chunked decoding and header
parsing are all ``http.client``'s.

Bulkheads live one level up. This client makes exactly one request on one
fresh connection and closes it (``Connection: close``); there is no pool to
exhaust and nothing is shared between destinations. Concurrency per
connection and per organization is bounded by the delivery pipeline's claim
logic, and total concurrency by the worker pool that runs it.
"""

from __future__ import annotations

import http.client
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass
from typing import Final

from workeasy360.integration.egress.policy import (
    EgressPolicy,
    EgressRejected,
    Target,
    check_address,
    parse_target,
)

USER_AGENT: Final = "Workeasy360-Integrations/1.0"

#: Resolved addresses tried per request. A name with hundreds of records is
#: not a webhook endpoint.
MAX_ADDRESSES: Final = 8

#: Response headers kept, and the length each is truncated to.
_MAX_HEADERS: Final = 64
_MAX_HEADER_VALUE: Final = 256

_READ_CHUNK: Final = 8192

#: ``getaddrinfo`` takes no timeout. Resolution therefore runs on a small,
#: bounded pool and is abandoned at the DNS deadline: a hung resolver costs
#: at most this many threads, never a worker.
_resolver_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="w360-egress-dns")


@dataclass(frozen=True, slots=True)
class OutboundRequest:
    """One request. Built by a connector; carried here unmodified."""

    url: str
    body: bytes
    headers: tuple[tuple[str, str], ...] = ()
    method: str = "POST"


@dataclass(frozen=True, slots=True)
class Timeouts:
    """Per-step bounds plus one overall deadline (09-failure-resilience.md
    section 4.2: customer webhook delivery 3 s connect, 10 s read, 15 s
    total)."""

    connect: float = 3.0
    read: float = 10.0
    total: float = 15.0
    dns: float = 2.0

    @classmethod
    def from_settings(cls) -> Timeouts:
        from django.conf import settings

        return cls(
            connect=float(settings.INTEGRATION_EGRESS_CONNECT_TIMEOUT_S),
            read=float(settings.INTEGRATION_EGRESS_READ_TIMEOUT_S),
            total=float(settings.INTEGRATION_EGRESS_TOTAL_TIMEOUT_S),
            dns=float(settings.INTEGRATION_EGRESS_DNS_TIMEOUT_S),
        )


@dataclass(frozen=True, slots=True)
class EgressResponse:
    status: int
    #: Lower-cased names; bounded in count and value length.
    headers: dict[str, str]
    #: At most ``max_response_bytes``. Never the whole of an oversized body.
    body: bytes
    truncated: bool
    duration_s: float


class EgressFailure(Exception):
    """The request could not be completed.

    Whether it *reached* the destination is unknowable for some categories
    (a read timeout may follow a fully processed request), which is exactly
    why delivery is at-least-once rather than exactly-once.

    ``category`` is a bounded code, safe as a metric label.
    """

    def __init__(
        self,
        category: str,
        message: str,
        *,
        retryable: bool = True,
        duration_s: float = 0.0,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.retryable = retryable
        self.duration_s = duration_s


def send(
    request: OutboundRequest,
    *,
    policy: EgressPolicy | None = None,
    timeouts: Timeouts | None = None,
    max_response_bytes: int | None = None,
    max_request_bytes: int | None = None,
) -> EgressResponse:
    """Send ``request`` under every guarantee in this package's docstring.

    Raises :class:`EgressRejected` (policy, never retryable) or
    :class:`EgressFailure` (transport). Any HTTP status - including a 3xx,
    which is never followed - is a *response*, returned for the caller to
    classify.
    """
    from django.conf import settings

    from workeasy360.platform.observability.metrics import metrics

    policy = policy or EgressPolicy.from_settings()
    timeouts = timeouts or Timeouts.from_settings()
    response_cap = int(
        max_response_bytes
        if max_response_bytes is not None
        else settings.INTEGRATION_EGRESS_MAX_RESPONSE_BYTES
    )
    request_cap = int(
        max_request_bytes
        if max_request_bytes is not None
        else settings.INTEGRATION_EGRESS_MAX_REQUEST_BYTES
    )

    started = time.monotonic()
    deadline = started + timeouts.total

    def remaining() -> float:
        left = deadline - time.monotonic()
        if left <= 0:
            raise EgressFailure(
                "deadline_exceeded",
                "The request exceeded its overall time budget.",
                duration_s=time.monotonic() - started,
            )
        return left

    watchdog = _Deadline(timeouts.total)
    try:
        if len(request.body) > request_cap:
            raise EgressFailure(
                "request_too_large",
                f"The request body exceeds {request_cap} bytes.",
                retryable=False,
            )
        target = parse_target(request.url, policy)
        addresses = _resolve(target, timeout=min(timeouts.dns, remaining()))
        # Every record, not only the one we will use: a host publishing one
        # public and one private address is refused as a whole.
        for address in addresses:
            check_address(address, policy)
        sock = _connect(
            target, addresses, timeouts=timeouts, remaining=remaining, watchdog=watchdog
        )
        response = _exchange(
            sock,
            target,
            request,
            timeouts=timeouts,
            remaining=remaining,
            response_cap=response_cap,
            watchdog=watchdog,
        )
    except EgressRejected as exc:
        metrics.increment("egress_rejected_total", reason=exc.reason)
        raise
    except EgressFailure as exc:
        exc.duration_s = time.monotonic() - started
        metrics.increment("egress_requests_total", outcome=exc.category)
        raise
    finally:
        watchdog.finish()

    duration = time.monotonic() - started
    response = EgressResponse(
        status=response.status,
        headers=response.headers,
        body=response.body,
        truncated=response.truncated,
        duration_s=duration,
    )
    metrics.increment("egress_requests_total", outcome=f"{response.status // 100}xx")
    metrics.observe("egress_request_duration_seconds", duration)
    return response


def _resolve(target: Target, *, timeout: float) -> list[str]:
    if target.is_literal_address:
        return [target.host]
    future = _resolver_pool.submit(
        socket.getaddrinfo, target.host, target.port, 0, socket.SOCK_STREAM
    )
    try:
        infos = future.result(timeout=timeout)
    except FuturesTimeout:
        future.cancel()
        raise EgressFailure("dns_timeout", "DNS resolution timed out.") from None
    except (socket.gaierror, UnicodeError, OSError):
        raise EgressFailure(
            "dns_failure", "The host name could not be resolved."
        ) from None

    addresses: list[str] = []
    for info in infos:
        address = str(info[4][0]).split("%", 1)[0]
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise EgressFailure("dns_failure", "The host name resolved to no address.")
    return addresses[:MAX_ADDRESSES]


class _Deadline:
    """Enforces the overall deadline on the request's socket, whatever it is
    blocked in.

    Socket timeouts bound each ``recv`` separately, and ``http.client`` and
    OpenSSL loop over as many ``recv`` calls as the peer makes them: a
    handshake, a status line, headers, an endless run of ``100 Continue``
    responses or a body trickled one byte per read timeout never trips a
    per-read timeout, and would hold the worker for as long as the peer
    liked. So a timer shuts the socket down at the deadline; the blocked
    read returns at once and the request fails as ``deadline_exceeded``.

    The shutdown happens under a lock that :meth:`finish` also takes, and
    ``finish`` runs before the socket is closed, so the timer can never act
    on a descriptor that has been closed and reused.
    """

    def __init__(self, seconds: float) -> None:
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._finished = False
        self.expired = False
        self._timer = threading.Timer(max(seconds, 0.0), self._expire)
        self._timer.daemon = True
        self._timer.start()

    def watch(self, sock: socket.socket) -> None:
        with self._lock:
            self._sock = sock
            if self.expired:
                self._shutdown(sock)

    def finish(self) -> None:
        with self._lock:
            self._finished = True
            self._sock = None
        self._timer.cancel()

    def _expire(self) -> None:
        with self._lock:
            if self._finished:
                return
            self.expired = True
            if self._sock is not None:
                self._shutdown(self._sock)

    @staticmethod
    def _shutdown(sock: socket.socket) -> None:
        # The plain-socket method, also for a TLS socket: it acts on the
        # descriptor without touching the TLS object the reading thread is
        # inside.
        try:
            socket.socket.shutdown(sock, socket.SHUT_RDWR)
        except OSError:
            pass


def _connect(
    target: Target,
    addresses: list[str],
    *,
    timeouts: Timeouts,
    remaining,
    watchdog: _Deadline,
) -> socket.socket:
    """Open a connection to one of the *validated* addresses.

    ``create_connection`` is given an address literal, so it performs no
    lookup of its own - which is the whole point.
    """
    last_error: Exception | None = None
    timed_out = False
    for address in addresses:
        try:
            sock = socket.create_connection(
                (address, target.port), timeout=min(timeouts.connect, remaining())
            )
        except TimeoutError as exc:
            last_error, timed_out = exc, True
            continue
        except OSError as exc:
            last_error = exc
            continue
        # http.client writes the headers and the body in separate sends. With
        # Nagle's algorithm on, the body waits for the ACK of the headers,
        # which a receiver that is itself waiting for the body delays (40 ms
        # on Linux, 200 ms on Windows) - on every request. Every request here
        # is written whole and at once, so there is nothing to coalesce.
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        watchdog.watch(sock)
        if target.scheme != "https":
            return sock
        tls: ssl.SSLSocket | None = None
        try:
            sock.settimeout(min(timeouts.connect, remaining()))
            # Handshake after the TLS socket is under the watchdog: a peer
            # trickling its certificate is bounded by the deadline too.
            tls = _tls_context().wrap_socket(
                sock, server_hostname=target.host, do_handshake_on_connect=False
            )
            watchdog.watch(tls)
            tls.do_handshake()
            return tls
        except ssl.SSLCertVerificationError:
            watchdog.finish()
            (tls or sock).close()
            raise EgressFailure(
                "tls_error", "The endpoint's TLS certificate did not verify."
            ) from None
        except EgressFailure:
            watchdog.finish()
            (tls or sock).close()
            raise
        except (ssl.SSLError, TimeoutError, OSError):
            expired = watchdog.expired
            watchdog.finish()
            (tls or sock).close()
            if expired:
                raise EgressFailure(
                    "deadline_exceeded", "The request exceeded its overall time budget."
                ) from None
            raise EgressFailure(
                "tls_error", "The TLS handshake with the endpoint failed."
            ) from None

    if timed_out:
        raise EgressFailure("connect_timeout", "Connecting to the endpoint timed out.")
    raise EgressFailure(
        "connect_failed",
        "The endpoint refused or dropped the connection."
        if last_error is not None
        else "No address to connect to.",
    )


@dataclass(frozen=True, slots=True)
class _RawResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    truncated: bool


def _exchange(
    sock: socket.socket,
    target: Target,
    request: OutboundRequest,
    *,
    timeouts: Timeouts,
    remaining,
    response_cap: int,
    watchdog: _Deadline,
) -> _RawResponse:
    connection = http.client.HTTPConnection(target.host, target.port)
    # Hand http.client the socket we opened and validated. It never opens
    # one of its own while ``sock`` is set.
    connection.sock = sock
    try:
        sock.settimeout(min(timeouts.read, remaining()))
        connection.putrequest(
            request.method,
            target.request_target,
            skip_host=True,
            skip_accept_encoding=True,
        )
        connection.putheader("Host", target.host_header)
        connection.putheader("User-Agent", USER_AGENT)
        connection.putheader("Content-Length", str(len(request.body)))
        connection.putheader("Connection", "close")
        for name, value in request.headers:
            connection.putheader(name, value)
        connection.endheaders(request.body)

        sock.settimeout(min(timeouts.read, remaining()))
        response = connection.getresponse()
        if watchdog.expired:
            # http.client takes an early EOF inside the header block for the
            # end of the headers, so a deadline that cut the headers off
            # returns a partial response here instead of raising.
            raise EgressFailure(
                "deadline_exceeded", "The request exceeded its overall time budget."
            )

        chunks: list[bytes] = []
        received = 0
        truncated = False
        # From here the status is known, and it is the receiver's answer. The
        # body is read only for a diagnostic excerpt, so a body that is slow,
        # never ends or breaks off ends the read - it does not turn a
        # received 2xx into a failure, which would re-send a delivery the
        # receiver has already accepted. http.client closes the socket itself
        # once the body is complete, so reading stops there too.
        while not response.isclosed():
            try:
                sock.settimeout(min(timeouts.read, remaining()))
                chunk = response.read(min(_READ_CHUNK, response_cap + 1 - received))
            except (EgressFailure, TimeoutError, http.client.HTTPException, OSError):
                truncated = True
                break
            if not chunk:
                break
            chunks.append(chunk)
            received += len(chunk)
            if received > response_cap:
                # Stop reading: the rest is never pulled into memory.
                truncated = True
                break
        if watchdog.expired:
            # The deadline cut the body off (the read saw an early EOF).
            truncated = True

        headers: dict[str, str] = {}
        for name, value in response.getheaders()[:_MAX_HEADERS]:
            headers[name.lower()] = value[:_MAX_HEADER_VALUE]
        return _RawResponse(
            status=int(response.status),
            headers=headers,
            body=b"".join(chunks)[:response_cap],
            truncated=truncated,
        )
    except (TimeoutError, http.client.HTTPException, OSError) as exc:
        if watchdog.expired:
            raise EgressFailure(
                "deadline_exceeded", "The request exceeded its overall time budget."
            ) from None
        if isinstance(exc, TimeoutError):
            raise EgressFailure(
                "read_timeout", "The endpoint did not respond in time."
            ) from None
        if isinstance(exc, http.client.HTTPException):
            raise EgressFailure(
                "protocol_error", "The endpoint returned a malformed HTTP response."
            ) from None
        raise EgressFailure(
            "connection_lost", "The connection to the endpoint was lost."
        ) from None
    except ValueError:
        # http.client refuses a header value containing CR/LF. Connectors
        # build every header themselves, so this is a bug, not a retry.
        raise EgressFailure(
            "request_invalid", "The request could not be encoded.", retryable=False
        ) from None
    finally:
        # Before the close, so the watchdog can never shut down a descriptor
        # that has been closed and handed to another connection.
        watchdog.finish()
        connection.close()


_TLS_CONTEXT: ssl.SSLContext | None = None


def _tls_context() -> ssl.SSLContext:
    """Certificate and hostname verification on, TLS 1.2 minimum.

    ``create_default_context`` sets ``CERT_REQUIRED`` and ``check_hostname``;
    nothing here relaxes either, and nothing may.
    """
    global _TLS_CONTEXT
    if _TLS_CONTEXT is None:
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        _TLS_CONTEXT = context
    return _TLS_CONTEXT

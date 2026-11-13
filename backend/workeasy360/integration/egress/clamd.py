"""ClamAV's ``clamd`` over TCP: the files platform's real malware scanner (2026-11-10).

It lives here, in the outbound boundary, because it opens a socket: every
socket the application opens is in ``integration/egress`` so one directory holds
all of the platform's ways out (the architecture suite pins that). The files
platform sits below integration and cannot import this module, so
:class:`EgressConfig` registers it with ``platform.files.scanning`` at start-up,
and ``FILES_MALWARE_SCANNER = "clamd"`` selects it.

The destination is an operator-set service on the deployment's own network
(``CLAMD_HOST``/``CLAMD_PORT``), never anything a user typed, so the HTTP
client's public-address policy does not apply; a timeout bounds every call.
The content is streamed with ``INSTREAM`` so the daemon never needs the file
system the files live on. No answer, a refused connection or a reply that is
neither "OK" nor "FOUND" is :class:`ScannerUnavailable` - never "clean".
"""

from __future__ import annotations

import socket
import struct
from typing import Any, Final

from django.conf import settings

from workeasy360.contracts.ports.files import ScanResult
from workeasy360.platform.files.scanning import ScannerUnavailable, parse_reply

_CHUNK: Final = 64 * 1024


class ClamdScanner:
    """ClamAV over TCP (``clamd``), streaming the content with ``INSTREAM``."""

    name = "clamd"

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    def scan_stream(self, handle: Any) -> ScanResult:
        try:
            with socket.create_connection(
                (self.host, self.port), timeout=self.timeout
            ) as sock:
                sock.sendall(b"zINSTREAM\0")
                while True:
                    chunk = handle.read(_CHUNK)
                    if not chunk:
                        break
                    sock.sendall(struct.pack("!L", len(chunk)) + chunk)
                sock.sendall(struct.pack("!L", 0))
                reply = b""
                while not reply.endswith(b"\0"):
                    part = sock.recv(4096)
                    if not part:
                        break
                    reply += part
        except OSError as exc:
            raise ScannerUnavailable(type(exc).__name__) from exc
        return parse_reply(reply, scanner=self.name)

    def scan(self, key: str) -> ScanResult:
        from workeasy360.platform.files.storage import get_store

        with get_store().get(key) as handle:
            return self.scan_stream(handle)


def from_settings() -> ClamdScanner:
    """The scanner as this deployment configures it, read at each use."""
    return ClamdScanner(
        host=str(getattr(settings, "CLAMD_HOST", "clamav")),
        port=int(getattr(settings, "CLAMD_PORT", 3310)),
        timeout=float(getattr(settings, "CLAMD_TIMEOUT_SECONDS", 30)),
    )

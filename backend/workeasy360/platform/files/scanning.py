"""Malware scanning of stored files (2026-11-10).

The files platform has always quarantined uploads and had a ``MalwareScanner``
port, with a ``NullScanner`` that answers "clean" for allowlisted types. That is
acceptable for a partner logo or a product screenshot, whose bytes are sniffed
and limited to images. It is **not** acceptable for KYC documents: PDFs from
people the platform does not yet know, opened by its own staff.

Two things here:

- **A real scanner**, chosen by ``FILES_MALWARE_SCANNER``: ``"clamd"`` is
  ClamAV's daemon over TCP. Its adapter opens a socket, so it lives in
  ``integration/egress/clamd.py`` with every other way out, and registers itself
  here at start-up (:func:`register_scanner`): this module only parses replies
  and decides.
- **Strict modules.** A file whose ``module`` is in ``FILES_STRICT_SCAN_MODULES``
  (``partner_kyc``) becomes servable only when a real scanner has said it is
  clean. With no scanner configured, or a scanner that cannot be reached or
  answers anything but "clean"/"found", the file **stays quarantined**: it cannot
  be opened, and nothing may treat it as safe. That is the fail-closed rule.
  ``platform.files.rescan_quarantined`` retries those files on a schedule, so a
  scanner that comes back clears the queue without anyone re-uploading.

Nothing here reads a file's bytes into a log, and an infected file's content is
deleted by ``services.mark_scanned``; only the signature name is kept.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final, Protocol

from django.conf import settings
from django.utils import timezone

from workeasy360.contracts.ports.files import ScanResult
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

#: The scanner name recorded on a file that waits because no real scanner is
#: configured (strict modules only).
NO_SCANNER: Final = "none"

#: Recorded when the scanner could not give an answer; the file stays quarantined.
SCAN_ERROR: Final = "error"


class ScannerUnavailable(Exception):
    """The scanner could not give a verdict. Never treated as clean."""


class Scanner(Protocol):
    """What a registered scanner offers: a verdict for a stored key or a stream."""

    name: str

    def scan(self, key: str) -> ScanResult: ...

    def scan_stream(self, handle: object) -> ScanResult: ...


#: Scanner factories by ``FILES_MALWARE_SCANNER`` name, registered at start-up by
#: the layer that owns the transport (``integration.egress`` for ``clamd``).
_factories: dict[str, Callable[[], Scanner]] = {}


def register_scanner(name: str, factory: Callable[[], Scanner]) -> None:
    """Make a real scanner selectable by name. Called from an ``AppConfig.ready``."""
    _factories[name] = factory


def parse_reply(reply: bytes, *, scanner: str) -> ScanResult:
    """``stream: OK`` is clean; ``stream: <name> FOUND`` is infected; anything
    else - an error, a size limit, an empty answer - is no verdict at all."""
    text = reply.rstrip(b"\0").decode("utf-8", "replace").strip()
    now = timezone.now()
    if text.endswith(" OK") or text == "OK":
        return ScanResult(clean=True, scanner=scanner, scanned_at=now)
    if text.endswith(" FOUND"):
        body = text.split(":", 1)[-1].strip()
        signature = body[: -len(" FOUND")].strip()[:255]
        return ScanResult(
            clean=False, scanner=scanner, signature=signature, scanned_at=now
        )
    raise ScannerUnavailable(text[:120] or "empty reply")


def configured_scanner() -> Scanner | None:
    """The real scanner this deployment has, or ``None``.

    A name with no registered adapter is reported and treated as no scanner:
    strict files then keep waiting in quarantine (fail closed), never "clean".
    """
    choice = str(getattr(settings, "FILES_MALWARE_SCANNER", "none") or "none")
    if choice == "none":
        return None
    factory = _factories.get(choice)
    if factory is None:
        logger.error("files.scanner.not_registered", scanner=choice)
        return None
    return factory()


def is_strict(module: str) -> bool:
    """Whether files of this module need a real scanner's verdict to be served."""
    if not bool(getattr(settings, "KYC_MALWARE_SCAN_REQUIRED", True)):
        return False
    return module in tuple(
        getattr(settings, "FILES_STRICT_SCAN_MODULES", ("partner_kyc",))
    )


def scan_state(status: str, scanner: str) -> str:
    """A file's scan as a screen says it: ``clean``, ``infected``, ``pending``
    (waiting for a scanner, or the scanner gave no verdict) or ``not_required``
    (served on the allowlist check alone, outside a strict module)."""
    if status == "infected":
        return "infected"
    if status == "clean":
        return "clean" if scanner not in ("", "null") else "not_required"
    return "pending"

"""Read a product's WorkEasy360 sign-in compatibility document (ADR-074).

A bounded, read-only GET of ``<application origin>/.well-known/workeasy360-sso``
for automatic sign-in setup - the same kind of send as ``probe.py``: the
address is a *registered* product's own (its catalog application URL, entered
by staff), never a customer- or request-supplied one; it is called only by the
scheduled setup sweep or a staff "Check now"; and it gets every egress
guarantee (resolve, validate every address, pin; no redirects; https-only and
no private addresses in production). The body is capped and returned as bytes:
judging it is ``commercial.controlplane.sso_setup``'s.

Never raises: a failure is a result.
"""

from __future__ import annotations

from typing import Final

from workeasy360.integration.egress.client import (
    EgressFailure,
    OutboundRequest,
    Timeouts,
    send,
)
from workeasy360.integration.egress.policy import EgressRejected

USER_AGENT: Final = "WorkEasy360-SSO-Setup/1.0"
TIMEOUTS: Final = Timeouts(connect=3.0, read=5.0, total=8.0, dns=2.0)
MAX_DOCUMENT_BYTES: Final = 65536


def fetch(url: str) -> tuple[str, bytes, str]:
    """``(outcome, body, detail)``. Outcomes: ``ok``, ``unreachable``,
    ``missing`` (404/410), ``refused`` (by the egress policy) or ``too_large``."""
    request = OutboundRequest(
        url=url,
        body=b"",
        method="GET",
        headers=(("accept", "application/json"), ("user-agent", USER_AGENT)),
    )
    try:
        response = send(request, timeouts=TIMEOUTS, max_response_bytes=MAX_DOCUMENT_BYTES)
    except EgressRejected as exc:
        return (
            "refused",
            b"",
            f"WorkEasy360's outbound rules refuse that address ({exc.reason})",
        )
    except EgressFailure as exc:
        return "unreachable", b"", f"could not be reached ({exc.category})"
    if response.truncated:
        return "too_large", b"", f"is larger than {MAX_DOCUMENT_BYTES} bytes"
    if response.status in (404, 410):
        return "missing", b"", f"answered HTTP {response.status}"
    if response.status != 200:
        return "unreachable", b"", f"answered HTTP {response.status}"
    return "ok", response.body, ""

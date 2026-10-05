"""Deploy-time checks for the product registry (Phase 12).

Registered from ``EcosystemConfig.ready``. Deploy-only because they read the
live registry.
"""

from __future__ import annotations

import ipaddress
from typing import Any, Final
from urllib.parse import urlsplit

from django.core.checks import CheckMessage, register
from django.core.checks import Warning as CheckWarning

#: Public DNS services that answer *any* name with the IP address written in
#: it (``65-1-134-3.sslip.io`` -> 65.1.134.3). Such a name belongs to whoever
#: holds the address today - and a cloud address that is not reserved changes
#: hands on stop/start - so it is not a stable identity for a place the
#: platform sends browsers and authorization codes.
WILDCARD_DNS_SUFFIXES: Final[tuple[str, ...]] = (
    ".sslip.io",
    ".nip.io",
    ".xip.io",
    ".traefik.me",
    ".localtest.me",
    ".lvh.me",
    ".vcap.me",
    ".nip.direct",
)


def unstable_host_reason(url: str) -> str | None:
    """Why ``url``'s host is not an owned, stable name - or ``None``."""
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return "unparseable"
    if not host:
        return None
    try:
        ipaddress.ip_address(host)
        return "an IP address"
    except ValueError:
        pass
    if host.endswith(WILDCARD_DNS_SUFFIXES):
        return "a wildcard-DNS name that follows whoever holds its IP address"
    return None


@register("w360", deploy=True)
def check_product_hosts_are_owned(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """W360.W010 - every registered product URL is on a name its owner holds.

    A Warning, not an Error: which host a product lives on is its owner's
    decision, and the platform must still start. The go-live checklist
    (docs/phase-12/04-deployment.md, checklist) requires this to be clean or the
    product to be held back.
    """
    from workeasy360.platform.ecosystem.models import ProductIntegration

    messages: list[CheckMessage] = []
    try:
        rows = list(ProductIntegration.objects.all())
    except Exception as exc:  # pragma: no cover - DB unreachable
        return [
            CheckWarning(
                f"Could not read the product registry: {type(exc).__name__}",
                id="W360.W010",
            )
        ]
    for row in rows:
        manifest = row.manifest_obj
        urls = [manifest.launch_url, *manifest.redirect_uris]
        if manifest.health_url:
            urls.append(manifest.health_url)
        for url in urls:
            reason = unstable_host_reason(url)
            if reason:
                messages.append(
                    CheckWarning(
                        f"Product {row.product_code!r} uses {url!r}, whose host is {reason}.",
                        hint=(
                            "Move the product to a host name its owner controls "
                            "(for example <product>.workeasy360.com) and update its "
                            "manifest before sending customers or authorization "
                            "codes there."
                        ),
                        id="W360.W010",
                    )
                )
    return messages

"""Destination policy: which URLs and addresses the platform will talk to.

Pure functions over a frozen ``EgressPolicy``. No I/O happens here; DNS
resolution is the client's job, and every address it resolves comes back
through :func:`check_address` before a socket is opened.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urlsplit

MAX_URL_LENGTH: Final = 2048

#: A DNS hostname after IDNA encoding: dot-separated LDH labels.
_HOSTNAME_RE: Final = re.compile(
    r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)

#: Names refused before any lookup. The address check at send time is the
#: real control - these resolve to blocked addresses anyway - but refusing
#: them at save time tells an administrator immediately rather than at the
#: first failed delivery.
_BLOCKED_NAMES: Final[frozenset[str]] = frozenset(
    {
        "localhost",
        "metadata",
        "metadata.google.internal",
        "instance-data",
        "kubernetes",
        "kubernetes.default",
    }
)
_BLOCKED_SUFFIXES: Final[tuple[str, ...]] = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".intranet",
    ".lan",
    ".home.arpa",
    ".svc",
    ".cluster.local",
)

#: NAT64 well-known prefix (RFC 6052). An address inside it carries an IPv4
#: destination in its last 32 bits, which must itself be checked.
_NAT64: Final = ipaddress.ip_network("64:ff9b::/96")

DEFAULT_PORTS: Final[dict[str, int]] = {"https": 443, "http": 80}


class EgressRejected(Exception):
    """The policy refuses this destination.

    Never retryable: the same URL will be refused again. ``reason`` is a
    short, bounded code (safe as a metric label); ``message`` is for the
    administrator and never includes an address the platform resolved -
    echoing internal addresses back would turn the check into a network
    scanner.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    """What a deployment permits. Production pins both flags off."""

    #: Plain http. Development and the test receiver only.
    allow_http: bool = False
    #: Loopback destinations (never private, link-local or metadata ranges).
    #: Development and the test receiver only.
    allow_loopback: bool = False
    allowed_ports: frozenset[int] = field(default_factory=lambda: frozenset({443, 8443}))

    @classmethod
    def from_settings(cls) -> EgressPolicy:
        from django.conf import settings

        return cls(
            allow_http=bool(settings.INTEGRATION_EGRESS_ALLOW_HTTP),
            allow_loopback=bool(settings.INTEGRATION_EGRESS_ALLOW_LOOPBACK),
            allowed_ports=frozenset(
                int(p) for p in settings.INTEGRATION_EGRESS_ALLOWED_PORTS
            ),
        )


@dataclass(frozen=True, slots=True)
class Target:
    """A parsed, statically validated destination."""

    scheme: str
    #: Lowercase, IDNA-encoded, without IPv6 brackets.
    host: str
    port: int
    #: Path plus query, never empty. The fragment is dropped: it is not sent
    #: over the wire and has no meaning to the receiver.
    request_target: str

    @property
    def is_literal_address(self) -> bool:
        return _parse_ip(self.host) is not None

    @property
    def host_header(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        if self.port == DEFAULT_PORTS[self.scheme]:
            return host
        return f"{host}:{self.port}"

    @property
    def display(self) -> str:
        """Safe to show and to audit: scheme, host and port only. A path or
        query can carry a token (chat-service webhook URLs routinely do)."""
        return f"{self.scheme}://{self.host_header}"


def parse_target(url: str, policy: EgressPolicy) -> Target:
    """Validate ``url`` statically - no DNS - or raise :class:`EgressRejected`."""
    if not isinstance(url, str) or not url:
        raise EgressRejected("url_invalid", "A URL is required.")
    if len(url) > MAX_URL_LENGTH:
        raise EgressRejected(
            "url_invalid", f"The URL exceeds {MAX_URL_LENGTH} characters."
        )
    if any(ord(ch) < 0x21 or ord(ch) == 0x7F for ch in url):
        raise EgressRejected(
            "url_invalid", "The URL contains whitespace or control characters."
        )

    try:
        parts = urlsplit(url)
        raw_port = parts.port
    except ValueError:
        raise EgressRejected("url_invalid", "The URL could not be parsed.") from None

    scheme = parts.scheme.lower()
    permitted = ("https", "http") if policy.allow_http else ("https",)
    if scheme not in permitted:
        raise EgressRejected(
            "scheme_not_allowed",
            "Only https:// destinations are accepted."
            if not policy.allow_http
            else "Only http:// and https:// destinations are accepted.",
        )

    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise EgressRejected(
            "credentials_in_url",
            "URLs carrying credentials (user:password@host) are refused.",
        )

    host = (parts.hostname or "").lower()
    if not host:
        raise EgressRejected("host_invalid", "The URL has no host.")

    literal = _parse_ip(host)
    if literal is not None:
        host = str(literal)
        loopback_ok = policy.allow_loopback and _block_reason(literal) == "loopback"
        if not loopback_ok:
            check_address(literal, policy)
    else:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            raise EgressRejected("host_invalid", "The host name is not valid.") from None
        loopback_ok = policy.allow_loopback and host == "localhost"
        if not loopback_ok:
            if host in _BLOCKED_NAMES or host.endswith(_BLOCKED_SUFFIXES):
                raise EgressRejected(
                    "host_blocked", "Internal and local host names are refused."
                )
            if not _HOSTNAME_RE.match(host):
                raise EgressRejected(
                    "host_invalid",
                    "The host must be a fully qualified public domain name.",
                )

    port = raw_port if raw_port is not None else DEFAULT_PORTS[scheme]
    if port not in policy.allowed_ports and not loopback_ok:
        allowed = ", ".join(str(p) for p in sorted(policy.allowed_ports))
        raise EgressRejected("port_not_allowed", f"The port must be one of: {allowed}.")

    request_target = parts.path or "/"
    if parts.query:
        request_target = f"{request_target}?{parts.query}"
    return Target(scheme=scheme, host=host, port=port, request_target=request_target)


def check_address(
    address: str | ipaddress.IPv4Address | ipaddress.IPv6Address,
    policy: EgressPolicy,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Refuse any address that is not a public unicast destination."""
    ip = _parse_ip(address) if isinstance(address, str) else address
    if ip is None:
        raise EgressRejected("address_blocked", "The destination address is invalid.")
    reason = _block_reason(ip)
    if reason is None:
        return ip
    if reason == "loopback" and policy.allow_loopback:
        return ip
    raise EgressRejected(
        "address_blocked",
        "The destination resolves to a private, loopback, link-local or "
        "otherwise non-public address, which is refused.",
    )


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        # A scoped IPv6 literal ("fe80::1%eth0") names an interface on *this*
        # host; the scope is stripped and the address is judged on its own.
        return ipaddress.ip_address(value.strip("[]").split("%", 1)[0])
    except ValueError:
        return None


def _block_reason(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Why ``ip`` is not a permissible destination, or ``None``.

    IPv6 forms that *embed* an IPv4 destination are unwrapped first, so
    ``::ffff:10.0.0.1``, ``2002:0a00:0001::`` (6to4) and ``64:ff9b::a00:1``
    (NAT64) are judged by the IPv4 address they actually reach.
    """
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return _block_reason(ip.ipv4_mapped)
        if ip.sixtofour is not None:
            embedded = _block_reason(ip.sixtofour)
            if embedded is not None:
                return embedded
        if ip.teredo is not None:
            # Teredo tunnels through a relay to a client address it embeds;
            # there is no legitimate webhook endpoint behind one.
            return "tunnel"
        if ip in _NAT64:
            embedded = _block_reason(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
            if embedded is not None:
                return embedded
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link_local"
    if ip.is_private:
        return "private"
    if ip.is_multicast:
        return "multicast"
    if ip.is_unspecified:
        return "unspecified"
    if ip.is_reserved:
        return "reserved"
    if not ip.is_global:
        # CGNAT (100.64/10) - where Alibaba's metadata service lives -
        # documentation and benchmarking ranges.
        return "not_global"
    return None

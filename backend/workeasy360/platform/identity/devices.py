"""What a sign-in or a session may record about *where* it came from.

Two coarse facts, and deliberately nothing sharper:

- **the network prefix**, not the address: an IPv4 /24 or an IPv6 /48. That is
  enough for a person to recognise "that was my office" or "that is not a
  network I use", and too coarse to locate a household. The full address is
  never stored by this module; rate limiting and audit keep their own peppered
  hashes (``audit.services.hash_ip``).
- **a device label** such as "Chrome on Windows", derived from the user agent
  and then discarded. A raw user-agent string is a fingerprinting aid and is
  attacker-controlled free text that would otherwise flow into every page that
  lists sessions.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Final

UNKNOWN_DEVICE: Final = "Unknown device"

#: Order matters: Edge and Opera also say "Chrome", Chrome also says "Safari".
_BROWSERS: Final = (
    ("Edge", re.compile(r"\bEdg(?:e|A|iOS)?/")),
    ("Opera", re.compile(r"\b(?:OPR|Opera)/")),
    ("Firefox", re.compile(r"\b(?:Firefox|FxiOS)/")),
    ("Chrome", re.compile(r"\b(?:Chrome|CriOS)/")),
    ("Safari", re.compile(r"\bVersion/[\d.]+.*Safari/")),
)

_SYSTEMS: Final = (
    ("iOS", re.compile(r"\b(?:iPhone|iPad|iPod)\b")),
    ("Android", re.compile(r"\bAndroid\b")),
    ("Windows", re.compile(r"\bWindows\b")),
    ("ChromeOS", re.compile(r"\bCrOS\b")),
    ("macOS", re.compile(r"\bMac OS X\b|\bMacintosh\b")),
    ("Linux", re.compile(r"\bLinux\b")),
)

#: A user agent longer than this is not a browser; parsing it is wasted work.
_MAX_UA: Final = 512


def device_label(user_agent: str | None) -> str:
    """``"Chrome on Windows"``, ``"Firefox"``, ``"iOS device"`` or
    ``"Unknown device"``. Always one of a closed vocabulary, never echoed input."""
    ua = (user_agent or "")[:_MAX_UA]
    if not ua:
        return UNKNOWN_DEVICE
    browser = next((name for name, pattern in _BROWSERS if pattern.search(ua)), "")
    system = next((name for name, pattern in _SYSTEMS if pattern.search(ua)), "")
    if browser and system:
        return f"{browser} on {system}"
    if browser:
        return browser
    if system:
        return f"{system} device"
    return UNKNOWN_DEVICE


def network_prefix(address: str | None) -> str:
    """The /24 (IPv4) or /48 (IPv6) containing ``address``, as CIDR text, or
    ``""`` when the value is not an address (``client_ip`` reports "unknown")."""
    try:
        ip = ipaddress.ip_address((address or "").strip())
    except ValueError:
        return ""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    prefix = 24 if ip.version == 4 else 48
    return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))

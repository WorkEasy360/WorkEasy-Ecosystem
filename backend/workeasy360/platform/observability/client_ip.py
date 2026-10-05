"""Who is actually calling - D-009, resolved.

**The problem this closes.** Every rate limit in this platform is keyed
partly on the caller's address. ``REMOTE_ADDR`` is that address only when
nothing sits in front of the application; behind CloudFront, an ALB or any
reverse proxy it is the *proxy's* address, so every request on earth shares
one bucket. The limit still fires - it just fires for everybody at once, so
one abusive caller locks every customer of that product out of signing in.

**Why the obvious fix is worse than the bug.** ``X-Forwarded-For`` is
appended to by each hop and is a plain request header, so anything on the
public internet can send one. Reading the *left-most* entry - the common
shortcut - means the caller names their own rate-limit bucket, which does not
weaken the limit so much as delete it. That is the trade D-009 recorded and
declined to make blind.

**What is safe.** A forwarded chain is trustworthy only from the right: the
right-most entry was written by the hop that spoke to us, the one before it
by the hop before that, and so on. With exactly ``W360_TRUSTED_PROXY_COUNT``
hops of infrastructure we control, the address that infrastructure observed
is ``chain[-count]`` - everything to its left is attacker-supplied and is
never read. The count is the deployment topology, stated once, in
configuration, because it is the one fact the application cannot discover
about itself:

===========================================  =====
Deployment                                   count
===========================================  =====
Directly exposed (local, CI, a bare server)      0
One reverse proxy / ALB / ingress                1
CloudFront in front of an ALB                    2
===========================================  =====

**Count only hops that append to the chain - never the frontend's Next.js
server.** Browser API calls reach this process through Next.js's ``/api``
rewrite (``frontend/next.config.ts``), which forwards ``X-Forwarded-For``
unchanged and appends nothing (verified against the production build in
the Phase 5 final audit). So ALB -> Next.js -> Django is still ``1``, and
counting Next.js as a hop makes every bucket caller-chosen. It also follows
that Next.js must never be the internet-facing edge: with nothing appending
in front of it, ``0`` puts every user in one shared bucket (``REMOTE_ADDR``
is the Next.js server, so 30 bad logins a minute from anyone throttle
everyone), and ``1`` reads a value the caller wrote. Put an appending proxy
(ALB, ingress, nginx with ``proxy_add_x_forwarded_for``) in front of it.
Direct calls to this process (OIDC endpoints, products) cross the same
front proxy and so need the same count.

At ``0`` the forwarded header is ignored entirely, which is the right answer
when nothing is in front: there, ``REMOTE_ADDR`` *is* the client and any
``X-Forwarded-For`` present is purely hostile. Production requires the value
to be set explicitly (``config/settings/production.py``) rather than
defaulting, because the failure mode of guessing it is silent in both
directions - guess too low and clients spoof their bucket, guess too high and
every client collapses into one.

A chain shorter than ``count`` means the request did not come through the
expected infrastructure at all (somebody reached the origin directly, or a
proxy was reconfigured). That is refused rather than salvaged: it falls back
to ``REMOTE_ADDR``, which is at worst over-strict and is never forgeable.
"""

from __future__ import annotations

import ipaddress
from typing import Any

from django.conf import settings

#: What is returned when no usable address can be determined at all. A stable
#: string rather than an empty one, so it reads as a bucket in a log line
#: instead of looking like a missing field.
UNKNOWN = "unknown"


def client_ip(request: Any) -> str:
    """The caller's address, as far as the deployment can actually vouch for it."""
    remote_addr = _clean(request.META.get("REMOTE_ADDR", ""))

    hops = int(getattr(settings, "TRUSTED_PROXY_COUNT", 0) or 0)
    if hops <= 0:
        return remote_addr or UNKNOWN

    header = str(getattr(settings, "CLIENT_IP_HEADER", "HTTP_X_FORWARDED_FOR"))
    chain = [_clean(part) for part in str(request.META.get(header, "")).split(",")]
    chain = [entry for entry in chain if entry]

    if len(chain) < hops:
        # Fewer hops than configured: this request did not traverse the
        # infrastructure the count describes. Trust nothing in the header.
        return remote_addr or UNKNOWN

    return chain[-hops]


def _clean(value: str) -> str:
    """Normalise one address, or return "" if it is not one.

    Anything unparseable is discarded rather than passed through: these
    values become cache keys, and an attacker who can put arbitrary text in
    one can grow the keyspace at will. ``ipaddress`` also collapses the
    equivalent spellings of an IPv6 address, so ``::1`` and ``0:0:0:0:0:0:0:1``
    cannot be used as two separate buckets.
    """
    candidate = value.strip()
    if not candidate:
        return ""
    # IPv6 in a forwarded chain is bracketed, optionally with a port.
    if candidate.startswith("["):
        candidate = candidate[1:].split("]", 1)[0]
    elif candidate.count(":") == 1:
        # host:port for IPv4; a bare IPv6 address always has more than one.
        candidate = candidate.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return ""


def rate_limit_identity(address: str) -> str:
    """The rate-limit bucket for a client address (Phase 12, G-12).

    An IPv4 address is one bucket. An IPv6 address is bucketed by its /64:
    a single customer line or cloud instance is routinely delegated a whole
    /64, so keying on the full address gave one caller 2^64 buckets on every
    per-address limit (login, signup, password reset, owner claim, OIDC). An
    IPv4-mapped address is its IPv4 address. Anything else is returned as is
    (``client_ip`` already discards unparseable values).
    """
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return address
    if isinstance(parsed, ipaddress.IPv6Address):
        if parsed.ipv4_mapped is not None:
            return str(parsed.ipv4_mapped)
        return str(ipaddress.ip_network(f"{parsed}/64", strict=False))
    return str(parsed)

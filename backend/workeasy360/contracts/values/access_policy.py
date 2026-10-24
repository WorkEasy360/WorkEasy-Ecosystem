"""An organization's access policy, and the one function that applies it.

Pure Python - no Django, no I/O - because it is applied at two doors that must
agree exactly: the tenancy middleware (every ``/orgs/<slug>/...`` request) and
the OIDC authorization endpoint (a product sign-in naming an organization). A
second implementation would be a second place for "and check the network" to
be forgotten.

**Additive only.** The policy is evaluated *after* the membership, organization
status and session have already been verified, and it can only turn an allowed
request into a denied one. Nothing here grants anything, and an empty policy
(the default for every organization) denies nothing.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

#: Denial codes, in the order they are checked. The network comes first so a
#: request from outside the allowed networks learns nothing else about the
#: organization's configuration.
NETWORK_NOT_ALLOWED: Final = "network_not_allowed"
REAUTHENTICATION_REQUIRED: Final = "reauthentication_required"
PASSWORD_CHANGE_REQUIRED: Final = "password_change_required"  # noqa: S105 - a denial code

#: HTTP status per denial. 401 where the remedy is signing in again, 403 where
#: it is doing something first (changing a password) or nothing the user can
#: do (network).
DENIAL_STATUS: Final = {
    NETWORK_NOT_ALLOWED: 403,
    REAUTHENTICATION_REQUIRED: 401,
    PASSWORD_CHANGE_REQUIRED: 403,
}

DENIAL_MESSAGES: Final = {
    NETWORK_NOT_ALLOWED: (
        "This organization only allows access from approved networks, and this "
        "connection is not one of them."
    ),
    REAUTHENTICATION_REQUIRED: "Your session for this organization has ended. Sign in again.",
    PASSWORD_CHANGE_REQUIRED: (
        "Your password does not meet this organization's password policy. "
        "Change it to continue."
    ),
}

#: Bounds an administrator can set.
MAX_ALLOWLIST_ENTRIES: Final = 50
MIN_SESSION_MAX_AGE_MINUTES: Final = 15
MAX_SESSION_MAX_AGE_MINUTES: Final = 60 * 24 * 30
MIN_PASSWORD_LENGTH_CEILING: Final = 64


@dataclass(frozen=True, slots=True)
class AccessPolicy:
    #: Seconds since sign-in after which the session no longer reaches this
    #: organization. ``None`` = the platform's own lifetime only.
    session_max_age_s: int | None = None
    ip_allowlist_enabled: bool = False
    #: Normalised CIDR strings (``"203.0.113.0/24"``, ``"2001:db8::/48"``).
    ip_allowlist: tuple[str, ...] = ()
    #: Epoch seconds. A session signed in before this moment is refused - an
    #: administrator's "sign this member out of the organization".
    sessions_valid_after: float | None = None

    @property
    def is_empty(self) -> bool:
        return not (
            self.session_max_age_s
            or self.ip_allowlist_enabled
            or self.sessions_valid_after
        )


@dataclass(frozen=True, slots=True)
class SessionFacts:
    """What the request's session and connection can vouch for."""

    client_ip: str
    #: Epoch seconds of the sign-in that established the session, or ``None``
    #: when the session cannot say (treated as too old whenever age matters).
    authenticated_at: float | None
    password_noncompliant: bool = False


def evaluate(policy: AccessPolicy, facts: SessionFacts, *, now: float) -> str | None:
    """The denial code for this request, or ``None`` when the policy allows it."""
    if policy.is_empty and not facts.password_noncompliant:
        return None
    if policy.ip_allowlist_enabled and not address_allowed(
        facts.client_ip, policy.ip_allowlist
    ):
        return NETWORK_NOT_ALLOWED
    if policy.sessions_valid_after is not None and (
        facts.authenticated_at is None
        or facts.authenticated_at < policy.sessions_valid_after
    ):
        return REAUTHENTICATION_REQUIRED
    if policy.session_max_age_s and (
        facts.authenticated_at is None
        or now - facts.authenticated_at > policy.session_max_age_s
    ):
        return REAUTHENTICATION_REQUIRED
    if facts.password_noncompliant:
        return PASSWORD_CHANGE_REQUIRED
    return None


def address_allowed(address: str, allowlist: Iterable[str]) -> bool:
    """Whether ``address`` falls inside any network in ``allowlist``.

    An unparseable address (``client_ip`` reports ``"unknown"`` when it cannot
    vouch for one) matches nothing: an enabled allow-list fails closed.
    """
    try:
        ip = ipaddress.ip_address((address or "").strip())
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    for entry in allowlist:
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            continue
        if ip.version == network.version and ip in network:
            return True
    return False


def normalise_allowlist(entries: Iterable[object]) -> tuple[str, ...]:
    """Validate and normalise administrator input into CIDR strings.

    Accepts single addresses (as /32 or /128) and networks; host bits are
    cleared. Refuses anything else, and refuses networks so wide they would
    make the list meaningless (``0.0.0.0/0``, anything shorter than /8 or
    /16 for IPv6) - an allow-list that allows everyone is a misconfiguration,
    not a policy. Raises ``ValueError`` naming the first bad entry.
    """
    seen: dict[str, None] = {}
    for raw in entries:
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            network = ipaddress.ip_network(text, strict=False)
        except ValueError as exc:
            raise ValueError(f"{text[:64]!r} is not an IP address or network.") from exc
        minimum = 8 if network.version == 4 else 16
        if network.prefixlen < minimum:
            raise ValueError(f"{network} is too broad; the widest allowed is /{minimum}.")
        seen[str(network)] = None
    if len(seen) > MAX_ALLOWLIST_ENTRIES:
        raise ValueError(f"At most {MAX_ALLOWLIST_ENTRIES} networks may be listed.")
    return tuple(seen)


def password_meets(password: str, *, min_length: int | None) -> bool:
    """Whether a password satisfies an organization's minimum length.

    Length only, deliberately. The platform follows current NIST guidance
    (12-security.md section 3): composition rules and forced rotation push
    people towards predictable passwords without adding entropy, so an
    organization can raise the minimum length but cannot demand symbols.
    """
    return not min_length or len(password or "") >= min_length

"""Partner lifecycle and input rules as pure Python. No Django, no I/O (G3).

The state machine is a table so the whole legal surface is readable at a
glance and an illegal move is impossible to express rather than merely
discouraged.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Final

from workeasy360.contracts.enums import PartnerLinkStatus, PartnerStatus
from workeasy360.contracts.errors import InvalidStateTransition, PartnerValidationError

P = PartnerStatus

#: Legal partner moves.
#:
#: Absent on purpose: ``terminated -> anything`` (termination is terminal - a
#: returning partner is a new account, so history is never ambiguous) and
#: ``suspended/active -> draft`` (a partner that has transacted is never
#: "not yet started" again).
PARTNER_TRANSITIONS: Final[dict[PartnerStatus, frozenset[PartnerStatus]]] = {
    P.DRAFT: frozenset({P.ACTIVE, P.TERMINATED}),
    P.ACTIVE: frozenset({P.SUSPENDED, P.TERMINATED}),
    P.SUSPENDED: frozenset({P.ACTIVE, P.TERMINATED}),
    P.TERMINATED: frozenset(),
}

L = PartnerLinkStatus

#: Legal relationship moves. ``ended`` is terminal: resuming a relationship is
#: a new link, so "when was this partner related to this customer" is a
#: sequence of rows and never a rewritten one.
LINK_TRANSITIONS: Final[dict[PartnerLinkStatus, frozenset[PartnerLinkStatus]]] = {
    L.ACTIVE: frozenset({L.SUSPENDED, L.ENDED}),
    L.SUSPENDED: frozenset({L.ACTIVE, L.ENDED}),
    L.ENDED: frozenset(),
}


def assert_partner_transition(current: PartnerStatus, target: PartnerStatus) -> None:
    """Raise unless the move is on the table. A self-move is a no-op, not an error."""
    if current is target:
        return
    if target not in PARTNER_TRANSITIONS[current]:
        raise InvalidStateTransition(
            entity="partners.PartnerAccount",
            from_status=current.value,
            to_status=target.value,
        )


def assert_link_transition(current: PartnerLinkStatus, target: PartnerLinkStatus) -> None:
    if current is target:
        return
    if target not in LINK_TRANSITIONS[current]:
        raise InvalidStateTransition(
            entity="partners.PartnerOrganizationLink",
            from_status=current.value,
            to_status=target.value,
        )


# --- owner activation -------------------------------------------------------

#: What a screen may say about whether a provisioned customer's owner has
#: claimed their account. A closed set, derived from the invitation row and the
#: clock - never stored, so it cannot drift from the row it describes.
OWNER_ACTIVATION_STATES: Final = (
    "pending",
    "activated",
    "expired",
    "revoked",
    "declined",
    "not_applicable",
)


def owner_activation(
    status: str | None, expires_at: datetime | None, now: datetime
) -> str:
    """The factual state of one owner invitation.

    ``None`` means the customer has no invitation at all - it was linked to an
    organization that already had an owner, so there is nothing to activate.
    A ``pending`` invitation past its expiry *is* expired: the row only learns
    that when someone next tries to use it, and a screen must not call an
    unusable invitation pending.
    """
    if status is None:
        return "not_applicable"
    if status == "pending":
        return "pending" if expires_at is not None and expires_at > now else "expired"
    return {
        "accepted": "activated",
        "expired": "expired",
        "revoked": "revoked",
        "declined": "declined",
    }.get(status, "expired")


# --- input rules ----------------------------------------------------------

_CODE_RE: Final = re.compile(r"^[a-z][a-z0-9-]{2,47}$")
#: ASCII controls, plus the Unicode characters that reorder or break a line
#: when *displayed*: bidi embeddings/overrides (U+202A-U+202E) and isolates
#: (U+2066-U+2069) let "Acme<RLO>exe.txt" render as something it is not, and
#: the line/paragraph separators (U+2028/U+2029) forge a second line exactly
#: as a newline would. Joiners (ZWJ/ZWNJ) stay allowed: real scripts need them.
_CONTROL_RE: Final = re.compile(r"[\x00-\x1f\x7f\u2028\u2029\u202a-\u202e\u2066-\u2069]")
MIN_REASON: Final = 10
MAX_REASON: Final = 200


def validate_partner_code(value: object) -> str:
    text = str(value or "").strip().lower()
    if not _CODE_RE.match(text):
        raise PartnerValidationError(
            "code must be 3-48 characters: lower-case letters, digits and "
            "hyphens, starting with a letter.",
            field="code",
        )
    return text


def clean_text(
    value: object, *, field: str, max_length: int, required: bool = True
) -> str:
    """A bounded single-line display string.

    Refuses control characters (newlines included) so a name cannot forge a
    second line in a log, an email header or a table row. Angle brackets are
    *not* stripped: output is escaped where it is rendered, and silently
    altering what an operator typed is worse than storing it faithfully.
    """
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise PartnerValidationError(f"{field} must be text.", field=field)
    text = value.strip()
    if required and not text:
        raise PartnerValidationError(f"{field} is required.", field=field)
    if len(text) > max_length:
        raise PartnerValidationError(
            f"{field} must be at most {max_length} characters.", field=field
        )
    if _CONTROL_RE.search(text):
        raise PartnerValidationError(
            f"{field} must not contain control characters.", field=field
        )
    return text


def clean_reason(value: object, *, field: str = "reason") -> str:
    """The recorded justification for a privileged action."""
    text = clean_text(value, field=field, max_length=MAX_REASON)
    if len(text) < MIN_REASON:
        raise PartnerValidationError(
            f"{field} must be at least {MIN_REASON} characters: it is what an "
            f"auditor reads.",
            field=field,
        )
    return text

"""Bounded input rules shared by the partner profile and the brand portal.

Everything a person types into the partner setup screens is *text that is stored
and shown*, never markup that is rendered and never an address the platform or a
visitor's browser fetches. These helpers are the one place those rules live so
the profile, the brand portal and the review's dry run refuse exactly the same
things.

Each ``clean_*`` returns the cleaned value or raises ``PartnerValidationError``
naming the field; :class:`Problems` runs one and, on a refusal, records it
against the *screen's* field name instead of stopping - the review reports every
problem at once.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Final
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from workeasy360.commercial.partners.domain import clean_text
from workeasy360.contracts.errors import PartnerValidationError
from workeasy360.contracts.values.countries import COUNTRY_CODES

_PHONE_RE: Final = re.compile(r"^\+?[0-9][0-9 ()\-.]{3,30}$")
_REFERENCE_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ./\-]{0,63}$")
_POSTAL_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 \-]{0,19}$")
#: Newline and tab are fine in a note; every other control character, and the
#: characters that reorder or break a line when displayed, are not.
_NOTE_CONTROL_RE: Final = re.compile(
    r"[\x00-\x08\x0b-\x1f\x7f\u2028\u2029\u202a-\u202e\u2066-\u2069]"
)


class Problems:
    """What is wrong with a draft, collected instead of raised one at a time."""

    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []

    def add(self, field: str, message: str) -> None:
        self.items.append({"field": field, "message": message})

    def run(
        self, on_field: str, fn: Callable[..., Any], /, *args: Any, **kwargs: Any
    ) -> Any:
        """Call a validating primitive; a refusal becomes a problem *on the
        wizard's own field name* (a primitive may name it differently)."""
        try:
            return fn(*args, **kwargs)
        except PartnerValidationError as exc:
            self.add(on_field, exc.message)
        return None

    def first(self) -> None:
        """Raise the first problem as the error a command would have raised."""
        if self.items:
            first = self.items[0]
            raise PartnerValidationError(first["message"], field=first["field"])


def clean_email(value: Any, *, field: str, required: bool = False) -> str:
    text = clean_text(value, field=field, max_length=254, required=required)
    if not text:
        return ""
    try:
        validate_email(text)
    except ValidationError as exc:
        raise PartnerValidationError("Enter a valid email address.", field=field) from exc
    return text


def clean_phone(value: Any, *, field: str) -> str:
    text = clean_text(value, field=field, max_length=32, required=False)
    if not text:
        return ""
    digits = sum(ch.isdigit() for ch in text)
    if not _PHONE_RE.match(text) or digits < 5:
        raise PartnerValidationError(
            "Enter a phone number using digits, spaces and + ( ) - . only.",
            field=field,
        )
    return text


def clean_https_url(value: Any, *, field: str) -> str:
    """A plain ``https`` address shown as text. Stored and rendered, never
    fetched - so there is no request for it to forge."""
    text = clean_text(value, field=field, max_length=300, required=False)
    if not text:
        return ""
    try:
        parts = urlsplit(text)
        hostname = parts.hostname
    except ValueError:
        raise PartnerValidationError(
            "Enter a plain https:// address.", field=field
        ) from None
    if (
        parts.scheme != "https"
        or not hostname
        or "." not in hostname
        or parts.username
        or parts.password
        or " " in text
    ):
        raise PartnerValidationError("Enter a plain https:// address.", field=field)
    return text


def clean_reference(value: Any, *, field: str) -> str:
    """A registration or tax number: letters, digits, spaces and . / - only.
    No country's format is enforced - none is known to be right for every
    partner - so this rejects only what cannot be a reference at all."""
    text = clean_text(value, field=field, max_length=64, required=False)
    if text and not _REFERENCE_RE.match(text):
        raise PartnerValidationError(
            "Use only letters, digits, spaces and . / - .",
            field=field,
        )
    return text


def clean_country(value: Any, *, field: str = "country") -> str:
    text = clean_text(value, field=field, max_length=2, required=False).upper()
    if text and text not in COUNTRY_CODES:
        raise PartnerValidationError("Choose a country from the list.", field=field)
    return text


def clean_timezone(value: Any, *, field: str = "timezone") -> str:
    """An IANA zone name from the platform's own zone database, or empty."""
    from zoneinfo import available_timezones

    text = clean_text(value, field=field, max_length=64, required=False)
    if text and text not in available_timezones():
        raise PartnerValidationError("Choose a time zone from the list.", field=field)
    return text


def clean_postal_code(value: Any, *, field: str) -> str:
    text = clean_text(value, field=field, max_length=20, required=False)
    if text and not _POSTAL_RE.match(text):
        raise PartnerValidationError(
            "Use only letters, digits, spaces and hyphens.",
            field=field,
        )
    return text


def clean_note(value: Any, *, field: str, max_length: int) -> str:
    """Multi-line plain text. Angle brackets are stored faithfully (output is
    escaped where it is shown); control characters are refused."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PartnerValidationError(f"{field} must be text.", field=field)
    text = value.replace("\r\n", "\n").strip()
    if len(text) > max_length:
        raise PartnerValidationError(
            f"{field} must be at most {max_length} characters.", field=field
        )
    if _NOTE_CONTROL_RE.search(text):
        raise PartnerValidationError(
            f"{field} must not contain control characters.", field=field
        )
    return text


def clean_markup_free(value: Any, *, field: str, max_length: int) -> str:
    """One line of plain text that can never be markup: the customer portal
    shows it as text, an email may repeat it, and neither may meet ``<`` or
    ``>``. Refused rather than stripped - silently editing what a person typed
    is worse than telling them."""
    text = clean_text(value, field=field, max_length=max_length, required=False)
    if "<" in text or ">" in text:
        raise PartnerValidationError(
            "Use plain text only: no < or > characters.", field=field
        )
    return text


def mapping(value: Any) -> dict[str, Any]:
    """``value`` as a dict, or an empty one: a section the client did not send."""
    return value if isinstance(value, dict) else {}


def reject_unknown(
    raw: dict[str, Any], allowed: tuple[str, ...], *, field: str, problems: Problems
) -> None:
    """A closed key set: an unknown key is refused, not ignored, so a typo does
    not silently do nothing and a client cannot smuggle a column in."""
    unknown = sorted(set(raw) - set(allowed))
    if unknown:
        problems.add(
            field, f"Unknown {field} fields {unknown}. Allowed: {list(allowed)}."
        )

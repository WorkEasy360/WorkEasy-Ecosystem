"""Email normalisation.

One function, in one place, used by the manager, the model and every lookup.
The failure it prevents: a user registers as ``Alice@Example.COM`` and cannot
log in as ``alice@example.com``, or worse, two accounts exist that differ only
by case and a uniqueness constraint that looked adequate did nothing.

Deliberately conservative about what it changes. Normalisation that is *too*
aggressive is its own security bug: stripping ``+tag`` suffixes or dots would
merge ``alice+admin@`` and ``alice@`` into one identity, and on some providers
those are genuinely different mailboxes belonging to different people.
"""

from __future__ import annotations

import unicodedata


def normalize_email(email: str) -> str:
    """Return the canonical stored form of an address.

    Applies exactly three transformations:

    1. Unicode NFKC, so visually identical addresses written with different
       code points compare equal.
    2. Strip surrounding whitespace, which users paste constantly.
    3. Lowercase the **domain** always, and the local part as well.

    The local part is technically case-sensitive per RFC 5321, but no
    significant mail provider treats it that way, and preserving case there
    would let two accounts exist for one human. ``email_original`` keeps the
    address as typed for display and delivery.
    """
    if not email:
        return ""

    cleaned = unicodedata.normalize("NFKC", email).strip()

    if "@" not in cleaned:
        # Not a valid address; hand it back for the field validator to reject
        # with a proper message rather than raising an opaque error here.
        return cleaned.lower()

    local, _, domain = cleaned.rpartition("@")
    return f"{local.lower()}@{domain.lower()}"


def is_plausible_email(email: str) -> bool:
    """Cheap structural check for uniform-response flows.

    Used where a full validation error would disclose whether an address is
    known. Not a substitute for Django's EmailValidator on the model.
    """
    if not email or email.count("@") != 1:
        return False
    local, _, domain = email.partition("@")
    return bool(local) and "." in domain and not domain.startswith(".")

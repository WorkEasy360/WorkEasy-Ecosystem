"""The brand portal: bounded plain text and flags a white-label customer sees.

``PartnerBrand.portal`` is a closed set of keys, validated here on write and read
back through :func:`view` so every consumer sees the same complete shape. Like the
theme, it is presentation only: nothing here is read by authorization,
entitlement, tenant resolution or pricing, and nothing here can carry markup -
every string is one line of plain text with no ``<`` or ``>``, every address is a
plain ``https`` link that is displayed and never fetched.

Two audiences, two views:

- :func:`public_view` is what a customer's browser receives (the sign-in page, the
  portal header and footer): the words and support details meant to be read;
- :func:`view` is what staff edit, which adds the sender identity of emails sent
  in the partner's name - never sent to a customer's browser.
"""

from __future__ import annotations

from typing import Any, Final

from workeasy360.commercial.partners import inputs
from workeasy360.commercial.partners.inputs import Problems

#: ``key -> max length`` of the plain-text fields.
TEXT_FIELDS: Final[dict[str, int]] = {
    "short_name": 24,
    "support_name": 80,
    "welcome_message": 280,
    "footer_text": 160,
    "sender_name": 80,
}

#: ``key -> default`` of the on/off choices.
FLAG_DEFAULTS: Final[dict[str, bool]] = {
    "login_show_welcome": True,
    "login_show_support": True,
    "use_for_invitations": False,
}

#: The addresses, each with its own rule.
OTHER_FIELDS: Final = ("support_phone", "website_url", "reply_to_email")

PORTAL_FIELDS: Final = (*TEXT_FIELDS, *OTHER_FIELDS, *FLAG_DEFAULTS)

#: What a customer's browser may receive.
PUBLIC_FIELDS: Final = (
    "short_name",
    "support_name",
    "support_phone",
    "website_url",
    "welcome_message",
    "footer_text",
    "login_show_welcome",
    "login_show_support",
)


def parse(raw: Any, problems: Problems) -> dict[str, Any]:
    """The cleaned keys of a portal request (only those sent). Problems are
    collected on ``problems``; an unknown key is one of them."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        problems.add("portal", "portal must be an object.")
        return {}
    inputs.reject_unknown(raw, PORTAL_FIELDS, field="portal", problems=problems)
    out: dict[str, Any] = {}
    for key, limit in TEXT_FIELDS.items():
        if key in raw:
            value = problems.run(
                f"portal.{key}",
                inputs.clean_markup_free,
                raw[key],
                field=key,
                max_length=limit,
            )
            if value is not None:
                out[key] = value
    if "support_phone" in raw:
        value = problems.run(
            "portal.support_phone",
            inputs.clean_phone,
            raw["support_phone"],
            field="support_phone",
        )
        if value is not None:
            out["support_phone"] = value
    if "website_url" in raw:
        value = problems.run(
            "portal.website_url",
            inputs.clean_https_url,
            raw["website_url"],
            field="website_url",
        )
        if value is not None:
            out["website_url"] = value
    if "reply_to_email" in raw:
        value = problems.run(
            "portal.reply_to_email",
            inputs.clean_email,
            raw["reply_to_email"],
            field="reply_to_email",
        )
        if value is not None:
            out["reply_to_email"] = value
    for key in FLAG_DEFAULTS:
        if key in raw:
            if isinstance(raw[key], bool):
                out[key] = raw[key]
            else:
                problems.add(f"portal.{key}", f"{key} must be true or false.")
    return out


def view(stored: Any) -> dict[str, Any]:
    """The complete portal for staff: every key, defaults filled in."""
    saved = stored if isinstance(stored, dict) else {}
    out: dict[str, Any] = {}
    for key in TEXT_FIELDS:
        out[key] = str(saved.get(key, ""))
    for key in OTHER_FIELDS:
        out[key] = str(saved.get(key, ""))
    for key, default in FLAG_DEFAULTS.items():
        value = saved.get(key, default)
        out[key] = value if isinstance(value, bool) else default
    return out


def public_view(stored: Any) -> dict[str, Any]:
    """The portal as a customer's browser may receive it."""
    full = view(stored)
    return {key: full[key] for key in PUBLIC_FIELDS}

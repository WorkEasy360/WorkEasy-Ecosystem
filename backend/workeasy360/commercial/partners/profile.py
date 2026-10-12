"""A partner's business profile and contacts: read, validate, save.

Three sections of one request - ``business``, ``contacts``, ``support`` and
``payout`` (plus staff-only ``internal_notes``) - so the Add Reseller wizard, the
partner's Business Details and Contacts tabs and the review's dry run all go
through the same parser. :func:`parse` never raises: it returns what is clean and
*every* problem found; :func:`update_profile` raises the first one, the review
reports them all.

Nothing here decides anything. No permission, price, commission or entitlement
reads a column written by this module; it records who a partner is and how to
reach it. Contact details are personal data, so the audit entry names *which*
fields changed and never their values, and only staff read them.
"""

from __future__ import annotations

from typing import Any, Final
from uuid import UUID

from django.db import transaction

from workeasy360.commercial.partners import inputs, selectors
from workeasy360.commercial.partners.domain import clean_text
from workeasy360.commercial.partners.inputs import Problems
from workeasy360.contracts.enums import PartnerContactKind, PayoutFrequency
from workeasy360.contracts.errors import PartnerRecordNotFound, PartnerValidationError
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope

MANAGE: Final = "platform.staff_partner.manage"
VIEW: Final = "platform.staff_partner.view"

ADDRESS_KEYS: Final = ("line1", "line2", "city", "region", "postal_code")
BUSINESS_KEYS: Final = (
    "display_name",
    "legal_name",
    "business_email",
    "website",
    "phone",
    "alternative_email",
    "registration_number",
    "tax_number",
    "country",
    "address",
)
CONTACT_KEYS: Final = ("name", "job_title", "email", "phone", "same_as_primary")
SUPPORT_KEYS: Final = ("email", "phone", "url")
PAYOUT_KEYS: Final = ("frequency", "contact_email")
SECTIONS: Final = ("business", "contacts", "support", "payout", "internal_notes")

MAX_NOTES: Final = 2000

#: The profile columns each ``business`` key is stored in (the two names that
#: belong to the account are written to it instead).
_ACCOUNT_KEYS: Final = ("display_name", "legal_name")


class Parsed:
    """The cleaned part of a profile request and everything wrong with the rest."""

    def __init__(self) -> None:
        self.problems = Problems()
        self.business: dict[str, Any] = {}
        #: kind -> cleaned fields, or ``None`` for "same as the primary contact".
        self.contacts: dict[str, dict[str, str] | None] = {}
        self.support: dict[str, str] = {}
        self.payout: dict[str, str] = {}
        self.internal_notes: str | None = None

    @property
    def sections(self) -> list[str]:
        """Which fields changed, as ``section.key`` - what the audit records."""
        out = [f"business.{k}" for k in self.business]
        out += [f"contacts.{k}" for k in self.contacts]
        out += [f"support.{k}" for k in self.support]
        out += [f"payout.{k}" for k in self.payout]
        if self.internal_notes is not None:
            out.append("internal_notes")
        return sorted(out)


def parse(raw: Any) -> Parsed:
    """Validate a profile request. Only the keys present are returned, so a
    partial edit changes only what it names."""
    out = Parsed()
    body = inputs.mapping(raw)
    inputs.reject_unknown(body, SECTIONS, field="profile", problems=out.problems)
    _business(inputs.mapping(body.get("business")), out)
    _contacts(inputs.mapping(body.get("contacts")), out)
    _support(inputs.mapping(body.get("support")), out)
    _payout(inputs.mapping(body.get("payout")), out)
    if "internal_notes" in body:
        out.internal_notes = out.problems.run(
            "internal_notes",
            inputs.clean_note,
            body["internal_notes"],
            field="internal_notes",
            max_length=MAX_NOTES,
        )
    return out


def _business(raw: dict[str, Any], out: Parsed) -> None:
    p = out.problems
    inputs.reject_unknown(raw, BUSINESS_KEYS, field="business", problems=p)
    cleaners: dict[str, Any] = {
        "display_name": lambda v: clean_text(v, field="display_name", max_length=120),
        "legal_name": lambda v: clean_text(
            v, field="legal_name", max_length=200, required=False
        ),
        "business_email": lambda v: inputs.clean_email(v, field="business_email"),
        "website": lambda v: inputs.clean_https_url(v, field="website"),
        "phone": lambda v: inputs.clean_phone(v, field="phone"),
        "alternative_email": lambda v: inputs.clean_email(v, field="alternative_email"),
        "registration_number": lambda v: inputs.clean_reference(
            v, field="registration_number"
        ),
        "tax_number": lambda v: inputs.clean_reference(v, field="tax_number"),
        "country": lambda v: inputs.clean_country(v),
    }
    for key, clean in cleaners.items():
        if key in raw:
            value = p.run(f"business.{key}", clean, raw[key])
            if value is not None:
                out.business[key] = value
    if "address" in raw:
        address = _address(raw["address"], p)
        if address is not None:
            out.business["address"] = address


def _address(raw: Any, p: Problems) -> dict[str, str] | None:
    if raw is None:
        return {k: "" for k in ADDRESS_KEYS}
    if not isinstance(raw, dict):
        p.add("business.address", "address must be an object.")
        return None
    inputs.reject_unknown(raw, ADDRESS_KEYS, field="business.address", problems=p)
    cleaned: dict[str, str] = {}
    for key in ADDRESS_KEYS:
        if key == "postal_code":
            value = p.run(
                "business.address.postal_code",
                inputs.clean_postal_code,
                raw.get(key),
                field="postal_code",
            )
        else:
            value = p.run(
                f"business.address.{key}",
                clean_text,
                raw.get(key),
                field=key,
                max_length=120,
                required=False,
            )
        cleaned[key] = value or ""
    return cleaned


def _contacts(raw: dict[str, Any], out: Parsed) -> None:
    p = out.problems
    kinds = tuple(k.value for k in PartnerContactKind)
    inputs.reject_unknown(raw, kinds, field="contacts", problems=p)
    for kind in kinds:
        if kind not in raw:
            continue
        spec = raw[kind]
        if spec is None or (
            isinstance(spec, dict) and spec.get("same_as_primary") is True
        ):
            if kind == PartnerContactKind.PRIMARY.value:
                p.add(
                    "contacts.primary",
                    "The primary contact cannot be the same as itself.",
                )
            else:
                out.contacts[kind] = None
            continue
        if not isinstance(spec, dict):
            p.add(f"contacts.{kind}", "A contact must be an object.")
            continue
        inputs.reject_unknown(spec, CONTACT_KEYS, field=f"contacts.{kind}", problems=p)
        cleaners: dict[str, Any] = {
            "name": lambda v: clean_text(v, field="name", max_length=120, required=False),
            "job_title": lambda v: clean_text(
                v, field="job_title", max_length=120, required=False
            ),
            "email": lambda v: inputs.clean_email(v, field="email"),
            "phone": lambda v: inputs.clean_phone(v, field="phone"),
        }
        # Only the keys sent: a contact is merged field by field like the rest of
        # the profile, so sending a phone number cannot blank the name and email.
        out.contacts[kind] = {
            key: p.run(f"contacts.{kind}.{key}", clean, spec[key]) or ""
            for key, clean in cleaners.items()
            if key in spec
        }


def _support(raw: dict[str, Any], out: Parsed) -> None:
    p = out.problems
    inputs.reject_unknown(raw, SUPPORT_KEYS, field="support", problems=p)
    for key, clean in (
        ("email", lambda v: inputs.clean_email(v, field="support_email")),
        ("phone", lambda v: inputs.clean_phone(v, field="support_phone")),
        ("url", lambda v: inputs.clean_https_url(v, field="support_url")),
    ):
        if key in raw:
            value = p.run(f"support.{key}", clean, raw[key])
            if value is not None:
                out.support[key] = value


def _payout(raw: dict[str, Any], out: Parsed) -> None:
    p = out.problems
    inputs.reject_unknown(raw, PAYOUT_KEYS, field="payout", problems=p)
    if "frequency" in raw:
        allowed = {f.value for f in PayoutFrequency}
        if raw["frequency"] not in allowed:
            p.add(
                "payout.frequency",
                f"frequency must be one of {sorted(allowed)}.",
            )
        else:
            out.payout["frequency"] = raw["frequency"]
    if "contact_email" in raw:
        value = p.run(
            "payout.contact_email",
            inputs.clean_email,
            raw["contact_email"],
            field="payout_contact_email",
        )
        if value is not None:
            out.payout["contact_email"] = value


# --- reading ------------------------------------------------------------------


def profile_view(partner_id: UUID) -> dict[str, Any] | None:
    """The profile as staff read and edit it, or ``None`` for an unknown
    partner. Always the same shape: a partner with no profile row yet reads as
    empty fields, and a contact role with no row reads ``same_as_primary``."""
    from workeasy360.commercial.partners.models import PartnerContact, PartnerProfile

    row = selectors.partner_row(partner_id)
    if row is None:
        return None
    profile = PartnerProfile.objects.filter(partner_id=partner_id).first()
    contacts = {c.kind: c for c in PartnerContact.objects.filter(partner_id=partner_id)}
    address = dict(profile.address) if profile and profile.address else {}

    def contact(kind: str) -> dict[str, Any]:
        found = contacts.get(kind)
        if kind == PartnerContactKind.PRIMARY.value:
            return {
                # The account mirrors the primary contact; fall back to it for a
                # partner created before contacts had a table.
                "name": found.name if found else row.contact_name,
                "job_title": found.job_title if found else "",
                "email": found.email if found else row.contact_email,
                "phone": found.phone if found else "",
            }
        if found is None:
            return {"same_as_primary": True}
        return {
            "same_as_primary": False,
            "name": found.name,
            "job_title": found.job_title,
            "email": found.email,
            "phone": found.phone,
        }

    return {
        "partner_id": str(row.id),
        "version": row.version,
        "business": {
            "display_name": row.display_name,
            "legal_name": row.legal_name,
            "business_email": profile.business_email if profile else "",
            "website": profile.website if profile else "",
            "phone": profile.phone if profile else "",
            "alternative_email": profile.alternative_email if profile else "",
            "registration_number": profile.registration_number if profile else "",
            "tax_number": profile.tax_number if profile else "",
            "country": profile.country if profile else "",
            "address": {key: address.get(key, "") for key in ADDRESS_KEYS},
        },
        "contacts": {kind.value: contact(kind.value) for kind in PartnerContactKind},
        "support": {
            "email": profile.support_email if profile else "",
            "phone": profile.support_phone if profile else "",
            "url": profile.support_url if profile else "",
        },
        "payout": {
            "currency": row.payout_currency,
            "frequency": profile.payout_frequency
            if profile
            else PayoutFrequency.MANUAL.value,
            "contact_email": profile.payout_contact_email if profile else "",
        },
        "internal_notes": profile.internal_notes if profile else "",
        "updated_at": (profile.updated_at if profile else row.updated_at).isoformat(),
    }


def get_profile(ctx: TenantContext, partner_id: UUID) -> dict[str, Any]:
    authz.require(ctx, VIEW)
    view = profile_view(partner_id)
    if view is None:
        raise PartnerRecordNotFound("No such partner.", partner_id=str(partner_id))
    return view


# --- writing ------------------------------------------------------------------


@in_scope
def update_profile(
    ctx: TenantContext, partner_id: UUID, *, expected_version: int, data: Any
) -> dict[str, Any]:
    """Change a partner's business profile and contacts.

    Authorized as every partner change is (``platform.staff_partner.manage``,
    dangerous: a second factor), validated in full **before** the first write,
    version-checked like :func:`services.update_partner` so two people editing
    one partner never silently overwrite each other, and audited with the names
    of the fields that changed - never their values.

    The partner's currency and code are not here and cannot be sent: neither
    changes after creation, because commission and payouts are denominated in
    the one and the other is the partner's permanent address in history.
    """
    from workeasy360.commercial.partners import services
    from workeasy360.commercial.partners.models import PartnerContact, PartnerProfile

    authz.require(ctx, MANAGE)
    if isinstance(expected_version, bool) or not isinstance(expected_version, int):
        raise PartnerValidationError(
            "expected_version is required: the version of the partner you read.",
            field="expected_version",
        )
    parsed = parse(data)
    parsed.problems.first()
    if not parsed.sections:
        raise PartnerValidationError("Nothing to change.", field="body")

    with transaction.atomic():
        partner = services._lock_partner(partner_id, expected_version=expected_version)
        original_contact_name = partner.contact_name
        original_contact_email = partner.contact_email

        account_fields = [k for k in _ACCOUNT_KEYS if k in parsed.business]
        for key in account_fields:
            setattr(partner, key, parsed.business[key])

        primary = parsed.contacts.get(PartnerContactKind.PRIMARY.value)
        if primary is not None:
            # The account mirrors the primary contact's name and email: only the
            # ones that were sent change.
            for source, column in (("name", "contact_name"), ("email", "contact_email")):
                if source in primary:
                    setattr(partner, column, primary[source])
                    account_fields.append(column)

        profile_fields = {
            **{k: v for k, v in parsed.business.items() if k not in _ACCOUNT_KEYS},
            **{f"support_{k}": v for k, v in parsed.support.items()},
            **{f"payout_{k}": v for k, v in parsed.payout.items()},
        }
        if parsed.internal_notes is not None:
            profile_fields["internal_notes"] = parsed.internal_notes
        if profile_fields:
            profile, _ = PartnerProfile.objects.get_or_create(
                partner_id=partner_id, defaults={"updated_by_id": ctx.user_id}
            )
            for key, value in profile_fields.items():
                setattr(profile, key, value)
            profile.updated_by_id = ctx.user_id
            profile.save()

        for kind, fields in parsed.contacts.items():
            if fields is None:
                PartnerContact.objects.filter(partner_id=partner_id, kind=kind).delete()
                continue
            contact = PartnerContact.objects.filter(
                partner_id=partner_id, kind=kind
            ).first()
            if contact is None:
                contact = PartnerContact(partner_id=partner_id, kind=kind)
                if kind == PartnerContactKind.PRIMARY.value:
                    # A partner created before contacts had a table keeps its
                    # contact on the account: start the row from it, so adding a
                    # phone number does not appear to remove the name.
                    contact.name = original_contact_name
                    contact.email = original_contact_email
            for key, value in fields.items():
                setattr(contact, key, value)
            contact.updated_by_id = ctx.user_id
            contact.save()

        services._bump(partner, account_fields)
        audit.record(
            ctx,
            "partner.profile_updated",
            resource_type="partners.partner",
            resource_id=partner.id,
            # Which fields changed, never their values (contact data is PII).
            changes={"partner_id": str(partner.id), "fields": parsed.sections},
        )
    view = profile_view(partner_id)
    assert view is not None
    return view

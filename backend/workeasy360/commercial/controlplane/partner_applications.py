"""The public partner application form's command (owner decision 2026-11-09).

Validation runs first and identically for every address, so a validation answer is
never a registration oracle; the HTTP answer is the same for every outcome. The returned
outcome is for metrics and tests only. See ``partners/applications.py``.
"""

from __future__ import annotations

from typing import Any

from django.db import IntegrityError, transaction

from workeasy360.commercial.controlplane.partner_onboarding import (
    _free_code,
    default_currency,
)
from workeasy360.commercial.partners import applications, capabilities
from workeasy360.contracts.enums import UserStatus
from workeasy360.contracts.errors import PartnerValidationError
from workeasy360.platform.observability.metrics import metrics

OPERATIONS = "partner_application_total"


def _name(raw: Any, field: str, label: str, *, limit: int = 120) -> str:
    value = " ".join(str(raw or "").split())
    if not value:
        raise PartnerValidationError(f"Enter your {label}.", field=field)
    if len(value) > limit or any(ord(ch) < 32 for ch in value):
        raise PartnerValidationError(
            f"Your {label} must be at most {limit} characters.", field=field
        )
    return value


def apply(
    *,
    business_name: Any,
    first_name: Any,
    last_name: Any,
    email: Any,
    password: Any,
    country: Any,
    phone: Any,
    interest: Any,
) -> str:
    """Start an application. The person confirms their address, signs in on the
    partner portal, sends their KYC, and a reviewer decides."""
    from workeasy360.platform.administration.self_service import (
        _clean_email,
        _send_existing_notice,
        _send_verification,
    )
    from workeasy360.platform.administration.site import partner_applications_enabled
    from workeasy360.platform.identity import lifecycle
    from workeasy360.platform.identity.account import check_new_password
    from workeasy360.platform.identity.models import User

    if not partner_applications_enabled():
        raise PartnerValidationError(
            "Partner applications are closed at the moment.", field="applications"
        )
    business = _name(business_name, "business_name", "business name")
    first = _name(first_name, "first_name", "first name", limit=100)
    last = _name(last_name, "last_name", "last name", limit=100)
    typed, address = _clean_email(email)
    code = str(country or "").strip().upper()
    if len(code) != 2 or not code.isalpha():
        raise PartnerValidationError("Choose your country.", field="country")
    contact_phone = " ".join(str(phone or "").split())[:32]
    wanted = str(interest or "").strip()
    if wanted not in capabilities.EXPERIENCES:
        raise PartnerValidationError("Choose how you want to partner.", field="interest")
    secret = password if isinstance(password, str) else ""
    full_name = f"{first} {last}"
    check_new_password(secret, user=User(email=address, full_name=full_name))

    with transaction.atomic():
        existing = lifecycle.lock_by_email(address)
        if existing is not None:
            lifecycle.spend_password_time(secret)
            if existing.status == UserStatus.ACTIVE.value:
                _send_existing_notice(existing)
            outcome = "existing"
        else:
            try:
                with transaction.atomic():
                    user = lifecycle.create_account(
                        email=typed,
                        password=secret,
                        full_name=full_name,
                        status=UserStatus.PENDING,
                        source=User.AcquisitionSource.SELF_SIGNUP,
                    )
            except IntegrityError:
                # The same address applied a moment ago in another request; that
                # request sent the link. Same answer to this one.
                lifecycle.spend_password_time(secret)
                outcome = "race"
            else:
                applications.create_draft_for_applicant(
                    user=user,
                    code=_free_code(business),
                    business_name=business,
                    contact_name=full_name,
                    contact_email=typed,
                    country=code,
                    phone=contact_phone,
                    payout_currency=default_currency(code),
                    requested_experience=wanted,
                )
                _send_verification(user, organization_name="")
                outcome = "created"
    metrics.increment(OPERATIONS, outcome=outcome)
    return outcome

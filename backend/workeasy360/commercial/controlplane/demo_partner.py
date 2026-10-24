"""A development-only demo partner (ADR-045).

``manage.py create_demo_partner`` builds one ordinary ``PartnerAccount`` holding
every capability, an owner member and a grant for every available product, so a
developer can sign in to the partner portal and try the whole panel. It adds no
type, no special account and no shortcut: the same registry, the same grants and
the same member row any partner has.

**It cannot exist in production.** :func:`assert_development` refuses any
environment but development and test, and the command checks it before touching
the database. The owner's password is generated here from the operating system's
random source, returned to the command *once*, never stored anywhere but as the
account's password hash, and the account is flagged so the first thing its holder
must do is choose their own (``User.password_change_required``).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from django.conf import settings
from django.db import transaction

from workeasy360.commercial.catalog import selectors as catalog
from workeasy360.commercial.partners import capabilities, services, system
from workeasy360.contracts.enums import UserStatus
from workeasy360.contracts.errors import PartnerValidationError
from workeasy360.contracts.values.control_plane import PLATFORM_SYSTEM_USER_EMAIL
from workeasy360.platform.authz.control_plane import (
    ensure_platform_organization,
    ensure_scope,
    platform_context,
)
from workeasy360.platform.identity import lifecycle
from workeasy360.platform.identity.models import User
from workeasy360.platform.tenancy.unscoped import unscoped_access

DEMO_EMAIL: Final = "partner.demo@workeasy360.test"
DEMO_CODE: Final = "demo-partner"
DEMO_NAME: Final = "Workeasy360 Demo Partner"
DEMO_OWNER_NAME: Final = "Demo Partner Owner"
REASON: Final = "Development demo partner (create_demo_partner)"

#: Environments a demo partner may be created in. Anything else - production,
#: staging, an unknown value - is refused, so a missing setting fails closed.
ALLOWED_ENVIRONMENTS: Final = frozenset({"development", "test"})


class DemoPartnerRefused(PartnerValidationError):
    """The demo partner was asked for somewhere it must not exist."""


def assert_development() -> None:
    environment = str(getattr(settings, "ENVIRONMENT", ""))
    if environment not in ALLOWED_ENVIRONMENTS:
        raise DemoPartnerRefused(
            "The demo partner exists for local development only and is never "
            f"created in the {environment or 'unknown'} environment.",
            field="environment",
        )


@dataclass(frozen=True, slots=True)
class DemoPartner:
    partner_id: UUID
    user_id: UUID
    partner_created: bool
    user_created: bool
    #: Present only when this call set the password (a new account, or a reset).
    #: The caller shows it once and discards it.
    temporary_password: str | None


def new_temporary_password() -> str:
    """24 random characters (~144 bits). Meets the password policy: long, and
    nothing guessable in it."""
    return secrets.token_urlsafe(18)


def will_set_password(*, reset_password: bool) -> bool:
    """Whether :func:`create_demo_partner` will generate a password, decided
    before it runs so the command can refuse to do so unless it can show it."""
    with unscoped_access(reason="Look up the demo partner owner", module=__name__):
        exists = User.objects.filter(email=DEMO_EMAIL).exists()
    return reset_password or not exists


def create_demo_partner(
    *, currency: str = "INR", reset_password: bool = False
) -> DemoPartner:
    assert_development()
    ensure_platform_organization()
    with unscoped_access(reason="Look up the control-plane identity", module=__name__):
        control_plane_user = User.objects.get(email=PLATFORM_SYSTEM_USER_EMAIL)
    ctx = platform_context(user_id=control_plane_user.id)
    password: str | None = None

    with transaction.atomic(), ensure_scope(ctx):
        with unscoped_access(reason="Create the demo partner owner", module=__name__):
            owner = lifecycle.lock_by_email(DEMO_EMAIL)
            user_created = owner is None
            if owner is None:
                password = new_temporary_password()
                owner = lifecycle.create_account(
                    email=DEMO_EMAIL,
                    password=password,
                    full_name=DEMO_OWNER_NAME,
                    status=UserStatus.ACTIVE,
                    source=User.AcquisitionSource.OPERATOR,
                    verified=True,
                )
            elif reset_password:
                password = new_temporary_password()
            if password is not None:
                lifecycle.set_temporary_password(owner, password)

        parsed_currency = services.parse_currency(currency)
        partner, partner_created = system.ensure_partner(
            ctx,
            code=DEMO_CODE,
            display_name=DEMO_NAME,
            legal_name=DEMO_NAME,
            contact_name=DEMO_OWNER_NAME,
            contact_email=DEMO_EMAIL,
            payout_currency=parsed_currency.value,
        )

        for key in sorted(capabilities.CAPABILITIES):
            services._grant_capability(ctx, partner.id, capability=key, reason=REASON)
        for product in catalog.visible_products():
            if product["status"] == "available":
                services._grant_product(ctx, partner.id, product_code=product["code"])
        services._invite_member(
            ctx,
            partner.id,
            email=DEMO_EMAIL,
            role="owner",
            full_name=DEMO_OWNER_NAME,
            send_link=False,
        )

        system.activate(ctx, partner.id, reason=REASON)

    return DemoPartner(
        partner_id=partner.id,
        user_id=owner.id,
        partner_created=partner_created,
        user_created=user_created,
        temporary_password=password,
    )

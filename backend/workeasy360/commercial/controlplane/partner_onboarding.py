"""Add Partner: the credentials check and the one atomic command (ADR-047, ADR-048).

Super Admin creates a partner with **only what the account needs**: the
partner's name, the administrator's email, the password Super Admin types (twice)
and a partner type - Reseller, White-label or Affiliate - with the country as an
option. Everything else (business details, products, prices, commission, brand,
domain, referral program, team) is set afterwards on the partner's own page, in
its *Complete Your Profile* wizard. Two functions:

- :func:`credentials_check` answers "can this email and password be used?"
  without writing anything - whether the address already has an account (so the
  form can offer to link it) and every password-policy problem at once;
- :func:`create` writes. Everything is validated **before the first write**, then
  one ``transaction.atomic()`` creates the account (or finds the one being
  linked), the partner (in ``draft``), the country on its profile, the type's
  capability grants and the administrator's owner membership. If any step fails,
  none of it happened: no half-created user, partner or grant.

**Draft, not active.** A new partner is a draft: it can do nothing until its
profile is complete and staff activate it (``profile_completion``; the console
calls a draft with a complete profile *ready*). Its administrator can already
sign in with the email and password typed here; the partner portal opens once
the partner is active.

**The password.** It is typed by Super Admin, validated against the platform
password policy (the same ``check_new_password`` every other password goes
through) and handed to ``lifecycle.create_account``, which hashes it with
Django's configured hasher before the row is written. Nothing here stores,
logs, audits, emails, echoes or returns it: it is read in exactly two places
(:func:`_check_password` judges it, :func:`_new_account` hands it to be
hashed), no error message or audit entry carries it, and the result carries the
email and the partner, never a secret. There is no temporary password, no
forced change and no invitation email, because there is nothing to invite: the
person signs in with what they were told.

**An existing account is never touched.** If the address already belongs to a
Workeasy360 account the form says so and offers to *link* it: the partner's
administrator is then that person, signing in with the password they already
have. A password is refused for a linked account, so this command can never
reset someone's password - a reset has its own flow.

**The type is a preset, not a kind.** Choosing Reseller, White-label or
Affiliate grants that experience's capability bundle
(``capabilities.EXPERIENCE_BUNDLES``) and records it as the landing preference
(``PartnerAccount.default_experience``). Authorization still comes from the
grants alone, and a partner can later hold several bundles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.views.decorators.debug import sensitive_variables

from workeasy360.commercial.partners import (
    capabilities,
    experience,
    profile,
    selectors,
    services,
)
from workeasy360.commercial.partners.domain import clean_text
from workeasy360.commercial.partners.inputs import Problems
from workeasy360.contracts.enums import UserStatus
from workeasy360.contracts.errors import PartnerValidationError, PasswordPolicyViolation
from workeasy360.contracts.values.countries import COUNTRY_CODES
from workeasy360.contracts.values.money import CurrencyCode
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.audit import services as audit
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import in_scope
from workeasy360.platform.identity import lifecycle
from workeasy360.platform.identity import selectors as identity
from workeasy360.platform.identity.email import normalize_email
from workeasy360.platform.observability.metrics import metrics

MANAGE: Final = services.MANAGE

REASON: Final = "Created through Add Partner"
OPERATIONS: Final = services.PARTNER_OPERATIONS

#: ``create``: a new account with the password typed here. ``link_existing``: the
#: administrator is an account that already exists, and keeps its own password.
ADMIN_MODES: Final = ("create", "link_existing")

#: The longest password accepted here (the form's own limit). Bounding it keeps the
#: similarity and hashing work a staff request can cause small; the platform's
#: minimum length and the other rules are in ``check_new_password``.
MAX_PASSWORD_LENGTH: Final = 128

#: The role the person Super Admin sets up holds. An owner can do everything the
#: partner can, including deciding who else acts for it - the person who runs the
#: partner, not one more member of its team.
ADMIN_ROLE: Final = "owner"

#: The keys the Add Partner request may carry. Anything else is refused, so the
#: browser cannot slip in capabilities, products, prices or a status.
_BODY_KEYS: Final = frozenset({"experience", "partner", "administrator"})
_PARTNER_KEYS: Final = frozenset({"display_name", "country"})

#: The preferred currency a new partner starts with, from its country when the
#: platform keeps that country's currency; otherwise the platform's default. It
#: can be changed in Complete Your Profile while the partner is a draft.
_COUNTRY_CURRENCY: Final = {
    "GB": "GBP",
    "US": "USD",
    "AU": "AUD",
    "CA": "CAD",
    "IN": "INR",
    "JP": "JPY",
    "AE": "AED",
    **{
        code: "EUR"
        for code in (
            "AT BE CY DE EE ES FI FR GR HR IE IT LT LU LV MT NL PT SI SK"
        ).split()
    },
}

_EXISTING_ACCOUNT: Final = (
    "This email already belongs to an existing Workeasy360 account."
)
_CANNOT_LINK: Final = "That account cannot be added."
_STAFF_ACCOUNT: Final = (
    "A Workeasy360 staff account cannot be a partner administrator. Use another email."
)


@dataclass(frozen=True, slots=True)
class Created:
    """The result of a successful :func:`create`: ids and facts, never a secret."""

    partner_id: UUID
    code: str
    display_name: str
    status: str
    experience: str
    default_experience: str
    administrator_email: str
    account_created: bool
    capabilities: list[str]
    payout_currency: str


# --- the administrator's input ---------------------------------------------------------


@dataclass(slots=True)
class _Admin:
    """The administrator as typed. ``password`` and ``confirm`` are carried so the
    two functions that need them (:func:`_check_password` to judge, and
    :func:`_new_account` to hash) can read them; nothing else touches them, and
    this object is never logged or returned."""

    email: str
    typed_email: str
    first_name: str
    last_name: str
    mode: str
    # Never in a repr: a traceback, a log line or a debugger that prints this
    # object must not print what was typed.
    password: str = field(repr=False)
    confirm: str = field(repr=False)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


def _admin_input(raw: Any, problems: Problems, *, with_password: bool) -> _Admin:
    spec = raw if isinstance(raw, dict) else {}
    # Parsed quietly: the shared review reports a missing or malformed address
    # once, against the same field, so it is not reported twice.
    typed = Problems().run(
        "administrator.email",
        services.clean_email,
        spec.get("email"),
        required=True,
        field="email",
    )
    first = problems.run(
        "administrator.first_name",
        clean_text,
        spec.get("first_name"),
        field="first_name",
        max_length=100,
        required=False,
    )
    last = problems.run(
        "administrator.last_name",
        clean_text,
        spec.get("last_name"),
        field="last_name",
        max_length=100,
        required=False,
    )
    mode = spec.get("mode") or "create"
    if mode not in ADMIN_MODES:
        problems.add("administrator.mode", f"mode must be one of {list(ADMIN_MODES)}.")
        mode = "create"

    def secret(key: str) -> str:
        # Read only when the caller asked for secrets; never coerced, so a
        # non-string is a refusal and not "None" or "123" set as a password.
        value = spec.get(key) if with_password else ""
        if value is None:
            return ""
        if not isinstance(value, str):
            problems.add(f"administrator.{key}", f"{key} must be text.")
            return ""
        return value

    return _Admin(
        email=normalize_email(typed or ""),
        typed_email=typed or "",
        first_name=first or "",
        last_name=last or "",
        mode=mode,
        password=secret("password"),
        confirm=secret("password_confirm"),
    )


def _account_state(email: str) -> tuple[str, UUID | None]:
    """``("new", None)`` | ``("existing", id)`` | ``("unavailable", None)`` |
    ``("staff", None)``.

    ``unavailable`` is an account that exists but may not be assigned anything
    (disabled, locked, not yet set up): the same word for each, so the answer
    reveals no more than "you cannot link this one". ``staff`` is a Workeasy360
    staff account: staff and partner access are separate doors, so one identity
    is never made to hold both (a customer's owner or a mistyped address matching
    a colleague is the case this guards)."""
    if not email:
        return "new", None
    facts = identity.user_facts_for_email(email)
    if facts is None:
        return "new", None
    if facts["is_staff"]:
        return "staff", None
    if (
        facts["status"] != UserStatus.ACTIVE.value
        or facts["is_locked_out"]
        or not identity.has_usable_password(facts["user_id"])
    ):
        return "unavailable", None
    return "existing", facts["user_id"]


def _check_account(admin: _Admin, problems: Problems) -> tuple[str, UUID | None]:
    state, user_id = _account_state(admin.email)
    if admin.mode == "create" and state != "new":
        problems.add("administrator.email", _EXISTING_ACCOUNT)
    if admin.mode == "link_existing":
        if state == "new":
            problems.add(
                "administrator.email",
                "No Workeasy360 account has that email, so there is nothing to link. "
                "Create the account instead.",
            )
        elif state == "staff":
            problems.add("administrator.email", _STAFF_ACCOUNT)
        elif state == "unavailable":
            problems.add("administrator.email", _CANNOT_LINK)
    return state, user_id


def _check_password(admin: _Admin, problems: Problems) -> None:
    """Every problem with the typed password, against the platform policy. The
    messages are the validators' fixed help texts: none repeats the password."""
    from workeasy360.platform.identity.account import check_new_password
    from workeasy360.platform.identity.models import User

    if admin.mode == "link_existing":
        if admin.password or admin.confirm:
            problems.add(
                "administrator.password",
                "A password cannot be set here for an existing account. It keeps "
                "the password it has; resetting one has its own action.",
            )
        return
    if not admin.password:
        problems.add("administrator.password", "Enter a password.")
        return
    if (
        len(admin.password) > MAX_PASSWORD_LENGTH
        or len(admin.confirm) > MAX_PASSWORD_LENGTH
    ):
        # Refused before any validator runs on it.
        problems.add(
            "administrator.password",
            f"The password must be at most {MAX_PASSWORD_LENGTH} characters.",
        )
        return
    if admin.password != admin.confirm:
        problems.add("administrator.password_confirm", "The two passwords do not match.")
    try:
        check_new_password(
            admin.password, user=User(email=admin.email, full_name=admin.full_name)
        )
    except PasswordPolicyViolation as exc:
        for message in exc.context.get("problems", []):
            problems.add("administrator.password", str(message))


def password_policy() -> dict[str, Any]:
    """What the form tells the person about the password rules."""
    return {"min_length": int(settings.PASSWORD_MIN_LENGTH)}


# --- the credentials check --------------------------------------------------------------


@sensitive_variables()
def credentials_check(ctx: TenantContext, raw: Any) -> dict[str, Any]:
    """Whether this email and password can be used, without writing anything.

    ``email_status`` is ``available`` (no account), ``existing_account`` (the form
    offers to link it) or ``unavailable`` (it exists but cannot be assigned). The
    password is judged only when a new account would be created. The answer
    carries no password and no detail of an existing account beyond that it
    exists - which is the one fact the form needs, and the reason this is a
    staff-only, second-factor-gated, rate-limited command.
    """
    authz.require(ctx, MANAGE)
    problems = Problems()
    body = raw if isinstance(raw, dict) else {}
    admin = _admin_input(body.get("administrator", body), problems, with_password=True)
    if not admin.email:
        # Parsed quietly by _admin_input: this check is where the address is judged.
        problems.add("administrator.email", "Enter a valid email address.")
    state = "new"
    if admin.email and not problems.items:
        state, _user = _account_state(admin.email)
    if state == "new" or admin.mode == "link_existing":
        _check_password(admin, problems)
    if admin.mode == "create" and state != "new" and not problems.items:
        problems.add("administrator.email", _EXISTING_ACCOUNT)
    if admin.mode == "link_existing" and state == "new" and admin.email:
        problems.add(
            "administrator.email",
            "No Workeasy360 account has that email, so there is nothing to link.",
        )
    if admin.mode == "link_existing" and state == "staff":
        problems.add("administrator.email", _STAFF_ACCOUNT)
    return {
        "email_status": {
            "new": "available",
            "existing": "existing_account",
            "unavailable": "unavailable",
            "staff": "unavailable",
        }[state],
        "problems": problems.items,
        "policy": password_policy(),
    }


# --- the request --------------------------------------------------------------------------


@dataclass(slots=True)
class _Plan:
    experience: str
    display_name: str
    country: str
    admin: _Admin
    account: str
    user_id: UUID | None
    problems: list[dict[str, str]]


def _plan(raw: Any, *, with_password: bool) -> _Plan:
    """Validate the whole request, reporting every problem at once."""
    body = raw if isinstance(raw, dict) else {}
    problems = Problems()
    unknown = sorted(set(body) - _BODY_KEYS)
    if unknown:
        problems.add(
            "body",
            f"Add Partner takes only {sorted(_BODY_KEYS)}; the rest of a partner's "
            "setup is done in Complete Your Profile.",
        )
    key = problems.run(
        "experience", capabilities.require_experience, body.get("experience")
    )
    partner = body.get("partner") if isinstance(body.get("partner"), dict) else {}
    if set(partner) - _PARTNER_KEYS:
        problems.add("partner", f"partner takes only {sorted(_PARTNER_KEYS)}.")
    name = problems.run(
        "partner.display_name",
        clean_text,
        partner.get("display_name"),
        field="display_name",
        max_length=120,
    )
    country = partner.get("country") or ""
    if country and (not isinstance(country, str) or country not in COUNTRY_CODES):
        problems.add("partner.country", "Choose a country from the list.")
        country = ""
    admin = _admin_input(body.get("administrator"), problems, with_password=with_password)
    if not admin.email:
        problems.add("administrator.email", "Enter a valid email address.")
    state, user_id = ("new", None)
    if admin.email:
        state, user_id = _check_account(admin, problems)
    return _Plan(
        experience=key or "",
        display_name=name or "",
        country=country,
        admin=admin,
        account=state,
        user_id=user_id,
        problems=problems.items,
    )


def default_currency(country: str) -> str:
    """The preferred currency a partner starts with (see ``_COUNTRY_CURRENCY``)."""
    fallback = str(getattr(settings, "PUBLIC_CATALOG_DEFAULT_CURRENCY", "INR"))
    chosen = _COUNTRY_CURRENCY.get(country, fallback)
    return chosen if chosen in {c.value for c in CurrencyCode} else CurrencyCode.INR.value


_SLUG_SPLIT: Final = re.compile(r"[^a-z0-9]+")


def _code_base(display_name: str) -> str:
    """A partner code from its name: lower-case words joined by hyphens, starting
    with a letter, short enough to take a numeric suffix. Never shown as the
    name; it is the partner's stable handle in URLs and audit entries."""
    words = [w for w in _SLUG_SPLIT.split(display_name.lower()) if w]
    base = "-".join(words)[:40].strip("-")
    if not base or not base[0].isalpha():
        base = f"partner-{base}".strip("-")[:40]
    if len(base) < 3:
        base = f"{base}-partner"
    return base


def _free_code(display_name: str) -> str:
    """The first unused code for this name (``acme``, ``acme-2``, ``acme-3``...).

    Called inside the creating transaction. The candidate itself is locked (the
    same key another creation uses for that code, whether it reached it as a
    name or as a suffix) and checked again once the lock is held, so two
    concurrent creations never pick the same code: "Acme" settling on
    ``acme-2`` and "Acme 2" starting from ``acme-2`` serialise on one key."""
    base = _code_base(display_name)
    _lock(f"partner-onboard:code:{base}")
    taken = selectors.codes_starting_with(base)
    n = 1
    while True:
        candidate = base if n == 1 else f"{base}-{n}"
        if candidate not in taken:
            if candidate != base:
                _lock(f"partner-onboard:code:{candidate}")
            if candidate not in selectors.codes_starting_with(candidate):
                return candidate
            taken.add(candidate)
        n += 1


# --- the command ------------------------------------------------------------------------------


def _lock(key: str) -> None:
    """Serialise concurrent creations that name the same address or code, so the
    second sees the first's committed rows instead of failing half-way."""
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [key])


def _new_account(admin: _Admin) -> Any:
    """The administrator's account, with the password Super Admin typed. This is
    one of the two functions that read ``admin.password`` (the other only judges
    it); the hash is made by Django's configured hasher inside
    ``lifecycle.create_account`` before the row is written, and the account is left
    unverified - nobody has proved the mailbox."""
    from workeasy360.platform.identity.models import User

    try:
        with transaction.atomic():
            return lifecycle.create_account(
                email=admin.typed_email,
                password=admin.password,
                full_name=admin.full_name,
                status=UserStatus.ACTIVE,
                source=User.AcquisitionSource.OPERATOR,
                verified=False,
            )
    except (IntegrityError, ValidationError) as exc:
        # Another request took the address between the check and the insert (the
        # row is validated before it is written, so the uniqueness check can be
        # the one that notices).
        raise PartnerValidationError(
            _EXISTING_ACCOUNT, field="administrator.email"
        ) from exc


def _raise(problems: list[dict[str, str]]) -> None:
    """Refuse with the first problem and the whole list, so the form can mark
    every field at once."""
    first = problems[0]
    raise PartnerValidationError(
        first["message"], field=first["field"], problems=problems
    )


@in_scope
@sensitive_variables()
def create(ctx: TenantContext, raw: Any) -> Created:
    """Create the partner (a draft) and its administrator's login, atomically.

    Order of events: authorize (the partner command's second-factor gate first),
    validate everything (nothing is written until all of it is right), then one
    transaction does every write. A refusal anywhere rolls the whole thing back.
    """
    authz.require(ctx, MANAGE)
    plan = _plan(raw, with_password=True)
    problems = Problems()
    problems.items.extend(plan.problems)
    _check_password(plan.admin, problems)
    if problems.items:
        metrics.increment(OPERATIONS, operation="onboard", outcome="refused")
        _raise(problems.items)

    key = plan.experience
    with transaction.atomic():
        _lock(f"partner-onboard:email:{plan.admin.email}")
        # Re-judge the address now that nothing else can change it.
        state, user_id = _account_state(plan.admin.email)
        if plan.admin.mode == "create" and state != "new":
            raise PartnerValidationError(_EXISTING_ACCOUNT, field="administrator.email")
        if plan.admin.mode == "link_existing" and state != "existing":
            raise PartnerValidationError(
                _STAFF_ACCOUNT if state == "staff" else _CANNOT_LINK,
                field="administrator.email",
            )

        account_created = plan.admin.mode == "create"
        if account_created:
            user = _new_account(plan.admin)
            user_id = user.id

        partner = services.create_partner(
            ctx,
            code=_free_code(plan.display_name),
            display_name=plan.display_name,
            payout_currency=default_currency(plan.country),
        )
        if plan.country:
            profile.update_profile(
                ctx,
                partner.id,
                expected_version=partner.version,
                data={"business": {"country": plan.country}},
            )

        # The type: its capability bundle and the landing preference, in one call.
        transition = experience.grant_experience(
            ctx, partner.id, experience=key, reason=REASON, make_default=True
        )
        services.attach_user_as_member(
            ctx,
            partner.id,
            user_id=user_id,
            role=ADMIN_ROLE,  # type: ignore[arg-type]
        )
        audit.record(
            ctx,
            "partner.onboarded",
            resource_type="partners.partner",
            resource_id=partner.id,
            # Kinds and counts only: no email, no name, no secret.
            changes={
                "partner_id": str(partner.id),
                "experience": key,
                "administrator": "created" if account_created else "linked",
                "capabilities": len(transition.capabilities_granted),
            },
            reason=REASON,
        )
        current = selectors.partner_row(partner.id)
    metrics.increment(OPERATIONS, operation="onboard", outcome="ok")
    return Created(
        partner_id=partner.id,
        code=current.code,
        display_name=current.display_name,
        status=current.status,
        experience=key,
        default_experience=current.default_experience,
        administrator_email=plan.admin.typed_email,
        account_created=account_created,
        capabilities=sorted(selectors.active_capabilities(partner.id)),
        payout_currency=current.payout_currency,
    )

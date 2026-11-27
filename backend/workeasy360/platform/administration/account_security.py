"""The person's own account security on WorkEasy360 Accounts, composed with the
notices it sends (ADR-075, Accounts V3 sections 08, 09 and 10).

``identity.two_step`` and ``identity.account`` make the state changes; this
module adds what must accompany them and lives above identity because it also
mails people (``notifications``) and reads organizations: ending the person's
other sessions when a credential changes, the security notice to the verified
address, account closure (which must never orphan an organization) and the
central account-security support queue.

Every action here is the signed-in person's own, through an interactive session
with CSRF (``account_views``, ``accounts_center_views``). None is an automation
action, a partner API or a product credential (Accounts V3 10.3).
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from workeasy360.contracts.enums import UserStatus
from workeasy360.contracts.errors import (
    LastPartnerAdminBlocksClosure,
    MembershipValidationError,
    SoleOwnerBlocksClosure,
    TwoStepCodeInvalid,
)
from workeasy360.platform.identity import account, two_step
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

TWO_STEP_CHANGED_TEMPLATE = "platform.account.two_step_changed"
ACCOUNT_CLOSED_TEMPLATE = "platform.account.closed"
NEW_SIGN_IN_TEMPLATE = "platform.account.new_sign_in"
SUPPORT_RECEIVED_TEMPLATE = "platform.account.support_received"
PASSWORD_CHANGED_SIGNED_IN_TEMPLATE = "platform.account.password_changed_signed_in"

#: A device + network pair seen in a successful sign-in this recently is not new.
NEW_DEVICE_LOOKBACK = timedelta(days=90)
CLOSURE_CONFIRMATION = "CLOSE"


def _notify(user: Any, template_key: str, context: dict[str, Any], key: str) -> None:
    from workeasy360.platform.administration.site import support_email
    from workeasy360.platform.notifications.services import notify_account

    notify_account(
        template_key=template_key,
        recipient_email=user.email_original or user.email,
        context={**context, "support_email": support_email()},
        idempotency_key=key,
    )


def _user(user_id: Any) -> Any:
    from workeasy360.platform.identity.models import User

    return User.objects.get(pk=user_id)


# --- two-step verification ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TwoStepTurnedOn:
    recovery_codes: list[str]
    replaced: bool
    other_sessions_ended: int


def confirm_two_step(
    user_id: Any,
    *,
    code: Any,
    keep_public_id: UUID | None,
    ip_prefix: str = "",
    device_label: str = "",
) -> TwoStepTurnedOn:
    """Turn on (or replace) the authenticator; then end every other session of
    the person - a new credential means anyone signed in elsewhere signs in
    again with it - and tell the verified address."""
    from workeasy360.platform.identity.models import AccountEvent

    with transaction.atomic():
        done = two_step.confirm_setup(user_id, code=code)
        ended = account.revoke_other_sessions(
            user_id,
            keep_public_id=keep_public_id,
            ip_prefix=ip_prefix,
            device_label=device_label,
        )
        kind = (
            AccountEvent.Kind.TWO_STEP_REPLACED
            if done.replaced
            else AccountEvent.Kind.TWO_STEP_ENABLED
        )
        account.record_account_event(
            user_id, kind, ip_prefix=ip_prefix, device_label=device_label
        )
        change = (
            "Your authenticator app was replaced"
            if done.replaced
            else "Two-step verification was turned on"
        )
        _notify(
            _user(user_id),
            TWO_STEP_CHANGED_TEMPLATE,
            {"change": change},
            f"account:{user_id}:two-step:{kind}:{secrets.token_hex(8)}",
        )
    return TwoStepTurnedOn(done.recovery_codes, done.replaced, int(ended))


def regenerate_codes(
    user_id: Any, *, password: Any, code: Any, ip_prefix: str = "", device_label: str = ""
) -> list[str]:
    from workeasy360.platform.identity.models import AccountEvent

    two_step.check_password_counted(user_id, password)
    with transaction.atomic():
        codes = two_step.regenerate_recovery_codes(user_id, password=None, code=code)
        account.record_account_event(
            user_id,
            AccountEvent.Kind.RECOVERY_CODES_REGENERATED,
            ip_prefix=ip_prefix,
            device_label=device_label,
        )
        _notify(
            _user(user_id),
            TWO_STEP_CHANGED_TEMPLATE,
            {"change": "New recovery codes were created (the old ones stopped working)"},
            f"account:{user_id}:two-step:codes:{secrets.token_hex(8)}",
        )
    return codes


def turn_off_two_step(
    user_id: Any,
    *,
    password: Any,
    code: Any,
    keep_public_id: UUID | None,
    ip_prefix: str = "",
    device_label: str = "",
) -> int:
    from workeasy360.platform.identity.models import AccountEvent

    two_step.check_password_counted(user_id, password)
    with transaction.atomic():
        two_step.disable(user_id, password=None, code=code)
        ended = account.revoke_other_sessions(
            user_id,
            keep_public_id=keep_public_id,
            ip_prefix=ip_prefix,
            device_label=device_label,
        )
        account.record_account_event(
            user_id,
            AccountEvent.Kind.TWO_STEP_DISABLED,
            ip_prefix=ip_prefix,
            device_label=device_label,
        )
        _notify(
            _user(user_id),
            TWO_STEP_CHANGED_TEMPLATE,
            {"change": "Two-step verification was turned off"},
            f"account:{user_id}:two-step:off:{secrets.token_hex(8)}",
        )
    return int(ended)


def recovery_code_spent_at_sign_in(
    user: Any, *, ip_prefix: str, device_label: str
) -> None:
    """A sign-in used a recovery code: on the trail, and the address is told
    (someone holding the codes can sign in)."""
    from workeasy360.platform.identity.models import AccountEvent

    account.record_account_event(
        user.id,
        AccountEvent.Kind.RECOVERY_CODE_USED,
        ip_prefix=ip_prefix,
        device_label=device_label,
    )
    _notify(
        user,
        TWO_STEP_CHANGED_TEMPLATE,
        {"change": "A recovery code was used to sign in"},
        f"account:{user.id}:two-step:recovery-used:{secrets.token_hex(8)}",
    )


#: After this many wrong sign-in codes in an hour, the person is told.
WRONG_CODES_NOTICE_AT = 5


def two_step_code_refused(
    user: Any, *, ip_prefix: str, device_label: str, at_sign_in: bool = True
) -> None:
    """A wrong two-step code: at the code step of a sign-in (someone has the
    password), or on an account change (``at_sign_in=False``: replacing the
    authenticator, new recovery codes, turning it off - someone has the
    password and a live session). A sign-in's is on the person's trail every
    time; both count toward one hourly notice, mailed from the fifth."""
    from django.core.cache import cache

    from workeasy360.platform.identity.models import AccountEvent

    try:
        if at_sign_in:
            account.record_account_event(
                user.id,
                AccountEvent.Kind.SIGN_IN_FAILED,
                reason="two_step_code_invalid",
                ip_prefix=ip_prefix,
                device_label=device_label,
            )
        hour = timezone.now().strftime("%Y%m%d%H")
        key = f"w360:two-step-wrong:{user.id}:{hour}"
        cache.add(key, 0, timeout=3600)
        count = cache.incr(key)
        if count == WRONG_CODES_NOTICE_AT:
            _notify(
                user,
                TWO_STEP_CHANGED_TEMPLATE,
                {
                    "change": "Several wrong two-step codes were entered after your "
                    "correct password - someone may know your password"
                },
                f"account:{user.id}:two-step:wrong-codes:{hour}",
            )
    except Exception:  # recording never changes the answer
        logger.exception("identity.two_step_refusal_record_failed", user_id=str(user.id))


# --- password changed while signed in ---------------------------------------------


def password_changed_notice(user: Any, *, device_label: str) -> None:
    """After a signed-in password change: tell the person's verified address, as
    a reset is told (Accounts V2/V3 8.2 - 2026-11-27 alignment audit). A security
    notice, so no preference turns it off. Never fails the change, which has
    already happened."""
    try:
        _notify(
            user,
            PASSWORD_CHANGED_SIGNED_IN_TEMPLATE,
            {"device": device_label or "an unknown device"},
            f"account:{user.id}:password-changed:{secrets.token_hex(8)}",
        )
    except Exception:  # the change stands whether or not the mail goes
        logger.exception("identity.password_change_notice_failed", user_id=str(user.id))


# --- new sign-in alerts -----------------------------------------------------------


def alert_if_new_device(user: Any, *, ip_prefix: str, device_label: str) -> bool:
    """After a successful password sign-in: mail the person when this device
    and network have not signed in to the account in ``NEW_DEVICE_LOOKBACK``
    - unless they turned these alerts off, or this is the account's first
    sign-in. Never fails the sign-in."""
    from workeasy360.platform.identity import preferences
    from workeasy360.platform.identity.models import AccountEvent

    try:
        if not preferences.enabled(user.id, preferences.NEW_SIGN_IN_ALERTS):
            return False
        since = timezone.now() - NEW_DEVICE_LOOKBACK
        earlier = AccountEvent.objects.filter(
            user_id=user.id, kind=AccountEvent.Kind.SIGN_IN
        ).order_by("-occurred_at")
        # The newest is the sign-in just recorded.
        previous = list(
            earlier.values_list("ip_prefix", "device_label", "occurred_at")[1:200]
        )
        if not previous:
            return False
        if any(
            p == ip_prefix and d == device_label and at >= since for p, d, at in previous
        ):
            return False
        _notify(
            user,
            NEW_SIGN_IN_TEMPLATE,
            {
                "device": device_label or "an unknown device",
                "network": ip_prefix or "unknown",
            },
            f"account:{user.id}:new-sign-in:{secrets.token_hex(8)}",
        )
        metrics.increment("identity_new_sign_in_alerts_total")
        return True
    except Exception:  # an alert never blocks a sign-in
        logger.exception("identity.new_sign_in_alert_failed", user_id=str(user.id))
        return False


# --- account closure ----------------------------------------------------------------


def _holds_staff_grants(user_id: Any) -> bool:
    """Whether the person holds any live control-plane grant: such an account
    is closed by a security administrator, never by itself."""
    from workeasy360.platform.authz.staff import staff_permissions

    return bool(staff_permissions(user_id))


#: ``(user_id, *, lock) -> [{"name": ...}, ...]``: the partners this person is
#: the last working administrator of. Partner administration lives in
#: ``commercial``, which ``platform`` may not import (F-4), so it registers
#: its answer here from ``ready()`` (ADR-075 review M-1).
PartnerAdminCheck = Callable[..., list[dict[str, Any]]]
_PARTNER_ADMIN_CHECKS: list[PartnerAdminCheck] = []


def register_partner_admin_check(check: PartnerAdminCheck) -> None:
    """Called from an app's ``ready()``; registering twice is a no-op."""
    if check not in _PARTNER_ADMIN_CHECKS:
        _PARTNER_ADMIN_CHECKS.append(check)


def _partners_left_without_admin(
    user_id: Any, *, lock: bool = False
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for check in list(_PARTNER_ADMIN_CHECKS):
        found.extend(check(user_id, lock=lock))
    return [{"name": p["name"]} for p in found]


def _blocking_organizations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"name": o["name"], "slug": o["slug"], "pending": bool(o.get("pending"))}
        for o in rows
    ]


def closure_check(user_id: Any) -> dict[str, Any]:
    from workeasy360.platform.organizations.selectors import sole_owned_organizations

    blocking = sole_owned_organizations(user_id)
    partners = _partners_left_without_admin(user_id)
    staff = _holds_staff_grants(user_id)
    return {
        "can_close": not blocking and not partners and not staff,
        "blocking_organizations": _blocking_organizations(blocking),
        "blocking_partners": partners,
        "staff_account": staff,
        "two_step_required": two_step.is_enabled(user_id),
    }


def close_account(
    user_id: Any,
    *,
    password: Any,
    code: Any = None,
    confirmation: Any,
    ip_prefix: str = "",
    device_label: str = "",
) -> None:
    """Close the person's own account (Accounts V3 10.2).

    - Reconfirmed: the password, the two-step code when it is on, and the
      typed word.
    - Never orphans an organization: an only owner is refused, with their
      organizations named, until ownership moves or the organization closes
      (those are User Admin's and support's, never a side effect here). An
      owner still INVITED counts: they are the organization's only owner.
    - Never locks a partner out: the last working administrator of an active
      partner is refused, with the partners named, until another administrator
      is added (the same rule ``partners.remove_member`` keeps).
    - Closing is not leaving: memberships, subscriptions and every
      organization-owned record stay; the account is disabled, so it opens
      nothing. Records are kept under the existing retention policy
      (``ACCOUNT_EVENT_RETENTION_DAYS``); reopening is a support request.
    - Every session ends now, in WorkEasy360 and, by back-channel logout, in
      the products.
    """
    from workeasy360.platform.identity import lifecycle
    from workeasy360.platform.identity.models import AccountEvent
    from workeasy360.platform.organizations.selectors import sole_owned_organizations

    if confirmation != CLOSURE_CONFIRMATION:
        raise MembershipValidationError(
            f"Type {CLOSURE_CONFIRMATION} to confirm.", field="confirmation"
        )
    # Outside the transaction, so a wrong password stays counted (lockout).
    two_step.check_password_counted(user_id, password)
    with transaction.atomic():
        user = lifecycle.lock(user_id)
        if user.status != UserStatus.ACTIVE.value:
            raise MembershipValidationError(
                "This account cannot be closed.", field="body"
            )
        if _holds_staff_grants(user_id):
            raise MembershipValidationError(
                "A staff account is closed by a security administrator.", field="body"
            )
        if two_step.is_enabled(user_id) and two_step.verify_code(user_id, code) is None:
            raise TwoStepCodeInvalid()
        blocking = sole_owned_organizations(user_id, lock=True)
        if blocking:
            raise SoleOwnerBlocksClosure(
                "You are the only owner of an organization. Make someone else its "
                "owner or close the organization first.",
                blocking_organizations=_blocking_organizations(blocking),
            )
        partners = _partners_left_without_admin(user_id, lock=True)
        if partners:
            raise LastPartnerAdminBlocksClosure(
                "You are the last administrator of a partner. Add another "
                "administrator in the Partner portal first.",
                blocking_partners=partners,
            )
        lifecycle.close(user)
        account.record_account_event(
            user_id,
            AccountEvent.Kind.ACCOUNT_CLOSED,
            ip_prefix=ip_prefix,
            device_label=device_label,
        )
        _notify(user, ACCOUNT_CLOSED_TEMPLATE, {}, f"account:{user_id}:closed")
    account.sign_out_everywhere(user_id)
    metrics.increment("identity_accounts_closed_total")
    logger.info("identity.account.closed", user_id=str(user_id))


# --- central account-security support ------------------------------------------------


def _reference() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # pragma: allowlist secret - an alphabet, not a secret
    return "AS-" + "".join(secrets.choice(alphabet) for _ in range(8))


def open_support_case(*, email: Any, name: Any, topic: Any, message: Any) -> str:
    """A request to central account support (Accounts V3 10.3). One answer
    whether or not the address has an account; the receipt goes to the
    address given. Not routed to any partner or organization."""
    from workeasy360.contracts.errors import SignupValidationError
    from workeasy360.platform.administration.self_service import _clean_email
    from workeasy360.platform.identity.models import AccountSupportCase, User

    try:
        typed, address = _clean_email(email)
    except SignupValidationError as exc:
        raise MembershipValidationError(exc.message, field="email") from exc
    if topic not in AccountSupportCase.Topic.values:
        raise MembershipValidationError(
            "Choose what the request is about.", field="topic"
        )
    text = str(message or "").strip() if isinstance(message, str) else ""
    if not text or len(text) > 4000:
        raise MembershipValidationError(
            "Describe the problem in at most 4000 characters.", field="message"
        )
    who = " ".join(str(name or "").split())[:201] if isinstance(name, str) else ""
    matched = User.objects.filter(email=address).values_list("id", flat=True).first()
    reference = _reference()
    with transaction.atomic():
        AccountSupportCase.objects.create(
            reference=reference,
            email=typed,
            name=who,
            topic=topic,
            message=text,
            matched_user_id=matched,
        )
        from workeasy360.platform.administration.site import support_email
        from workeasy360.platform.notifications.services import notify_account

        notify_account(
            template_key=SUPPORT_RECEIVED_TEMPLATE,
            recipient_email=typed,
            context={"reference": reference, "support_email": support_email()},
            idempotency_key=f"account-support:{reference}",
        )
    metrics.increment("identity_account_support_cases_total", topic=str(topic))
    logger.info("identity.account_support.opened", reference=reference, topic=topic)
    return reference


def support_cases(*, status: str | None, limit: int = 100) -> list[dict[str, Any]]:
    from workeasy360.platform.identity.models import AccountSupportCase

    rows = AccountSupportCase.objects.order_by("-created_at")
    if status in AccountSupportCase.Status.values:
        rows = rows.filter(status=status)
    return [_case_json(c) for c in rows[: max(1, min(limit, 200))]]


def _case_json(case: Any) -> dict[str, Any]:
    return {
        "id": str(case.id),
        "reference": case.reference,
        "email": case.email,
        "name": case.name,
        "topic": case.topic,
        "message": case.message,
        "status": case.status,
        "has_account": case.matched_user_id is not None,
        "created_at": case.created_at.isoformat(),
        "resolved_at": case.resolved_at.isoformat() if case.resolved_at else None,
        "note": case.note,
    }


def update_support_case(
    case_id: UUID, *, status: Any, note: Any, staff_user_id: Any
) -> dict[str, Any]:
    from workeasy360.platform.identity.models import AccountSupportCase

    if status not in AccountSupportCase.Status.values:
        raise MembershipValidationError("Choose open or resolved.", field="status")
    if note is not None and (not isinstance(note, str) or len(note) > 4000):
        raise MembershipValidationError(
            "A note is at most 4000 characters.", field="note"
        )
    with transaction.atomic():
        case = AccountSupportCase.objects.select_for_update().get(pk=case_id)
        case.status = status
        if note is not None:
            case.note = note
        if status == AccountSupportCase.Status.RESOLVED:
            case.resolved_at = case.resolved_at or timezone.now()
            case.resolved_by_id = staff_user_id
        else:
            case.resolved_at = None
            case.resolved_by_id = None
        case.save()
    return _case_json(case)

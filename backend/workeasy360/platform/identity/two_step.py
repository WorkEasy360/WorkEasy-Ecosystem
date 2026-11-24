"""Personal two-step verification: an authenticator app (TOTP) and recovery
codes (ADR-075, Accounts V3 sections 04 2.3 and 08.3).

**Scope, and what stays removed.** ADR-050 removed every second factor from
the platform. ADR-075 brings back exactly one thing: a person may turn on
two-step verification for their own account on WorkEasy360 Accounts, and then
every password sign-in of that account (customer, Accounts, product sign-in,
Admin, Partner) asks for a code before a session exists. Nothing else returns:
no organization can require it, nothing steps up to it, no token carries an
assurance level, and the factors enrolled before ADR-050 stay dormant in
their own table (a different table is used, so none of them comes back).

**Primitives.** RFC 4226/6238 HOTP/TOTP over the standard library (the code
ADR-050 removed, checked against the RFC's own test vectors in the tests).
The secret is stored as Fernet ciphertext under ``settings.TWO_STEP_KEY``;
recovery codes as an HMAC-SHA256 keyed with it. Without the key the feature is
unavailable: setup answers ``two_step_unavailable``, and a sign-in of an
account that has two-step on fails closed rather than skipping the code.

**State changes** are here; the e-mail notices and the HTTP adapters that call
them live above (``administration.account_security``, ``account_views``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
import urllib.parse
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from workeasy360.contracts.errors import (
    CurrentPasswordIncorrect,
    TwoStepChallengeExpired,
    TwoStepCodeInvalid,
    TwoStepStateConflict,
    TwoStepUnavailable,
)
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

#: 160 bits: what RFC 4226's reference implementation and every authenticator
#: app assume.
SECRET_BYTES = 20
STEP_SECONDS = 30
DIGITS = 6
#: One step either side of now, for clock drift. Wider would weaken a
#: six-digit code against a fast online guesser.
WINDOW = 1
RECOVERY_CODE_COUNT = 10
#: How long a setup in progress can be confirmed.
SETUP_TTL = timedelta(minutes=10)
#: How long the code step of a sign-in stays open, and how many tries it allows.
CHALLENGE_TTL = timedelta(minutes=5)
CHALLENGE_MAX_ATTEMPTS = 5
ISSUER_LABEL = "WorkEasy360"


# --- primitives ------------------------------------------------------------------


def available() -> bool:
    """Whether this deployment can protect a second factor at all."""
    return bool(getattr(settings, "TWO_STEP_KEY", b""))


def _key() -> bytes:
    key = getattr(settings, "TWO_STEP_KEY", b"")
    if not key:
        raise TwoStepUnavailable()
    return key if isinstance(key, bytes) else str(key).encode("ascii")


def _fernet() -> Any:
    from cryptography.fernet import Fernet

    return Fernet(_key())


def encrypt_secret(raw: bytes) -> str:
    return _fernet().encrypt(raw).decode("ascii")


def decrypt_secret(token: str) -> bytes:
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(token.encode("ascii"))
    except InvalidToken as exc:
        # Only when the key changed under stored secrets; never user input.
        raise TwoStepUnavailable() from exc


def base32_secret(raw: bytes) -> str:
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def provisioning_uri(secret: bytes, account_email: str) -> str:
    """The ``otpauth://`` URI an authenticator app scans. Shown once, during
    setup; never logged or stored."""
    label = urllib.parse.quote(f"{ISSUER_LABEL}:{account_email}")
    params = urllib.parse.urlencode(
        {
            "secret": base32_secret(secret),
            "issuer": ISSUER_LABEL,
            "algorithm": "SHA1",
            "digits": DIGITS,
            "period": STEP_SECONDS,
        }
    )
    return f"otpauth://totp/{label}?{params}"


def qr_svg_data_uri(text: str) -> str:
    """The URI as a QR code, an SVG ``data:`` URI for an ``<img>``."""
    import segno

    svg = segno.make(text, error="m").svg_inline(scale=4, border=2)
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode(
        "ascii"
    )


def hotp(secret: bytes, counter: int) -> str:
    """RFC 4226 HOTP."""
    digest = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return str(truncated % (10**DIGITS)).zfill(DIGITS)


def totp_now(secret: bytes, *, at: float | None = None) -> str:
    """The code valid at ``at`` (tests and the setup check only)."""
    return hotp(secret, int((at if at is not None else time.time()) // STEP_SECONDS))


def match_step(secret: bytes, code: str, *, at: float | None = None) -> int | None:
    """The time step ``code`` is valid for, or ``None``. Constant-time against
    each step in the window. The caller refuses a step it already accepted."""
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS:
        return None
    counter = int((at if at is not None else time.time()) // STEP_SECONDS)
    found = None
    for step in range(counter - WINDOW, counter + WINDOW + 1):
        if hmac.compare_digest(hotp(secret, step), code):
            found = step
    return found


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """Plaintext codes, returned to the person exactly once. 40 bits each, and
    distinct within the set (one person's codes are unique together)."""
    raw: dict[str, None] = {}
    while len(raw) < count:
        raw[secrets.token_hex(5)] = None
    return [f"{code[:5]}-{code[5:]}" for code in raw]


def normalize_recovery_code(code: str) -> str:
    return "".join((code or "").split()).lower()


def hash_recovery_code(code: str) -> str:
    """Keyed, so a database dump alone cannot brute-force 40-bit codes."""
    key = b"w360-two-step-recovery:" + _key()
    return hmac.new(
        key, normalize_recovery_code(code).encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _looks_like_recovery_code(code: str) -> bool:
    value = normalize_recovery_code(code)
    return len(value) == 11 and value[5] == "-"


# --- reading ---------------------------------------------------------------------


def _confirmed(user_id: Any, *, lock: bool = False) -> Any:
    from workeasy360.platform.identity.models import TwoStepMethod

    rows = TwoStepMethod.objects.filter(user_id=user_id, confirmed_at__isnull=False)
    if lock:
        rows = rows.select_for_update()
    return rows.first()


def is_enabled(user_id: Any) -> bool:
    from workeasy360.platform.identity.models import TwoStepMethod

    return TwoStepMethod.objects.filter(
        user_id=user_id, confirmed_at__isnull=False
    ).exists()


def status(user_id: Any) -> dict[str, Any]:
    from workeasy360.platform.identity.models import TwoStepRecoveryCode

    method = _confirmed(user_id)
    remaining = 0
    if method is not None:
        remaining = TwoStepRecoveryCode.objects.filter(
            user_id=user_id, used_at__isnull=True
        ).count()
    return {
        "available": available(),
        "enabled": method is not None,
        "method": "totp" if method is not None else None,
        "enabled_at": method.confirmed_at.isoformat() if method is not None else None,
        "recovery_codes_remaining": remaining,
    }


# --- verifying a code ------------------------------------------------------------


def verify_code(user_id: Any, code: Any, *, allow_recovery: bool = True) -> str | None:
    """Spend ``code`` for this person: their authenticator's current code (each
    time step once) or one unused recovery code. Returns ``"totp"`` or
    ``"recovery_code"``, or ``None`` when it is not valid. Must run inside the
    caller's transaction; the rows are locked so two requests cannot both
    spend one code."""
    from workeasy360.platform.identity.models import TwoStepRecoveryCode

    text = str(code or "") if isinstance(code, str | int) else ""
    method = _confirmed(user_id, lock=True)
    if method is None:
        return None
    if allow_recovery and _looks_like_recovery_code(text):
        row = (
            TwoStepRecoveryCode.objects.select_for_update()
            .filter(
                user_id=user_id, code_hash=hash_recovery_code(text), used_at__isnull=True
            )
            .first()
        )
        if row is None:
            return None
        row.used_at = timezone.now()
        row.save(update_fields=["used_at"])
        return "recovery_code"
    step = match_step(decrypt_secret(method.secret_ciphertext), text)
    if step is None or step <= method.last_used_step:
        return None
    method.last_used_step = step
    method.save(update_fields=["last_used_step"])
    return "totp"


def check_password_counted(user_id: Any, password: Any) -> None:
    """Reauthentication for a security action (Accounts V3 8.3).

    A wrong password counts toward the account lockout exactly as on the
    sign-in form, and a locked account is refused without checking. It must be
    called **outside** any transaction: the count is written in autocommit, so
    the refusal that follows cannot roll it back (``change_password`` and
    ``verify_email`` do the same)."""
    from workeasy360.platform.identity.models import User
    from workeasy360.platform.identity.services import _record_failure

    user = User.objects.get(pk=user_id)
    if user.locked_until is not None and user.locked_until > timezone.now():
        raise CurrentPasswordIncorrect("The password is not correct.", field="password")
    if not isinstance(password, str) or not user.check_password(password):
        _record_failure(user)
        raise CurrentPasswordIncorrect("The password is not correct.", field="password")


def _check_password_locked(user: Any, password: Any) -> None:
    """Inside a transaction, for a caller that did not check first: refuses a
    wrong password (not counted - use :func:`check_password_counted` first)."""
    if not isinstance(password, str) or not user.check_password(password):
        raise CurrentPasswordIncorrect("The password is not correct.", field="password")


def _require_code(user_id: Any, code: Any) -> str:
    used = verify_code(user_id, code)
    if used is None:
        metrics.increment("identity_two_step_code_failures_total", stage="account")
        raise TwoStepCodeInvalid()
    return used


# --- setup, replacement, recovery codes, turning off ---------------------------------


@dataclass(frozen=True, slots=True)
class Setup:
    secret: str
    otpauth_uri: str
    qr_svg_data_uri: str
    expires_at: Any


def begin_setup(user_id: Any, *, password: Any, code: Any = None) -> Setup:
    """Start setting up (or replacing) the authenticator. Nothing changes for
    sign-in until ``confirm_setup``. Replacing needs a current code or a
    recovery code as well as the password."""
    from workeasy360.platform.identity.models import TwoStepMethod, User

    if not available():
        raise TwoStepUnavailable()
    check_password_counted(user_id, password)
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user_id)
        if _confirmed(user_id) is not None:
            _require_code(user_id, code)
        TwoStepMethod.objects.filter(user_id=user_id, confirmed_at__isnull=True).delete()
        raw = os.urandom(SECRET_BYTES)
        expires_at = timezone.now() + SETUP_TTL
        TwoStepMethod.objects.create(
            user_id=user_id, secret_ciphertext=encrypt_secret(raw), expires_at=expires_at
        )
    uri = provisioning_uri(raw, user.email_original or user.email)
    logger.info("identity.two_step.setup_started", user_id=str(user_id))
    return Setup(
        secret=base32_secret(raw),
        otpauth_uri=uri,
        qr_svg_data_uri=qr_svg_data_uri(uri),
        expires_at=expires_at,
    )


@dataclass(frozen=True, slots=True)
class Confirmed:
    recovery_codes: list[str]
    replaced: bool


def _replace_recovery_codes(user_id: Any) -> list[str]:
    from workeasy360.platform.identity.models import TwoStepRecoveryCode

    TwoStepRecoveryCode.objects.filter(user_id=user_id).delete()
    codes = generate_recovery_codes()
    TwoStepRecoveryCode.objects.bulk_create(
        [
            TwoStepRecoveryCode(user_id=user_id, code_hash=hash_recovery_code(c))
            for c in codes
        ]
    )
    return codes


def confirm_setup(user_id: Any, *, code: Any) -> Confirmed:
    """A code from the new authenticator turns it on (or makes it the one that
    replaces the old). New recovery codes are made; the old ones stop
    working. The caller ends the person's other sessions and sends the
    notice, in this transaction."""
    from workeasy360.platform.identity.models import TwoStepMethod, User

    if not available():
        raise TwoStepUnavailable()
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user_id)
        pending = (
            TwoStepMethod.objects.select_for_update()
            .filter(user_id=user_id, confirmed_at__isnull=True)
            .first()
        )
        now = timezone.now()
        if pending is None or (
            pending.expires_at is not None and pending.expires_at <= now
        ):
            raise TwoStepStateConflict(
                "Start the setup again: it was not found or has expired.",
                code="two_step_setup_missing",
            )
        text = str(code or "") if isinstance(code, str | int) else ""
        step = match_step(decrypt_secret(pending.secret_ciphertext), text)
        if step is None:
            metrics.increment("identity_two_step_code_failures_total", stage="setup")
            raise TwoStepCodeInvalid()
        previous = _confirmed(user_id, lock=True)
        replaced = previous is not None
        if replaced:
            previous.delete()
        pending.confirmed_at = now
        pending.expires_at = None
        pending.last_used_step = step
        pending.save(update_fields=["confirmed_at", "expires_at", "last_used_step"])
        codes = _replace_recovery_codes(user_id)
    metrics.increment("identity_two_step_enabled_total", replaced=str(replaced).lower())
    logger.info("identity.two_step.confirmed", user_id=str(user_id), replaced=replaced)
    return Confirmed(recovery_codes=codes, replaced=replaced)


def regenerate_recovery_codes(user_id: Any, *, password: Any, code: Any) -> list[str]:
    """New recovery codes. The caller has run :func:`check_password_counted`
    (outside its transaction) when ``password`` is ``None``."""
    from workeasy360.platform.identity.models import User

    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user_id)
        if _confirmed(user_id) is None:
            raise TwoStepStateConflict()
        if password is not None:
            _check_password_locked(user, password)
        _require_code(user_id, code)
        codes = _replace_recovery_codes(user_id)
    logger.info("identity.two_step.recovery_codes_regenerated", user_id=str(user_id))
    return codes


def disable(user_id: Any, *, password: Any, code: Any) -> None:
    """Turn two-step off: the password and a current code (or a recovery
    code). The password and the verified email stay as the way in, so this
    never leaves the account without a sign-in or recovery route."""
    from workeasy360.platform.identity.models import (
        TwoStepMethod,
        TwoStepRecoveryCode,
        User,
    )

    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user_id)
        if _confirmed(user_id) is None:
            raise TwoStepStateConflict()
        if password is not None:
            _check_password_locked(user, password)
        _require_code(user_id, code)
        TwoStepMethod.objects.filter(user_id=user_id).delete()
        TwoStepRecoveryCode.objects.filter(user_id=user_id).delete()
    metrics.increment("identity_two_step_disabled_total")
    logger.info("identity.two_step.disabled", user_id=str(user_id))


# --- the code step of a sign-in ----------------------------------------------------


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def open_challenge(user: Any, *, auth_state: dict[str, Any]) -> str:
    """After a correct password, for an account with two-step on: the opaque
    challenge the browser sends back with the code. No session exists yet."""
    from workeasy360.platform.identity.models import TwoStepChallenge

    if not available():
        # Fail closed: an account that turned two-step on is never signed in
        # by its password alone because the key went missing.
        raise TwoStepUnavailable()
    token = secrets.token_urlsafe(32)
    TwoStepChallenge.objects.create(
        token_hash=_digest(token),
        user=user,
        auth_state=auth_state,
        session_epoch=user.session_epoch,
        expires_at=timezone.now() + CHALLENGE_TTL,
    )
    return token


@dataclass(frozen=True, slots=True)
class Passed:
    user: Any
    auth_state: dict[str, Any]
    used: str


def challenge_user(token: Any) -> Any:
    """The person a live challenge belongs to, or ``None`` (for throttling)."""
    from workeasy360.platform.identity.models import TwoStepChallenge

    if not isinstance(token, str) or not token or len(token) > 128:
        return None
    row = (
        TwoStepChallenge.objects.filter(
            token_hash=_digest(token),
            consumed_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .select_related("user")
        .first()
    )
    return row.user if row is not None else None


def pass_challenge(token: Any, code: Any) -> Passed:
    """Spend the challenge with a valid code. A wrong code costs an attempt;
    the last allowed wrong one ends the challenge. Expired, spent, or voided by
    a password reset / "sign out everywhere" since: start again."""
    from workeasy360.contracts.enums import UserStatus
    from workeasy360.platform.identity.models import TwoStepChallenge

    if not isinstance(token, str) or not token or len(token) > 128:
        raise TwoStepChallengeExpired()
    with transaction.atomic():
        row = (
            TwoStepChallenge.objects.select_for_update()
            .select_related("user")
            .filter(token_hash=_digest(token))
            .first()
        )
        now = timezone.now()
        if (
            row is None
            or row.consumed_at is not None
            or row.expires_at <= now
            or row.attempts >= CHALLENGE_MAX_ATTEMPTS
            or row.user.status != UserStatus.ACTIVE.value
            or row.user.session_epoch != row.session_epoch
        ):
            raise TwoStepChallengeExpired()
        used = verify_code(row.user_id, code)
        if used is None:
            row.attempts += 1
            if row.attempts >= CHALLENGE_MAX_ATTEMPTS:
                row.consumed_at = now
            row.save(update_fields=["attempts", "consumed_at"])
            metrics.increment("identity_two_step_code_failures_total", stage="sign_in")
            # Refused after the commit, so the attempt is counted.
            failed = True
        else:
            row.consumed_at = now
            row.save(update_fields=["consumed_at"])
            failed = False
    if failed:
        raise TwoStepCodeInvalid()
    metrics.increment("identity_two_step_sign_ins_total", method=used)
    return Passed(user=row.user, auth_state=dict(row.auth_state or {}), used=used)


def prune_challenges(*, older_than: timedelta = timedelta(days=1)) -> int:
    from workeasy360.platform.identity.models import TwoStepChallenge, TwoStepMethod

    cutoff = timezone.now() - older_than
    gone, _ = TwoStepChallenge.objects.filter(expires_at__lt=cutoff).delete()
    TwoStepMethod.objects.filter(
        confirmed_at__isnull=True, expires_at__lt=cutoff
    ).delete()
    return int(gone)

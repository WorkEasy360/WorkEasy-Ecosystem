"""TOTP and recovery codes: the minimum standards-based MFA implementation
needed to make the platform's existing AAL2 enforcement usable (Phase 5,
Task 37).

**Why TOTP and not WebAuthn/passkeys, even though ``AuthFactor`` already
models both.** TOTP alone satisfies the actual trigger - AAL2 enforcement
existing with no way to reach it - with a stdlib-only implementation of a
published standard (RFC 4226/6238: HOTP/TOTP, HMAC-SHA1). WebAuthn needs a
relying-party ceremony, attestation handling and a browser API surface that
is real scope beyond this phase's brief ("implement only the minimum...
necessary"); the model support is left in place, unused, for whenever that
scope is taken on deliberately.

**Why no dependency was added.** TOTP is ~25 lines of HMAC over stdlib
primitives (``hmac``, ``hashlib``, ``struct``) - adding ``pyotp`` for that
would be exactly the "unnecessary abstraction" the lean-code requirement
warns against. This is not "custom cryptography": every primitive
(HMAC-SHA1, the RFC's byte-truncation step) is the published algorithm,
verified against RFC 6238's own test vectors in
``platform/identity/tests/test_mfa.py``.

**Secret storage.** ``AuthFactor.secret_ref`` follows the same rule
12-security.md section 6 states for MFA secrets and
``contracts/ports/signing.py`` already applies to the platform's own signing
key: the database stores a reference, not the raw secret. Here that
reference is a Fernet (AES-128-CBC + HMAC, authenticated, from the
``cryptography`` package this codebase already depends on for the signing
key) ciphertext of the raw TOTP secret, keyed by ``settings.MFA_ENCRYPTION_KEY``
- narrower than a full external-KMS reference like the signing key's (real
scope this phase's trigger does not require), but never plaintext at rest.
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

#: 160-bit secret - the size RFC 4226's own reference implementation uses,
#: and what every authenticator app (Google/Microsoft Authenticator, 1Password,
#: Authy) assumes when scanning a QR code.
SECRET_BYTES = 20
TOTP_STEP_SECONDS = 30
TOTP_DIGITS = 6
#: Accept one step either side of "now" - clock drift tolerance every real
#: TOTP implementation needs; wider would meaningfully weaken the six-digit
#: code's effective entropy against a fast online guesser.
TOTP_WINDOW = 1

RECOVERY_CODE_COUNT = 10


def generate_totp_secret() -> bytes:
    return os.urandom(SECRET_BYTES)


def base32_secret(raw: bytes) -> str:
    """The form an authenticator app's manual-entry field expects."""
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def provisioning_uri(
    *, secret: bytes, account_email: str, issuer: str = "Workeasy360"
) -> str:
    """An ``otpauth://`` URI, rendered as a QR code by the frontend.

    Never logged, never persisted - it embeds the raw secret and is shown to
    the user exactly once, during enrollment.
    """
    label = urllib.parse.quote(f"{issuer}:{account_email}")
    params = urllib.parse.urlencode(
        {
            "secret": base32_secret(secret),
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": TOTP_DIGITS,
            "period": TOTP_STEP_SECONDS,
        }
    )
    return f"otpauth://totp/{label}?{params}"


def _hotp(secret: bytes, counter: int) -> str:
    """RFC 4226 HOTP(secret, counter). The primitive TOTP is built from."""
    msg = struct.pack(">Q", counter)
    digest = hmac.new(secret, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return str(truncated % (10**TOTP_DIGITS)).zfill(TOTP_DIGITS)


def current_totp(secret: bytes, *, at: float | None = None) -> str:
    """The code valid right now (or at ``at``, for tests). Not exposed to
    any HTTP caller - test and provisioning-verification use only."""
    counter = int((at if at is not None else time.time()) // TOTP_STEP_SECONDS)
    return _hotp(secret, counter)


def match_totp_step(secret: bytes, code: str, *, at: float | None = None) -> int | None:
    """The time step ``code`` is valid for, or ``None``.

    Constant-time compare against the current code and ``TOTP_WINDOW``
    steps either side, so a code accepted just before/after a step boundary
    is not treated as an attacker guessing correctly by pure luck.

    Returns the *step*, not a bool, because a match alone is not enough:
    RFC 6238 section 5.2 forbids accepting the same OTP twice, so the caller
    records the step and refuses any code at or before it
    (``identity.services``, ``AuthFactor.sign_count``).
    """
    code = (code or "").strip()
    if not code.isdigit() or len(code) != TOTP_DIGITS:
        return None
    now = at if at is not None else time.time()
    counter = int(now // TOTP_STEP_SECONDS)
    for step in range(counter - TOTP_WINDOW, counter + TOTP_WINDOW + 1):
        if hmac.compare_digest(_hotp(secret, step), code):
            return step
    return None


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """Plaintext codes, returned to the caller **exactly once** - the caller
    (``identity.services.confirm_totp_enrollment``) stores only their hash.

    40 bits each (``xxxxx-xxxxx``): with the step-up throttle an online
    guesser gets nowhere, and the keyed hash below is what protects them
    offline."""
    codes = (secrets.token_hex(5) for _ in range(count))
    return [f"{code[:5]}-{code[5:]}" for code in codes]


def hash_recovery_code(code: str) -> str:
    """HMAC-SHA256 keyed with ``MFA_ENCRYPTION_KEY``, not a bare hash.

    A code verifies by comparison and never needs to be recovered, so it is
    stored one-way. It is *keyed* because a short code's plain SHA-256 is
    brute-forced offline in seconds by anyone holding a database dump; with
    the key held outside the database, the dump alone is not enough. Still
    deterministic, so a code is looked up by its hash in one indexed query.
    """
    from django.conf import settings

    normalized = code.strip().lower().encode("utf-8")
    key = b"w360-recovery-code:" + settings.MFA_ENCRYPTION_KEY
    return hmac.new(key, normalized, hashlib.sha256).hexdigest()


def _fernet():
    from cryptography.fernet import Fernet
    from django.conf import settings

    return Fernet(settings.MFA_ENCRYPTION_KEY)


def encrypt_totp_secret(raw: bytes) -> str:
    """The value stored in ``AuthFactor.secret_ref``."""
    return _fernet().encrypt(raw).decode("ascii")


def decrypt_totp_secret(token: str) -> bytes:
    from cryptography.fernet import InvalidToken

    from workeasy360.contracts.errors import MfaError

    try:
        return _fernet().decrypt(token.encode("ascii"))
    except InvalidToken as exc:
        # Only reachable if MFA_ENCRYPTION_KEY was rotated without migrating
        # existing factors, or the stored value was corrupted - never from
        # user input (the ciphertext is never client-supplied).
        raise MfaError("Could not decrypt the stored authentication factor.") from exc

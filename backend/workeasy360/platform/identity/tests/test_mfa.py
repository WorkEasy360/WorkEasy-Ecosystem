"""TOTP (RFC 6238), recovery codes, and AAL propagation (Phase 5, Task 37-39)."""

from __future__ import annotations

import time

import pytest

from workeasy360.platform.identity import mfa

pytestmark = pytest.mark.django_db


def test_totp_matches_rfc_6238_test_vector():
    """RFC 6238 Appendix B, SHA1, 8-digit truncated to this module's 6.
    Seed ``"12345678901234567890"`` at T=59 (counter=1) produces HOTP
    94287082 (RFC 4226/6238's own published vector); the low 6 digits of
    that value are what a 6-digit TOTP implementation must also produce,
    since the truncation algorithm is identical and only the digit count
    at the final modulo differs.
    """
    secret = b"12345678901234567890"
    code = mfa.current_totp(secret, at=59)
    assert code == "287082"


def test_totp_match_returns_the_step_and_rejects_a_wrong_code():
    secret = mfa.generate_totp_secret()
    now = 1_700_000_000.0
    code = mfa.current_totp(secret, at=now)
    assert mfa.match_totp_step(secret, code, at=now) == int(now // 30)
    wrong = "000000" if code != "000000" else "111111"
    assert mfa.match_totp_step(secret, wrong, at=now) is None


def test_totp_window_is_exactly_one_step_either_side():
    """RFC 6238 section 5.2: the window is deliberate and bounded."""
    secret = mfa.generate_totp_secret()
    now = 1_700_000_000.0
    step = mfa.TOTP_STEP_SECONDS
    for offset in (-1, 0, 1):
        code = mfa.current_totp(secret, at=now + offset * step)
        assert mfa.match_totp_step(secret, code, at=now) is not None
    for offset in (-2, 2):
        code = mfa.current_totp(secret, at=now + offset * step)
        if code not in {mfa.current_totp(secret, at=now + d * step) for d in (-1, 0, 1)}:
            assert mfa.match_totp_step(secret, code, at=now) is None


def test_totp_rejects_malformed_input():
    secret = mfa.generate_totp_secret()
    assert mfa.match_totp_step(secret, "") is None
    assert mfa.match_totp_step(secret, "abcdef") is None
    assert mfa.match_totp_step(secret, "12345") is None  # too short
    assert mfa.match_totp_step(secret, "1234567") is None  # too long


def test_secret_round_trips_through_encryption():
    raw = mfa.generate_totp_secret()
    ref = mfa.encrypt_totp_secret(raw)
    assert ref != raw  # never stored in plaintext
    assert mfa.decrypt_totp_secret(ref) == raw


def test_recovery_codes_are_unique_and_hash_is_one_way():
    codes = mfa.generate_recovery_codes()
    assert len(codes) == len(set(codes)) == mfa.RECOVERY_CODE_COUNT
    hashes = {mfa.hash_recovery_code(c) for c in codes}
    assert len(hashes) == len(codes)
    assert all(h != c for h, c in zip(hashes, codes))


# --- enrollment/confirm/step-up services ------------------------------------


def test_enrollment_confirm_and_step_up_round_trip(user_factory, monkeypatch):
    from workeasy360.platform.identity.services import (
        begin_totp_enrollment,
        confirm_totp_enrollment,
        list_mfa_factors,
        verify_step_up_code,
    )

    user = user_factory()
    start = begin_totp_enrollment(user.id)
    assert start.secret_base32
    assert "otpauth://totp/" in start.provisioning_uri

    from workeasy360.platform.identity.models import AuthFactor

    raw_secret = mfa.decrypt_totp_secret(
        AuthFactor.objects.get(id=start.factor_id).secret_ref
    )
    code = mfa.current_totp(raw_secret)

    recovery_codes = confirm_totp_enrollment(user.id, start.factor_id, code)
    assert len(recovery_codes) == mfa.RECOVERY_CODE_COUNT

    factors = list_mfa_factors(user.id)
    assert any(f["kind"] == "totp" and f["confirmed"] for f in factors)
    assert any("recovery code" in f["label"] for f in factors)

    # The code that confirmed enrollment is spent (RFC 6238 section 5.2)...
    assert not verify_step_up_code(user.id, code)
    # ...the next step's code succeeds once, then is spent too...
    later = time.time() + mfa.TOTP_STEP_SECONDS
    monkeypatch.setattr(mfa.time, "time", lambda: later)
    fresh = mfa.current_totp(raw_secret)
    assert verify_step_up_code(user.id, fresh)
    assert not verify_step_up_code(user.id, fresh)
    # ...and a recovery code succeeds once, then is consumed.
    one_code = recovery_codes[0]
    assert verify_step_up_code(user.id, one_code)
    assert not verify_step_up_code(user.id, one_code)


def test_confirm_enrollment_rejects_a_wrong_code(user_factory):
    from workeasy360.contracts.errors import MfaCodeInvalid
    from workeasy360.platform.identity.services import (
        begin_totp_enrollment,
        confirm_totp_enrollment,
    )

    user = user_factory()
    start = begin_totp_enrollment(user.id)
    with pytest.raises(MfaCodeInvalid):
        confirm_totp_enrollment(user.id, start.factor_id, "000000")


# --- AAL propagation (Task 39; the resolve_tenant_from_path fix) -----------


def test_session_aal_propagates_into_the_tenant_context(rf, organization_factory):
    """The bug this fix closes: TenantResolution.aal defaulted to 1
    regardless of request.session["w360_aal"], so step-up never actually
    reached authz.check(). Verified directly against
    resolve_tenant_from_path rather than only through authz, since the
    defect was in the plumbing between the session and the resolution, not
    in authz itself."""
    from workeasy360.platform.tenancy.resolution import (
        record_step_up,
        resolve_tenant_from_path,
    )

    org, _membership, user = organization_factory()

    request = rf.get(f"/api/v1/orgs/{org.slug}/organization")
    request.user = user
    request.session = {}
    record_step_up(request.session)

    resolution = resolve_tenant_from_path(request)
    assert resolution is not None
    assert resolution.aal == 2

    request.session = {}  # absent -> default of 1, never a silent 2
    resolution = resolve_tenant_from_path(request)
    assert resolution.aal == 1

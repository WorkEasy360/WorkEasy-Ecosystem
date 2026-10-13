"""The pure rules (ADR-044): which door a person takes, and what a product's
manifest may declare. No database - these are the functions the launcher, the
authorization endpoint and the settings page all call."""

from __future__ import annotations

import itertools

import pytest

from workeasy360.contracts.enums import ProductSignInMode, SignInPreference
from workeasy360.contracts.values.manifest import (
    LEGACY_MANIFEST_VERSION,
    MANIFEST_VERSION,
    ManifestError,
    from_stored_manifest,
    parse_manifest,
)
from workeasy360.contracts.values.sign_in import (
    DEFAULT_PREFERENCE,
    PRODUCT_REQUIRES_SSO,
    SSO_UNAVAILABLE,
    ProductDoors,
    resolve,
    stored_preference,
)

P = SignInPreference
M = ProductSignInMode

# --- the resolution rule -----------------------------------------------------------------------------


def test_a_person_who_never_chose_is_signed_in_automatically():
    # docs/universal-sso/one-login.md section 1: the recommended experience.
    assert DEFAULT_PREFERENCE is P.AUTO_SSO
    assert stored_preference(None, None) is P.AUTO_SSO
    assert stored_preference(None, P.INHERIT) is P.AUTO_SSO
    # ...which never overrides a product that cannot take it.
    not_ready = ProductDoors(mode=M.SSO_ONLY, sso_ready=False, has_product_login=False)
    assert resolve(not_ready, default=None, override=None).effective is P.PRODUCT_LOGIN


def test_an_override_beats_the_default_and_inherit_follows_it():
    assert stored_preference(P.AUTO_SSO, None) is P.AUTO_SSO
    assert stored_preference(P.AUTO_SSO, P.INHERIT) is P.AUTO_SSO
    assert stored_preference(P.AUTO_SSO, P.PRODUCT_LOGIN) is P.PRODUCT_LOGIN
    assert stored_preference(P.ASK, P.AUTO_SSO) is P.AUTO_SSO


DOORS = [
    ProductDoors(mode=mode, sso_ready=ready, has_product_login=login)
    for mode, ready, login in itertools.product(M, (True, False), (True, False))
]
CHOICES = [None, P.AUTO_SSO, P.ASK, P.PRODUCT_LOGIN]


@pytest.mark.parametrize(
    "doors",
    DOORS,
    ids=lambda d: f"{d.mode.value}-ready{d.sso_ready}-login{d.has_product_login}",
)
@pytest.mark.parametrize(
    "default", CHOICES, ids=lambda c: f"default-{c.value if c else 'none'}"
)
@pytest.mark.parametrize(
    "override",
    [None, P.INHERIT, P.AUTO_SSO, P.ASK, P.PRODUCT_LOGIN],
    ids=lambda c: f"override-{c.value if c else 'none'}",
)
def test_the_whole_matrix_honours_product_constraints_and_never_auto_signs_in_against_a_wish(
    doors, default, override
):
    decision = resolve(doors, default=default, override=override)
    wished = stored_preference(default, override)
    assert decision.preference is wished
    assert decision.effective is not P.INHERIT

    if not doors.sso_available:
        # No Workeasy360 sign-in can complete: never pretended.
        assert decision.effective is P.PRODUCT_LOGIN
        assert not decision.uses_sso and not decision.silent
        assert decision.constraint == (
            "" if wished is P.PRODUCT_LOGIN else SSO_UNAVAILABLE
        )
        return
    # Silent only when the person chose automatic - nothing else ever is.
    assert decision.silent is (wished is P.AUTO_SSO)
    if wished is P.PRODUCT_LOGIN and not doors.product_login_available:
        # The product's constraint wins, but the person is still asked rather
        # than signed in automatically.
        assert decision.effective is P.ASK
        assert decision.constraint == PRODUCT_REQUIRES_SSO
    else:
        assert decision.effective is wished
        assert decision.constraint == ""


def test_doors_say_honestly_what_a_product_offers():
    ready_optional = ProductDoors(
        mode=M.SSO_OPTIONAL, sso_ready=True, has_product_login=True
    )
    assert ready_optional.sso_state == "enabled"
    assert ready_optional.override_choices() == (
        P.INHERIT,
        P.AUTO_SSO,
        P.ASK,
        P.PRODUCT_LOGIN,
    )

    only_sso = ProductDoors(mode=M.SSO_ONLY, sso_ready=True, has_product_login=True)
    assert not only_sso.product_login_available  # sso_only never has its own door
    assert only_sso.override_choices() == (P.INHERIT, P.AUTO_SSO, P.ASK)

    not_connected = ProductDoors(
        mode=M.SSO_ONLY, sso_ready=False, has_product_login=False
    )
    assert not_connected.sso_state == "integration_required"
    assert not_connected.override_choices() == ()

    local = ProductDoors(mode=M.LOCAL_ONLY, sso_ready=True, has_product_login=True)
    assert local.sso_state == "disabled"
    assert not local.sso_available
    assert local.override_choices() == ()


# --- the manifest ------------------------------------------------------------------------------------


def _manifest(**overrides):
    payload = {
        "manifest_version": MANIFEST_VERSION,
        "product_code": "future_product",
        "launch_url": "https://future.example.com/",
        "redirect_uris": ["https://future.example.com/api/auth/callback"],
    }
    payload.update(overrides)
    return payload


def test_the_current_manifest_declares_sign_in_and_round_trips():
    parsed = parse_manifest(
        _manifest(
            sign_in_mode="sso_optional",
            login_url="https://future.example.com/login",
            post_logout_redirect_uris=["https://future.example.com/signed-out"],
        )
    )
    assert parsed.sign_in_mode is M.SSO_OPTIONAL
    assert parsed.product_login_url == "https://future.example.com/login"
    assert parsed.permits_post_logout_redirect("https://future.example.com/signed-out")
    assert not parsed.permits_post_logout_redirect(
        "https://future.example.com/signed-out/"
    )
    rebuilt = from_stored_manifest(parsed.as_dict())
    assert rebuilt == parsed


def test_a_manifest_without_the_field_reads_as_sso_only_with_no_product_door():
    legacy = parse_manifest(_manifest(manifest_version=LEGACY_MANIFEST_VERSION))
    assert legacy.sign_in_mode is M.SSO_ONLY
    assert legacy.product_login_url == ""
    assert "sign_in_mode" not in legacy.as_dict()  # round-trips to what was submitted
    current_default = parse_manifest(_manifest())
    assert current_default.sign_in_mode is M.SSO_ONLY


@pytest.mark.parametrize("field", ["sign_in_mode", "login_url"])
def test_an_older_manifest_version_cannot_declare_the_new_fields(field):
    value = (
        "sso_optional" if field == "sign_in_mode" else "https://future.example.com/login"
    )
    with pytest.raises(ManifestError) as raised:
        parse_manifest(_manifest(manifest_version="2026-10-01", **{field: value}))
    assert raised.value.field == field


def test_optional_sso_must_name_the_products_own_sign_in_page():
    with pytest.raises(ManifestError) as raised:
        parse_manifest(_manifest(sign_in_mode="sso_optional"))
    assert raised.value.field == "login_url"


def test_an_unknown_mode_is_refused_not_defaulted():
    with pytest.raises(ManifestError) as raised:
        parse_manifest(_manifest(sign_in_mode="sometimes"))
    assert raised.value.field == "sign_in_mode"


@pytest.mark.parametrize(
    "url",
    [
        "http://future.example.com/login",  # plaintext, not loopback
        "javascript:alert(1)",
        "https://user:pass@future.example.com/login",
        "https://169.254.169.254/latest",
        "https://localhost/login",
        "https://*.example.com/login",
        "https://future.example.com\\@evil.com/",
    ],
)
def test_the_product_sign_in_page_passes_the_same_url_gate_as_the_launch_url(url):
    with pytest.raises(ManifestError) as raised:
        parse_manifest(_manifest(sign_in_mode="sso_optional", login_url=url))
    assert raised.value.field == "login_url"


def test_a_local_only_product_falls_back_to_its_launch_url_and_sso_only_never_has_a_door():
    local = parse_manifest(_manifest(sign_in_mode="local_only"))
    assert local.product_login_url == "https://future.example.com/"
    only = parse_manifest(
        _manifest(sign_in_mode="sso_only", login_url="https://future.example.com/login")
    )
    assert only.product_login_url == ""


def test_post_logout_uris_are_exact_and_carry_no_query_or_fragment():
    for bad in (
        "https://future.example.com/out?x=1",
        "https://future.example.com/out#x",
        "https://*.example.com/out",
    ):
        with pytest.raises(ManifestError):
            parse_manifest(_manifest(post_logout_redirect_uris=[bad]))

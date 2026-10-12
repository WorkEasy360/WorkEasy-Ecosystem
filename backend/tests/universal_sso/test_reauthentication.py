"""``prompt=login`` and ``auth_time`` (ADR-044, design section 3.4): a product
that asks for a fresh sign-in gets one, and can verify that it did."""

from __future__ import annotations

import time

import pytest
from django.test import Client

from tests.universal_sso.conftest import (
    AUTHORIZE,
    authorize_query,
    path_of,
    query_of,
    resume_token_of,
)

pytestmark = pytest.mark.django_db


def test_prompt_login_asks_for_the_password_even_with_a_live_session(
    signing_key,
    sso_product,
    entitled_org,
    with_password,
    password_login,
    product_client,
    prefer,
):
    crm = sso_product("crm")
    org = entitled_org("crm")
    user = with_password(org["user"])
    prefer(user, "auto_sso")
    browser = password_login(Client(), user)
    signed_in_before = time.time()

    verifier, query = authorize_query(crm, org["organization_id"], prompt="login")
    parked = browser.get(AUTHORIZE, query)
    assert path_of(parked) == "/login"
    assert query_of(parked)["reauth"] == ["1"]
    token = resume_token_of(parked)

    # The old session cannot complete it: back to the password, token still live.
    stale = browser.get(f"/api/v1/integrations/oauth/resume?resume={token}")
    assert path_of(stale) == "/login" and query_of(stale)["reauth"] == ["1"]

    time.sleep(0.01)
    password_login(browser, user)
    granted = browser.get(f"/api/v1/integrations/oauth/resume?resume={token}")
    assert granted["Location"].startswith(crm["redirect_uri"] + "?")
    reference = product_client(crm)
    tokens = reference.exchange_code(
        code=query_of(granted)["code"][0], code_verifier=verifier
    )
    claims = reference.verify_id_token(tokens["id_token"], expected_nonce="client-nonce")
    # The product can check the sign-in is fresh, not trust the round trip.
    assert claims["auth_time"] >= int(signed_in_before)


def test_a_silent_sign_in_carries_the_original_auth_time(
    signing_key,
    sso_product,
    entitled_org,
    with_password,
    password_login,
    product_client,
    prefer,
):
    crm = sso_product("crm")
    org = entitled_org("crm")
    user = with_password(org["user"])
    prefer(user, "auto_sso")
    before = int(time.time())
    browser = password_login(Client(), user)
    verifier, query = authorize_query(crm, org["organization_id"])
    granted = browser.get(AUTHORIZE, query)
    reference = product_client(crm)
    tokens = reference.exchange_code(
        code=query_of(granted)["code"][0], code_verifier=verifier
    )
    claims = reference.verify_id_token(tokens["id_token"], expected_nonce="client-nonce")
    assert before - 1 <= claims["auth_time"] <= int(time.time())


def test_reauthentication_continues_the_same_sign_in_so_sign_out_still_ends_everything(
    signing_key, sso_product, entitled_org, with_password, password_login
):
    from workeasy360.platform.identity.models import UserSession
    from workeasy360.platform.identity.sessions import SESSION_SIGN_IN_ID_KEY

    org = entitled_org("crm")
    user = with_password(org["user"])
    browser = password_login(Client(), user)
    first = browser.session[SESSION_SIGN_IN_ID_KEY]
    password_login(browser, user)
    assert browser.session[SESSION_SIGN_IN_ID_KEY] == first
    browser.post("/api/v1/auth/logout")
    assert not UserSession.objects.filter(user=user).exists()


def test_a_different_person_signing_in_ends_the_previous_persons_sign_in(
    signing_key, entitled_org, with_password, password_login, user_factory
):
    """Two people's sessions never coexist in one browser: the previous
    person's whole sign-in (every portal session of it) ends."""
    from workeasy360.platform.identity.models import UserSession
    from workeasy360.platform.identity.sessions import SESSION_SIGN_IN_ID_KEY

    first = with_password(entitled_org("crm")["user"])
    second = with_password(user_factory())
    browser = password_login(Client(), first)
    sign_in = browser.session[SESSION_SIGN_IN_ID_KEY]
    # A sibling portal session of the same sign-in (as ADR-043 hands off).
    sibling = UserSession.objects.get(user=first)
    UserSession.objects.create(
        session_key="sibling-session-key-0000000000000000",
        session_data=sibling.session_data,
        expire_date=sibling.expire_date,
        user=first,
        sign_in_id=sign_in,
    )
    password_login(browser, second)
    assert not UserSession.objects.filter(user=first).exists()
    assert browser.session[SESSION_SIGN_IN_ID_KEY] != sign_in

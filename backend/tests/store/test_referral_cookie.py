"""Referral attribution survives a new tab (ADR-052; Store Working Flow PDF page 6:
"Customer Clicks -> Attribution ID/cookie recorded -> Customer Registers ->
Referral persists to account"). The referral link sets an HttpOnly cookie with
the same opaque token the page keeps, so a verification link opened in a new
tab - where the page's own copy is gone - still converts it, exactly once."""

from __future__ import annotations

import pytest
from django.conf import settings
from django.test import Client

from tests.extension.conftest import PASSWORD
from workeasy360.commercial.commissions import referrals

pytestmark = pytest.mark.django_db


@pytest.fixture
def affiliate(partner_factory):
    return partner_factory("referral.create", "commission.earn")


def _capture(client, code):
    return client.post(
        "/api/v1/public/referrals", {"code": code}, content_type="application/json"
    )


def test_following_a_referral_link_sets_an_httponly_cookie(affiliate):
    code = referrals.create_referral_code(affiliate["actor"]()).code
    response = _capture(Client(), code)
    assert response.status_code == 201
    cookie = response.cookies[settings.REFERRAL_COOKIE_NAME]
    assert cookie.value == response.json()["token"]
    assert cookie["httponly"] and cookie["samesite"] == "Lax" and cookie["path"] == "/"
    # ADR-054: as long as the programme's attribution window.
    assert int(cookie["max-age"]) == referrals.capture_window().total_seconds()


def test_an_unknown_code_sets_no_cookie():
    response = _capture(Client(), "nosuchcode")
    assert response.status_code == 404
    assert settings.REFERRAL_COOKIE_NAME not in response.cookies


def test_a_signup_in_a_new_tab_is_attributed_through_the_cookie_once(
    affiliate, organization_factory, user_factory, login
):
    from workeasy360.commercial.commissions.models import Referral

    code = referrals.create_referral_code(affiliate["actor"]()).code
    owner = user_factory(password=PASSWORD)
    org, _membership, _ = organization_factory(owner=owner)
    browser = login(owner)  # the same browser: it carries the referral cookie
    assert _capture(browser, code).status_code == 201

    # The verification tab has no token of its own: it sends an empty one.
    converted = browser.post(
        "/api/v1/referrals/convert",
        {"token": "", "organization_id": str(org.id)},
        content_type="application/json",
    )
    assert converted.status_code == 200 and converted.json() == {"converted": True}
    assert (
        Referral.objects.get(organization_id=org.id).partner_id == affiliate["partner"].id
    )
    # Used once: the cookie is cleared, and a second attempt converts nothing.
    assert converted.cookies[settings.REFERRAL_COOKIE_NAME].value == ""
    again = browser.post(
        "/api/v1/referrals/convert",
        {"token": "", "organization_id": str(org.id)},
        content_type="application/json",
    )
    assert again.json() == {"converted": False}


def test_a_token_in_the_body_still_wins(
    affiliate, organization_factory, user_factory, login
):
    """The page's own token keeps working exactly as before."""
    code = referrals.create_referral_code(affiliate["actor"]()).code
    token = referrals.capture_referral(code)
    owner = user_factory(password=PASSWORD)
    org, _membership, _ = organization_factory(owner=owner)
    response = login(owner).post(
        "/api/v1/referrals/convert",
        {"token": token, "organization_id": str(org.id)},
        content_type="application/json",
    )
    assert response.json() == {"converted": True}

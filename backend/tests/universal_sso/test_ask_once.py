"""Same account or a different one (the unified-login brief section 5, ADR-066).

The default is Ask once: the first time a product is opened in a Workeasy360
sign-in, the access chooser names the signed-in account and offers "Continue
with the same account", "Use a different account" and "Create a new account";
for the rest of that sign-in the product opens with no question. These suites
pin when the question comes and when it does not (section 5.5), that it never
comes before a refusal, and what each answer does.
"""

from __future__ import annotations

import pytest

from tests.universal_sso.conftest import (
    AUTHORIZE,
    CHOOSE,
    PENDING,
    RESUME,
    authorize_query,
    path_of,
    query_of,
    resume_token_of,
)
from workeasy360.platform.ecosystem import protocol

pytestmark = pytest.mark.django_db


def _authorize(client, registration, org_id, **extra):
    _verifier, query = authorize_query(registration, org_id, **extra)
    return client.get(AUTHORIZE, query)


def _choose(client, token, choice, remember=False):
    return client.post(
        CHOOSE,
        {"resume": token, "choice": choice, "remember": remember},
        content_type="application/json",
    )


def _enter(client, registration, org_id):
    """Open the product once and answer "Continue with the same account"."""
    asked = _authorize(client, registration, org_id)
    assert path_of(asked) == "/product-access", asked["Location"]
    answered = _choose(client, resume_token_of(asked), "workeasy360")
    assert answered.status_code == 200, answered.content
    granted = client.get(answered.json()["redirect_url"])
    assert granted["Location"].startswith(registration["redirect_uri"] + "?")
    return granted


def _granted(response, registration) -> bool:
    return response["Location"].startswith(registration["redirect_uri"] + "?") and bool(
        query_of(response).get("code")
    )


# --- when the question comes ---------------------------------------------------------------------


def test_first_entry_asks_and_names_the_signed_in_account(
    client, signing_key, sso_product, entitled_org, signed_in
):
    crm = sso_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"], sign_in_preference=None)
    asked = _authorize(client, crm, org["organization_id"])
    assert path_of(asked) == "/product-access"
    shown = client.get(PENDING, {"resume": resume_token_of(asked)}).json()
    assert shown["account"] == {
        "email": org["user"].email,
        "name": org["user"].full_name,
    }
    assert shown["organization"]["name"]
    assert shown["choices"]["workeasy360"] and shown["choices"]["create_account"]


def test_once_answered_the_same_sign_in_opens_the_product_with_no_question(
    client, signing_key, sso_product, entitled_org, signed_in
):
    crm = sso_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"], sign_in_preference=None)
    _enter(client, crm, org["organization_id"])
    for _ in range(3):  # every later launch, refresh or deep link
        assert _granted(_authorize(client, crm, org["organization_id"]), crm)


def test_each_product_is_asked_about_on_its_own_first_entry(
    client, signing_key, sso_product, entitled_org, signed_in, second_org_for
):
    crm, books = sso_product("crm"), sso_product("books")
    org = entitled_org("crm")
    other = second_org_for(org["user"], "books")
    signed_in(org["user"], sign_in_preference=None)
    _enter(client, crm, org["organization_id"])
    assert path_of(_authorize(client, books, other["organization_id"])) == (
        "/product-access"
    )
    assert _granted(_authorize(client, crm, org["organization_id"]), crm)


def test_a_new_sign_in_is_asked_again(
    client, signing_key, sso_product, entitled_org, signed_in
):
    crm = sso_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"], sign_in_preference=None)
    _enter(client, crm, org["organization_id"])
    client.post("/api/v1/auth/logout")
    signed_in(org["user"], sign_in_preference=None)
    assert path_of(_authorize(client, crm, org["organization_id"])) == "/product-access"


def test_the_record_answers_only_for_the_person_and_sign_in_it_was_written_for(
    client, signing_key, sso_product, entitled_org, signed_in
):
    from workeasy360.platform.ecosystem.sign_in import SESSION_ENTERED_KEY
    from workeasy360.platform.identity.sessions import SESSION_SIGN_IN_ID_KEY

    crm = sso_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"], sign_in_preference=None)
    session = client.session
    sign_in_id = session.get(SESSION_SIGN_IN_ID_KEY) or ""
    for forged in (
        {"u": "someone-else", "s": sign_in_id, "p": ["crm"]},
        {"u": str(org["user"].id), "s": "another-sign-in", "p": ["crm"]},
        {"u": str(org["user"].id), "s": sign_in_id, "p": "crm"},
        ["crm"],
    ):
        session[SESSION_ENTERED_KEY] = forged
        session.save()
        assert path_of(_authorize(client, crm, org["organization_id"])) == (
            "/product-access"
        ), forged


def test_signing_in_for_the_request_is_the_answer_and_counts_as_entry(
    client, signing_key, sso_product, entitled_org, with_password, password_login
):
    crm = sso_product("crm")
    org = entitled_org("crm")
    parked = _authorize(client, crm, None)
    assert path_of(parked) == "/login"
    password_login(client, with_password(org["user"]))
    assert _granted(client.get(RESUME, {"resume": resume_token_of(parked)}), crm)
    assert _granted(_authorize(client, crm, org["organization_id"]), crm)


def test_a_refusal_always_comes_before_the_question(
    client, signing_key, sso_product, entitled_org, signed_in
):
    crm = sso_product("crm")
    org = entitled_org("crm", seat=False)
    signed_in(org["user"], sign_in_preference=None)
    assert query_of(_authorize(client, crm, org["organization_id"]))["error"] == [
        protocol.PRODUCT_NO_SEAT
    ]


def test_a_product_asking_to_confirm_the_account_is_always_asked(
    client, signing_key, sso_product, entitled_org, signed_in
):
    crm = sso_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"], sign_in_preference=None)
    _enter(client, crm, org["organization_id"])
    again = _authorize(client, crm, org["organization_id"], prompt="select_account")
    assert path_of(again) == "/product-access"


def test_stored_choices_are_kept_and_honoured(
    client, signing_key, sso_product, entitled_org, signed_in
):
    """People who chose before ADR-066 keep their choice: Automatic never
    asks, and Ask each time asks even after an entry."""
    crm = sso_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"], sign_in_preference="auto_sso")
    assert _granted(_authorize(client, crm, org["organization_id"]), crm)

    signed_in(org["user"], sign_in_preference="ask")
    _enter(client, crm, org["organization_id"])
    assert path_of(_authorize(client, crm, org["organization_id"])) == "/product-access"


def test_ask_once_can_be_chosen_explicitly(client, sso_product, user_factory):
    from workeasy360.platform.ecosystem.models import UserSignInPreference

    sso_product("crm")
    person = user_factory()
    client.force_login(person)
    stored = client.put(
        "/api/v1/auth/sign-in-preferences",
        {"default": "auto_sso", "products": {"crm": "ask_once"}},
        content_type="application/json",
    )
    assert stored.status_code == 200, stored.content
    assert UserSignInPreference.objects.get(user=person, product_code="crm").mode == (
        "ask_once"
    )


# --- "Create a new account" -----------------------------------------------------------------------


def _list_in_store(code: str, slug: str, *, listed: bool = True) -> None:
    from workeasy360.commercial.catalog.models import Product
    from workeasy360.contracts.enums import ProductListing, ProductStatus

    Product.objects.filter(code=code).update(
        slug=slug,
        listing=(ProductListing.LISTED if listed else ProductListing.HIDDEN).value,
        status=ProductStatus.AVAILABLE.value,
        customer_store_enabled=True,
    )


def test_create_account_signs_out_and_continues_at_the_products_store_page(
    client, signing_key, sso_product, entitled_org, signed_in
):
    crm = sso_product("crm")
    org = entitled_org("crm")
    _list_in_store("crm", "easy-crm")
    signed_in(org["user"], sign_in_preference=None)
    asked = _authorize(client, crm, org["organization_id"])
    token = resume_token_of(asked)
    answered = _choose(client, token, "create_account")
    assert answered.status_code == 200, answered.content
    target = answered.json()["redirect_url"]
    assert target.endswith("/signup?next=%2Fstore%2Fproduct%2Feasy-crm"), target
    # Signed out: the new account inherits nothing of this session.
    assert client.get("/api/v1/auth/session").json().get("authenticated") is not True
    # The request is spent: nothing of it - its organization least of all -
    # follows the browser into somebody else's sign-up.
    assert client.get(RESUME, {"resume": token})["Location"].endswith(
        "/login?error=resume_invalid"
    )


def test_create_account_for_a_product_the_store_does_not_sell_opens_plain_sign_up(
    client, signing_key, sso_product, entitled_org, signed_in
):
    crm = sso_product("crm")
    org = entitled_org("crm")
    _list_in_store("crm", "easy-crm", listed=False)
    signed_in(org["user"], sign_in_preference=None)
    asked = _authorize(client, crm, org["organization_id"])
    target = _choose(client, resume_token_of(asked), "create_account").json()[
        "redirect_url"
    ]
    assert target.endswith("/signup"), target


def test_create_account_is_not_offered_or_accepted_while_signup_is_closed(
    client, signing_key, sso_product, entitled_org, signed_in, monkeypatch
):
    from workeasy360.platform.administration import site

    monkeypatch.setattr(site, "signup_enabled", lambda: False)
    crm = sso_product("crm")
    org = entitled_org("crm")
    signed_in(org["user"], sign_in_preference=None)
    token = resume_token_of(_authorize(client, crm, org["organization_id"]))
    assert (
        client.get(PENDING, {"resume": token}).json()["choices"]["create_account"]
        is False
    )
    refused = _choose(client, token, "create_account")
    assert refused.status_code == 400 and refused.json()["code"] == "signup_closed"
    # The request was not touched: "Continue" still works, and the person is
    # still signed in.
    answered = _choose(client, token, "workeasy360")
    assert _granted(client.get(answered.json()["redirect_url"]), crm)

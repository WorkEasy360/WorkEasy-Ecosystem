"""The person's own sign-in preferences (ADR-044, design section 5):
``/api/v1/auth/sign-in-preferences``."""

from __future__ import annotations

import pytest
from django.test import Client

from tests.universal_sso.conftest import PREFS

pytestmark = pytest.mark.django_db


@pytest.fixture
def products(sso_product):
    """CRM offers its own sign-in too; Books signs in with Workeasy360 only."""
    return {
        "crm": sso_product("crm", mode="sso_optional"),
        "books": sso_product("books", mode="sso_only"),
    }


def _by_code(body):
    return {p["product_code"]: p for p in body["products"]}


def test_a_new_person_is_signed_in_automatically_and_sees_every_product_that_can_be_entered(
    client, products, user_factory
):
    client.force_login(user_factory())
    body = client.get(PREFS).json()
    # Nothing stored: the platform default, Automatic (one-login brief).
    assert body["default"] == "auto_sso" and body["default_is_set"] is False
    assert body["default_choices"] == ["auto_sso", "ask", "product_login"]
    rows = _by_code(body)
    assert set(rows) == {"crm", "books"}
    crm, books = rows["crm"], rows["books"]
    assert crm["mode"] == "sso_optional" and crm["sso"] == "enabled"
    assert crm["choices"] == ["inherit", "auto_sso", "ask", "product_login"]
    assert crm["override"] == "inherit" and crm["effective"] == "auto_sso"
    assert books["choices"] == ["inherit", "auto_sso", "ask"]  # no door of its own
    # Display facts only: nothing identifying the registration.
    for row in body["products"]:
        assert not {"client_id", "redirect_uris", "login_url", "credential_id"} & set(row)


def test_the_default_applies_to_every_product_and_an_override_to_one(
    client, products, user_factory
):
    client.force_login(user_factory())
    body = client.put(
        PREFS,
        {"default": "auto_sso", "products": {"books": "ask"}},
        content_type="application/json",
    ).json()
    rows = _by_code(body)
    assert body["default"] == "auto_sso" and body["default_is_set"] is True
    assert rows["crm"]["effective"] == "auto_sso" and rows["crm"]["override"] == "inherit"
    assert rows["books"]["effective"] == "ask" and rows["books"]["override"] == "ask"

    # Only the keys sent change; inherit removes an override.
    body = client.put(
        PREFS, {"products": {"books": "inherit"}}, content_type="application/json"
    ).json()
    assert body["default"] == "auto_sso"
    assert _by_code(body)["books"]["effective"] == "auto_sso"


def test_a_preference_can_never_choose_a_door_the_product_does_not_have(
    client, products, user_factory
):
    from workeasy360.platform.ecosystem.models import UserSignInPreference

    client.force_login(user_factory())
    refused = client.put(
        PREFS, {"products": {"books": "product_login"}}, content_type="application/json"
    )
    assert refused.status_code == 400
    assert refused.json()["code"] == "sign_in_preference_invalid"
    assert refused.json()["field"] == "products.books"
    # The whole request is refused, so nothing half-applied either.
    refused = client.put(
        PREFS,
        {"default": "auto_sso", "products": {"books": "product_login"}},
        content_type="application/json",
    )
    assert refused.status_code == 400
    assert not UserSignInPreference.objects.exists()

    # The global default may prefer product sign-in: it applies where offered.
    body = client.put(
        PREFS, {"default": "product_login"}, content_type="application/json"
    ).json()
    rows = _by_code(body)
    assert rows["crm"]["effective"] == "product_login"
    assert rows["books"]["effective"] == "ask"
    assert rows["books"]["constraint"] == "product_requires_sso"


@pytest.mark.parametrize(
    "body",
    [
        {"default": "inherit"},  # inherit is per product only
        {"default": "sometimes"},
        {"products": {"crm": "always"}},
        {"products": {"unknown_product": "auto_sso"}},
        {"products": ["crm"]},
    ],
)
def test_unknown_values_and_products_are_refused_not_ignored(
    client, products, user_factory, body
):
    client.force_login(user_factory())
    response = client.put(PREFS, body, content_type="application/json")
    assert response.status_code == 400
    assert response.json()["code"] == "sign_in_preference_invalid"


def test_a_product_that_cannot_be_entered_through_workeasy360_has_nothing_to_choose(
    client, sso_product, user_factory
):
    sso_product("crm", mode="local_only")
    client.force_login(user_factory())
    row = _by_code(client.get(PREFS).json())["crm"]
    assert row["sso"] == "disabled" and row["choices"] == []
    assert row["effective"] == "product_login" and row["constraint"] == "sso_unavailable"
    refused = client.put(
        PREFS, {"products": {"crm": "auto_sso"}}, content_type="application/json"
    )
    assert refused.status_code == 400


def test_draft_and_retired_products_are_not_listed(
    client, sso_product, platform_admin, in_tenant, user_factory
):
    from workeasy360.platform.ecosystem import services

    sso_product("crm", activate=False)
    books = sso_product("books")
    with in_tenant(platform_admin["ctx"]):
        services.transition(
            platform_admin["ctx"], client_id=books["client_id"], to_status="retired"
        )
    client.force_login(user_factory())
    assert client.get(PREFS).json()["products"] == []


def test_reset_puts_everything_back_to_the_automatic_default(
    client, products, user_factory
):
    from workeasy360.platform.ecosystem.models import UserSignInPreference

    client.force_login(user_factory())
    client.put(
        PREFS,
        {"default": "ask", "products": {"crm": "product_login"}},
        content_type="application/json",
    )
    body = client.delete(PREFS).json()
    assert body["default"] == "auto_sso" and body["default_is_set"] is False
    assert all(
        p["override"] == "inherit" and p["effective"] == "auto_sso"
        for p in body["products"]
    )
    assert not UserSignInPreference.objects.exists()


def test_each_change_is_recorded_in_the_persons_own_trail_never_an_organizations(
    client, products, entitled_org, monkeypatch
):
    from workeasy360.platform.audit import services as audit
    from workeasy360.platform.identity.models import AccountEvent

    org = entitled_org("crm")
    client.force_login(org["user"])
    written = []
    monkeypatch.setattr(audit, "record", lambda *a, **k: written.append((a, k)))
    client.put(
        PREFS,
        {"default": "auto_sso", "products": {"crm": "ask"}},
        content_type="application/json",
    )
    client.put(
        PREFS, {"default": "auto_sso"}, content_type="application/json"
    )  # no change: no entry
    client.delete(PREFS)
    reasons = list(
        AccountEvent.objects.filter(
            user=org["user"], kind=AccountEvent.Kind.SIGN_IN_PREFERENCE_CHANGED
        )
        .order_by("occurred_at")
        .values_list("reason", flat=True)
    )
    assert sorted(reasons[:2]) == ["crm=ask", "default=auto_sso"]
    assert reasons[2:] == ["reset"]
    # An organization administrator never learns a member's preference.
    assert written == []


def test_a_preference_is_private_to_its_person(products, user_factory):
    ana, ben = user_factory(), user_factory()
    a, b = Client(), Client()
    a.force_login(ana)
    b.force_login(ben)
    a.put(PREFS, {"default": "ask"}, content_type="application/json")
    assert b.get(PREFS).json()["default"] == "auto_sso"
    # No endpoint takes a person: a user id in the body changes nothing.
    b.put(
        PREFS,
        {"default": "product_login", "user_id": str(ana.id)},
        content_type="application/json",
    )
    assert a.get(PREFS).json()["default"] == "ask"


def test_signed_out_callers_get_nothing(products):
    anonymous = Client()
    assert anonymous.get(PREFS).status_code == 401
    assert (
        anonymous.put(
            PREFS, {"default": "auto_sso"}, content_type="application/json"
        ).status_code
        == 401
    )


def test_a_change_needs_the_csrf_token(products, user_factory):
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(user_factory())
    strict.get("/api/v1/auth/session")
    assert (
        strict.put(
            PREFS, {"default": "auto_sso"}, content_type="application/json"
        ).status_code
        == 403
    )
    token = strict.cookies["w360_csrf"].value
    ok = strict.put(
        PREFS,
        {"default": "auto_sso"},
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert ok.status_code == 200

"""A held product the launcher cannot open yet is not offered an Open button.

Production, 2026-11-27: an organization bought Easy CRM; its integration was
still a draft (no credential, domain unverified), so the Store card's Open
answered "That product isn't available yet." Explore - and so the Store card
and the Store product page - now carry ``launch_ready``, the launcher's own
structural test, so a held product that cannot open says it is being connected
instead. The launch endpoint still decides every launch."""

from __future__ import annotations

import pytest

from tests.phase11.conftest import register_bare, start_trial, url

pytestmark = pytest.mark.django_db


def _explore(shop) -> dict[str, dict]:
    response = shop["client"].get(url(shop["slug"], "explore"))
    assert response.status_code == 200, response.content
    return {p["code"]: p for p in response.json()["products"]}


def _launch_state(shop, product="crm") -> tuple[int, str | None]:
    response = shop["client"].post(
        url(shop["slug"], f"apps/{product}/launch"), HTTP_ACCEPT="application/json"
    )
    return response.status_code, response.json().get("state")


def _seat(shop, product="crm") -> None:
    response = shop["client"].post(
        url(shop["slug"], f"apps/{product}/seats/{shop['membership'].id}")
    )
    assert response.status_code in (200, 201), response.content


def test_a_held_product_with_no_integration_is_not_ready(shop):
    assert start_trial(shop).status_code == 201
    crm = _explore(shop)["crm"]
    assert crm["trial_state"] == "active"
    assert crm["launch_ready"] is False
    # The card's answer is the launcher's: Open would have been refused.
    assert _launch_state(shop) == (409, "product_unavailable")


def test_a_held_product_with_a_draft_integration_is_not_ready(
    shop, platform_admin, in_tenant
):
    """The production case: registered, never activated."""
    register_bare(
        platform_admin, in_tenant, "crm", origin="https://crm.example.com", activate=False
    )
    assert start_trial(shop).status_code == 201
    _seat(shop)
    assert _explore(shop)["crm"]["launch_ready"] is False
    assert _launch_state(shop) == (409, "product_unavailable")


def test_an_active_integration_is_ready_and_opens(shop, platform_admin, in_tenant):
    register_bare(platform_admin, in_tenant, "crm", origin="https://crm.example.com")
    assert start_trial(shop).status_code == 201
    _seat(shop)
    assert _explore(shop)["crm"]["launch_ready"] is True
    assert _launch_state(shop)[0] == 200


def test_a_suspended_integration_stays_ready_for_its_own_message(
    shop, platform_admin, in_tenant
):
    """Suspension is operational: Open stays, and the launcher answers it with
    its own "try again shortly" - a missing product and a paused one differ."""
    from workeasy360.platform.ecosystem import services

    integration = register_bare(
        platform_admin, in_tenant, "crm", origin="https://crm.example.com"
    )
    ctx = platform_admin["ctx"]
    with in_tenant(ctx):
        services.transition(
            ctx, client_id=integration.client_id, to_status="suspended", reason="test"
        )
    assert start_trial(shop).status_code == 201
    _seat(shop)
    assert _explore(shop)["crm"]["launch_ready"] is True
    assert _launch_state(shop) == (409, "integration_suspended")


def test_readiness_is_per_product_and_not_about_holding(shop, platform_admin, in_tenant):
    register_bare(platform_admin, in_tenant, "crm", origin="https://crm.example.com")
    products = _explore(shop)
    assert products["crm"]["launch_ready"] is True  # not held, still a product fact
    assert products["books"]["launch_ready"] is False


def test_the_store_card_and_product_page_carry_it(shop):
    assert start_trial(shop).status_code == 201
    store = shop["client"].get(url(shop["slug"], "store"))
    assert store.status_code == 200, store.content
    card = next(p for p in store.json()["products"] if p["code"] == "crm")
    assert card["launch_ready"] is False
    page = shop["client"].get(url(shop["slug"], "store/crm"))
    assert page.status_code == 200, page.content
    assert page.json()["product"]["launch_ready"] is False

"""The subscriptions read names who collects each subscription (2026-11-11): the
User Admin's "purchase source" column, and whether its web cancel applies."""

from __future__ import annotations

import pytest

from tests.extension.conftest import url

pytestmark = pytest.mark.django_db


def test_a_subscription_says_who_collects_it(shop):
    from tests.extension.conftest import buy_directly

    buy_directly(shop, shop["catalog"]["versions"]["crm_professional"].id)
    response = shop["client"].get(url(shop["slug"], "subscriptions"))
    assert response.status_code == 200, response.content
    (sub,) = response.json()["results"]
    assert sub["collection_mode"] == "automatic"
    # Still narrow: no pricing row's id comes back to the browser.
    assert "price_id" not in sub and all("plan_version_id" not in i for i in sub["items"])

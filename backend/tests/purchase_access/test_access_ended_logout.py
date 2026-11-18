"""ADR-073, G10: losing a product reaches the product at once.

Before, a person removed from an organization, a seat taken back, or an
organization whose entitlement ended kept a live product session until the
product's next entitlement re-check (up to 15 minutes). Now each of them queues
an ADR-069 back-channel logout - ``sub`` only, so the product ends that person's
sessions in that product - in the same transaction that took the access away.
Only the people who lost the product, and only the products they lost, are told.
"""

from __future__ import annotations

import threading
import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from tests.purchase_access.conftest import buy, in_scope, post, url

pytestmark = pytest.mark.django_db


def _signed_in(integration, user):
    """What the token endpoint records when a person opens a product that takes
    back-channel logout (``backchannel.record_product_sign_in``)."""
    from workeasy360.platform.ecosystem.models import ProductSignIn

    return ProductSignIn.objects.create(
        integration=integration, user_id=user.pk, sign_in_id=uuid.uuid4()
    )


def _owed(*users):
    """``{(product_code, user_id, reason)}`` of every queued logout for these people."""
    from workeasy360.platform.ecosystem.models import BackchannelLogout

    rows = BackchannelLogout.objects.filter(
        user_id__in=[u.pk for u in users]
    ).select_related("integration")
    for row in rows:
        assert row.sign_in_id is None  # the person, never one sign-in
    return {(row.integration.product_code, row.user_id, row.reason) for row in rows}


def _live(user):
    from workeasy360.platform.ecosystem.models import ProductSignIn

    return set(
        ProductSignIn.objects.filter(user_id=user.pk, ended_at__isnull=True).values_list(
            "integration__product_code", flat=True
        )
    )


@pytest.fixture
def team(shop):
    """CRM (per seat) and Books (organization-wide) bought; the owner and a
    seated colleague signed in to both, a colleague without a CRM seat signed in
    to Books, and the colleague also signed in to Desk, which this organization
    does not hold (they use it through another one)."""
    buy(shop, "crm_professional")
    buy(shop, "books_professional")
    seated, unseated = shop["add"]("member"), shop["add"]("member")
    assigned = shop["client"].post(
        url(shop["slug"], f"apps/crm/seats/{seated['membership'].id}")
    )
    assert assigned.status_code == 201, assigned.content
    integrations = {
        code: shop["products"][code]["integration"] for code in shop["products"]
    }
    for person in (shop["owner"], seated["user"]):
        _signed_in(integrations["crm"], person)
        _signed_in(integrations["books"], person)
    _signed_in(integrations["books"], unseated["user"])
    _signed_in(integrations["desk"], seated["user"])
    return {**shop, "seated": seated, "unseated": unseated, "integrations": integrations}


def test_removing_a_member_tells_each_product_they_could_open_here(team):
    seated = team["seated"]
    removed = team["client"].delete(
        url(team["slug"], f"memberships/{seated['membership'].id}")
    )
    assert removed.status_code == 200, removed.content

    assert _owed(seated["user"]) == {
        ("crm", seated["user"].pk, "membership_removed"),
        ("books", seated["user"].pk, "membership_removed"),
    }
    # Desk is not this organization's to end; the colleagues lost nothing.
    assert _live(seated["user"]) == {"desk"}
    assert _owed(team["owner"], team["unseated"]["user"]) == set()


def test_a_member_without_a_seat_is_not_logged_out_of_a_per_seat_product(team):
    unseated = team["unseated"]
    _signed_in(
        team["integrations"]["crm"], unseated["user"]
    )  # through another organization
    removed = team["client"].delete(
        url(team["slug"], f"memberships/{unseated['membership'].id}")
    )
    assert removed.status_code == 200
    assert _owed(unseated["user"]) == {
        ("books", unseated["user"].pk, "membership_removed")
    }
    assert _live(unseated["user"]) == {"crm"}


def test_suspending_a_member_tells_their_products_too(team):
    seated = team["seated"]
    answer = team["client"].post(
        url(team["slug"], f"members/{seated['membership'].id}/suspend"),
        content_type="application/json",
    )
    assert answer.status_code == 200, answer.content
    assert _owed(seated["user"]) == {
        ("crm", seated["user"].pk, "membership_suspended"),
        ("books", seated["user"].pk, "membership_suspended"),
    }


def test_taking_a_seat_back_tells_that_product_only(team):
    seated = team["seated"]
    released = team["client"].delete(
        url(team["slug"], f"apps/crm/seats/{seated['membership'].id}")
    )
    assert released.status_code == 200 and released.json()["released"] is True
    assert _owed(seated["user"]) == {("crm", seated["user"].pk, "seat_released")}
    assert _live(seated["user"]) == {"books", "desk"}
    # Releasing a seat nobody holds ends nothing and tells nobody.
    again = team["client"].delete(
        url(team["slug"], f"apps/crm/seats/{seated['membership'].id}")
    )
    assert again.json()["released"] is False
    assert len(_owed(seated["user"])) == 1


def test_a_seat_in_an_organization_wide_product_grants_nothing_to_take_back(team):
    from workeasy360.platform.entitlements.services import seats

    seated = team["seated"]
    with in_scope(team["id"]):
        seats.assign_seat(
            team["id"], pool_key="books", membership_id=seated["membership"].id
        )
    released = team["client"].delete(
        url(team["slug"], f"apps/books/seats/{seated['membership'].id}")
    )
    assert released.json()["released"] is True
    assert _owed(seated["user"]) == set()


def _subscription(shop):
    return shop["client"].get(url(shop["slug"], "subscriptions")).json()["results"][0]


def test_an_entitlement_ended_now_tells_everyone_who_could_open_it(team):
    cancelled = post(
        team["client"],
        url(team["slug"], f"subscriptions/{_subscription(team)['id']}/cancel"),
        {"immediate": True, "reason_code": "customer_request"},
    )
    assert cancelled.status_code == 200, cancelled.content

    owner, seated, unseated = (
        team["owner"],
        team["seated"]["user"],
        team["unseated"]["user"],
    )
    assert _owed(owner, seated, unseated) == {
        ("crm", owner.pk, "entitlement_ended"),
        ("crm", seated.pk, "entitlement_ended"),
        ("books", owner.pk, "entitlement_ended"),
        ("books", seated.pk, "entitlement_ended"),
        ("books", unseated.pk, "entitlement_ended"),
    }
    assert _live(seated) == {"desk"}


def test_an_entitlement_reaching_its_end_tells_products_when_the_sweep_records_it(team):
    from workeasy360.commercial.subscriptions.system import process_due_subscriptions

    subscription = _subscription(team)
    cancelled = post(
        team["client"],
        url(team["slug"], f"subscriptions/{subscription['id']}/cancel"),
        {"immediate": False, "reason_code": "customer_request"},
    )
    assert cancelled.status_code == 200
    # Still running to the period end: nobody is signed out yet.
    assert _owed(team["owner"]) == set()

    after = timezone.datetime.fromisoformat(
        subscription["current_period_end"]
    ) + timedelta(minutes=1)
    process_due_subscriptions(at=after)
    assert ("crm", team["owner"].pk, "entitlement_ended") in _owed(team["owner"])
    assert ("books", team["unseated"]["user"].pk, "entitlement_ended") in _owed(
        team["unseated"]["user"]
    )


def test_a_reconciliation_that_changes_nothing_tells_nobody(team):
    from workeasy360.commercial.provisioning.services import (
        reconcile,
        reconcile_and_report_drift,
    )

    reconcile(team["id"])
    reconcile_and_report_drift(team["id"])
    assert _owed(team["owner"], team["seated"]["user"], team["unseated"]["user"]) == set()


def test_another_organization_holding_the_product_is_untouched(team, console_org, login):
    """Someone in a different organization, signed in to CRM there, is not told
    anything when this organization's CRM ends."""
    other = console_org()
    _signed_in(team["integrations"]["crm"], other["owner"])
    post(
        team["client"],
        url(team["slug"], f"subscriptions/{_subscription(team)['id']}/cancel"),
        {"immediate": True, "reason_code": "customer_request"},
    )
    assert _owed(other["owner"]) == set()
    assert _live(other["owner"]) == {"crm"}


def test_the_queued_logout_is_delivered_as_a_sub_only_token(team, signing_key):
    import jwt

    from workeasy360.platform.ecosystem import backchannel

    seated = team["seated"]
    team["client"].delete(url(team["slug"], f"apps/crm/seats/{seated['membership'].id}"))
    (delivery,) = [d for d in backchannel.claim_due() if d.user_id == seated["user"].pk]
    assert delivery.sid == "" and delivery.product_code == "crm"
    assert delivery.url == "http://localhost:9101/auth/backchannel-logout"
    claims = jwt.decode(
        backchannel.logout_token_for(delivery), options={"verify_signature": False}
    )
    assert claims["sub"] == str(seated["user"].pk) and "sid" not in claims


def test_a_product_without_back_channel_logout_is_never_queued(team):
    """A sign-in record exists only for a product that takes back-channel logout;
    a hand-made one for a product that does not is still never queued."""
    from workeasy360.platform.ecosystem.backchannel import record_access_ended
    from workeasy360.platform.ecosystem.models import ProductIntegration

    plain = ProductIntegration.objects.get(product_code="crm")
    manifest = dict(plain.manifest)
    manifest.pop("backchannel_logout_uri", None)
    ProductIntegration.objects.filter(pk=plain.pk).update(manifest=manifest)
    assert (
        record_access_ended(
            user_ids=(team["owner"].pk,), product_codes=("crm",), reason="seat_released"
        )
        == 0
    )


def test_an_unknown_reason_is_refused():
    from workeasy360.contracts.ports.sign_ins import notify_access_ended

    with pytest.raises(ValueError):
        notify_access_ended(user_ids=(uuid.uuid4(),), product_codes=("crm",), reason="x")


@pytest.mark.django_db(transaction=True)
def test_simultaneous_reconciliations_queue_each_logout_once(team):
    """Two passes racing after the subscription ended (the change listener and
    the hourly pass): one logout per (product, person)."""
    from workeasy360.commercial.provisioning.services import reconcile
    from workeasy360.commercial.subscriptions.models import Subscription
    from workeasy360.platform.ecosystem.models import BackchannelLogout

    with in_scope(team["id"]):
        Subscription.unscoped.filter(organization_id=team["id"]).update(
            status="cancelled", cancel_at=timezone.now(), cancelled_at=timezone.now()
        )
    barrier = threading.Barrier(3)

    def run():
        barrier.wait()
        reconcile(team["id"])
        connection.close()

    threads = [threading.Thread(target=run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = list(
        BackchannelLogout.objects.filter(user_id=team["owner"].pk).values_list(
            "integration__product_code", flat=True
        )
    )
    assert sorted(rows) == ["books", "crm"]

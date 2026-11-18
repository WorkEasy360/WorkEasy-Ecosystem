"""Connected Logins (ADR-072): the durable per-(person, product,
organization) record, what it may truthfully say, and the per-product
sign-out.

The record is written in every mode of the platform (identity on or off): the
sign-ins below are made in single-host mode, then read on WorkEasy360
Accounts with ``W360_ACCOUNTS_IDENTITY`` on.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from tests.accounts_center.conftest import (
    ACCOUNT_API,
    BROWSER_AGENT,
    ask_about,
    sign_in_to_product,
)

pytestmark = pytest.mark.django_db

CONNECTIONS = f"{ACCOUNT_API}/connections"
#: What a product session must never be called from history.
NEVER = {"active", "online", "signed_in_now"}


@pytest.fixture
def crm_bcl(register_product, bcl_manifest):
    return register_product("crm", manifest=bcl_manifest("crm"))


@pytest.fixture
def signed_in_to_crm(signing_key, person, crm_bcl, password_login, product_client):
    """The person signed in to WorkEasy360 (a real password sign-in) and,
    from that sign-in, to CRM - which takes back-channel logout."""
    browser = password_login(Client(HTTP_USER_AGENT=BROWSER_AGENT), person["user"])
    claims = sign_in_to_product(
        browser, crm_bcl, person["organization_id"], product_client
    )
    return {**person, "browser": browser, "claims": claims, "crm": crm_bcl}


def _crm(body: dict) -> dict:
    (found,) = [p for p in body["products"] if p["product_code"] == "crm"]
    return found


# --- what is recorded -----------------------------------------------------------------


def test_a_product_sign_in_records_a_connection_and_a_history_entry(
    signed_in_to_crm, product_client
):
    from workeasy360.platform.ecosystem.models import ProductConnection
    from workeasy360.platform.identity.models import AccountEvent

    user = signed_in_to_crm["user"]
    row = ProductConnection.objects.get(user_id=user.pk)
    assert str(row.organization_id) == signed_in_to_crm["organization_id"]
    assert (row.sign_in_count, row.last_activity_at) == (1, None)
    assert signed_in_to_crm["claims"]["sub"] == str(user.pk)
    first = row.first_sign_in_at

    sign_in_to_product(
        signed_in_to_crm["browser"],
        signed_in_to_crm["crm"],
        signed_in_to_crm["organization_id"],
        product_client,
    )
    row.refresh_from_db()
    assert row.sign_in_count == 2 and row.first_sign_in_at == first
    assert row.last_sign_in_at > first

    events = AccountEvent.objects.filter(user_id=user.pk, kind="product_sign_in")
    assert events.count() == 2
    assert {(e.reason, e.device_label, e.ip_prefix) for e in events} == {
        ("crm", "Chrome on Windows", "127.0.0.0/24")
    }


def test_the_short_security_trail_reads_as_before(signed_in_to_crm):
    """Identity off is unchanged: product sign-ins are Login History only."""
    kinds = [
        e["kind"]
        for e in signed_in_to_crm["browser"].get("/api/v1/auth/activity").json()["events"]
    ]
    assert "sign_in" in kinds and "product_sign_in" not in kinds


def test_a_product_without_backchannel_logout_is_recorded_too(
    signing_key, person, sso_product, password_login, product_client
):
    from workeasy360.platform.ecosystem.models import ProductConnection, ProductSignIn

    crm = sso_product("crm")
    browser = password_login(Client(HTTP_USER_AGENT=BROWSER_AGENT), person["user"])
    sign_in_to_product(browser, crm, person["organization_id"], product_client)
    assert ProductConnection.objects.filter(user_id=person["user"].pk).count() == 1
    assert not ProductSignIn.objects.exists()


def test_an_entitlement_question_about_the_person_is_their_last_seen(signed_in_to_crm):
    from workeasy360.platform.ecosystem.models import ProductConnection

    user, crm = signed_in_to_crm["user"], signed_in_to_crm["crm"]
    org = signed_in_to_crm["organization_id"]
    assert ask_about(crm, org, str(user.pk))["decision"] == "allow"
    row = ProductConnection.objects.get(user_id=user.pk)
    seen = row.last_activity_at
    assert seen is not None

    # Kept to a resolution: a second question at once writes nothing.
    ask_about(crm, org, str(user.pk))
    row.refresh_from_db()
    assert row.last_activity_at == seen

    # A question about nobody, or about someone never connected, creates nothing.
    ask_about(crm, org, None)
    ask_about(crm, org, "00000000-0000-4000-8000-000000000000")
    ask_about(crm, org, "not-a-uuid")
    assert ProductConnection.objects.count() == 1


# --- what is shown --------------------------------------------------------------------


def test_connected_logins_say_what_is_known_and_never_active(
    signed_in_to_crm, accounts_on, on_accounts
):
    user = signed_in_to_crm["user"]
    ask_about(signed_in_to_crm["crm"], signed_in_to_crm["organization_id"], str(user.pk))
    accounts_on()
    body = on_accounts(user).get(CONNECTIONS).json()
    crm = _crm(body)
    assert body["subject"] == crm["subject"] == str(user.pk)
    assert crm["sso_connected"] is True and crm["can_sign_out"] is True
    assert crm["domain"] == {"host": "localhost", "verified": True}
    assert crm["last_sign_in_at"] and crm["last_activity_at"]
    # The WorkEasy360 sign-in that opened CRM is still open - which says
    # nothing about CRM's own session, and is never called "active".
    assert crm["session"]["platform_sign_in"] == "open"
    assert not NEVER & {str(v) for v in crm["session"].values()}
    (org,) = crm["organizations"]
    assert org["organization_id"] == signed_in_to_crm["organization_id"]
    assert org["member"] is True
    assert org["access"]["entitlement_status"] == "active"
    assert org["access"]["state"] == "active"  # the launcher's: seat assigned
    assert org["connection"]["sign_in_count"] == 1


def test_after_the_sign_in_ends_it_is_ended_not_active(
    signed_in_to_crm, accounts_on, on_accounts
):
    signed_in_to_crm["browser"].post("/api/v1/auth/logout")
    accounts_on()
    crm = _crm(on_accounts(signed_in_to_crm["user"]).get(CONNECTIONS).json())
    assert crm["session"]["platform_sign_in"] == "ended"
    assert crm["session"]["ended_at"] is not None
    assert crm["last_sign_out"]["status"] == "pending"  # owed, not yet confirmed
    assert crm["last_activity_at"] is None  # never asked: unknown, not invented


def test_a_product_that_is_held_but_never_opened_is_not_connected(
    signing_key, person, crm_bcl, accounts_on, on_accounts
):
    accounts_on()
    crm = _crm(on_accounts(person["user"]).get(CONNECTIONS).json())
    assert crm["sso_connected"] is False and crm["can_sign_out"] is False
    assert crm["session"]["platform_sign_in"] == "none"
    assert crm["last_sign_in_at"] is None
    (org,) = crm["organizations"]
    assert org["connection"] is None and org["access"]["entitlement_status"] == "active"


def test_one_persons_connections_are_not_anothers(
    signed_in_to_crm, user_factory, accounts_on, on_accounts, no_wait
):
    from workeasy360.platform.ecosystem.models import BackchannelLogout

    stranger = user_factory()
    accounts_on()
    theirs = on_accounts(stranger)
    body = theirs.get(CONNECTIONS).json()
    assert body["products"] == [] and body["subject"] == str(stranger.pk)
    assert str(signed_in_to_crm["user"].pk) not in str(body)
    refused = theirs.post(f"{CONNECTIONS}/crm/sign-out")
    assert refused.status_code == 404
    assert not BackchannelLogout.objects.exists()


def test_a_former_organization_is_not_named(signed_in_to_crm, accounts_on, on_accounts):
    from workeasy360.platform.ecosystem.models import ProductConnection

    user = signed_in_to_crm["user"]
    row = ProductConnection.objects.get(user_id=user.pk)
    ProductConnection.objects.create(
        user_id=user.pk,
        integration_id=row.integration_id,
        organization_id="11111111-1111-4111-8111-111111111111",
    )
    accounts_on()
    crm = _crm(on_accounts(user).get(CONNECTIONS).json())
    former = next(o for o in crm["organizations"] if not o["member"])
    assert (former["name"], former["slug"], former["access"]) == ("", "", None)


# --- sign out of one product ----------------------------------------------------------


def test_sign_out_of_one_product_is_one_logout_naming_the_person(
    signed_in_to_crm, accounts_on, on_accounts, no_wait
):
    from workeasy360.platform.ecosystem.models import BackchannelLogout, ProductSignIn
    from workeasy360.platform.identity.models import AccountEvent

    user = signed_in_to_crm["user"]
    accounts_on()
    response = on_accounts(user).post(f"{CONNECTIONS}/crm/sign-out")
    assert response.status_code == 200, response.content
    # Reported as it is: the worker has not delivered it yet.
    assert response.json() == {"product_code": "crm", "status": "pending"}
    (delivery,) = BackchannelLogout.objects.filter(user_id=user.pk)
    assert (delivery.sign_in_id, delivery.reason) == (None, "product_signed_out")
    assert not ProductSignIn.objects.filter(
        user_id=user.pk, ended_at__isnull=True
    ).exists()
    # The WorkEasy360 sign-in itself stays open: this is not sign out everywhere.
    from workeasy360.platform.identity.models import UserSession

    browser = signed_in_to_crm["browser"]
    assert UserSession.objects.filter(session_key=browser.session.session_key).exists()
    assert AccountEvent.objects.filter(
        user_id=user.pk, kind="product_signed_out", reason="crm"
    ).exists()


def test_a_delivered_product_sign_out_is_reported_delivered(
    signed_in_to_crm, accounts_on, on_accounts, no_wait, monkeypatch
):
    from workeasy360.integration.backchannel import delivery
    from workeasy360.integration.egress.client import EgressResponse

    monkeypatch.setattr(
        delivery,
        "send",
        lambda request, **kw: EgressResponse(
            status=200, headers={}, body=b"", truncated=False, duration_s=0.01
        ),
    )
    accounts_on()
    browser = on_accounts(signed_in_to_crm["user"])
    browser.post(f"{CONNECTIONS}/crm/sign-out")
    assert delivery.deliver_due()["delivered"] == 1
    crm = _crm(browser.get(CONNECTIONS).json())
    assert crm["last_sign_out"]["status"] == "delivered"
    assert crm["last_sign_out"]["by_this_product_sign_out"] is True
    assert crm["session"]["platform_sign_in"] == "ended"


def test_a_product_without_backchannel_logout_says_so(
    signing_key,
    person,
    sso_product,
    password_login,
    product_client,
    accounts_on,
    on_accounts,
):
    from workeasy360.platform.ecosystem.models import BackchannelLogout

    crm = sso_product("crm")
    browser = password_login(Client(HTTP_USER_AGENT=BROWSER_AGENT), person["user"])
    sign_in_to_product(browser, crm, person["organization_id"], product_client)
    accounts_on()
    me = on_accounts(person["user"])
    shown = _crm(me.get(CONNECTIONS).json())
    assert shown["can_sign_out"] is False
    assert shown["session"]["platform_sign_in"] == "untracked"
    assert me.post(f"{CONNECTIONS}/crm/sign-out").json()["status"] == "unsupported"
    assert not BackchannelLogout.objects.exists()


# --- retention ------------------------------------------------------------------------


def test_connections_end_after_the_retention_window(signed_in_to_crm):
    from workeasy360.platform.ecosystem import connections
    from workeasy360.platform.ecosystem.models import ProductConnection

    user = signed_in_to_crm["user"]
    assert connections.purge_stale() == 0
    ProductConnection.objects.filter(user_id=user.pk).update(
        last_seen_at=timezone.now() - timedelta(days=401)
    )
    assert connections.purge_stale() == 1
    assert not ProductConnection.objects.filter(user_id=user.pk).exists()


def test_connections_are_deleted_with_the_account(user_factory, crm_bcl):
    from workeasy360.platform.ecosystem.models import ProductConnection
    from workeasy360.platform.identity.models import User

    user = user_factory()
    ProductConnection.objects.create(
        user_id=user.pk,
        integration=crm_bcl["integration"],
        organization_id="11111111-1111-4111-8111-111111111111",
    )
    User.objects.filter(pk=user.pk).delete()
    assert not ProductConnection.objects.filter(user_id=user.pk).exists()

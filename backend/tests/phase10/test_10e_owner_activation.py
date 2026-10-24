"""Phase 10e: a partner-provisioned customer owner claims their account (D-020).

The journey these tests pin, through the real HTTP stack with CSRF enforced:

    provision -> one email with a one-time link -> preview -> set a password (or
    sign in, if the address already has one) -> the existing owner activation ->
    signed in as the owner of an organization that keeps working

and every way it must fail: the same link twice, an expired link, another
organization's link, another person's session, an account that already exists, a
weak password - none of which may change anything.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.phase7.support import sell
from tests.phase10.conftest import (
    MANAGE,
    PASSWORD,
    deliver_account_mail,
    post,
    token_from,
)
from workeasy360.commercial.partners import customers
from workeasy360.commercial.partners.models import (
    PartnerOrganizationLink,
    PartnerOwnerInvite,
)

pytestmark = pytest.mark.django_db

PREVIEW = "/api/v1/public/owner-invitations/preview"
ACCEPT = "/api/v1/public/owner-invitations/accept"
GOOD = "A long unusual passphrase 9271!"


# --- helpers --------------------------------------------------------------------


@pytest.fixture
def browser(anonymous):
    """A fresh anonymous browser, each on its own address so that one test's
    requests never count against another's rate limit."""
    numbers = itertools.count(1)

    def make():
        n = next(numbers)
        return anonymous(ip=f"10.20.{n // 250}.{n % 250 + 1}")

    return make


def mail_to(address: str) -> list:
    return [m for m in deliver_account_mail() if address in m.to]


@pytest.fixture
def journey(channel):
    """Provision a customer for a brand-new address and return what a person in
    that mailbox would have: the link from the one email they received."""

    def make(*, address: str | None = None, name: str = "Journey Ltd") -> dict:
        who = address or f"owner-{uuid.uuid4().hex[:10]}@customer-example.com"
        result = customers.provision_customer(
            channel["actor"](),
            organization_name=name,
            owner_email=who,
            idempotency_key=uuid.uuid4().hex,
        )
        sent = mail_to(who)
        assert len(sent) == 1, "exactly one email goes to the named owner"
        return {
            "email": who,
            "org": result.organization_id,
            "slug": result.slug,
            "message": sent[0],
            "token": token_from(sent[0]),
        }

    return make


def invite_of(org_id) -> PartnerOwnerInvite:
    return PartnerOwnerInvite.objects.get(organization_id=org_id)


def audit_of(resource_id, *fields):
    """The control-plane audit rows about one resource. The platform's own trail
    is row-level-secured like every tenant's, so it is read the way the platform
    reads it: inside the platform organization's scope."""
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    with (
        tenant_context(routing_context(PLATFORM_ORGANIZATION_ID)),
        organization_db_scope(PLATFORM_ORGANIZATION_ID),
    ):
        return list(
            AuditEvent.objects.filter(resource_id=str(resource_id)).values(*fields)
        )


def user_of(address: str):
    from workeasy360.platform.identity.models import User

    return User.objects.get(email=address)


def as_owner(browser, journey_data, *, name="Nadia Owner", password=GOOD):
    client = browser()
    response = post(
        client,
        ACCEPT,
        {"token": journey_data["token"], "full_name": name, "password": password},
    )
    return client, response


def code(response) -> str:
    return response.json()["code"]


# --- provisioning sends one secure link ---------------------------------------------------


def test_provisioning_emails_the_named_owner_one_link_and_no_password(journey):
    data = journey()
    message = data["message"]
    assert message.to == [data["email"]]
    assert "/owner-invitations/accept#token=" in message.body
    # The credential is in the fragment: never in a query string a server logs.
    assert "?token=" not in message.body and "&token=" not in message.body
    assert "password" not in message.body.lower().replace("choose a password", "")
    assert "Journey Ltd" in message.body
    assert data["token"].startswith(data["org"].hex + ".")


def test_only_a_hash_of_the_link_is_stored_and_the_email_context_is_wiped(journey):
    data = journey()
    row = invite_of(data["org"])
    assert row.token_hash == hashlib.sha256(data["token"].encode()).hexdigest()
    assert data["token"] not in row.token_hash
    assert row.status == "pending" and row.send_count == 1
    assert row.expires_at > timezone.now() + timedelta(days=13)

    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.integration.scheduling.services import routing_context
    from workeasy360.platform.notifications.models import Notification
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import organization_db_scope

    with (
        tenant_context(routing_context(PLATFORM_ORGANIZATION_ID)),
        organization_db_scope(PLATFORM_ORGANIZATION_ID),
    ):
        stored = list(
            Notification.objects.filter(
                template_key="platform.customer.owner_invited"
            ).values("context", "sealed_context", "idempotency_key")
        )
    assert stored, "the invitation was queued"
    blob = json.dumps(stored, default=str)
    assert data["token"] not in blob
    assert all(row["sealed_context"] == "" for row in stored), "wiped once delivered"


def test_the_link_is_not_in_the_audit_trail_or_the_logs(journey, caplog):
    with caplog.at_level("DEBUG"):
        data = journey()
    row = invite_of(data["org"])
    audited = json.dumps(audit_of(row.id, "changes", "action"), default=str)
    assert data["token"] not in audited and data["token"] not in caplog.text
    assert row.token_hash not in caplog.text


def test_a_second_provisioning_for_the_same_key_sends_no_second_email(channel):
    who = f"idem-{uuid.uuid4().hex[:8]}@customer-example.com"
    key = uuid.uuid4().hex
    for _ in range(2):
        customers.provision_customer(
            channel["actor"](),
            organization_name="Idem Ltd",
            owner_email=who,
            idempotency_key=key,
        )
    assert len(mail_to(who)) == 1


# --- previewing ---------------------------------------------------------------------------------


def test_the_preview_says_what_the_link_is_for_and_nothing_secret(journey, browser):
    data = journey()
    response = post(browser(), PREVIEW, {"token": data["token"]})
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["organization_name"] == "Journey Ltd"
    assert body["partner_name"]
    assert body["email"] == data["email"]
    assert body["account_exists"] is False and body["needs_name"] is True
    assert body["signed_in_as_invitee"] is False
    text = json.dumps(body)
    assert data["token"] not in text and invite_of(data["org"]).token_hash not in text
    assert str(user_of(data["email"]).id) not in text


@pytest.mark.parametrize(
    "token",
    ["", "x", "not.a.token", "a" * 300, None, 12345, ["a"], "0" * 32 + "." + "y" * 43],
)
def test_a_malformed_or_unknown_link_is_one_uniform_refusal(browser, token):
    response = post(browser(), PREVIEW, {"token": token})
    assert (response.status_code, code(response)) == (404, "invitation_invalid")


# --- claiming: a brand-new person ------------------------------------------------------------------


def test_a_new_owner_sets_a_password_and_is_signed_in_as_the_owner(journey, browser):
    data = journey()
    client, response = as_owner(browser, data)
    assert response.status_code == 200, response.content
    assert response.json() == {
        "organization_slug": data["slug"],
        "organization_id": str(data["org"]),
        "credentials_set": True,
    }
    session = client.get("/api/v1/auth/session").json()
    assert session["authenticated"] is True and session["email"] == data["email"]
    assert session["name"] == "Nadia Owner"

    user = user_of(data["email"])
    assert user.status == "active" and user.has_usable_password()
    assert user.check_password(GOOD)
    from workeasy360.platform.organizations.resolver import is_active_owner

    assert is_active_owner(user_id=user.id, organization_id=data["org"])
    orgs = client.get("/api/v1/organizations").json()["organizations"]
    assert [o["organization_id"] for o in orgs] == [str(data["org"])]
    assert orgs[0]["is_owner"] is True

    row = invite_of(data["org"])
    assert row.status == "accepted" and row.resolved_at is not None
    assert row.token_hash == "", "spent: nothing left that could match"


def test_the_new_owner_can_sign_in_again_with_the_password_they_chose(journey, browser):
    data = journey()
    as_owner(browser, data)
    again = browser()
    response = post(
        again, "/api/v1/auth/login", {"email": data["email"], "password": GOOD}
    )
    assert response.status_code == 200, response.content


def test_the_owner_gets_no_credentials_by_email(journey, browser):
    data = journey()
    as_owner(browser, data)
    everything = " ".join(m.body for m in mail_to(data["email"]))
    assert GOOD not in everything


def test_claiming_is_audited_without_the_secret(journey, browser):
    data = journey()
    as_owner(browser, data)
    row = invite_of(data["org"])
    [entry] = [
        e
        for e in audit_of(row.id, "action", "changes")
        if e["action"] == "partner.owner_activated"
    ]
    assert entry["changes"]["organization_id"] == str(data["org"])
    assert entry["changes"]["account_completed"] is True
    text = json.dumps(entry["changes"])
    assert GOOD not in text and data["token"] not in text


# --- claiming: every way it must fail ----------------------------------------------------------------


def test_the_same_link_cannot_be_used_twice(journey, browser):
    data = journey()
    first = as_owner(browser, data)[1]
    second = post(
        browser(),
        ACCEPT,
        {
            "token": data["token"],
            "full_name": "Someone Else",
            "password": "Another passphrase 1234!",
        },
    )
    assert first.status_code == 200
    assert (second.status_code, code(second)) == (404, "invitation_invalid")
    assert user_of(data["email"]).check_password(GOOD), "the second try changed nothing"
    assert post(browser(), PREVIEW, {"token": data["token"]}).status_code == 404, (
        "and a spent link previews as nothing"
    )


def test_an_expired_link_is_refused_and_changes_nothing(journey, browser):
    data = journey()
    PartnerOwnerInvite.objects.filter(organization_id=data["org"]).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert post(browser(), PREVIEW, {"token": data["token"]}).status_code == 404
    _client, response = as_owner(browser, data)
    assert (response.status_code, code(response)) == (404, "invitation_invalid")
    assert not user_of(data["email"]).has_usable_password()
    assert invite_of(data["org"]).status == "pending"


def test_a_link_for_another_organization_does_not_open_this_one(journey, browser):
    one, two = journey(name="One Ltd"), journey(name="Two Ltd")
    # One's secret behind Two's organization prefix, and the reverse.
    grafted = two["org"].hex + "." + one["token"].partition(".")[2]
    for token in (grafted, one["org"].hex + "." + two["token"].partition(".")[2]):
        assert post(browser(), PREVIEW, {"token": token}).status_code == 404
        response = post(
            browser(), ACCEPT, {"token": token, "full_name": "X Y", "password": GOOD}
        )
        assert (response.status_code, code(response)) == (404, "invitation_invalid")
    assert invite_of(one["org"]).status == "pending"
    assert invite_of(two["org"]).status == "pending"


def test_a_tampered_link_is_refused(journey, browser):
    data = journey()
    head, _dot, tail = data["token"].partition(".")
    flipped = tail[:-1] + ("A" if tail[-1] != "A" else "B")
    for token in (
        head + "." + flipped,
        head + "." + tail + "x",
        head + "." + tail.upper(),
    ):
        response = post(
            browser(), ACCEPT, {"token": token, "full_name": "X Y", "password": GOOD}
        )
        assert (response.status_code, code(response)) == (404, "invitation_invalid")
    assert invite_of(data["org"]).status == "pending"


def test_another_signed_in_person_cannot_use_the_link(
    journey, browser, user_factory, http_login
):
    data = journey()
    stranger = user_factory(password=PASSWORD)
    client = http_login(stranger)
    response = post(client, ACCEPT, {"token": data["token"]})
    assert (response.status_code, code(response)) == (403, "invitation_email_mismatch")
    assert invite_of(data["org"]).status == "pending"
    assert not user_of(data["email"]).has_usable_password()
    from workeasy360.platform.organizations.resolver import is_active_owner

    assert not is_active_owner(user_id=stranger.id, organization_id=data["org"]), (
        "and the stranger gained nothing"
    )


def test_an_account_that_can_already_sign_in_must_sign_in_not_set_a_password(
    journey, browser, user_factory
):
    existing = user_factory(password=PASSWORD)
    data = journey(address=existing.email)
    before = user_of(existing.email).password
    assert (
        post(browser(), PREVIEW, {"token": data["token"]}).json()["account_exists"]
        is True
    )
    response = post(
        browser(),
        ACCEPT,
        {"token": data["token"], "full_name": "Mallory", "password": GOOD},
    )
    assert (response.status_code, code(response)) == (409, "sign_in_required")
    assert user_of(existing.email).password == before, (
        "holding a link never resets a password"
    )
    assert invite_of(data["org"]).status == "pending"


def test_an_existing_person_signs_in_and_accepts_without_a_second_account(
    journey, browser, user_factory, http_login
):
    from workeasy360.platform.identity.models import User

    existing = user_factory(password=PASSWORD, full_name="Existing Person")
    data = journey(address=existing.email)
    client = http_login(existing)
    preview = post(client, PREVIEW, {"token": data["token"]}).json()
    assert preview["signed_in_as_invitee"] is True
    response = post(client, ACCEPT, {"token": data["token"]})
    assert response.status_code == 200, response.content
    assert response.json()["credentials_set"] is False
    assert User.objects.filter(email=existing.email).count() == 1
    orgs = client.get("/api/v1/organizations").json()["organizations"]
    assert str(data["org"]) in [o["organization_id"] for o in orgs]
    assert invite_of(data["org"]).status == "accepted"


def test_a_pending_self_signup_for_the_address_is_replaced_by_the_invitees_password(
    journey, browser, user_factory
):
    """Someone registered the address but nobody verified the mailbox. The link's
    holder has proven it, so their password wins - pre-registering an address
    must not let anyone else hold the account."""
    squatter = user_factory(
        password="the squatters passphrase 55!",
        status="pending",
        full_name="Squatter Name",
    )
    data = journey(address=squatter.email)
    preview = post(browser(), PREVIEW, {"token": data["token"]}).json()
    assert preview["account_exists"] is False, "a pending account cannot sign in"
    assert preview["needs_name"] is True, (
        "and its name was not chosen by the mailbox owner"
    )
    _client, response = as_owner(browser, data)
    assert response.status_code == 200, response.content
    user = user_of(squatter.email)
    assert user.status == "active"
    assert user.check_password(GOOD) and not user.check_password(
        "the squatters passphrase 55!"
    )
    assert user.full_name == "Nadia Owner", "the mailbox owner's name, not the squatter's"


def test_a_pending_accounts_old_name_cannot_be_kept_by_leaving_the_field_empty(
    journey, browser, user_factory
):
    squatter = user_factory(
        password="the squatters passphrase 55!",
        status="pending",
        full_name="Squatter Name",
    )
    data = journey(address=squatter.email)
    refused = post(
        browser(), ACCEPT, {"token": data["token"], "full_name": "", "password": GOOD}
    )
    assert refused.status_code == 400 and refused.json()["field"] == "full_name"
    assert invite_of(data["org"]).status == "pending"


def test_a_weak_password_is_refused_and_the_link_survives(journey, browser):
    data = journey()
    response = post(
        browser(),
        ACCEPT,
        {"token": data["token"], "full_name": "Nadia", "password": "short"},
    )
    assert response.status_code == 400, response.content
    assert not user_of(data["email"]).has_usable_password()
    assert invite_of(data["org"]).status == "pending"
    assert as_owner(browser, data)[1].status_code == 200, "the same link still works"


def test_a_name_is_required_when_the_account_has_none(journey, browser):
    data = journey()
    response = post(
        browser(), ACCEPT, {"token": data["token"], "full_name": "  ", "password": GOOD}
    )
    assert response.status_code == 400 and response.json()["field"] == "full_name"
    assert invite_of(data["org"]).status == "pending"


def test_a_suspended_organization_cannot_be_claimed(journey, browser):
    from workeasy360.platform.organizations.models import Organization
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    data = journey()
    with unscoped_access(reason="Test suspends the organization", module="tests"):
        Organization.objects.filter(id=data["org"]).update(status="suspended")
    _client, response = as_owner(browser, data)
    assert response.status_code == 402 and code(response) == "organization_not_writable"
    assert invite_of(data["org"]).status == "pending"
    assert not user_of(data["email"]).has_usable_password()


def test_a_disabled_account_cannot_be_claimed(journey, browser):
    data = journey()
    from workeasy360.platform.identity.models import User

    User.objects.filter(email=data["email"]).update(status="disabled")
    _client, response = as_owner(browser, data)
    assert (response.status_code, code(response)) == (404, "invitation_invalid")


def test_the_public_calls_need_a_csrf_token(journey, browser):
    from django.test import Client

    data = journey()
    bare = Client(enforce_csrf_checks=True)
    for url in (PREVIEW, ACCEPT):
        response = bare.post(
            url, json.dumps({"token": data["token"]}), content_type="application/json"
        )
        assert response.status_code == 403, url
    assert invite_of(data["org"]).status == "pending"


def test_the_public_calls_are_rate_limited_per_address(journey, anonymous, settings):
    from django.core.cache import cache

    cache.clear()
    settings.CONTROL_PLANE_RATE_LIMITS = {
        **settings.CONTROL_PLANE_RATE_LIMITS,
        "owner_invitation_public_ip": (3, 60),
    }
    data = journey()
    client = anonymous(ip="10.77.7.7")
    statuses = [
        post(client, PREVIEW, {"token": data["token"] + "x"}).status_code
        for _ in range(4)
    ]
    assert statuses == [404, 404, 404, 429]
    # Another address is not held up by it.
    assert (
        post(anonymous(ip="10.77.7.8"), PREVIEW, {"token": data["token"]}).status_code
        == 200
    )
    cache.clear()


def test_a_get_never_carries_or_accepts_the_link(journey, browser):
    data = journey()
    client = browser()
    for url in (PREVIEW, ACCEPT):
        assert client.get(url, {"token": data["token"]}).status_code == 405


# --- resending and withdrawing ----------------------------------------------------------------------------


@pytest.fixture
def partner_http(channel, http_login):
    """The provisioning partner's own signed-in administrator."""
    channel["user"].set_password(PASSWORD)
    channel["user"].save(update_fields=["password"])
    return http_login(channel["user"])


def resend_url(channel, org, *, staff=False):
    if staff:
        return f"/api/v1/admin/partners/{channel['partner'].id}/customers/{org}/owner-invitation/resend"
    return (
        f"/api/v1/partner/{channel['partner'].id}/customers/{org}/owner-invitation/resend"
    )


def revoke_url(channel, org, *, staff=False):
    return resend_url(channel, org, staff=staff).replace("/resend", "/revoke")


def test_a_resend_replaces_the_link_and_the_old_one_dies(
    channel, journey, browser, partner_http
):
    data = journey()
    response = post(partner_http, resend_url(channel, data["org"]))
    assert response.status_code == 200, response.content
    state = response.json()
    assert state["activation"] == "pending" and state["send_count"] == 2
    text = json.dumps(state)
    assert data["token"] not in text and "token" not in text.replace("send_count", "")

    fresh = [m for m in mail_to(data["email"]) if token_from(m) != data["token"]]
    assert len(fresh) == 1
    new_token = token_from(fresh[0])
    assert new_token != data["token"]
    assert post(browser(), PREVIEW, {"token": data["token"]}).status_code == 404
    assert post(browser(), PREVIEW, {"token": new_token}).status_code == 200
    assert as_owner(browser, {**data, "token": new_token})[1].status_code == 200


def test_a_revoked_link_stops_working_and_a_resend_revives_it(
    channel, journey, browser, partner_http
):
    data = journey()
    revoked = post(partner_http, revoke_url(channel, data["org"]))
    assert revoked.status_code == 200 and revoked.json()["activation"] == "revoked"
    assert invite_of(data["org"]).token_hash == ""
    assert post(browser(), PREVIEW, {"token": data["token"]}).status_code == 404
    again = post(partner_http, revoke_url(channel, data["org"]))
    assert again.status_code == 200, "withdrawing twice is the same outcome"

    assert post(partner_http, resend_url(channel, data["org"])).status_code == 200
    fresh = [m for m in mail_to(data["email"]) if token_from(m) != data["token"]][-1]
    assert as_owner(browser, {**data, "token": token_from(fresh)})[1].status_code == 200


def test_nothing_can_be_resent_or_withdrawn_once_the_owner_has_activated(
    channel, journey, browser, partner_http
):
    data = journey()
    as_owner(browser, data)
    for url in (resend_url(channel, data["org"]), revoke_url(channel, data["org"])):
        response = post(partner_http, url)
        assert response.status_code == 400, response.content
    assert invite_of(data["org"]).status == "accepted"


def test_another_partners_customer_is_a_404_not_a_403(
    channel, journey, partner_client, browser
):
    data = journey()
    other, other_fx = partner_client("customer.provision")
    url = f"/api/v1/partner/{other_fx['partner'].id}/customers/{data['org']}/owner-invitation/resend"
    assert post(other, url).status_code in (403, 404)
    assert invite_of(data["org"]).send_count == 1
    # And naming this partner in the path does not lend its authority.
    own_path = resend_url(channel, data["org"])
    assert post(other, own_path).status_code in (403, 404)
    assert invite_of(data["org"]).send_count == 1


def test_a_partner_without_the_provisioning_capability_cannot_resend(
    channel, journey, partner_factory, http_login
):
    data = journey()
    reader = partner_factory("pricing.view")
    reader["user"].set_password(PASSWORD)
    reader["user"].save(update_fields=["password"])
    client = http_login(reader["user"])
    response = post(
        client,
        f"/api/v1/partner/{reader['partner'].id}/customers/{data['org']}/owner-invitation/resend",
    )
    assert response.status_code in (403, 404)
    assert invite_of(data["org"]).send_count == 1


def test_a_partner_that_is_suspended_cannot_resend(channel, journey, partner_http):
    from workeasy360.commercial.partners import services
    from workeasy360.contracts.enums import PartnerStatus

    data = journey()
    services.set_partner_status(
        channel["staff_ctx"],
        channel["partner"].id,
        target=PartnerStatus.SUSPENDED,
        reason="Suspended for the resend test",
    )
    response = post(partner_http, resend_url(channel, data["org"]))
    assert response.status_code in (403, 404)
    assert invite_of(data["org"]).send_count == 1


def test_staff_may_resend_and_withdraw_and_it_is_audited(channel, journey, staff_client):
    data = journey()
    staff, _user = staff_client(MANAGE)
    sent = post(staff, resend_url(channel, data["org"], staff=True))
    assert sent.status_code == 200, sent.content
    assert sent.json()["send_count"] == 2
    revoked = post(staff, revoke_url(channel, data["org"], staff=True))
    assert revoked.json()["activation"] == "revoked"
    row = invite_of(data["org"])
    actions = {e["action"] for e in audit_of(row.id, "action")}
    assert {
        "partner.owner_invitation_sent",
        "partner.owner_invitation_revoked",
    } <= actions


def test_staff_need_the_manage_permission(channel, journey, staff_client):
    data = journey()
    viewer, _ = staff_client("platform.staff_partner.view")
    assert post(viewer, resend_url(channel, data["org"], staff=True)).status_code == 403
    assert invite_of(data["org"]).send_count == 1


def test_staff_naming_the_wrong_partner_finds_no_invitation(
    channel, journey, staff_client, partner_factory
):
    data = journey()
    other = partner_factory("customer.provision")
    staff, _ = staff_client(MANAGE)
    url = f"/api/v1/admin/partners/{other['partner'].id}/customers/{data['org']}/owner-invitation/resend"
    assert post(staff, url).status_code == 404
    unknown = f"/api/v1/admin/partners/{uuid.uuid4()}/customers/{data['org']}/owner-invitation/resend"
    assert post(staff, unknown).status_code == 404
    assert invite_of(data["org"]).send_count == 1


def test_a_row_from_before_links_existed_can_be_resent(
    channel, journey, browser, partner_http
):
    """Invitations created by earlier phases hold no link. Sending one is what
    gives them one - nothing is lost for having been provisioned first."""
    data = journey()
    PartnerOwnerInvite.objects.filter(organization_id=data["org"]).update(
        token_hash="", send_count=10, last_sent_at=None
    )
    assert post(browser(), PREVIEW, {"token": data["token"]}).status_code == 404
    assert post(partner_http, resend_url(channel, data["org"])).status_code == 200
    fresh = [m for m in mail_to(data["email"]) if token_from(m) != data["token"]][-1]
    assert as_owner(browser, {**data, "token": token_from(fresh)})[1].status_code == 200


# --- what the console shows -------------------------------------------------------------------------------


def customers_json(client, partner, **params):
    return client.get(f"/api/v1/admin/partners/{partner.id}/customers", params).json()


def test_the_console_shows_each_state_and_never_the_link(
    channel, journey, staff_client, browser, partner_http
):
    staff, _ = staff_client("platform.staff_partner.view")
    data = journey(name="States Ltd")

    def owner_state():
        rows = customers_json(staff, channel["partner"])["results"]
        row = next(r for r in rows if r["organization_id"] == str(data["org"]))
        return row["owner"], json.dumps(rows)

    owner, raw = owner_state()
    assert owner["activation"] == "pending" and owner["send_count"] == 1
    assert data["token"] not in raw and invite_of(data["org"]).token_hash not in raw

    PartnerOwnerInvite.objects.filter(organization_id=data["org"]).update(
        expires_at=timezone.now() - timedelta(hours=1)
    )
    assert owner_state()[0]["activation"] == "expired"

    assert post(partner_http, resend_url(channel, data["org"])).status_code == 200
    assert owner_state()[0]["activation"] == "pending"
    assert post(partner_http, revoke_url(channel, data["org"])).status_code == 200
    assert owner_state()[0]["activation"] == "revoked"

    assert post(partner_http, resend_url(channel, data["org"])).status_code == 200
    fresh = [m for m in mail_to(data["email"]) if token_from(m) != data["token"]][-1]
    as_owner(browser, {**data, "token": token_from(fresh)})
    owner, raw = owner_state()
    assert owner["activation"] == "activated"
    assert token_from(fresh) not in raw


def test_the_partner_portal_shows_the_state_but_not_the_address_or_the_link(
    channel, journey, partner_http
):
    data = journey()
    body = partner_http.get(f"/api/v1/partner/{channel['partner'].id}/customers").json()
    row = next(r for r in body["results"] if r["organization_id"] == str(data["org"]))
    assert row["owner_activation"]["activation"] == "pending"
    text = json.dumps(body)
    assert data["email"] not in text and data["token"] not in text


# --- customer independence -----------------------------------------------------------------------------------


def end_relationship(channel, org):
    """End the partner relationship the way staff do - through the service."""
    from workeasy360.commercial.partners import services
    from workeasy360.contracts.enums import PartnerLinkStatus

    link = PartnerOrganizationLink.objects.get(
        organization_id=org, partner_id=channel["partner"].id
    )
    services.change_link_status(
        channel["staff_ctx"],
        link.id,
        target=PartnerLinkStatus.ENDED,
        reason="Ending the relationship for the independence test",
    )
    return link


def test_ending_the_relationship_does_not_lock_the_owner_out_or_remove_products(
    channel, journey, browser, register_product
):
    from workeasy360.platform.entitlements.selectors import decision_for_organization

    register_product("crm")
    data = journey(name="Independent Ltd")
    sell(channel, data["org"])
    client, response = as_owner(browser, data)
    assert response.status_code == 200
    assert decision_for_organization(data["org"], "crm").allowed

    end_relationship(channel, data["org"])

    assert (
        not PartnerOrganizationLink.objects.filter(organization_id=data["org"])
        .exclude(status="ended")
        .exists()
    )
    again = browser()
    assert (
        post(
            again, "/api/v1/auth/login", {"email": data["email"], "password": GOOD}
        ).status_code
        == 200
    ), "the owner still signs in"
    orgs = again.get("/api/v1/organizations").json()["organizations"]
    assert str(data["org"]) in [o["organization_id"] for o in orgs]
    assert decision_for_organization(data["org"], "crm").allowed, "and keeps the product"
    apps = again.get(f"/api/v1/orgs/{data['slug']}/apps").json()["apps"]
    assert any(a["product_code"] == "crm" for a in apps)


def test_an_unclaimed_link_still_works_after_the_relationship_ends(
    channel, journey, browser
):
    """The owner may read the email a week after the partner has gone. What they
    are claiming is their own organization; the partner's relationship to it
    was never the source of that authority."""
    data = journey()
    end_relationship(channel, data["org"])
    _client, response = as_owner(browser, data)
    assert response.status_code == 200, response.content
    assert invite_of(data["org"]).status == "accepted"


def test_a_partner_that_has_gone_cannot_resend_to_a_customer_it_no_longer_has(
    channel, journey, partner_http
):
    data = journey()
    end_relationship(channel, data["org"])
    response = post(partner_http, resend_url(channel, data["org"]))
    assert response.status_code in (403, 404)
    assert invite_of(data["org"]).send_count == 1


def test_the_owner_launches_what_was_sold_to_the_organization_they_claimed(
    channel, journey, browser, register_product
):
    register_product("crm")
    data = journey()
    sell(channel, data["org"])
    client, response = as_owner(browser, data)
    assert response.status_code == 200
    apps = client.get(f"/api/v1/orgs/{data['slug']}/apps").json()["apps"]
    crm = next(a for a in apps if a["product_code"] == "crm")
    assert (
        crm["state"] in ("active", "no_seat", "seat_required")
        or crm["can_launch"] is not None
    )


# --- the client types match what the API returns ------------------------------------------------------


def test_the_activation_screens_types_match_the_api(
    channel, journey, browser, partner_http, staff_client
):
    from tests.phase10 import ts_contract as ts

    data = journey()
    client = browser()
    ts.assert_matches(
        post(client, PREVIEW, {"token": data["token"]}).json(),
        "OwnerInvitationPreview",
        "preview",
    )
    state = post(partner_http, resend_url(channel, data["org"])).json()
    ts.assert_matches(state, "OwnerInvitationState", "resend")

    portal = partner_http.get(f"/api/v1/partner/{channel['partner'].id}/customers").json()
    ts.assert_page(portal, "PartnerCustomerRow", "portal customers")
    ts.assert_matches(
        portal["results"][0]["owner_activation"],
        "PortalOwnerActivation",
        "owner_activation",
    )
    staff, _ = staff_client("platform.staff_partner.view")
    ts.assert_matches(
        customers_json(staff, channel["partner"])["results"][0]["owner"],
        "OwnerActivation",
        "owner",
    )
    fresh = [m for m in mail_to(data["email"]) if token_from(m) != data["token"]][-1]
    _client, claimed = as_owner(browser, {**data, "token": token_from(fresh)})
    ts.assert_matches(claimed.json(), "OwnerInvitationClaim", "claim")

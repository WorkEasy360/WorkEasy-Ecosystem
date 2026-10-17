"""The Notification Center (ADR-046): the audit log, shaped for the partner.

Pinned: nothing is stored twice, a person is told only what their permissions
allow, read-state is personal, and another partner's events can never appear.
"""

from __future__ import annotations

import uuid

import pytest

from tests.partner_menu.conftest import api, partner_audit, send
from workeasy360.commercial.controlplane import partner_help
from workeasy360.commercial.partners import services

pytestmark = pytest.mark.django_db

URL = "notifications"
SHAPE = {"id", "occurred_at", "title", "category", "important", "detail", "read"}


def feed(client, pid, view="all"):
    response = client.get(api(pid, URL) + f"?view={view}")
    assert response.status_code == 200, response.content
    return response.json()


def titles(body):
    return [i["title"] for i in body["items"]]


@pytest.fixture
def happenings(seller, owner_client):
    """A few real things that happened to the partner."""
    pid = seller["partner"].id
    services.grant_capability(
        seller["staff_ctx"],
        pid,
        capability="branding.manage",
        reason="Granted for the test",
    )
    send(
        owner_client,
        "post",
        api(pid, "members"),
        {"email": "new.hire@example.com", "role": "viewer"},
    )
    send(
        owner_client,
        "post",
        api(pid, "api-keys"),
        {"name": "Reporting", "scopes": ["account.read"]},
    )
    return pid


def test_the_feed_is_the_audit_log_worded_for_the_partner(
    seller, owner_client, happenings
):
    body = feed(owner_client, happenings)
    assert {"Access changed", "Team member invited", "API key created"} <= set(
        titles(body)
    )
    assert all(
        set(i) == SHAPE for i in body["items"]
    )  # the recorded changes are never echoed
    assert body["counts"]["all"] == len(body["items"])
    assert body["window"] == partner_help.WINDOW
    # Newest first.
    stamps = [i["occurred_at"] for i in body["items"]]
    assert stamps == sorted(stamps, reverse=True)


def test_nothing_is_copied_into_a_notification_table():
    from django.apps import apps

    names = {m.__name__ for m in apps.get_app_config("partners").get_models()}
    assert "PartnerNotification" not in names
    assert {"PartnerNotificationRead", "PartnerNotificationState"} <= names
    for model in (
        apps.get_model("partners", "PartnerNotificationRead"),
        apps.get_model("partners", "PartnerNotificationState"),
    ):
        fields = {f.name for f in model._meta.get_fields()}
        assert not fields & {
            "title",
            "body",
            "message",
            "context",
            "payload",
            "changes",
        }, model


def test_a_person_is_told_only_what_their_permissions_allow(
    seller, owner_client, member_client, happenings
):
    pid = happenings
    viewer, _user = member_client(seller, "viewer")
    seen = titles(feed(viewer, pid))
    assert (
        "Access changed" in seen
    )  # every member may be told the account's access changed
    assert (
        "Team member invited" not in seen
    )  # a security event is for those who run the team
    assert "API key created" not in seen  # and keys for those who run keys
    admin = titles(feed(owner_client, pid))
    assert {"Team member invited", "API key created"} <= set(admin)


def test_only_kinds_the_registry_names_can_ever_appear(seller, owner_client, happenings):
    pid = happenings
    # An export is audited, but it is not a notification kind: it never shows.
    owner_client.get(api(pid, "customers/export"))
    body = feed(owner_client, pid)
    assert all(i["title"] for i in body["items"])
    allowed = {k.title for k in partner_help.KINDS.values()}
    assert set(titles(body)) <= allowed
    assert "Partner customers exported" not in titles(body)


def test_another_partners_events_never_appear(
    seller, owner_client, partner_factory, happenings
):
    other = partner_factory("branding.manage")
    services.grant_capability(
        other["staff_ctx"],
        other["partner"].id,
        capability="customer.provision",
        reason="Granted for the test",
    )
    mine = feed(owner_client, happenings)
    theirs_ids = {i["id"] for i in mine["items"]}
    other_events = [e["id"] for e in partner_audit(other["partner"].id, "id")]
    assert other_events  # they did have events
    assert not theirs_ids & {str(e) for e in other_events}


def test_unread_important_and_all_views_agree_with_the_counts(
    seller, owner_client, happenings
):
    pid = happenings
    everything = feed(owner_client, pid, "all")
    unread = feed(owner_client, pid, "unread")
    important = feed(owner_client, pid, "important")
    assert (
        len(unread["items"])
        == unread["counts"]["unread"]
        == everything["counts"]["unread"]
    )
    assert all(i["important"] for i in important["items"])
    assert len(important["items"]) == everything["counts"]["important"]
    assert important["counts"]["important_unread"] <= important["counts"]["unread"]


def test_an_unknown_view_is_refused(seller, owner_client):
    assert (
        owner_client.get(api(seller["partner"].id, URL) + "?view=secret").status_code
        == 400
    )


# --- reading ---------------------------------------------------------------------------------


def test_marking_one_read_changes_only_that_one_and_only_for_that_person(
    seller, owner_client, member_client, happenings
):
    pid = happenings
    other, _user = member_client(seller, "admin")
    before = feed(owner_client, pid)
    target = before["items"][0]["id"]
    after = send(
        owner_client, "post", api(pid, "notifications/read"), {"ids": [target]}
    ).json()
    assert next(i for i in after["items"] if i["id"] == target)["read"] is True
    assert after["counts"]["unread"] == before["counts"]["unread"] - 1
    # A colleague has their own read-state.
    colleague = feed(other, pid)
    assert next(i for i in colleague["items"] if i["id"] == target)["read"] is False


def test_marking_all_read_covers_everything_so_far_and_not_what_comes_next(
    seller, owner_client, happenings
):
    pid = happenings
    assert feed(owner_client, pid)["counts"]["unread"] > 0
    cleared = send(
        owner_client, "post", api(pid, "notifications/read"), {"all": True}
    ).json()
    assert cleared["counts"]["unread"] == 0
    services.grant_capability(
        seller["staff_ctx"],
        pid,
        capability="domain.manage",
        reason="Granted for the test",
    )
    later = feed(owner_client, pid)
    assert later["counts"]["unread"] == 1
    assert later["items"][0]["read"] is False


def test_an_id_the_person_may_not_see_is_ignored_not_confirmed(
    seller, owner_client, member_client, happenings
):
    from workeasy360.commercial.partners.models import PartnerNotificationRead

    pid = happenings
    viewer, _user = member_client(seller, "viewer")
    hidden = next(
        i["id"]
        for i in feed(owner_client, pid)["items"]
        if i["title"] == "API key created"
    )
    foreign = str(uuid.uuid4())
    response = send(
        viewer, "post", api(pid, "notifications/read"), {"ids": [hidden, foreign]}
    )
    assert response.status_code == 200
    assert PartnerNotificationRead.objects.count() == 0


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"ids": []},
        {"ids": "x"},
        {"ids": ["not-a-uuid"]},
        {"ids": [str(uuid.uuid4())] * 51},
        {"all": "yes"},
    ],
)
def test_the_read_call_validates_what_it_is_given(seller, owner_client, body):
    assert (
        send(
            owner_client, "post", api(seller["partner"].id, "notifications/read"), body
        ).status_code
        == 400
    )


def test_reading_is_idempotent(seller, owner_client, happenings):
    pid = happenings
    target = feed(owner_client, pid)["items"][0]["id"]
    for _ in range(2):
        assert (
            send(
                owner_client, "post", api(pid, "notifications/read"), {"ids": [target]}
            ).status_code
            == 200
        )


def test_the_feed_is_bounded_and_does_not_grow_with_the_log(
    seller, owner_client, happenings
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    pid = happenings
    with CaptureQueriesContext(connection) as small:
        first = feed(owner_client, pid)
    for n in range(5):  # five more things happen
        send(
            owner_client,
            "post",
            api(pid, "api-keys"),
            {"name": f"more {n}", "scopes": ["account.read"]},
        )
    with CaptureQueriesContext(connection) as again:
        second = feed(owner_client, pid)
    assert len(again) == len(small)  # the same handful of queries, however long the log
    assert second["counts"]["all"] == first["counts"]["all"] + 5


def test_a_customer_or_a_stranger_cannot_read_a_partners_notifications(
    seller, console_org, login, anonymous
):
    pid = seller["partner"].id
    org = console_org()
    assert login(org["owner"]).get(api(pid, URL)).status_code == 404
    assert anonymous().get(api(pid, URL)).status_code in (401, 403)

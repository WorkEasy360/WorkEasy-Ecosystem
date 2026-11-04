"""The partner helpdesk (2026-11-03): upward tickets to Workeasy360, the
reseller's desk for its own customers, the SLA, saved replies - and isolation in
every direction: another partner, another customer, the asking side never
seeing an internal note."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from tests.partner_dashboard.conftest import api, send
from workeasy360.commercial.partners import services, tickets
from workeasy360.commercial.partners.models import PartnerTicket, PartnerTicketMessage

pytestmark = pytest.mark.django_db

STAFF = ("platform.staff_partner.view", "platform.staff_partner.manage")


def _open(client, partner_id, **extra):
    body = {
        "subject": "Commission missing",
        "message": "Order 42 earned nothing.",
        "category": "commission",
    }
    body.update(extra)
    return send(client, "post", api(partner_id, "tickets"), body)


def test_a_partner_opens_a_ticket_and_staff_answer_it(reseller, staff_client):
    client, fx = reseller
    pid = fx["partner"].id
    created = _open(client, pid, priority="urgent")
    assert created.status_code == 201, created.content
    ticket = created.json()
    assert ticket["reference"] == "#1" and ticket["status"] == "open"
    assert ticket["sla"]["state"] == "on_track"
    assert ticket["messages"][0]["mine"] is True

    staff, _user = staff_client(*STAFF)
    queue = send(staff, "get", "/api/v1/admin/partner-tickets").json()
    assert [i["id"] for i in queue["items"]] == [ticket["id"]]
    assert queue["items"][0]["partner_name"] == fx["partner"].display_name
    url = f"/api/v1/admin/partner-tickets/{ticket['id']}"
    send(staff, "post", url, {"message": "Checking the ledger.", "internal": True})
    answered = send(
        staff, "post", url, {"message": "Fixed - it shows now.", "status": "waiting"}
    ).json()
    assert answered["status"] == "waiting" and answered["first_response_at"]
    assert len(answered["messages"]) == 3

    seen = send(client, "get", api(pid, f"tickets/{ticket['id']}")).json()
    bodies = [m["body"] for m in seen["messages"]]
    assert "Checking the ledger." not in bodies  # the staff note stays with staff
    assert seen["messages"][-1]["author"] == "Workeasy360 Support"
    # The partner's reply reopens a waiting ticket; it may close it, not resolve it.
    again = send(
        client, "post", api(pid, f"tickets/{ticket['id']}"), {"message": "Thanks"}
    ).json()
    assert again["status"] == "open"
    refused = send(
        client, "post", api(pid, f"tickets/{ticket['id']}"), {"status": "resolved"}
    )
    assert refused.status_code == 400
    closed = send(
        client, "post", api(pid, f"tickets/{ticket['id']}"), {"status": "closed"}
    ).json()
    assert closed["status"] == "closed" and closed["closed_at"]
    late = send(
        client, "post", api(pid, f"tickets/{ticket['id']}"), {"message": "One more"}
    )
    assert late.status_code == 409


def test_another_partner_cannot_read_or_answer_the_ticket(reseller, partner_client):
    client, fx = reseller
    ticket = _open(client, fx["partner"].id).json()
    other, ofx = partner_client("referral.view", "referral.create")
    assert (
        send(other, "get", api(ofx["partner"].id, f"tickets/{ticket['id']}")).status_code
        == 404
    )
    assert send(
        other, "get", api(fx["partner"].id, f"tickets/{ticket['id']}")
    ).status_code in (403, 404)
    listed = send(other, "get", api(ofx["partner"].id, "tickets")).json()
    assert listed["items"] == []


def test_an_affiliate_can_raise_a_ticket_but_has_no_desk(partner_client):
    client, fx = partner_client("referral.view", "referral.create", "commission.view")
    assert _open(client, fx["partner"].id).status_code == 201
    desk = send(client, "get", api(fx["partner"].id, "tickets") + "?direction=customer")
    assert desk.status_code == 403


def test_the_sla_reads_breached_once_a_deadline_passes(reseller):
    client, fx = reseller
    ticket = _open(client, fx["partner"].id, priority="high").json()
    PartnerTicket.objects.filter(id=ticket["id"]).update(
        first_response_due_at=timezone.now() - timedelta(minutes=5)
    )
    body = send(client, "get", api(fx["partner"].id, "tickets")).json()
    assert body["items"][0]["sla"]["first_response"] == "breached"
    dash = send(client, "get", api(fx["partner"].id, "dashboard")).json()
    assert dash["tickets"] == {"open": 1, "breached": 1}


def test_bad_input_is_refused(reseller):
    client, fx = reseller
    pid = fx["partner"].id
    assert _open(client, pid, subject="").status_code == 400
    assert _open(client, pid, subject="<script>").status_code == 400
    assert _open(client, pid, priority="asap").status_code == 400
    assert _open(client, pid, category="gossip").status_code == 400
    assert _open(client, pid, related_ref="commission:not-a-uuid").status_code == 400
    assert (
        _open(
            client, pid, related_ref="user:00000000-0000-0000-0000-000000000000"
        ).status_code
        == 400
    )
    assert PartnerTicket.objects.count() == 0


def test_the_words_never_reach_the_audit_log(reseller):
    from tests.partner_panel.conftest import partner_audit

    client, fx = reseller
    _open(client, fx["partner"].id, message="secret-words-xyz")
    rows = partner_audit(fx["partner"].id, "action", "changes")
    assert any(r["action"] == "partner.ticket_opened" for r in rows)
    assert "secret-words-xyz" not in str(rows)


# --- the customer desk -------------------------------------------------------------------------


def _desk(console):
    services.grant_capability(
        console["staff_ctx"],
        console["reseller"]["partner"].id,
        capability="support.access",
        reason="Runs a desk",
    )


def test_a_customer_without_a_desk_partner_has_no_provider(console, customer):
    client, slug = customer
    body = send(client, "get", f"/api/v1/orgs/{slug}/provider-tickets").json()
    assert body["provider"] is None
    refused = send(
        client,
        "post",
        f"/api/v1/orgs/{slug}/provider-tickets",
        {"subject": "Hi", "message": "Help"},
    )
    assert refused.status_code == 400


def test_a_customer_asks_its_partner_and_the_desk_answers(console, customer, reseller):
    _desk(console)
    cclient, slug = customer
    pclient, fx = reseller
    pid = fx["partner"].id
    overview = send(cclient, "get", f"/api/v1/orgs/{slug}/provider-tickets").json()
    assert overview["provider"] == {"name": fx["partner"].display_name}
    made = send(
        cclient,
        "post",
        f"/api/v1/orgs/{slug}/provider-tickets",
        {"subject": "Cannot log in", "message": "Since today."},
    )
    assert made.status_code == 201, made.content
    ticket = made.json()

    desk = send(pclient, "get", api(pid, "tickets") + "?direction=customer").json()
    assert [i["id"] for i in desk["items"]] == [ticket["id"]]
    assert desk["items"][0]["customer"] == "Console Customer"
    url = api(pid, f"tickets/{ticket['id']}")
    send(pclient, "post", url, {"message": "Internal: check SSO", "internal": True})
    send(
        pclient,
        "post",
        url,
        {"message": "Reset your password, please.", "status": "resolved"},
    )
    mine = send(
        cclient, "get", f"/api/v1/orgs/{slug}/provider-tickets/{ticket['id']}"
    ).json()
    assert [m["body"] for m in mine["messages"]] == [
        "Since today.",
        "Reset your password, please.",
    ]
    assert mine["status"] == "resolved"
    # The desk's own view keeps the note.
    assert len(send(pclient, "get", url).json()["messages"]) == 3
    # The customer's reply reopens it.
    again = send(
        cclient,
        "post",
        f"/api/v1/orgs/{slug}/provider-tickets/{ticket['id']}",
        {"message": "Still broken"},
    )
    assert again.json()["status"] == "open"


def test_a_desk_member_without_manage_reads_but_cannot_answer(
    console, customer, member_client
):
    _desk(console)
    cclient, slug = customer
    ticket = send(
        cclient,
        "post",
        f"/api/v1/orgs/{slug}/provider-tickets",
        {"subject": "Q", "message": "A question"},
    ).json()
    viewer, _u = member_client(console["reseller"], "viewer")
    pid = console["reseller"]["partner"].id
    assert send(viewer, "get", api(pid, f"tickets/{ticket['id']}")).status_code == 200
    answer = send(
        viewer, "post", api(pid, f"tickets/{ticket['id']}"), {"message": "Hello"}
    )
    assert answer.status_code == 403
    assert PartnerTicketMessage.objects.filter(ticket_id=ticket["id"]).count() == 1


def test_a_customer_cannot_reach_a_partner_ticket_or_another_organizations(
    console, customer, reseller
):
    _desk(console)
    cclient, slug = customer
    pclient, fx = reseller
    upward = _open(pclient, fx["partner"].id).json()
    assert (
        send(
            cclient, "get", f"/api/v1/orgs/{slug}/provider-tickets/{upward['id']}"
        ).status_code
        == 404
    )
    other = PartnerTicket.objects.create(
        partner_id=fx["partner"].id,
        number=99,
        direction="customer",
        organization_id=fx["partner"].id,  # any other organization id
        subject="Not yours",
        opened_by_id=fx["user"].id,
        opened_at=timezone.now(),
        updated_at=timezone.now(),
        first_response_due_at=timezone.now(),
        resolve_due_at=timezone.now(),
    )
    assert (
        send(
            cclient, "get", f"/api/v1/orgs/{slug}/provider-tickets/{other.id}"
        ).status_code
        == 404
    )
    listed = send(cclient, "get", f"/api/v1/orgs/{slug}/provider-tickets").json()
    assert all(i["id"] != str(other.id) for i in listed["items"])


def test_saved_replies_are_the_desks_own(console, reseller, partner_client):
    _desk(console)
    client, fx = reseller
    pid = fx["partner"].id
    made = send(
        client,
        "post",
        api(pid, "canned-responses"),
        {"title": "Password reset", "body": "Use Forgot password."},
    )
    assert made.status_code == 201, made.content
    dup = send(
        client,
        "post",
        api(pid, "canned-responses"),
        {"title": "Password reset", "body": "Again"},
    )
    assert dup.status_code == 400
    rid = made.json()["id"]
    assert (
        send(
            client,
            "put",
            api(pid, f"canned-responses/{rid}"),
            {"title": "Reset", "body": "New"},
        ).status_code
        == 200
    )
    other, ofx = partner_client("support.access", "customer.manage")
    assert (
        send(
            other, "delete", api(ofx["partner"].id, f"canned-responses/{rid}")
        ).status_code
        == 404
    )
    assert send(client, "delete", api(pid, f"canned-responses/{rid}")).status_code == 204
    assert send(client, "get", api(pid, "canned-responses")).json()["items"] == []


def test_the_ticket_number_counts_per_partner(reseller, partner_client):
    client, fx = reseller
    _open(client, fx["partner"].id)
    second = _open(client, fx["partner"].id).json()
    other, ofx = partner_client("referral.view")
    first_elsewhere = _open(other, ofx["partner"].id).json()
    assert second["number"] == 2 and first_elsewhere["number"] == 1
    assert tickets.staff_open_count() == 3

"""Staff operations on a customer: subscription administration, entitlement
overrides, the dashboard and the audit trail - through the real HTTP stack."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.phase7.support import provision, sell

pytestmark = pytest.mark.django_db


def send(client, url, body=None, *, key=True):
    extra = {"HTTP_IDEMPOTENCY_KEY": uuid.uuid4().hex} if key else {}
    return client.post(
        url, json.dumps(body or {}), content_type="application/json", **extra
    )


@pytest.fixture
def customer(channel):
    from types import SimpleNamespace

    result, _owner = provision(channel)
    item = sell(channel, result.organization_id)
    return {
        "org": result.organization_id,
        "item": item,
        "subscription": SimpleNamespace(id=item.subscription_id),
    }


def _allowed(org, product="crm", *, write=False):
    from workeasy360.platform.entitlements.selectors import decision_for_organization

    return decision_for_organization(org, product, write=write).allowed


# --- subscription administration -----------------------------------------------------------


def test_staff_suspend_and_resume_change_access_and_are_audited(customer, staff_client):
    staff, user = staff_client(
        "platform.staff_subscription.manage", "platform.staff_audit.view"
    )
    org, sub = customer["org"], customer["subscription"]
    base = f"/api/v1/admin/customers/{org}/subscriptions/{sub.id}"
    assert _allowed(org, write=True)

    suspended = send(
        staff, f"{base}/suspend", {"reason": "Investigating a chargeback dispute"}
    )
    assert suspended.status_code in (200, 201), suspended.content
    # A suspended subscription keeps read access (the customer can still see and
    # export their data) but loses the right to write.
    assert _allowed(org) and not _allowed(org, write=True), "suspension must block writes"

    resumed = send(
        staff, f"{base}/resume", {"reason": "Dispute resolved in customer's favour"}
    )
    assert resumed.status_code in (200, 201), resumed.content
    assert _allowed(org, write=True)

    rows = staff.get("/api/v1/admin/audit", {"action": "staff.", "limit": 100}).json()[
        "results"
    ]
    actions = {row["action"] for row in rows}
    assert any("suspend" in a for a in actions) and any("resume" in a for a in actions), (
        actions
    )
    assert all(row["actor_kind"] == "staff" for row in rows)


def test_a_suspension_needs_a_real_reason_and_the_permission(customer, staff_client):
    org, sub = customer["org"], customer["subscription"]
    url = f"/api/v1/admin/customers/{org}/subscriptions/{sub.id}/suspend"

    staff, _u = staff_client("platform.staff_subscription.manage")
    assert send(staff, url, {"reason": "short"}).status_code == 400
    assert send(staff, url, {}).status_code == 400

    reader, _u = staff_client("platform.staff_customer.view")
    assert (
        send(reader, url, {"reason": "Long enough reason to be recorded"}).status_code
        == 403
    )
    assert _allowed(org, write=True), "no refused attempt changed anything"


def test_a_replayed_staff_command_is_one_change_and_a_stale_version_is_a_conflict(
    customer, staff_client
):
    staff, _u = staff_client("platform.staff_subscription.manage")
    org, sub = customer["org"], customer["subscription"]
    url = f"/api/v1/admin/customers/{org}/subscriptions/{sub.id}/suspend"
    key = uuid.uuid4().hex
    body = json.dumps({"reason": "Investigating a payment dispute"})
    first = staff.post(
        url, body, content_type="application/json", HTTP_IDEMPOTENCY_KEY=key
    )
    again = staff.post(
        url, body, content_type="application/json", HTTP_IDEMPOTENCY_KEY=key
    )
    assert first.status_code in (200, 201) and again.status_code in (200, 201)

    stale = send(
        staff,
        f"/api/v1/admin/customers/{org}/subscriptions/{sub.id}/resume",
        {"reason": "A stale screen tries to resume", "expected_version": 0},
    )
    assert stale.status_code == 409


def test_staff_change_quantity_is_bounded(customer, staff_client):
    staff, _u = staff_client("platform.staff_subscription.manage")
    org, item = customer["org"], customer["item"]
    url = f"/api/v1/admin/customers/{org}/items/{item.id}/change-quantity"
    for bad in (0, -1, 10**9, "3", True):
        response = send(
            staff, url, {"quantity": bad, "reason": "Adjusting to what was ordered"}
        )
        assert 400 <= response.status_code < 500, (bad, response.status_code)
    assert send(
        staff, url, {"quantity": 3, "reason": "Customer added two colleagues"}
    ).status_code in (200, 201)


def test_staff_cannot_reach_another_customers_subscription_through_a_url(
    customer, staff_client, channel
):
    """The subscription id must belong to the organization in the path."""
    other, _o = provision(channel, name="Other Co")
    sell(channel, other.organization_id)
    staff, _u = staff_client("platform.staff_subscription.manage")
    response = send(
        staff,
        f"/api/v1/admin/customers/{other.organization_id}/subscriptions/{customer['subscription'].id}/suspend",
        {"reason": "Wrong organization in the path"},
    )
    assert response.status_code == 404
    assert _allowed(customer["org"], write=True), (
        "the mismatched request suspended nothing"
    )


# --- entitlement overrides -----------------------------------------------------------------------


def _override(staff, org, **overrides):
    body = {
        "product_code": "books",
        "reason": "Sales promised a pilot of Books",
        "expires_at": (timezone.now() + timedelta(days=30)).isoformat(),
        **overrides,
    }
    return send(
        staff, f"/api/v1/admin/customers/{org}/entitlement-overrides", body, key=False
    )


def test_an_override_grants_access_until_it_is_revoked_and_survives_a_rebuild(
    customer, staff_client
):
    from workeasy360.commercial.provisioning.services import reconcile

    staff, _u = staff_client(
        "platform.staff_subscription.manage", "platform.staff_customer.view"
    )
    org = customer["org"]
    assert not _allowed(org, "books")

    created = _override(staff, org)
    assert created.status_code == 201, created.content
    grant_id = created.json()["id"]
    assert _allowed(org, "books"), "the override must take effect immediately"

    reconcile(org)  # a rebuild from subscriptions must not erase a live override
    assert _allowed(org, "books")

    detail = staff.get(f"/api/v1/admin/customers/{org}").json()
    assert [o["id"] for o in detail["entitlement_overrides"]] == [grant_id]

    revoked = send(
        staff,
        f"/api/v1/admin/customers/{org}/entitlement-overrides/{grant_id}/revoke",
        {"reason": "The pilot ended"},
    )
    assert revoked.status_code == 200, revoked.content
    assert not _allowed(org, "books")


def test_an_override_is_bounded_in_time_and_needs_a_reason(customer, staff_client):
    staff, _u = staff_client("platform.staff_subscription.manage")
    org = customer["org"]
    past = (timezone.now() - timedelta(days=1)).isoformat()
    too_long = (timezone.now() + timedelta(days=91)).isoformat()
    assert _override(staff, org, expires_at=past).status_code == 400
    assert _override(staff, org, expires_at=too_long).status_code == 400
    assert _override(staff, org, reason="short").status_code == 400
    # Exactly one of a product or a feature.
    assert (
        _override(staff, org, product_code="books", feature_key="pipelines").status_code
        == 400
    )
    assert _override(staff, org, product_code="", feature_key="").status_code == 400
    assert not _allowed(org, "books")


def test_an_override_never_leaks_to_another_customer(customer, staff_client, channel):
    other, _o = provision(channel, name="Bystander Co")
    staff, _u = staff_client("platform.staff_subscription.manage")
    assert _override(staff, customer["org"]).status_code == 201
    assert _allowed(customer["org"], "books")
    assert not _allowed(other.organization_id, "books")


def test_an_override_needs_the_subscription_permission(customer, staff_client):
    org = customer["org"]
    reader, _u = staff_client("platform.staff_customer.view")
    assert _override(reader, org).status_code == 403
    assert not _allowed(org, "books")


# --- the dashboard and the audit trail -----------------------------------------------------------


def test_the_dashboard_counts_what_exists_and_hides_what_the_caller_may_not_see(
    customer, staff_client
):
    narrow, _u = staff_client("platform.staff_customer.view")
    body = narrow.get("/api/v1/admin/dashboard").json()
    assert body["organizations"].get("active", 0) >= 1
    assert body["subscriptions"].get("active", 0) >= 1
    assert body["mrr"]["available"] is False and body["mrr"]["reason"]
    assert "partners" not in body and "pending_payouts" not in body

    wide, _u = staff_client(
        "platform.staff_customer.view",
        "platform.staff_partner.view",
        "platform.staff_commission.view",
    )
    body = wide.get("/api/v1/admin/dashboard").json()
    assert "partners" in body and "pending_payouts" in body


def test_the_audit_page_is_permissioned_filterable_and_paginated(customer, staff_client):
    staff, _u = staff_client(
        "platform.staff_subscription.manage", "platform.staff_audit.view"
    )
    org, sub = customer["org"], customer["subscription"]
    send(
        staff,
        f"/api/v1/admin/customers/{org}/subscriptions/{sub.id}/suspend",
        {"reason": "Audit page fixture suspension"},
    )
    page = staff.get("/api/v1/admin/audit", {"limit": 1}).json()
    assert len(page["results"]) == 1 and page["has_more"] is True and page["next_cursor"]
    following = staff.get(
        "/api/v1/admin/audit", {"limit": 1, "cursor": page["next_cursor"]}
    ).json()
    assert (
        following["results"] and following["results"][0]["id"] != page["results"][0]["id"]
    )

    filtered = staff.get("/api/v1/admin/audit", {"action": "staff."}).json()["results"]
    assert filtered and all(row["action"].startswith("staff.") for row in filtered)

    nobody, _u = staff_client("platform.staff_customer.view")
    assert nobody.get("/api/v1/admin/audit").status_code == 403

"""The partner's Overview (setup progress and the readiness to activate) and its
Activity feed (who did what, in words, without leaking a value)."""

from __future__ import annotations

import pytest

from tests.partner_setup.conftest import profile_url, put
from workeasy360.commercial.controlplane import partner_detail, readiness
from workeasy360.commercial.partners import services
from workeasy360.contracts.enums import PartnerStatus

pytestmark = pytest.mark.django_db

READY_PROFILE = {
    "business": {"business_email": "hello@acme.example", "country": "GB"},
    "contacts": {"primary": {"name": "Priya Shah", "email": "priya@acme.example"}},
}


def summary(client, partner_id):
    response = client.get(f"/api/v1/admin/partners/{partner_id}/summary")
    assert response.status_code == 200, response.content
    return response.json()


def steps(body):
    return {s["key"]: s for s in body["setup_progress"]}


def missing(body):
    return {m["key"] for m in body["activation"]["missing"]}


# --- the readiness rule ---------------------------------------------------------------------------------------


def test_a_bare_draft_lists_exactly_what_is_missing(manager, draft_partner):
    body = summary(manager, draft_partner.id)
    assert body["activation"]["can_activate"] is False
    assert missing(body) == {
        "primary_contact",
        "business_email",
        "capabilities",
        "administrator",
    }
    assert all(m["label"] and m["detail"] for m in body["activation"]["missing"])


def test_saving_the_profile_removes_the_profile_gaps(manager, draft_partner):
    put(
        manager,
        profile_url(draft_partner.id),
        {**READY_PROFILE, "expected_version": draft_partner.version},
    )
    body = summary(manager, draft_partner.id)
    assert {"primary_contact", "business_email"}.isdisjoint(missing(body))
    assert steps(body)["profile"]["state"] == "complete"
    assert steps(body)["profile"]["label"] == "Business profile"


def test_the_profile_step_says_which_part_is_missing(manager, draft_partner, console):
    services.update_partner(
        console["staff_ctx"],
        draft_partner.id,
        expected_version=0,
        contact_name="A",
        contact_email="a@example.com",
    )
    body = summary(manager, draft_partner.id)
    assert steps(body)["profile"]["state"] == "incomplete"
    assert steps(body)["profile"]["detail"] == "Add a business email."


def test_a_complete_partner_can_be_activated(manager, console, partner_factory):
    ready = partner_factory("customer.provision", "subscription.sell")
    services.grant_product(ready["staff_ctx"], ready["partner"].id, product_code="crm")
    put(
        manager,
        profile_url(ready["partner"].id),
        {**READY_PROFILE, "expected_version": ready["partner"].version},
    )
    body = summary(manager, ready["partner"].id)
    assert body["activation"] == {"can_activate": True, "missing": []}


def test_a_seller_with_no_product_is_not_ready_but_a_viewer_of_commission_is(
    manager, partner_factory
):
    seller = partner_factory("subscription.sell")
    put(
        manager,
        profile_url(seller["partner"].id),
        {**READY_PROFILE, "expected_version": seller["partner"].version},
    )
    assert missing(summary(manager, seller["partner"].id)) == {"products"}
    watcher = partner_factory("commission.view")
    put(
        manager,
        profile_url(watcher["partner"].id),
        {**READY_PROFILE, "expected_version": watcher["partner"].version},
    )
    assert summary(manager, watcher["partner"].id)["activation"]["can_activate"] is True


def test_the_review_and_the_overview_apply_the_same_rule():
    base = {
        "contact_name": "P",
        "contact_email": "p@example.com",
        "business_email": "b@example.com",
        "held": {"subscription.sell"},
        "product_count": 0,
        "has_administrator": True,
    }
    assert [m["key"] for m in readiness.evaluate(**base)["missing"]] == ["products"]
    assert readiness.evaluate(**{**base, "product_count": 1})["can_activate"] is True
    assert (
        readiness.evaluate(**{**base, "held": set(), "has_administrator": False})[
            "missing"
        ][0]["key"]
        == "capabilities"
    )


def test_the_setup_progress_uses_the_briefs_words(manager, console):
    body = summary(manager, console["reseller"]["partner"].id)
    assert [s["label"] for s in body["setup_progress"]] == [
        "Business profile",
        "Capabilities",
        "Products",
        "Pricing",
        "Commission",
        "Admin user",
        "White-label",
        "Domain",
    ]


def test_activation_is_still_refused_without_an_administrator(draft_partner, console):
    with pytest.raises(Exception, match="administrator"):
        services.set_partner_status(
            console["staff_ctx"],
            draft_partner.id,
            target=PartnerStatus.ACTIVE,
            reason="Trying to go live",
        )


# --- the activity feed ----------------------------------------------------------------------------------------


def activity(client, partner_id):
    response = client.get(f"/api/v1/admin/partners/{partner_id}/activity")
    assert response.status_code == 200, response.content
    return response.json()["results"]


@pytest.fixture
def auditor(staff_client):
    client, user = staff_client(
        "platform.staff_partner.manage", "platform.staff_audit.view"
    )
    return client, user


def test_each_entry_has_a_label_an_actor_a_target_and_a_reason(auditor, draft_partner):
    client, user = auditor
    put(
        client,
        profile_url(draft_partner.id),
        {**READY_PROFILE, "expected_version": draft_partner.version},
    )
    entries = activity(client, draft_partner.id)
    updated = next(e for e in entries if e["action"] == "partner.profile_updated")
    assert updated["label"] == "Business details or contacts changed"
    assert updated["actor"] == user.email
    assert updated["target"] == "business, contacts"
    assert {
        "id",
        "occurred_at",
        "action",
        "label",
        "actor",
        "actor_kind",
        "target",
        "outcome",
        "reason",
    } <= set(updated)


def test_the_recorded_changes_name_fields_and_never_hold_a_value(auditor, draft_partner):
    client, _user = auditor
    put(
        client,
        profile_url(draft_partner.id),
        {**READY_PROFILE, "expected_version": draft_partner.version},
    )
    body = str(activity(client, draft_partner.id))
    for secret in ("hello@acme.example", "priya@acme.example", "Priya Shah"):
        assert secret not in body


def test_the_whole_setup_story_reads_in_words(auditor, channel):
    from workeasy360.commercial.partners import branding, domains
    from workeasy360.commercial.partners.models import PartnerProductGrant

    client, _user = auditor
    ctx, pid = channel["staff_ctx"], channel["partner"].id
    services.enable_white_label(ctx, pid, reason="Enabled for the story")
    branding.update_brand(ctx, partner_id=pid, brand_name="Story Brand")
    domains.request_domain(ctx, hostname="app.story-example.com", partner_id=pid)
    grant = PartnerProductGrant.objects.get(partner_id=pid, status="active")
    services.update_product_grant(ctx, pid, grant_id=grant.id, trial_allowed=False)
    labels = {e["label"] for e in activity(client, pid)}
    assert {
        "Partner created",
        "Partner activated",
        "Capability granted",
        "White-label enabled",
        "Brand changed",
        "Domain requested",
        "Product options changed",
        "Product granted",
    } <= labels


def test_a_capability_entry_names_the_capability_in_words(auditor, channel):
    client, _user = auditor
    entry = next(
        e
        for e in activity(client, channel["partner"].id)
        if e["action"] == "partner.capability_granted"
    )
    assert entry["target"] in {
        "Create customer organizations",
        "Sell approved products and plans to related customers",
        "Manage the commercial setup of related customers",
        "Change, cancel and activate related customers' subscriptions",
        "Earn commission on attributed sales",
        "See the prices approved for this partner",
        "View earned commission",
        "View payouts",
    }
    assert entry["reason"]


def test_an_action_with_no_label_still_reads_as_words():
    item = {
        "id": "1",
        "occurred_at": "2026-10-06T00:00:00+00:00",
        "action": "partner.some_future_thing",
        "actor_kind": "staff",
        "actor_user_id": None,
        "resource_type": "partners.partner",
        "resource_id": "x",
        "outcome": "success",
        "reason": "",
        "changes": {},
    }
    entry = partner_detail._activity_item(item, {})
    assert entry["label"] == "Partner some future thing"
    assert entry["actor"] == "Staff"


def test_the_commission_and_payout_actions_have_labels():
    for action in (
        "commission.partner_rule_assigned",
        "commission.accrued",
        "payout.approved",
        "payout.paid",
    ):
        assert action in partner_detail.ACTION_LABELS


def test_the_feed_needs_the_audit_permission(staff_client, draft_partner):
    client, _user = staff_client("platform.staff_partner.view")
    assert (
        client.get(f"/api/v1/admin/partners/{draft_partner.id}/activity").status_code
        == 403
    )

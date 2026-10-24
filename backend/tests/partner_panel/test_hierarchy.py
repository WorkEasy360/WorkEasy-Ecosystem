"""Sub-partners: one partner delegating to another, with ceilings and isolation."""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from tests.partner_panel.conftest import api, send
from workeasy360.commercial.partners import capabilities, hierarchy, services
from workeasy360.commercial.partners.models import (
    PartnerAccount,
    PartnerCapabilityGrant,
    PartnerHierarchyLink,
    PartnerMember,
    PartnerProductGrant,
)

pytestmark = pytest.mark.django_db


def create(client, pid, **overrides):
    body = {
        "display_name": "Northern Agency",
        "administrator_email": "admin@northern.example.com",
        "administrator_name": "Nora North",
        "capabilities": ["customer.provision", "subscription.sell", "pricing.view"],
        "products": ["crm"],
        **overrides,
    }
    return send(client, "post", api(pid, "sub-partners"), body, key=True)


# --- creating and equipping -------------------------------------------------------------


def test_a_parent_creates_a_draft_sub_partner_with_an_attached_administrator(
    seller, owner_client
):
    pid = seller["partner"].id
    response = create(owner_client, pid)
    assert response.status_code == 201, response.content
    child = PartnerAccount.objects.get(id=response.json()["id"])
    assert child.status == "draft"
    assert child.code != "" and child.code.startswith(seller["partner"].code[:10])
    link = PartnerHierarchyLink.objects.get(child=child)
    assert link.parent_id == pid and link.status == "active"
    admin = PartnerMember.objects.get(partner=child)
    assert admin.role == "owner" and admin.invitation_deferred is True
    assert set(
        PartnerCapabilityGrant.objects.filter(partner=child, status="active").values_list(
            "capability", flat=True
        )
    ) == {"customer.provision", "subscription.sell", "pricing.view"}
    assert PartnerProductGrant.objects.filter(partner=child, status="active").count() == 1


def test_a_parent_cannot_give_what_it_does_not_hold(seller, owner_client):
    pid = seller["partner"].id
    services.revoke_capability(
        seller["staff_ctx"],
        pid,
        capability="pricing.view",
        reason="Programme change for test",
    )
    response = create(owner_client, pid, capabilities=["pricing.view"])
    assert response.status_code == 400
    assert not PartnerHierarchyLink.objects.exists()


def test_money_domains_and_the_hierarchy_itself_can_never_be_delegated(
    seller, owner_client
):
    pid = seller["partner"].id
    services.grant_capability(
        seller["staff_ctx"], pid, capability="domain.manage", reason="Granted for test"
    )
    for forbidden in (
        "commission.earn",
        "commission.view",
        "payout.view",
        "domain.manage",
        "subpartner.manage",
    ):
        response = create(owner_client, pid, capabilities=[forbidden])
        assert response.status_code == 400, forbidden
    assert (
        PartnerAccount.objects.filter(
            code__startswith=seller["partner"].code[:10]
        ).count()
        == 1
    )
    assert capabilities.DELEGABLE_CAPABILITIES.isdisjoint(
        {
            "commission.earn",
            "commission.view",
            "payout.view",
            "domain.manage",
            "subpartner.manage",
        }
    )


def test_a_parent_can_only_hand_on_a_product_it_holds_whole(seller, owner_client):
    pid = seller["partner"].id
    assert create(owner_client, pid, products=["books"]).status_code == 400
    # A plan-level grant narrows what may be handed on: it is not enough.
    PartnerProductGrant.objects.filter(partner_id=pid).update(
        status="revoked", revoked_at="2026-01-01T00:00:00Z"
    )
    services.grant_product(
        seller["staff_ctx"], pid, product_code="crm", plan_code="crm_professional"
    )
    assert create(owner_client, pid, products=["crm"]).status_code == 400


def test_capabilities_and_products_must_be_lists_of_known_keys(seller, owner_client):
    pid = seller["partner"].id
    assert create(owner_client, pid, capabilities="customer.provision").status_code == 400
    assert create(owner_client, pid, capabilities=["not.a.capability"]).status_code == 400
    assert create(owner_client, pid, products="crm").status_code == 400


def test_creating_needs_the_capability_and_an_admin_role_and_the_second_factor(
    seller, member_client, partner_factory, http_login
):
    pid = seller["partner"].id
    sales_client, _u = member_client(seller, "sales")
    assert create(sales_client, pid).status_code == 403
    other = partner_factory("customer.provision")
    other["user"].set_password("correct horse battery staple 42")
    other["user"].save(update_fields=["password"])
    client = http_login(other["user"])
    assert (
        create(client, other["partner"].id).status_code == 403
    )  # no subpartner capability


def test_the_server_chooses_the_code(seller, owner_client):
    pid = seller["partner"].id
    response = create(owner_client, pid, code="mine-now", display_name="Sneaky")
    assert response.status_code == 201
    assert response.json()["code"] != "mine-now"


# --- activation, suspension -------------------------------------------------------------------


def test_activation_needs_an_administrator_and_a_capability(seller, owner_client):
    pid = seller["partner"].id
    child_id = create(owner_client, pid, capabilities=[], products=[]).json()["id"]
    refused = send(
        owner_client, "post", api(pid, f"sub-partners/{child_id}/activate"), {}
    )
    assert refused.status_code == 400  # no capability yet
    send(
        owner_client,
        "put",
        api(pid, f"sub-partners/{child_id}/equipment"),
        {"capabilities": ["pricing.view"], "products": []},
    )
    ok = send(owner_client, "post", api(pid, f"sub-partners/{child_id}/activate"), {})
    assert ok.status_code == 200 and ok.json()["status"] == "active"


def test_a_parent_can_suspend_and_resume_but_never_terminate(seller, owner_client):
    pid = seller["partner"].id
    child_id = create(owner_client, pid).json()["id"]
    send(owner_client, "post", api(pid, f"sub-partners/{child_id}/activate"), {})
    suspended = send(
        owner_client,
        "post",
        api(pid, f"sub-partners/{child_id}/suspend"),
        {"reason": "Pausing the agency for review"},
    )
    assert suspended.json()["status"] == "suspended"
    assert PartnerHierarchyLink.objects.get(child_id=child_id).status == "suspended"
    resumed = send(owner_client, "post", api(pid, f"sub-partners/{child_id}/resume"), {})
    assert resumed.json()["status"] == "active"
    assert PartnerHierarchyLink.objects.get(child_id=child_id).status == "active"
    assert not hasattr(hierarchy, "terminate_sub_partner")


def test_suspension_needs_a_reason(seller, owner_client):
    pid = seller["partner"].id
    child_id = create(owner_client, pid).json()["id"]
    send(owner_client, "post", api(pid, f"sub-partners/{child_id}/activate"), {})
    assert (
        send(
            owner_client,
            "post",
            api(pid, f"sub-partners/{child_id}/suspend"),
            {"reason": "x"},
        ).status_code
        == 400
    )


def test_equipment_changes_touch_only_what_a_parent_may_give(seller, owner_client):
    pid = seller["partner"].id
    child_id = create(owner_client, pid).json()["id"]
    # Staff gave the child commission: the parent's equip command must not remove it.
    services.grant_capability(
        seller["staff_ctx"],
        child_id,
        capability="commission.earn",
        reason="Staff grant for test",
    )
    send(
        owner_client,
        "put",
        api(pid, f"sub-partners/{child_id}/equipment"),
        {"capabilities": ["pricing.view"], "products": []},
    )
    held = set(
        PartnerCapabilityGrant.objects.filter(
            partner_id=child_id, status="active"
        ).values_list("capability", flat=True)
    )
    assert held == {"pricing.view", "commission.earn"}
    assert not PartnerProductGrant.objects.filter(
        partner_id=child_id, status="active"
    ).exists()


# --- the structure ----------------------------------------------------------------------------------


def test_the_database_refuses_self_parenting_and_a_second_parent(seller, partner_factory):
    a = seller["partner"]
    b = partner_factory("customer.provision")["partner"]
    c = partner_factory("customer.provision")["partner"]
    staff_id = seller["user"].id
    from django.utils import timezone

    now = timezone.now()
    with pytest.raises(IntegrityError), transaction.atomic():
        PartnerHierarchyLink.objects.create(
            parent=a, child=a, created_by_id=staff_id, created_at=now
        )
    PartnerHierarchyLink.objects.create(
        parent=a, child=b, created_by_id=staff_id, created_at=now
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        PartnerHierarchyLink.objects.create(
            parent=c, child=b, created_by_id=staff_id, created_at=now
        )


def test_a_sub_partner_cannot_have_sub_partners_of_its_own(
    seller, owner_client, partner_factory, http_login
):
    pid = seller["partner"].id
    child_id = create(owner_client, pid).json()["id"]
    services.grant_capability(
        seller["staff_ctx"],
        child_id,
        capability="subpartner.manage",
        reason="Staff grant for test",
    )
    send(owner_client, "post", api(pid, f"sub-partners/{child_id}/activate"), {})
    child_member = PartnerMember.objects.get(partner_id=child_id)
    child_member.user.set_password("correct horse battery staple 42")
    child_member.user.save(update_fields=["password"])
    client = http_login(child_member.user)
    refused = create(
        client, child_id, display_name="Grandchild", administrator_email="gc@example.com"
    )
    assert refused.status_code == 400
    options = client.get(api(child_id, "sub-partners/options")).json()
    assert options["depth"]["can_have_children"] is False


def test_depth_helpers_see_no_cycle(seller, owner_client):
    pid = seller["partner"].id
    child_id = create(owner_client, pid).json()["id"]
    assert hierarchy.depth_above(PartnerAccount.objects.get(id=child_id).id) == 1
    assert hierarchy.depth_below(pid) == 1
    assert hierarchy.is_descendant(pid, PartnerAccount.objects.get(id=child_id).id)
    assert not hierarchy.is_descendant(PartnerAccount.objects.get(id=child_id).id, pid)


# --- isolation --------------------------------------------------------------------------------------------


def test_a_parent_sees_status_and_counts_never_the_childs_customers_or_secrets(
    seller, owner_client
):
    pid = seller["partner"].id
    child_id = create(owner_client, pid).json()["id"]
    rows = owner_client.get(api(pid, "sub-partners")).json()["results"]
    row = next(r for r in rows if r["id"] == child_id)
    assert set(row) == {
        "id",
        "link_id",
        "name",
        "code",
        "status",
        "link_status",
        "currency",
        "country",
        "administrator",
        "customers",
        "products",
        "capabilities",
        "experiences",
        "created_at",
    }
    assert set(row["administrator"]) == {"name", "email", "invited", "last_login_at"}
    flat = str(row).lower()
    for secret in ("password", "token", "tax", "payout", "notes"):
        assert secret not in flat


def test_a_stranger_cannot_act_on_someone_elses_sub_partner(
    seller, owner_client, partner_factory, http_login
):
    pid = seller["partner"].id
    child_id = create(owner_client, pid).json()["id"]
    other = partner_factory("subpartner.manage", "customer.provision")
    other["user"].set_password("correct horse battery staple 42")
    other["user"].save(update_fields=["password"])
    client = http_login(other["user"])
    oid = other["partner"].id
    for action in ("activate", "suspend", "resume"):
        assert (
            send(
                client,
                "post",
                api(oid, f"sub-partners/{child_id}/{action}"),
                {"reason": "long enough reason"},
            ).status_code
            == 404
        )
    assert (
        send(
            client,
            "put",
            api(oid, f"sub-partners/{child_id}/equipment"),
            {"capabilities": [], "products": []},
        ).status_code
        == 404
    )
    assert client.get(api(oid, "sub-partners")).json()["results"] == []


def test_a_child_cannot_see_or_act_on_its_parent(seller, owner_client, http_login):
    pid = seller["partner"].id
    child_id = create(owner_client, pid).json()["id"]
    send(owner_client, "post", api(pid, f"sub-partners/{child_id}/activate"), {})
    member = PartnerMember.objects.get(partner_id=child_id)
    member.user.set_password("correct horse battery staple 42")
    member.user.save(update_fields=["password"])
    child = http_login(member.user)
    # The child's token against the parent's path: the parent is not its partner.
    assert child.get(api(pid, "dashboard")).status_code == 404
    assert child.get(api(pid, "members")).status_code == 404
    assert child.get(api(pid, "sub-partners")).status_code == 404


def test_a_sub_partner_sees_only_its_own_customers(seller, owner_client, http_login):
    from tests.phase7.support import provision

    pid = seller["partner"].id
    child_id = create(owner_client, pid).json()["id"]
    send(owner_client, "post", api(pid, f"sub-partners/{child_id}/activate"), {})
    provision(seller, name="Parent Customer")
    member = PartnerMember.objects.get(partner_id=child_id)
    member.user.set_password("correct horse battery staple 42")
    member.user.save(update_fields=["password"])
    child = http_login(member.user)
    assert child.get(api(child_id, "customers")).json()["results"] == []
    parent_rows = owner_client.get(api(pid, "customers")).json()["results"]
    assert any(r["name"] == "Parent Customer" for r in parent_rows)


def test_the_hierarchy_writes_are_audited_without_secrets(seller, owner_client):
    from tests.partner_panel.conftest import partner_audit

    pid = seller["partner"].id
    child_id = create(owner_client, pid).json()["id"]
    send(owner_client, "post", api(pid, f"sub-partners/{child_id}/activate"), {})
    rows = partner_audit(pid, "action", "changes")
    assert {"partner.sub_partner_created", "partner.sub_partner_activated"} <= {
        r["action"] for r in rows
    }
    assert "admin@northern.example.com" not in str(rows)

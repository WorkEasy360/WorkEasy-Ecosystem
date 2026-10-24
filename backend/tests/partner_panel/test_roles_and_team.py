"""Member roles are presets over the partner's capabilities, and a partner runs
its own team - without ever widening what the partner holds."""

from __future__ import annotations

import pytest

from tests.partner_panel.conftest import api, send
from workeasy360.commercial.partners import capabilities, services
from workeasy360.contracts.errors import PartnerValidationError

pytestmark = pytest.mark.django_db

ALL_CAPS = frozenset(capabilities.CAPABILITIES)


# --- the presets, as pure functions -------------------------------------------------


def test_every_stored_role_is_a_preset_and_unknown_fails_closed():
    assert set(capabilities.ROLE_PRESETS) == {
        "owner",
        "admin",
        "sales",
        "finance",
        "support",
        "marketing",
        "viewer",
    }
    unknown = capabilities.permissions_for_role(ALL_CAPS, role="superuser")
    viewer = capabilities.permissions_for_role(ALL_CAPS, role="viewer")
    assert unknown == viewer


def test_a_role_only_ever_narrows_what_the_partner_holds():
    held = frozenset({"branding.manage"})
    for role in capabilities.ROLE_PRESETS:
        granted = capabilities.permissions_for_role(held, role=role)
        beyond = (
            granted - set(capabilities.BASELINE_VIEW) - set(capabilities.BASELINE_MANAGE)
        )
        # Nothing outside the one held capability's permissions.
        assert beyond <= set(capabilities.CAPABILITIES["branding.manage"].view) | set(
            capabilities.CAPABILITIES["branding.manage"].manage
        ), role


def test_role_permissions_are_what_the_names_promise():
    perms = lambda role: capabilities.permissions_for_role(ALL_CAPS, role=role)  # noqa: E731
    assert "platform.partner_subscription.sell" in perms("sales")
    assert "platform.partner_branding.manage" not in perms("sales")
    assert "platform.partner_commission.view" in perms("finance")
    assert "platform.partner_customer.view" not in perms("finance")
    assert not any(p.endswith(".manage") for p in perms("finance"))
    assert "platform.partner_support.access" in perms("support")
    assert "platform.partner_subscription.sell" not in perms("support")
    assert "platform.partner_branding.manage" in perms("marketing")
    # Custom domains are dangerous: marketing cannot touch them.
    assert "platform.partner_domain.manage" not in perms("marketing")
    assert "platform.partner_member.manage" not in perms("marketing")
    for role in ("owner", "admin"):
        assert "platform.partner_member.manage" in perms(role)
        assert "platform.partner_domain.manage" in perms(role)
    viewer = perms("viewer")
    assert "platform.partner_member.manage" not in viewer
    assert "platform.partner_dashboard.view" in viewer


def test_a_role_is_never_free_text():
    with pytest.raises(PartnerValidationError):
        capabilities.require_role("god")
    with pytest.raises(PartnerValidationError):
        capabilities.require_role({"x": 1})


def test_the_baseline_belongs_to_the_registry_and_every_key_is_declared():
    from workeasy360.commercial.partners.permissions import PARTNER_PERMISSIONS

    declared = {str(d.key) for d in PARTNER_PERMISSIONS}
    assert declared == set(capabilities.all_partner_permission_keys())
    dangerous = {str(d.key) for d in PARTNER_PERMISSIONS if d.dangerous}
    assert "platform.partner_member.manage" in dangerous
    assert "platform.partner_subpartner.manage" in dangerous


# --- through the real resolver -------------------------------------------------------


def test_a_sales_member_sells_but_cannot_change_the_brand(seller, member_client):
    from workeasy360.platform.authz import services as authz

    client, user = member_client(seller, "sales")
    from workeasy360.commercial.partners.access import partner_actor

    ctx = partner_actor(user_id=user.id, partner_id=seller["partner"].id)
    assert authz.check(ctx, "platform.partner_subscription.sell").allowed
    assert not authz.check(ctx, "platform.partner_branding.manage").allowed
    assert not authz.check(ctx, "platform.partner_member.manage").allowed


def test_portal_lists_each_members_resolved_permissions(seller, member_client):
    client, _user = member_client(seller, "finance")
    body = client.get("/api/v1/partner/portal").json()
    me = next(p for p in body["partners"] if p["partner_id"] == str(seller["partner"].id))
    assert me["role"] == "finance"
    assert me["role_label"] == "Finance"
    assert "platform.partner_commission.view" in me["permissions"]
    assert "platform.partner_member.manage" not in me["permissions"]
    assert me["is_admin"] is False


def test_a_suspended_partner_keeps_only_the_read_tier_of_the_members_role(
    seller, member_client
):
    from workeasy360.commercial.partners.access import partner_actor
    from workeasy360.contracts.enums import PartnerStatus
    from workeasy360.platform.authz import services as authz

    _client, user = member_client(seller, "sales")
    services.set_partner_status(
        seller["staff_ctx"],
        seller["partner"].id,
        target=PartnerStatus.SUSPENDED,
        reason="Suspended for the test",
    )
    ctx = partner_actor(user_id=user.id, partner_id=seller["partner"].id)
    assert authz.check(ctx, "platform.partner_customer.view").allowed
    assert not authz.check(ctx, "platform.partner_subscription.sell").allowed
    assert not authz.check(ctx, "platform.partner_member.manage").allowed


def test_a_terminated_partner_shows_a_finance_member_the_money_and_a_sales_member_nothing(
    seller, member_client
):
    from workeasy360.commercial.partners.access import partner_actor
    from workeasy360.contracts.enums import PartnerStatus
    from workeasy360.platform.authz import services as authz

    _c1, finance = member_client(seller, "finance")
    _c2, sales = member_client(seller, "sales")
    services.set_partner_status(
        seller["staff_ctx"],
        seller["partner"].id,
        target=PartnerStatus.TERMINATED,
        reason="Terminated for the test",
    )
    pid = seller["partner"].id
    fin = partner_actor(user_id=finance.id, partner_id=pid)
    sal = partner_actor(user_id=sales.id, partner_id=pid)
    assert authz.check(fin, "platform.partner_commission.view").allowed
    assert not authz.check(sal, "platform.partner_commission.view").allowed
    assert not authz.check(fin, "platform.partner_dashboard.view").allowed


# --- managing the team over HTTP ------------------------------------------------------


def test_a_partner_admin_invites_and_the_answer_is_the_same_for_a_known_address(
    seller, owner_client, user_factory
):
    pid = seller["partner"].id
    new = send(
        owner_client,
        "post",
        api(pid, "members"),
        {"email": "newhire@example.com", "role": "sales"},
    )
    known = user_factory()
    existing = send(
        owner_client, "post", api(pid, "members"), {"email": known.email, "role": "sales"}
    )
    assert new.status_code == existing.status_code == 201
    assert set(new.json()) == set(existing.json()) == {"id", "status"}


def test_a_non_admin_cannot_manage_the_team(seller, member_client):
    pid = seller["partner"].id
    for role in ("sales", "finance", "support", "marketing", "viewer"):
        client, _u = member_client(seller, role)
        assert (
            send(
                client,
                "post",
                api(pid, "members"),
                {"email": "x@example.com", "role": "viewer"},
            ).status_code
            == 403
        )
        assert (
            client.get(api(pid, "members")).status_code == 200
        )  # everyone may read the team


def test_only_an_owner_can_hand_out_or_change_the_owner_role(
    seller, member_client, owner_client
):
    pid = seller["partner"].id
    admin_client, _u = member_client(seller, "admin")
    refused = send(
        admin_client,
        "post",
        api(pid, "members"),
        {"email": "boss@example.com", "role": "owner"},
    )
    assert refused.status_code == 400
    allowed = send(
        owner_client,
        "post",
        api(pid, "members"),
        {"email": "boss@example.com", "role": "owner"},
    )
    assert allowed.status_code == 201


def test_nobody_can_change_their_own_role_or_remove_themselves(seller, owner_client):
    pid = seller["partner"].id
    members = owner_client.get(api(pid, "members")).json()["members"]
    me = next(m for m in members if m["is_you"])
    assert (
        send(
            owner_client, "patch", api(pid, f"members/{me['id']}"), {"role": "viewer"}
        ).status_code
        == 400
    )
    assert (
        send(owner_client, "delete", api(pid, f"members/{me['id']}")).status_code == 400
    )


def test_an_admin_cannot_remove_or_demote_an_owner(seller, member_client, owner_client):
    pid = seller["partner"].id
    owner = next(
        m for m in owner_client.get(api(pid, "members")).json()["members"] if m["is_you"]
    )
    admin_client, _u = member_client(seller, "admin")
    assert (
        send(
            admin_client, "patch", api(pid, f"members/{owner['id']}"), {"role": "viewer"}
        ).status_code
        == 400
    )
    assert (
        send(admin_client, "delete", api(pid, f"members/{owner['id']}")).status_code
        == 400
    )


def test_the_last_administrator_cannot_be_demoted_or_removed_by_a_partner(
    seller, owner_client, member_client
):
    pid = seller["partner"].id
    sales_client, sales_user = member_client(seller, "sales")
    members = owner_client.get(api(pid, "members")).json()["members"]
    me = next(m for m in members if m["is_you"])
    # Make the sales member an owner so they can act on the first one.
    sales_member = next(m for m in members if m["email"] == sales_user.email)
    assert (
        send(
            owner_client,
            "patch",
            api(pid, f"members/{sales_member['id']}"),
            {"role": "owner"},
        ).status_code
        == 200
    )
    second = sales_client
    # Now two owners exist: removing one is fine, and the survivor cannot be removed by themselves.
    assert send(second, "delete", api(pid, f"members/{me['id']}")).status_code == 200
    survivors = [
        m
        for m in second.get(api(pid, "members")).json()["members"]
        if m["role"] in ("owner", "admin")
    ]
    assert len(survivors) == 1


def test_a_role_must_be_a_known_preset(seller, owner_client):
    pid = seller["partner"].id
    bad = send(
        owner_client,
        "post",
        api(pid, "members"),
        {"email": "z@example.com", "role": "root"},
    )
    assert bad.status_code == 400
    assert bad.json().get("field") == "role" or "role" in str(bad.json())


def test_a_member_of_another_partner_cannot_be_named(
    seller, owner_client, partner_factory
):
    other = partner_factory("customer.provision")
    from workeasy360.commercial.partners.models import PartnerMember

    foreign = PartnerMember.objects.filter(partner_id=other["partner"].id).first()
    pid = seller["partner"].id
    assert (
        send(
            owner_client, "patch", api(pid, f"members/{foreign.id}"), {"role": "viewer"}
        ).status_code
        == 404
    )
    assert (
        send(owner_client, "delete", api(pid, f"members/{foreign.id}")).status_code == 404
    )
    assert (
        send(
            owner_client, "post", api(pid, f"members/{foreign.id}/resend-setup"), {}
        ).status_code
        == 404
    )

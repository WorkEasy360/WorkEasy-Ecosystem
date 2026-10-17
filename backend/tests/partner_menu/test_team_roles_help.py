"""Role & Permissions, Team Members (suspend / resume), Channel Manager, Support
Manager and the Users search (ADR-046)."""

from __future__ import annotations

import pytest

from tests.partner_menu.conftest import api, send
from workeasy360.commercial.partners import capabilities, services
from workeasy360.commercial.partners.models import PartnerMember
from workeasy360.contracts.enums import PartnerLinkStatus

pytestmark = pytest.mark.django_db


def member_of(pid, user):
    return PartnerMember.objects.get(partner_id=pid, user=user)


# --- roles ----------------------------------------------------------------------------------


def test_roles_are_the_fixed_presets_intersected_with_what_the_partner_holds(
    partner_factory, http_login
):
    fx = partner_factory("branding.manage", "referral.create")
    fx["user"].set_password("correct horse battery staple 42")
    fx["user"].save(update_fields=["password"])
    PartnerMember.objects.filter(partner_id=fx["partner"].id, user=fx["user"]).update(
        role="owner"
    )
    client = http_login(fx["user"])
    body = client.get(api(fx["partner"].id, "roles")).json()
    assert [r["key"] for r in body["roles"]] == list(capabilities.ROLE_PRESETS)
    held = {c["key"] for c in body["partner_capabilities"]}
    assert held == {"branding.manage", "referral.create"}
    by_key = {r["key"]: r for r in body["roles"]}
    for role in body["roles"]:
        # A role never lists a capability the partner does not hold.
        assert {c["key"] for c in role["capabilities"]} <= held, role["key"]
    assert {c["key"] for c in by_key["sales"]["capabilities"]} == {
        "referral.create"
    }  # sales has no brand
    assert {c["key"] for c in by_key["marketing"]["capabilities"]} == {
        "branding.manage",
        "referral.create",
    }
    assert by_key["support"]["capabilities"] == []
    assert by_key["viewer"]["can_change"] is False
    assert {c["access"] for c in by_key["viewer"]["capabilities"]} == {"view"}
    assert by_key["owner"]["owner_only"] is True
    assert body["not_implemented"]["custom_roles"]


def test_everyone_on_the_team_may_read_the_roles_and_nobody_may_write_them(
    seller, member_client
):
    client, _user = member_client(seller, "viewer")
    pid = seller["partner"].id
    assert client.get(api(pid, "roles")).status_code == 200
    for verb in ("post", "put", "patch", "delete"):
        assert send(client, verb, api(pid, "roles"), {"key": "root"}).status_code == 405


def test_there_is_no_endpoint_for_a_custom_role_and_no_stored_role_outside_the_presets():
    from workeasy360.commercial.partners.models import PartnerMember as Member

    assert {c[0] for c in Member._meta.get_field("role").choices} == set(
        capabilities.ROLE_PRESETS
    )


def test_the_account_access_text_matches_the_baseline_it_describes():
    from workeasy360.commercial.controlplane import partner_network

    assert len(partner_network.ACCOUNT_ACCESS_EVERYONE) >= 1
    assert "platform.partner_apikey.manage" in capabilities.BASELINE_MANAGE
    assert "platform.partner_apikey.view" in capabilities.BASELINE_MANAGE
    assert not {
        k for k in capabilities.BASELINE_VIEW if "apikey" in k
    }  # not for every member


# --- suspend and resume ------------------------------------------------------------------------


def test_a_suspended_member_loses_the_partner_at_once_and_regains_it_on_resume(
    seller, owner_client, member_client
):
    pid = seller["partner"].id
    sales_client, sales = member_client(seller, "sales")
    assert sales_client.get(api(pid, "members")).status_code == 200
    target = member_of(pid, sales)
    assert (
        send(
            owner_client, "post", api(pid, f"members/{target.id}/suspend"), {}
        ).status_code
        == 200
    )
    # The very next request: the partner does not exist for them.
    assert sales_client.get(api(pid, "members")).status_code == 404
    assert sales_client.get(api(pid, "dashboard")).status_code == 404
    portal = sales_client.get("/api/v1/partner/portal").json()
    assert all(p["partner_id"] != str(pid) for p in portal["partners"])
    # The team screen still lists them, marked suspended, with the role they held.
    row = next(
        m
        for m in owner_client.get(api(pid, "members")).json()["members"]
        if m["id"] == str(target.id)
    )
    assert (row["status"], row["role"]) == ("suspended", "sales")
    assert (
        send(
            owner_client, "post", api(pid, f"members/{target.id}/resume"), {}
        ).status_code
        == 200
    )
    assert sales_client.get(api(pid, "members")).status_code == 200
    assert member_of(pid, sales).role == "sales"


def test_suspending_never_touches_the_identity(seller, owner_client, member_client):
    pid = seller["partner"].id
    _client, sales = member_client(seller, "sales")
    send(
        owner_client, "post", api(pid, f"members/{member_of(pid, sales).id}/suspend"), {}
    )
    sales.refresh_from_db()
    assert sales.status == "active" and sales.is_active


def test_nobody_suspends_or_resumes_themselves(seller, owner_client):
    pid = seller["partner"].id
    me = member_of(pid, seller["user"])
    assert (
        send(owner_client, "post", api(pid, f"members/{me.id}/suspend"), {}).status_code
        == 400
    )
    assert member_of(pid, seller["user"]).status == "active"


def test_an_admin_cannot_suspend_an_owner(seller, owner_client, member_client):
    pid = seller["partner"].id
    admin_client, _user = member_client(seller, "admin")
    me = member_of(pid, seller["user"])
    assert (
        send(admin_client, "post", api(pid, f"members/{me.id}/suspend"), {}).status_code
        == 400
    )


def test_the_last_working_administrator_cannot_be_suspended(
    seller, owner_client, member_client
):
    pid = seller["partner"].id
    admin_client, admin = member_client(seller, "admin")
    me = member_of(pid, seller["user"])
    # Two administrators: the admin may suspend the owner only if an owner/admin remains.
    # Suspend the admin first (by the owner); the owner is now the only one working.
    assert (
        send(
            owner_client,
            "post",
            api(pid, f"members/{member_of(pid, admin).id}/suspend"),
            {},
        ).status_code
        == 200
    )
    # A suspended administrator does not count: the owner is now the last working one.
    assert services is not None
    with pytest.raises(Exception) as refused:
        services.suspend_member(seller["staff_ctx"], pid, member_id=me.id)
    assert "administrator" in str(refused.value).lower()
    assert member_of(pid, seller["user"]).status == "active"


def test_only_administrators_suspend_and_it_needs_the_second_factor(
    seller, owner_client, member_client, http_login
):
    pid = seller["partner"].id
    sales_client, sales = member_client(seller, "sales")
    finance_client, _fin = member_client(seller, "finance")
    target = member_of(pid, sales)
    assert (
        send(
            finance_client, "post", api(pid, f"members/{target.id}/suspend"), {}
        ).status_code
        == 403
    )
    low = http_login(seller["user"], aal2=False)
    assert send(
        low, "post", api(pid, f"members/{target.id}/suspend"), {}
    ).status_code in (401, 403)
    assert member_of(pid, sales).status == "active"


def test_a_member_of_another_partner_cannot_be_suspended(
    seller, owner_client, partner_factory
):
    other = partner_factory("customer.provision")
    foreign = PartnerMember.objects.filter(partner_id=other["partner"].id).first()
    pid = seller["partner"].id
    for verb in ("suspend", "resume"):
        assert (
            send(
                owner_client, "post", api(pid, f"members/{foreign.id}/{verb}"), {}
            ).status_code
            == 404
        )


def test_a_suspended_member_cannot_be_invited_again_but_can_be_removed(
    seller, owner_client, member_client
):
    pid = seller["partner"].id
    _client, sales = member_client(seller, "sales")
    target = member_of(pid, sales)
    send(owner_client, "post", api(pid, f"members/{target.id}/suspend"), {})
    again = send(
        owner_client, "post", api(pid, "members"), {"email": sales.email, "role": "sales"}
    )
    assert (
        again.status_code == 400
    )  # the same uniform refusal as any address that cannot be added
    assert PartnerMember.objects.filter(partner_id=pid, user=sales).count() == 1
    assert (
        send(owner_client, "delete", api(pid, f"members/{target.id}")).status_code == 200
    )
    assert member_of(pid, sales).status == "removed"


def test_a_suspended_member_gets_no_new_role_and_is_not_resumed_twice(
    seller, owner_client, member_client
):
    pid = seller["partner"].id
    _client, sales = member_client(seller, "sales")
    target = member_of(pid, sales)
    send(owner_client, "post", api(pid, f"members/{target.id}/suspend"), {})
    assert (
        send(
            owner_client, "patch", api(pid, f"members/{target.id}"), {"role": "admin"}
        ).status_code
        == 404
    )
    assert (
        send(
            owner_client, "post", api(pid, f"members/{target.id}/suspend"), {}
        ).status_code
        == 404
    )
    send(owner_client, "post", api(pid, f"members/{target.id}/resume"), {})
    assert (
        send(
            owner_client, "post", api(pid, f"members/{target.id}/resume"), {}
        ).status_code
        == 404
    )


def test_suspension_and_resumption_are_audited(seller, owner_client, member_client):
    from tests.partner_menu.conftest import partner_audit

    pid = seller["partner"].id
    _client, sales = member_client(seller, "sales")
    target = member_of(pid, sales)
    send(owner_client, "post", api(pid, f"members/{target.id}/suspend"), {})
    send(owner_client, "post", api(pid, f"members/{target.id}/resume"), {})
    actions = {row["action"] for row in partner_audit(pid, "action")}
    assert {"partner.member_suspended", "partner.member_resumed"} <= actions


# --- channel manager --------------------------------------------------------------------------


def test_the_channel_manager_calls_the_missing_providers_not_available(
    seller, owner_client
):
    body = owner_client.get(api(seller["partner"].id, "channels")).json()
    by_key = {c["key"]: c for c in body["channels"]}
    assert set(by_key) == {"email", "webhook", "whatsapp", "sms"}
    assert by_key["email"]["state"] in ("connected", "needs_setup")
    for key in ("webhook", "whatsapp", "sms"):
        assert by_key[key]["state"] == "not_available"
        assert by_key[key]["editable"] is False
    assert [s["key"] for s in body["states"]] == [
        "connected",
        "needs_setup",
        "disconnected",
        "error",
        "not_available",
    ]
    # No secret of any kind in the answer.
    assert not any(
        word in str(body).lower() for word in ("password", "token", "secret", "smtp_")
    )


def test_email_needs_setup_until_the_sender_is_named(seller, owner_client):
    pid = seller["partner"].id  # the seller holds branding.manage (white-label brand)
    body = owner_client.get(api(pid, "channels")).json()
    assert (
        next(c for c in body["channels"] if c["key"] == "email")["state"] == "needs_setup"
    )


def test_a_partner_without_a_brand_has_email_connected(partner_factory, http_login):
    fx = partner_factory("customer.provision")
    fx["user"].set_password("correct horse battery staple 42")
    fx["user"].save(update_fields=["password"])
    client = http_login(fx["user"])
    body = client.get(api(fx["partner"].id, "channels")).json()
    assert (
        next(c for c in body["channels"] if c["key"] == "email")["state"] == "connected"
    )


# --- support manager ---------------------------------------------------------------------------


def test_support_points_at_the_contact_and_the_help_desk_and_lists_no_tickets(
    seller, owner_client, settings, monkeypatch
):
    from workeasy360.platform.ecosystem import selectors as ecosystem

    settings.W360_PARTNER_SUPPORT_EMAIL = "partners@workeasy360.example"
    pid = seller["partner"].id
    body = owner_client.get(api(pid, "support")).json()
    assert body["contact"] == {"email": "partners@workeasy360.example", "available": True}
    assert body["requests"] is None
    assert body["not_implemented"]["partner_tickets"]
    assert body["partner"]["name"] == seller["partner"].display_name
    assert body["priorities"] == ["low", "normal", "high", "urgent"]
    assert (
        body["help_desk"]["available"] is False
    )  # no Easy Help Desk registered in this database

    class Target:
        launch_url = "https://help.workeasy360.com/"
        is_terminal = False
        is_suspended = False

    monkeypatch.setattr(
        ecosystem,
        "launch_target",
        lambda code: Target() if code == "easy_helpdesk" else None,
    )
    desk = owner_client.get(api(pid, "support")).json()["help_desk"]
    assert desk == {
        "name": "Easy Help Desk",
        "available": True,
        "url": "https://help.workeasy360.com/",
        "suspended": False,
    }


def test_support_without_an_address_offers_none(seller, owner_client, settings):
    settings.W360_PARTNER_SUPPORT_EMAIL = ""
    assert owner_client.get(api(seller["partner"].id, "support")).json()["contact"] == {
        "email": "",
        "available": False,
    }


def test_no_endpoint_creates_a_ticket():
    import pathlib
    import re

    urls = (
        pathlib.Path(__file__).resolve().parents[2]
        / "workeasy360"
        / "interfaces"
        / "api"
        / "v1"
        / "urls.py"
    ).read_text(encoding="utf-8")
    assert not re.search(r'"partner/[^"]*(ticket|helpdesk|case)', urls)


# --- users search --------------------------------------------------------------------------------


def test_the_customer_search_finds_live_customers_by_name_and_never_a_former_one(
    seller, owner_client, console_org, catalog
):
    pid = seller["partner"].id
    live = console_org()
    former = console_org()
    from workeasy360.platform.organizations.models import Organization

    Organization.objects.filter(id=live["org"].id).update(display_name="Zenith Retail")
    Organization.objects.filter(id=former["org"].id).update(
        display_name="Zenith Wholesale"
    )
    for org in (live, former):
        services.link_customer(
            seller["staff_ctx"], pid, org["org"].id, reason="Customer of the partner"
        )
    from workeasy360.commercial.partners.models import PartnerOrganizationLink

    link = PartnerOrganizationLink.objects.get(
        partner_id=pid, organization_id=former["org"].id
    )
    services.change_link_status(
        seller["staff_ctx"],
        link.id,
        target=PartnerLinkStatus.ENDED,
        reason="Customer left the partner",
    )
    assert PartnerOrganizationLink.objects.get(id=link.id).status == "ended"
    found = owner_client.get(api(pid, "customers") + "?q=zenith").json()["results"]
    names = [c["name"] for c in found]
    assert names == ["Zenith Retail"]
    # A search never confirms the name of a relationship that has ended: the row
    # hides the name, so the name cannot be what finds it.
    assert (
        owner_client.get(api(pid, "customers") + "?q=wholesale").json()["results"] == []
    )
    assert (
        owner_client.get(api(pid, "customers") + "?q=nothing-like-this").json()["results"]
        == []
    )
    # LIKE metacharacters are data.
    assert owner_client.get(api(pid, "customers") + "?q=%25").json()["results"] == []
    assert (
        len(owner_client.get(api(pid, "customers") + "?q=" + "x" * 500).json()["results"])
        == 0
    )

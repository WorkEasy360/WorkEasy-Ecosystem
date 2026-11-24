"""Regression tests for the independent review of ADR-075 (M-1, M-2): closing
an account must never lock an active partner out of its own portal, nor leave
a provisioned customer whose invited owner can never accept."""

from __future__ import annotations

import uuid

import pytest

from tests.accounts_v3.conftest import ACCOUNT_API, PASSWORD
from tests.phase7 import conftest as _phase7

pytestmark = pytest.mark.django_db

# Re-exported by assignment, as tests/phase10 does.
channel = _phase7.channel


def _close(user):
    from workeasy360.platform.administration import account_security

    account_security.close_account(user.pk, password=PASSWORD, confirmation="CLOSE")


# --- M-1: the last administrator of an active partner --------------------------------------


def test_the_last_partner_administrator_cannot_close(partner_factory, with_password):
    from workeasy360.contracts.errors import LastPartnerAdminBlocksClosure
    from workeasy360.platform.administration import account_security

    fx = partner_factory()
    admin = with_password(fx["user"])
    check = account_security.closure_check(admin.pk)
    assert check["can_close"] is False
    assert check["blocking_partners"] == [{"name": "Test Partner"}]
    assert check["blocking_organizations"] == []

    with pytest.raises(LastPartnerAdminBlocksClosure) as refused:
        _close(admin)
    assert refused.value.http_status == 409
    assert refused.value.code == "last_partner_admin"
    admin.refresh_from_db()
    assert admin.status == "active"


def test_the_refusal_reaches_the_browser_with_the_partners_named(
    partner_factory, with_password, accounts_on, on_accounts
):
    fx = partner_factory()
    admin = with_password(fx["user"])
    accounts_on()
    here = on_accounts(admin)
    assert here.get(f"{ACCOUNT_API}/closure").json()["blocking_partners"] == [
        {"name": "Test Partner"}
    ]
    refused = here.post(
        f"{ACCOUNT_API}/closure", {"password": PASSWORD, "confirmation": "CLOSE"}
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "last_partner_admin"
    assert refused.json()["blocking_partners"] == [{"name": "Test Partner"}]


def test_an_administrator_with_a_working_colleague_can_close(
    partner_factory, user_factory, with_password
):
    from workeasy360.commercial.partners import services
    from workeasy360.platform.administration import account_security

    fx = partner_factory()
    services.add_member(
        fx["staff_ctx"], fx["partner"].id, email=user_factory().email, role="admin"
    )
    admin = with_password(fx["user"])
    assert account_security.closure_check(admin.pk)["can_close"] is True
    _close(admin)
    admin.refresh_from_db()
    assert admin.status == "disabled"


def test_a_closed_colleague_does_not_count_as_an_administrator(
    partner_factory, user_factory, with_password
):
    from workeasy360.commercial.partners import services
    from workeasy360.platform.administration import account_security
    from workeasy360.platform.identity import lifecycle

    fx = partner_factory()
    colleague = user_factory()
    services.add_member(
        fx["staff_ctx"], fx["partner"].id, email=colleague.email, role="admin"
    )
    lifecycle.close(colleague)
    admin = with_password(fx["user"])
    assert account_security.closure_check(admin.pk)["blocking_partners"] == [
        {"name": "Test Partner"}
    ]


def test_a_viewer_and_an_inactive_partner_never_block(partner_factory, with_password):
    from workeasy360.commercial.partners import services
    from workeasy360.contracts.enums import PartnerStatus
    from workeasy360.platform.administration import account_security

    viewer = partner_factory(role="viewer")
    assert account_security.closure_check(viewer["user"].pk)["blocking_partners"] == []

    fx = partner_factory()
    services.set_partner_status(
        fx["staff_ctx"],
        fx["partner"].id,
        target=PartnerStatus.SUSPENDED,
        reason="Suspended by the closure test",
    )
    assert account_security.closure_check(fx["user"].pk)["blocking_partners"] == []


# --- M-2: an owner who is still INVITED ----------------------------------------------------


def _provision(channel, address: str):
    from workeasy360.commercial.partners import customers

    return customers.provision_customer(
        channel["actor"](),
        organization_name="Pending Owner Ltd",
        owner_email=address,
        idempotency_key=uuid.uuid4().hex,
    )


def test_an_invited_owner_is_the_only_owner_and_cannot_close(channel, with_password):
    from workeasy360.contracts.errors import SoleOwnerBlocksClosure
    from workeasy360.platform.administration import account_security
    from workeasy360.platform.identity.models import User
    from workeasy360.platform.organizations.models import UserOrganizationAccess

    address = f"invited-{uuid.uuid4().hex[:8]}@customer-example.com"
    _provision(channel, address)
    owner = with_password(User.objects.get(email=address))
    # The projection does not know them: this is what hid them before.
    assert not UserOrganizationAccess.objects.filter(user=owner).exists()

    check = account_security.closure_check(owner.pk)
    assert check["can_close"] is False
    assert [(o["name"], o["pending"]) for o in check["blocking_organizations"]] == [
        ("Pending Owner Ltd", True)
    ]
    with pytest.raises(SoleOwnerBlocksClosure) as refused:
        _close(owner)
    assert refused.value.context["blocking_organizations"][0]["pending"] is True
    owner.refresh_from_db()
    assert owner.status == "active"


def test_an_accepted_owner_is_counted_once_and_not_as_pending(channel):
    from workeasy360.platform.identity.models import User
    from workeasy360.platform.onboarding.services import activate_pending_owner
    from workeasy360.platform.organizations.selectors import sole_owned_organizations

    address = f"accepted-{uuid.uuid4().hex[:8]}@customer-example.com"
    created = _provision(channel, address)
    owner = User.objects.get(email=address)
    activate_pending_owner(user_id=owner.pk, organization_id=created.organization_id)

    blocking = sole_owned_organizations(owner.pk)
    assert [(b["organization_id"], b.get("pending", False)) for b in blocking] == [
        (str(created.organization_id), False)
    ]


def test_an_invitation_alone_is_not_ownership(channel, user_factory):
    """The partner record only names candidates: a person whose owner
    membership is not INVITED (here: no owner membership of that
    organization at all) is not reported."""
    from workeasy360.commercial.partners.models import PartnerOwnerInvite
    from workeasy360.platform.organizations.selectors import pending_owned_organizations

    address = f"named-{uuid.uuid4().hex[:8]}@customer-example.com"
    _provision(channel, address)
    stranger = user_factory()
    invite = PartnerOwnerInvite.objects.get(user__email=address)
    PartnerOwnerInvite.objects.filter(pk=invite.pk).update(user=stranger)
    assert pending_owned_organizations(stranger.pk) == []

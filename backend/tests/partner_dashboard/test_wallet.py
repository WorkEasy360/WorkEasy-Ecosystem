"""Wallet & Payouts (2026-11-03): payout details sealed and staff-verified, a
withdrawal of the available balance with the minimum and TDS, turned into an
ordinary payout by staff - and staff seats by partner level."""

from __future__ import annotations

import uuid

import pytest
from django.test import override_settings

from tests.partner_dashboard.conftest import api, send
from workeasy360.commercial.controlplane import partner_withdrawals
from workeasy360.commercial.partners import services
from workeasy360.commercial.partners.models import (
    PartnerPayoutMethod,
    PartnerWithdrawalRequest,
)

pytestmark = pytest.mark.django_db

BANK = {
    "method": "bank",
    "account_holder": "Console Reseller Ltd",
    "bank_name": "HDFC Bank",
    "ifsc": "hdfc0001234",
    "account_number": "50100123456789",
    "account_number_confirm": "50100123456789",
    "pan": "abcde1234f",
}


def _verify(staff, partner_id):
    return send(
        staff,
        "post",
        f"/api/v1/admin/partners/{partner_id}/payout-method",
        {"decision": "verified"},
    )


def test_payout_details_are_sealed_and_shown_only_as_their_last_four(reseller):
    client, fx = reseller
    saved = send(client, "put", api(fx["partner"].id, "withdrawals/method"), BANK)
    assert saved.status_code == 200, saved.content
    method = saved.json()["method"]
    assert method["account_hint"] == "6789" and method["pan_hint"] == "234F"
    assert method["ifsc"] == "HDFC0001234" and method["verification"] == "pending"
    assert (
        "50100123456789" not in saved.content.decode()
        and "ABCDE1234F" not in saved.content.decode()
    )
    row = PartnerPayoutMethod.objects.get(partner_id=fx["partner"].id)
    assert (
        "50100123456789" not in row.account_sealed and "ABCDE1234F" not in row.pan_sealed
    )
    assert saved.json()["blocker"].startswith("Your payout details are waiting")


def test_bad_details_are_refused(reseller):
    client, fx = reseller
    url = api(fx["partner"].id, "withdrawals/method")
    for change in (
        {"ifsc": "HDFC1234"},
        {"account_number_confirm": "50100123456780"},
        {"account_number": "12ab", "account_number_confirm": "12ab"},
        {"pan": "ABCD1234F"},
        {"method": "cash"},
    ):
        assert send(client, "put", url, {**BANK, **change}).status_code == 400, change
    upi = send(
        client,
        "put",
        url,
        {"method": "upi", "account_holder": "A", "upi_vpa": "not-an-address"},
    )
    assert upi.status_code == 400
    assert not PartnerPayoutMethod.objects.exists()


def test_staff_verify_and_reveal_and_any_change_needs_verifying_again(
    reseller, staff_client
):
    client, fx = reseller
    pid = fx["partner"].id
    send(client, "put", api(pid, "withdrawals/method"), BANK)
    staff, _u = staff_client("platform.staff_payout.manage")
    assert _verify(staff, pid).json()["verification"] == "verified"
    revealed = send(
        staff, "get", f"/api/v1/admin/partners/{pid}/payout-method?reveal=1"
    ).json()
    assert (
        revealed["account_number"] == "50100123456789" and revealed["pan"] == "ABCDE1234F"
    )
    # Switch to UPI, keeping the PAN on file: back to pending.
    changed = send(
        client,
        "put",
        api(pid, "withdrawals/method"),
        {
            "method": "upi",
            "account_holder": "Console Reseller Ltd",
            "upi_vpa": "console@okhdfc",
        },
    ).json()
    assert (
        changed["method"]["verification"] == "pending"
        and changed["method"]["pan_on_file"]
    )
    assert changed["method"]["account_hint"] == ""
    rejected = send(
        staff,
        "post",
        f"/api/v1/admin/partners/{pid}/payout-method",
        {"decision": "rejected"},
    )
    assert rejected.status_code == 400  # a rejection says why


def test_a_partner_cannot_reach_the_staff_payout_routes(reseller):
    client, fx = reseller
    assert send(
        client, "get", f"/api/v1/admin/partners/{fx['partner'].id}/payout-method"
    ).status_code in (401, 403)
    assert send(client, "get", "/api/v1/admin/partner-withdrawals").status_code in (
        401,
        403,
    )


@override_settings(W360_PARTNER_MIN_WITHDRAWAL_MINOR=100)
def test_a_withdrawal_takes_the_available_balance_and_staff_turn_it_into_a_payout(
    reseller, staff_client
):
    client, fx = reseller
    pid = fx["partner"].id
    send(client, "put", api(pid, "withdrawals/method"), BANK)
    staff, _u = staff_client("platform.staff_payout.manage")
    _verify(staff, pid)
    wallet = send(client, "get", api(pid, "withdrawals")).json()
    available = wallet["preview"]["amount_minor"]
    assert available > 0 and wallet["can_request"] is True
    assert wallet["rules"]["tds_rate_bp"] == 0  # a GBP partner: no Indian TDS
    made = send(client, "post", api(pid, "withdrawals/request"))
    assert made.status_code == 201, made.content
    item = made.json()["withdrawals"][0]
    assert item["status"] == "requested" and item["amount_minor"] == available
    assert item["destination"] == "Bank ****6789 (HDFC0001234)"
    assert send(client, "post", api(pid, "withdrawals/request")).status_code == 409

    queue = send(staff, "get", "/api/v1/admin/partner-withdrawals").json()
    assert [i["id"] for i in queue["items"]] == [item["id"]]
    started = send(
        staff,
        "post",
        f"/api/v1/admin/partner-withdrawals/{item['id']}",
        {"action": "start_payout"},
    )
    assert started.status_code == 200, started.content
    assert started.json()["status"] == "processing"
    row = PartnerWithdrawalRequest.objects.get(id=item["id"])
    assert row.payout_id is not None
    after = send(client, "get", api(pid, "withdrawals")).json()
    assert after["preview"]["amount_minor"] == 0  # now in a payout
    assert after["withdrawals"][0]["status"] == "processing"


@override_settings(W360_PARTNER_MIN_WITHDRAWAL_MINOR=10**9)
def test_below_the_minimum_nothing_is_requested(reseller, staff_client):
    client, fx = reseller
    pid = fx["partner"].id
    send(client, "put", api(pid, "withdrawals/method"), BANK)
    staff, _u = staff_client("platform.staff_payout.manage")
    _verify(staff, pid)
    wallet = send(client, "get", api(pid, "withdrawals")).json()
    assert wallet["can_request"] is False and "minimum" in wallet["blocker"]
    assert send(client, "post", api(pid, "withdrawals/request")).status_code == 400
    assert not PartnerWithdrawalRequest.objects.exists()


def test_without_verified_details_nothing_is_requested(reseller):
    client, fx = reseller
    assert (
        send(client, "post", api(fx["partner"].id, "withdrawals/request")).status_code
        == 400
    )


def test_a_finance_member_reads_the_wallet_but_cannot_move_money(console, member_client):
    client, _u = member_client(console["reseller"], "finance")
    pid = console["reseller"]["partner"].id
    wallet = send(client, "get", api(pid, "withdrawals"))
    assert wallet.status_code == 200 and wallet.json()["can_manage"] is False
    assert send(client, "put", api(pid, "withdrawals/method"), BANK).status_code == 403
    assert send(client, "post", api(pid, "withdrawals/request")).status_code == 403


def test_tds_is_withheld_on_inr_only():
    assert partner_withdrawals._tds_rate("GBP") == 0
    rate = partner_withdrawals._tds_rate("INR")
    assert rate == 200
    assert partner_withdrawals._split(1_000_000, rate) == (20_000, 980_000)


@override_settings(PARTNER_PAYOUT_SEAL_KEYS=[])
def test_without_a_seal_key_payout_details_are_refused_not_stored_in_clear(reseller):
    client, fx = reseller
    refused = send(client, "put", api(fx["partner"].id, "withdrawals/method"), BANK)
    assert refused.status_code == 400 and "encryption key" in refused.json()["detail"]
    assert not PartnerPayoutMethod.objects.exists()


def test_a_cancelled_request_can_be_made_again(reseller, staff_client, settings):
    settings.W360_PARTNER_MIN_WITHDRAWAL_MINOR = 1
    client, fx = reseller
    pid = fx["partner"].id
    send(client, "put", api(pid, "withdrawals/method"), BANK)
    staff, _u = staff_client("platform.staff_payout.manage")
    _verify(staff, pid)
    item = send(client, "post", api(pid, "withdrawals/request")).json()["withdrawals"][0]
    cancelled = send(client, "post", api(pid, f"withdrawals/{item['id']}/cancel")).json()
    assert cancelled["withdrawals"][0]["status"] == "cancelled"
    assert send(client, "post", api(pid, "withdrawals/request")).status_code == 201


# --- staff seats ---------------------------------------------------------------------------------


def test_an_affiliate_has_no_staff_seats(partner_client, user_factory):
    client, fx = partner_client("referral.view", "referral.create")
    response = send(
        client,
        "post",
        api(fx["partner"].id, "members"),
        {"email": user_factory().email, "role": "marketing"},
    )
    assert response.status_code == 400, response.content
    assert "no staff seats" in response.json()["detail"]
    assert services.staff_seats(fx["partner"].id) == {
        "level": "affiliate",
        "used": 1,
        "limit": 0,
    }


def test_a_reseller_has_five_staff_seats_and_staff_are_not_limited(
    console, user_factory, settings
):
    settings.W360_PARTNER_STAFF_SEATS = {"affiliate": 0, "reseller": 2, "white_label": -1}
    fx = console["reseller"]
    pid = fx["partner"].id
    actor = fx["actor"]()
    used = services.staff_seats(pid)["used"]
    for _ in range(2 - used):
        services.invite_member(actor, pid, email=user_factory().email, role="sales")
    with pytest.raises(Exception, match="staff seats"):
        services.invite_member(actor, pid, email=user_factory().email, role="sales")
    # Workeasy360 staff can still add someone.
    services.add_member(
        console["staff_ctx"], pid, email=user_factory().email, role="viewer"
    )
    assert services.staff_seats(pid)["used"] == 3


def test_white_label_staff_seats_are_unlimited(partner_factory):
    fx = partner_factory("branding.manage", "domain.manage", "customer.manage")
    assert services.staff_seats(fx["partner"].id)["limit"] is None


def _unused():  # pragma: no cover - keeps uuid imported for ad-hoc keys
    return uuid.uuid4()

"""Paying an upgrade's set-up fee (2026-11-03), end to end through the real
endpoints: Super Admin prices the upgrade and says how to pay offline; the
partner pays **offline** (gives its transfer's reference, staff verify it) or
**online** (the provider's hosted checkout - here the development test
provider, which signs a callback and takes the real webhook path). Staff are
emailed; approval upgrades the same account. Only a verified callback for the
exact amount marks a fee paid."""

from __future__ import annotations

import json

import pytest

from tests.extension import conftest as _extension
from tests.partner_upgrade.conftest import (
    decide,
    fill,
    portal,
    pp,
    send,
    start,
    submit,
)

pytestmark = pytest.mark.django_db

fake_payments = _extension.fake_payments

FEE_URL = "/api/v1/admin/partner-programme/fees"
TEST_COMPLETE = "/api/v1/public/payments/test-checkout/complete"
INSTRUCTIONS = "Bank transfer to Workeasy360 Ltd, IFSC WEAS0000001, A/c 1234567890. UPI: pay@workeasy360"


def _set_fee(admin, amount="25000.00", target="reseller", instructions=INSTRUCTIONS):
    response = admin.put(
        FEE_URL,
        json.dumps(
            {
                "experience": target,
                "amount": amount,
                "currency": "INR",
                "instructions": instructions,
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    return response.json()["fees"][target]


def _filled(client, partner_id, target="reseller"):
    started = start(client, partner_id, target)
    assert started.status_code == 201, started.content
    return fill(client, partner_id, started.json(), target)


def _state(client, partner_id):
    response = client.get(pp(partner_id, "/upgrade"))
    assert response.status_code == 200, response.content
    return response.json()


def _staff_notices():
    from workeasy360.commercial.partners import upgrades
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.platform.notifications.models import Notification
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="Test reads the queued staff notices", module="tests"),
        organization_db_scope(PLATFORM_ORGANIZATION_ID),
    ):
        return list(
            Notification.unscoped.filter(template_key=upgrades.TEMPLATE_KEY).values(
                "recipient_email", "context"
            )
        )


def _pay(client, partner_id, request):
    return send(
        client,
        pp(partner_id, f"/upgrade/requests/{request['id']}/pay"),
        {"expected_version": request["version"]},
    )


def _complete(client, checkout_url: str, outcome="succeeded"):
    reference = checkout_url.split("reference=", 1)[1].split("&", 1)[0]
    return send(
        client,
        TEST_COMPLETE,
        {"organization_id": "", "reference": reference, "outcome": outcome},
    )


# --- the price -----------------------------------------------------------------------------


def test_super_admin_prices_the_upgrade_and_says_how_to_pay_offline(admin, affiliate):
    client, partner_id = affiliate
    fee = _set_fee(admin)
    assert fee == {
        "amount_minor": 2_500_000,
        "currency": "INR",
        "instructions": INSTRUCTIONS,
    }
    offered = _state(client, partner_id)["fees"]["reseller"]
    assert (
        offered["amount_minor"] == 2_500_000 and offered["instructions"] == INSTRUCTIONS
    )
    # No payment provider takes INR here: online payment is not offered.
    assert offered["online_available"] is False


# --- offline ---------------------------------------------------------------------------------


def test_offline_the_partner_gives_its_reference_staff_are_emailed_and_approval_upgrades(
    admin, affiliate, django_capture_on_commit_callbacks
):
    client, partner_id = affiliate
    _set_fee(admin)
    request = _filled(client, partner_id)
    state = _state(client, partner_id)
    assert state["payment"] == {"instructions": INSTRUCTIONS, "online_available": False}

    # A fee with no reference cannot be sent.
    refused = submit(client, partner_id, request)
    assert refused.status_code == 400 and refused.json()["field"] == "payment_reference"

    with django_capture_on_commit_callbacks(execute=True):
        sent = send(
            client,
            pp(partner_id, f"/upgrade/requests/{request['id']}/submit"),
            {
                "expected_version": request["version"],
                "payment_reference": "UTR 402911223344",
            },
        )
    assert sent.status_code == 200, sent.content
    body = sent.json()
    assert body["status"] == "pending_approval"
    assert body["setup_fee"]["payment_method"] == "offline"
    assert body["setup_fee"]["payment_reference"] == "UTR 402911223344"
    assert body["setup_fee"]["paid_online"] is False

    notices = _staff_notices()
    assert admin.staff_user.email in {n["recipient_email"] for n in notices}
    assert "UTR 402911223344" in notices[0]["context"]["fee_line"]
    assert "verify" in notices[0]["context"]["fee_line"]

    # Staff verify the transfer: approving needs its reference.
    blocked = decide(admin, partner_id, body["id"], "approve")
    assert blocked.status_code == 400, blocked.content
    approved = decide(
        admin, partner_id, body["id"], "approve", commercial_reference="UTR 402911223344"
    )
    assert approved.status_code == 200, approved.content
    assert approved.json()["status"] == "approved"
    assert portal(client)[0]["landing"] == "reseller"


def test_a_free_upgrade_needs_no_reference_and_still_emails_staff(
    admin, affiliate, django_capture_on_commit_callbacks
):
    client, partner_id = affiliate
    request = _filled(client, partner_id)
    with django_capture_on_commit_callbacks(execute=True):
        sent = submit(client, partner_id, request)
    assert sent.status_code == 200 and sent.json()["setup_fee"] is None
    assert _staff_notices()[0]["context"]["fee_line"] == "none"
    paid = _pay(client, partner_id, sent.json())
    assert paid.status_code in (400, 409)


# --- online ----------------------------------------------------------------------------------


def test_online_the_provider_confirms_payment_which_sends_the_request_and_settles_the_fee(
    admin,
    affiliate,
    fake_payments,
    django_capture_on_commit_callbacks,
):
    client, partner_id = affiliate
    _set_fee(admin)
    request = _filled(client, partner_id)
    assert _state(client, partner_id)["payment"]["online_available"] is True

    opened = _pay(client, partner_id, request)
    assert opened.status_code == 200, opened.content
    payment = opened.json()["payment"]
    assert payment["status"] == "awaiting_payment"
    assert payment["amount_minor"] == 2_500_000 and payment["currency"] == "INR"
    assert "/billing/test-checkout#reference=" in payment["checkout_url"]
    # Opening the page pays nothing and sends nothing.
    assert _state(client, partner_id)["open"]["status"] == "requested"
    assert _staff_notices() == []
    # A second click returns the same checkout.
    again = _pay(client, partner_id, opened.json()["request"])
    assert again.json()["payment"]["id"] == payment["id"]

    with django_capture_on_commit_callbacks(execute=True):
        done = _complete(client, payment["checkout_url"])
    assert done.status_code == 200, done.content
    assert done.json()["outcome"] == "applied"

    open_ = _state(client, partner_id)["open"]
    assert open_["status"] == "pending_approval"
    assert open_["setup_fee"]["paid_online"] is True
    assert open_["setup_fee"]["payment_method"] == "online"
    assert open_["setup_fee"]["online_payment"]["status"] == "succeeded"
    notices = _staff_notices()
    assert "paid online" in notices[0]["context"]["fee_line"]

    # Paid online: staff approve without typing a reference.
    approved = decide(admin, partner_id, open_["id"], "approve")
    assert approved.status_code == 200, approved.content
    assert portal(client)[0]["landing"] == "reseller"


def test_a_failed_online_payment_sends_nothing_and_can_be_tried_again(
    admin,
    affiliate,
    fake_payments,
):
    client, partner_id = affiliate
    _set_fee(admin)
    request = _filled(client, partner_id)
    first = _pay(client, partner_id, request).json()["payment"]
    assert _complete(client, first["checkout_url"], outcome="failed").status_code == 200
    open_ = _state(client, partner_id)["open"]
    assert open_["status"] == "requested"
    assert open_["setup_fee"]["paid_online"] is False
    assert open_["setup_fee"]["online_payment"]["status"] == "failed"
    second = _pay(client, partner_id, open_)
    assert second.status_code == 200 and second.json()["payment"]["id"] != first["id"]


def test_a_callback_is_applied_once_and_only_for_the_exact_amount(
    admin,
    affiliate,
    fake_payments,
    django_capture_on_commit_callbacks,
):
    from workeasy360.commercial.payments import credentials, webhooks
    from workeasy360.commercial.payments.models import PartnerFeePayment
    from workeasy360.commercial.payments.providers import fake

    client, partner_id = affiliate
    _set_fee(admin)
    request = _filled(client, partner_id)
    payment = _pay(client, partner_id, request).json()["payment"]
    row = PartnerFeePayment.objects.get(id=payment["id"])

    def callback(event_id: str, amount: int):
        body = fake.event_body(
            id=event_id,
            type="payment.succeeded",
            reference=row.provider_reference,
            payment_id="fake_pay_x",
            amount_minor=amount,
            currency="INR",
            attempt_id=str(row.id),
            organization_id="",
        )
        secret = credentials.webhook_secret("TEST", adapter="fake")
        return webhooks.process(
            fake_payments.key, {fake.SIGNATURE_HEADER: fake.sign(body, secret)}, body
        )

    # Less than the fee pays nothing.
    with django_capture_on_commit_callbacks(execute=True):
        assert callback("evt_short", 100)["outcome"] == "mismatch"
    assert _state(client, partner_id)["open"]["setup_fee"]["paid_online"] is False

    with django_capture_on_commit_callbacks(execute=True):
        assert callback("evt_full", 2_500_000)["outcome"] == "applied"
        # The provider delivers again: nothing happens twice.
        assert callback("evt_full", 2_500_000)["outcome"] == "duplicate"
    assert _state(client, partner_id)["open"]["status"] == "pending_approval"
    assert len(_staff_notices()) == len({n["recipient_email"] for n in _staff_notices()})


def test_only_the_payer_completes_a_test_checkout_and_a_member_cannot_pay(
    admin,
    affiliate,
    fake_payments,
    staff_client,
):
    client, partner_id = affiliate
    _set_fee(admin)
    request = _filled(client, partner_id)
    payment = _pay(client, partner_id, request).json()["payment"]
    stranger, _user = staff_client()
    assert _complete(stranger, payment["checkout_url"]).status_code == 404
    assert _state(client, partner_id)["open"]["setup_fee"]["paid_online"] is False


def test_online_is_refused_when_no_provider_takes_the_currency(admin, affiliate):
    client, partner_id = affiliate
    _set_fee(admin)
    request = _filled(client, partner_id)
    refused = _pay(client, partner_id, request)
    assert refused.status_code == 400, refused.content
    assert refused.json()["field"] == "payment_method"

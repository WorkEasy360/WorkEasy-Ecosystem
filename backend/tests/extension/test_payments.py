"""Payments: invoices, hosted checkout through the real adapters and egress,
signed webhooks, dunning, refunds, manual settlement, isolation and races
(Phase 8 extension, section 6)."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from tests.extension.conftest import buy_directly, url

pytestmark = pytest.mark.django_db

STRIPE_KEY = "sk_test_do_not_leak_4242"
STRIPE_SECRET = "whsec_test_signing_secret_1"
RAZORPAY_KEY = "rzp_test_id:rzp_test_secret_do_not_leak"
RAZORPAY_SECRET = "razorpay_webhook_secret_1"


# --- fixtures ---------------------------------------------------------------------


@pytest.fixture
def provider_server(local_egress, settings):
    from tests.factories.fake_payment_provider import FakePaymentProvider

    server = FakePaymentProvider()
    base = server.start()
    settings.PAYMENT_PROVIDER_ENDPOINTS = {"stripe": base, "razorpay": base}
    yield server
    server.stop()


@pytest.fixture
def providers(monkeypatch, provider_server):
    from workeasy360.commercial.payments.models import PaymentProvider

    monkeypatch.setenv("W360_PAYMENT_CREDENTIAL_STRIPE_TEST", STRIPE_KEY)
    monkeypatch.setenv("W360_PAYMENT_WEBHOOK_SECRET_STRIPE_TEST", STRIPE_SECRET)
    monkeypatch.setenv("W360_PAYMENT_CREDENTIAL_RAZORPAY_TEST", RAZORPAY_KEY)
    monkeypatch.setenv("W360_PAYMENT_WEBHOOK_SECRET_RAZORPAY_TEST", RAZORPAY_SECRET)
    stripe = PaymentProvider.objects.create(
        key="stripe-uk",
        adapter="stripe",
        display_name="Stripe UK",
        status="enabled",
        credential_ref="STRIPE_TEST",
        webhook_secret_ref="STRIPE_TEST",
        currencies=["GBP"],
        priority=10,
    )
    razorpay = PaymentProvider.objects.create(
        key="razorpay-in",
        adapter="razorpay",
        display_name="Razorpay India",
        status="enabled",
        credential_ref="RAZORPAY_TEST",
        webhook_secret_ref="RAZORPAY_TEST",
        currencies=["INR"],
        priority=10,
    )
    return {"stripe": stripe, "razorpay": razorpay, "server": provider_server}


def _buy(org, catalog, plan="crm_professional", currency="GBP"):
    """An active subscription with an open period invoice (see buy_directly)."""
    buy_directly(org, catalog["versions"][plan].id, currency=currency)


@pytest.fixture
def paying_org(console_org, login, catalog):
    org = console_org()
    owner = login(org["owner"])
    _buy(org, catalog)
    invoices = owner.get(url(org["slug"], "billing/invoices")).json()["results"]
    assert len(invoices) == 1
    return {**org, "client": owner, "invoice": invoices[0], "catalog": catalog}


def _pay(fx, key=None):
    response = fx["client"].post(
        url(fx["slug"], f"billing/invoices/{fx['invoice']['invoice_id']}/pay"),
        HTTP_IDEMPOTENCY_KEY=key or uuid.uuid4().hex,
    )
    assert response.status_code == 202, response.content
    return response.json()


def _checkout(fx, attempt):
    from workeasy360.commercial.payments.checkout import create_checkout

    return create_checkout(fx["org"].id, uuid.UUID(attempt["attempt_id"]))


def _status(fx, attempt):
    return (
        fx["client"]
        .get(url(fx["slug"], f"billing/payments/{attempt['attempt_id']}"))
        .json()
    )


def _stripe_event(
    fx,
    attempt,
    *,
    kind="checkout.session.completed",
    amount=None,
    org=None,
    reference=None,
    event_id=None,
    status="paid",
):
    body = json.dumps(
        {
            "id": event_id or f"evt_{uuid.uuid4().hex}",
            "type": kind,
            "data": {
                "object": {
                    "id": reference
                    or _status(fx, attempt).get("reference")
                    or attempt.get("reference"),
                    "payment_status": status,
                    "payment_intent": "pi_test_1",
                    "amount_total": fx["invoice"]["total_minor"]
                    if amount is None
                    else amount,
                    "currency": "gbp",
                    "metadata": {
                        "w360_attempt": attempt["attempt_id"],
                        "w360_org": str(org or fx["org"].id),
                    },
                }
            },
        }
    ).encode()
    return body


def _stripe_signature(body, *, secret=STRIPE_SECRET, at=None):
    t = int(at if at is not None else time.time())
    digest = hmac.new(
        secret.encode(), f"{t}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return f"t={t},v1={digest}"


def _deliver(
    client, provider_key, body, signature, header="HTTP_STRIPE_SIGNATURE", **extra
):
    return client.post(
        f"/api/v1/public/payments/webhooks/{provider_key}",
        data=body,
        content_type="application/json",
        **{header: signature},
        **extra,
    )


def _reference(fx, attempt_id):
    from workeasy360.commercial.payments.models import PaymentAttempt
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="Test reads a payment attempt", module="tests"),
        organization_db_scope(fx["org"].id),
    ):
        return PaymentAttempt.unscoped.get(id=attempt_id).provider_reference


def _invoice(fx):
    return (
        fx["client"]
        .get(url(fx["slug"], f"billing/invoices/{fx['invoice']['invoice_id']}"))
        .json()
    )


# --- invoices ----------------------------------------------------------------------


def test_a_purchase_issues_one_invoice_from_the_price_agreed(paying_org):
    from workeasy360.commercial.payments.invoicing import ensure_invoices

    invoice = _invoice(paying_org)
    assert invoice["status"] == "open" and invoice["currency"] == "GBP"
    assert invoice["total_minor"] == 4999
    assert [line["plan_code"] for line in invoice["lines"]] == ["crm_professional"]
    assert invoice["number"].startswith("INV-")
    # Idempotent under every retry path: listener, clock, a second worker.
    assert ensure_invoices(paying_org["org"].id) == []
    assert (
        len(
            paying_org["client"]
            .get(url(paying_org["slug"], "billing/invoices"))
            .json()["results"]
        )
        == 1
    )


def test_a_trial_owes_nothing_yet(console_org, login, catalog):
    org = console_org()
    owner = login(org["owner"])
    owner.post(
        url(org["slug"], "billing-account"),
        {"currency": "GBP"},
        content_type="application/json",
    )
    started = owner.post(
        url(org["slug"], "orders"),
        {
            "plan_version_id": str(catalog["versions"]["crm_starter"].id),
            "with_trial": True,
        },
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert started.status_code == 201, started.content
    assert started.json()["requires_payment"] is False
    assert owner.get(url(org["slug"], "billing/invoices")).json()["results"] == []


def test_invoices_are_the_organizations_own(paying_org, console_org, login):
    other = console_org()
    stranger = login(other["owner"])
    invoice_id = paying_org["invoice"]["invoice_id"]
    assert (
        stranger.get(url(other["slug"], f"billing/invoices/{invoice_id}")).status_code
        == 404
    )
    response = stranger.post(
        url(other["slug"], f"billing/invoices/{invoice_id}/pay"),
        HTTP_IDEMPOTENCY_KEY="k1",
    )
    assert response.status_code == 404
    member = paying_org["add"]("member")
    assert (
        login(member["user"]).get(url(paying_org["slug"], "billing/invoices")).status_code
        == 403
    )


# --- starting a payment -------------------------------------------------------------


def test_paying_needs_billing_manage_but_no_step_up(paying_org, providers, login):
    """Phase 9 (owner decision): paying through hosted checkout needs the
    permission, not a second factor - the provider authenticates the payer."""
    member = paying_org["add"]("member")
    billing = paying_org["add"]("billing_manager")
    target = url(
        paying_org["slug"], f"billing/invoices/{paying_org['invoice']['invoice_id']}/pay"
    )
    assert login(member["user"]).post(target, HTTP_IDEMPOTENCY_KEY="a").status_code == 403
    accepted = login(billing["user"]).post(target, HTTP_IDEMPOTENCY_KEY="b")
    assert accepted.status_code == 202 and accepted.json()["status"] == "pending"


def test_a_billing_manager_sees_the_subscription_but_cannot_buy(paying_org, login):
    """The role is "billing and subscriptions only": it reads the subscription
    it pays for (granted by authz.0008), and buying stays the owner's."""
    billing = login(paying_org["add"]("billing_manager")["user"])
    permissions = billing.get(url(paying_org["slug"], "access")).json()["permissions"]
    assert "commercial.subscription.view" in permissions
    assert "commercial.subscription.manage" not in permissions
    assert billing.get(url(paying_org["slug"], "subscriptions")).status_code == 200
    bought = billing.post(
        url(paying_org["slug"], "orders"),
        {
            "plan_version_id": str(
                paying_org["catalog"]["versions"]["books_professional"].id
            )
        },
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert bought.status_code == 403


def test_one_payment_in_flight_per_invoice(paying_org, providers):
    first = _pay(paying_org, key="same-key")
    assert _pay(paying_org, key="same-key")["attempt_id"] == first["attempt_id"]
    assert _pay(paying_org)["attempt_id"] == first["attempt_id"]  # another tab


def test_no_provider_for_the_currency_is_a_clear_503(paying_org):
    response = paying_org["client"].post(
        url(
            paying_org["slug"],
            f"billing/invoices/{paying_org['invoice']['invoice_id']}/pay",
        ),
        HTTP_IDEMPOTENCY_KEY="k",
    )
    assert (response.status_code, response.json()["code"]) == (
        503,
        "payment_provider_unavailable",
    )


# --- checkout through the real adapter and egress -----------------------------------------


def test_stripe_checkout_is_created_with_the_invoice_and_nothing_else(
    paying_org, providers
):
    attempt = _pay(paying_org)
    assert _checkout(paying_org, attempt) == "awaiting_payment"
    (sent,) = providers["server"].requests
    form = sent.form()
    assert sent.path == "/v1/checkout/sessions"
    assert sent.headers["authorization"] == f"Bearer {STRIPE_KEY}"
    assert sent.headers["idempotency-key"] == f"w360-checkout-{attempt['attempt_id']}"
    assert form["mode"] == "payment"
    assert form["line_items[0][price_data][unit_amount]"] == "4999"
    assert form["line_items[0][price_data][currency]"] == "gbp"
    assert form["metadata[w360_attempt]"] == attempt["attempt_id"]
    assert form["metadata[w360_org]"] == str(paying_org["org"].id)
    assert form["customer_email"] == "billing@example.com"
    assert "card" not in json.dumps(form).lower()
    # The payer comes back to the organization's billing page, which reads
    # the attempt and outcome (and only trusts the webhook).
    slug = paying_org["slug"]
    assert form["success_url"].endswith(
        f"/o/{slug}/settings/billing?payment={attempt['attempt_id']}&outcome=success"
    )
    assert form["cancel_url"].endswith(
        f"/o/{slug}/settings/billing?payment={attempt['attempt_id']}&outcome=cancelled"
    )

    view = _status(paying_org, attempt)
    assert view["status"] == "awaiting_payment"
    assert view["checkout_url"].startswith("https://checkout.stripe.com/")
    # A second run finds nothing pending and calls nobody.
    assert _checkout(paying_org, attempt) == "not_pending"
    assert len(providers["server"].requests) == 1


def test_a_provider_outage_is_retried(paying_org, providers):
    providers["server"].fail("/v1/checkout/sessions", 503)
    attempt = _pay(paying_org)
    assert _checkout(paying_org, attempt) == "retry"
    assert _status(paying_org, attempt)["status"] == "pending"
    assert _checkout(paying_org, attempt) == "awaiting_payment"


def test_a_provider_refusal_fails_the_attempt_and_a_new_one_can_start(
    paying_org, providers
):
    providers["server"].fail("/v1/checkout/sessions", 400)
    attempt = _pay(paying_org)
    assert _checkout(paying_org, attempt) == "failed"
    assert _status(paying_org, attempt)["failure_code"] == "checkout_bad_request"
    retry = _pay(paying_org)
    assert retry["attempt_id"] != attempt["attempt_id"]


def test_a_missing_credential_fails_checkout_without_leaking(
    paying_org, providers, monkeypatch, caplog
):
    monkeypatch.delenv("W360_PAYMENT_CREDENTIAL_STRIPE_TEST")
    attempt = _pay(paying_org)
    assert _checkout(paying_org, attempt) == "failed"
    assert _status(paying_org, attempt)["failure_code"] == "checkout_unavailable"
    assert STRIPE_KEY not in caplog.text


# --- webhooks -----------------------------------------------------------------------


def test_a_signed_webhook_settles_the_invoice_exactly_once(paying_org, providers, client):
    attempt = _pay(paying_org)
    _checkout(paying_org, attempt)
    reference = _reference(paying_org, attempt["attempt_id"])
    body = _stripe_event(
        paying_org, attempt, reference=reference, event_id="evt_settle_1"
    )

    first = _deliver(client, "stripe-uk", body, _stripe_signature(body))
    assert first.status_code == 200 and first.json()["outcome"] == "applied"
    again = _deliver(client, "stripe-uk", body, _stripe_signature(body))
    assert again.json()["outcome"] == "duplicate"

    invoice = _invoice(paying_org)
    assert (invoice["status"], invoice["amount_paid_minor"]) == ("paid", 4999)
    assert _status(paying_org, attempt)["status"] == "succeeded"
    assert _status(paying_org, attempt)["checkout_url"] is None
    audit = (
        paying_org["client"]
        .get(url(paying_org["slug"], "audit"), {"action": "billing."})
        .json()["results"]
    )
    assert "billing.payment_succeeded" in {e["action"] for e in audit}


@pytest.mark.parametrize(
    "tamper", ["bad_signature", "stale", "other_secret", "unknown_provider", "too_large"]
)
def test_unverifiable_webhooks_are_refused_uniformly(
    paying_org, providers, client, settings, tamper
):
    attempt = _pay(paying_org)
    _checkout(paying_org, attempt)
    body = _stripe_event(
        paying_org, attempt, reference=_reference(paying_org, attempt["attempt_id"])
    )
    provider_key = "stripe-uk"
    signature = _stripe_signature(body)
    if tamper == "bad_signature":
        signature = signature[:-4] + "0000"
    elif tamper == "stale":
        signature = _stripe_signature(body, at=time.time() - 3600)
    elif tamper == "other_secret":
        signature = _stripe_signature(body, secret="whsec_someone_else")
    elif tamper == "unknown_provider":
        provider_key = "nope"
    elif tamper == "too_large":
        settings.PAYMENT_WEBHOOK_MAX_BYTES = 10
    response = _deliver(client, provider_key, body, signature)
    assert (response.status_code, response.json()["code"]) == (400, "webhook_rejected")
    assert _invoice(paying_org)["status"] == "open"


def test_a_short_payment_is_recorded_but_never_settles(paying_org, providers, client):
    attempt = _pay(paying_org)
    _checkout(paying_org, attempt)
    body = _stripe_event(
        paying_org,
        attempt,
        reference=_reference(paying_org, attempt["attempt_id"]),
        amount=1,
    )
    assert (
        _deliver(client, "stripe-uk", body, _stripe_signature(body)).json()["outcome"]
        == "mismatch"
    )
    assert _invoice(paying_org)["status"] == "open"


def test_a_webhook_cannot_reach_another_tenants_payment(
    paying_org, providers, client, console_org
):
    other = console_org()
    attempt = _pay(paying_org)
    _checkout(paying_org, attempt)
    body = _stripe_event(
        paying_org,
        attempt,
        reference=_reference(paying_org, attempt["attempt_id"]),
        org=other["org"].id,
    )
    assert (
        _deliver(client, "stripe-uk", body, _stripe_signature(body)).json()["outcome"]
        == "unmatched"
    )
    assert _invoice(paying_org)["status"] == "open"


def test_order_of_delivery_does_not_matter(paying_org, providers, client):
    attempt = _pay(paying_org)
    _checkout(paying_org, attempt)
    reference = _reference(paying_org, attempt["attempt_id"])
    expired = _stripe_event(
        paying_org, attempt, kind="checkout.session.expired", reference=reference
    )
    paid = _stripe_event(paying_org, attempt, reference=reference)
    _deliver(client, "stripe-uk", expired, _stripe_signature(expired))
    assert _status(paying_org, attempt)["status"] == "expired"
    # The provider took the money after all: a late success is still money.
    assert (
        _deliver(client, "stripe-uk", paid, _stripe_signature(paid)).json()["outcome"]
        == "applied"
    )
    assert _invoice(paying_org)["status"] == "paid"
    failed = _stripe_event(
        paying_org,
        attempt,
        kind="checkout.session.async_payment_failed",
        reference=reference,
    )
    _deliver(client, "stripe-uk", failed, _stripe_signature(failed))
    assert _status(paying_org, attempt)["status"] == "succeeded"  # never undone


def test_razorpay_payment_links_end_to_end(
    console_org, login, catalog, providers, client
):
    from tests.extension.conftest import staff_scope
    from workeasy360.commercial.pricing import services as pricing
    from workeasy360.contracts.enums import BillingInterval, PriceBookChannel
    from workeasy360.contracts.values.money import CurrencyCode

    with staff_scope("platform.staff_pricing.manage") as sctx:
        book = pricing.create_price_book(
            sctx,
            code="direct_inr",
            name="Direct INR",
            currency=CurrencyCode.INR,
            channel=PriceBookChannel.DIRECT,
            valid_from=timezone.now() - timedelta(days=1),
        )
        pricing.publish_price(
            sctx,
            price_book_id=book.id,
            plan_version_id=catalog["versions"]["crm_professional"].id,
            interval=BillingInterval.MONTHLY,
            amount_minor=499900,
            valid_from=timezone.now() - timedelta(days=1),
        )
    org = console_org()
    owner = login(org["owner"])
    _buy(org, catalog, currency="INR")
    invoice = owner.get(url(org["slug"], "billing/invoices")).json()["results"][0]
    fx = {**org, "client": owner, "invoice": invoice}
    attempt = _pay(fx)
    assert _checkout(fx, attempt) == "awaiting_payment"
    sent = providers["server"].requests[-1]
    assert sent.path == "/v1/payment_links"
    assert sent.json()["amount"] == 499900 and sent.json()["currency"] == "INR"
    assert sent.json()["notes"]["w360_attempt"] == attempt["attempt_id"]
    assert sent.headers["authorization"].startswith("Basic ")

    reference = _reference(fx, attempt["attempt_id"])
    body = json.dumps(
        {
            "entity": "event",
            "event": "payment_link.paid",
            "payload": {
                "payment_link": {
                    "entity": {
                        "id": reference,
                        "amount": 499900,
                        "currency": "INR",
                        "notes": {
                            "w360_attempt": attempt["attempt_id"],
                            "w360_org": str(org["org"].id),
                        },
                    }
                },
                "payment": {
                    "entity": {
                        "id": "pay_1",
                        "amount": 499900,
                        "currency": "INR",
                        "status": "captured",
                    }
                },
            },
        }
    ).encode()
    signature = hmac.new(RAZORPAY_SECRET.encode(), body, hashlib.sha256).hexdigest()
    ok = _deliver(
        client,
        "razorpay-in",
        body,
        signature,
        header="HTTP_X_RAZORPAY_SIGNATURE",
        HTTP_X_RAZORPAY_EVENT_ID="evt_rzp_1",
    )
    assert ok.json()["outcome"] == "applied"
    dup = _deliver(
        client,
        "razorpay-in",
        body,
        signature,
        header="HTTP_X_RAZORPAY_SIGNATURE",
        HTTP_X_RAZORPAY_EVENT_ID="evt_rzp_1",
    )
    assert dup.json()["outcome"] == "duplicate"
    forged = _deliver(
        client,
        "razorpay-in",
        body,
        "0" * 64,
        header="HTTP_X_RAZORPAY_SIGNATURE",
        HTTP_X_RAZORPAY_EVENT_ID="evt_rzp_2",
    )
    assert forged.status_code == 400
    assert (
        owner.get(url(org["slug"], f"billing/invoices/{invoice['invoice_id']}")).json()[
            "status"
        ]
        == "paid"
    )


# --- dunning -----------------------------------------------------------------------------


def _subscription_status(fx):
    return (
        fx["client"].get(url(fx["slug"], "subscriptions")).json()["results"][0]["status"]
    )


def test_dunning_moves_past_due_grace_suspended_and_payment_restores(
    paying_org, providers, client, settings
):
    from workeasy360.commercial.payments.dunning import process_due

    due = timezone.now()
    process_due(at=due + timedelta(hours=1))
    assert _subscription_status(paying_org) == "past_due"
    process_due(at=due + timedelta(days=settings.PAYMENT_GRACE_AFTER_DAYS, hours=2))
    assert _subscription_status(paying_org) == "grace"
    process_due(at=due + timedelta(days=settings.PAYMENT_SUSPEND_AFTER_DAYS, hours=3))
    assert _subscription_status(paying_org) == "suspended"
    process_due(
        at=due + timedelta(days=settings.PAYMENT_SUSPEND_AFTER_DAYS, hours=4)
    )  # idempotent
    assert _subscription_status(paying_org) == "suspended"

    attempt = _pay(paying_org)
    _checkout(paying_org, attempt)
    body = _stripe_event(
        paying_org, attempt, reference=_reference(paying_org, attempt["attempt_id"])
    )
    _deliver(client, "stripe-uk", body, _stripe_signature(body))
    assert _subscription_status(paying_org) == "active"


def test_a_payment_never_lifts_a_suspension_staff_applied(
    paying_org, providers, client, staff_login
):
    subscription_id = (
        paying_org["client"]
        .get(url(paying_org["slug"], "subscriptions"))
        .json()["results"][0]["id"]
    )
    staff, _ = staff_login("platform.staff_subscription.manage")
    suspended = staff.post(
        f"/api/v1/admin/customers/{paying_org['org'].id}/subscriptions/{subscription_id}/suspend",
        {"reason": "Fraud review in progress"},
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="suspend-1",
    )
    assert suspended.status_code == 200, suspended.content
    attempt = _pay(paying_org)
    _checkout(paying_org, attempt)
    body = _stripe_event(
        paying_org, attempt, reference=_reference(paying_org, attempt["attempt_id"])
    )
    _deliver(client, "stripe-uk", body, _stripe_signature(body))
    assert _invoice(paying_org)["status"] == "paid"
    assert _subscription_status(paying_org) == "suspended"


def test_stale_checkouts_expire_on_the_clock(paying_org, providers):
    from workeasy360.commercial.payments.dunning import process_due

    attempt = _pay(paying_org)
    _checkout(paying_org, attempt)
    process_due(at=timezone.now() + timedelta(hours=3))
    view = _status(paying_org, attempt)
    assert (view["status"], view["checkout_url"]) == ("expired", None)


# --- staff: refunds, manual settlement, void, providers -------------------------------------


def _settle(fx, client):
    attempt = _pay(fx)
    _checkout(fx, attempt)
    body = _stripe_event(fx, attempt, reference=_reference(fx, attempt["attempt_id"]))
    _deliver(client, "stripe-uk", body, _stripe_signature(body))
    return attempt


def test_refunds_are_bounded_idempotent_and_confirmed(
    paying_org, providers, client, staff_login
):
    from workeasy360.commercial.payments.refunds import send_refund

    attempt = _settle(paying_org, client)
    staff, _ = staff_login("platform.staff_payment.manage")
    target = f"/api/v1/admin/customers/{paying_org['org'].id}/payments/{attempt['attempt_id']}/refund"
    too_much = staff.post(
        target,
        {"amount_minor": 5000, "reason": "Customer asked for a refund"},
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="r0",
    )
    assert (too_much.status_code, too_much.json()["code"]) == (409, "payment_not_allowed")
    first = staff.post(
        target,
        {"amount_minor": 999, "reason": "Partial refund agreed"},
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="r1",
    )
    assert first.status_code == 202
    replay = staff.post(
        target,
        {"amount_minor": 999, "reason": "Partial refund agreed"},
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="r1",
    )
    assert replay.json()["refund_id"] == first.json()["refund_id"]
    # 999 is in flight: only 4000 more may be requested.
    over = staff.post(
        target,
        {"amount_minor": 4001, "reason": "Second partial refund"},
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="r2",
    )
    assert over.status_code == 409

    assert (
        send_refund(paying_org["org"].id, uuid.UUID(first.json()["refund_id"]))
        == "applied"
    )
    sent = providers["server"].requests[-1]
    assert sent.path == "/v1/refunds" and sent.form()["amount"] == "999"
    assert sent.headers["idempotency-key"] == f"w360-refund-{first.json()['refund_id']}"
    invoice = _invoice(paying_org)
    assert invoice["amount_refunded_minor"] == 999
    billing = staff.get(f"/api/v1/admin/customers/{paying_org['org'].id}/billing").json()
    assert billing["invoices"][0]["attempts"][0]["refunds"][0]["status"] == "succeeded"


def test_an_unknown_razorpay_refund_outcome_is_never_resent(
    paying_org, providers, client, staff_login
):
    from workeasy360.commercial.payments.models import PaymentAttempt, Refund
    from workeasy360.commercial.payments.refunds import send_refund
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    attempt = _settle(paying_org, client)
    staff, _ = staff_login("platform.staff_payment.manage")
    refund = staff.post(
        f"/api/v1/admin/customers/{paying_org['org'].id}/payments/{attempt['attempt_id']}/refund",
        {"amount_minor": 100, "reason": "Goodwill refund for downtime"},
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="rz",
    ).json()
    with (
        unscoped_access(reason="Test simulates a crashed refund", module="tests"),
        organization_db_scope(paying_org["org"].id),
    ):
        PaymentAttempt.unscoped.filter(id=attempt["attempt_id"]).update(
            provider_key="razorpay-in"
        )
        Refund.unscoped.filter(id=refund["refund_id"]).update(status="submitting")
    before = len(providers["server"].requests)
    assert (
        send_refund(paying_org["org"].id, uuid.UUID(refund["refund_id"]))
        == "needs_review"
    )
    assert len(providers["server"].requests) == before


def test_manual_settlement_and_void_need_manage_reason_and_version(
    paying_org, staff_login, catalog, console_org, login
):
    viewer, _ = staff_login("platform.staff_payment.view")
    base = f"/api/v1/admin/customers/{paying_org['org'].id}/invoices/{paying_org['invoice']['invoice_id']}"
    body = {
        "reference": "BANK-TX-8842",
        "reason": "Paid by bank transfer",
        "version": paying_org["invoice"]["version"],
    }
    assert (
        viewer.post(
            f"{base}/mark-paid", body, content_type="application/json"
        ).status_code
        == 403
    )
    manager, _ = staff_login("platform.staff_payment.manage")
    stale = manager.post(
        f"{base}/mark-paid", {**body, "version": 99}, content_type="application/json"
    )
    assert stale.status_code == 409
    paid = manager.post(f"{base}/mark-paid", body, content_type="application/json")
    assert paid.status_code == 200 and paid.json()["status"] == "paid"
    assert (
        manager.post(
            f"{base}/void",
            {"reason": "Raised in error", "version": paid.json()["version"]},
            content_type="application/json",
        ).status_code
        == 409
    )
    audit = (
        paying_org["client"]
        .get(url(paying_org["slug"], "audit"), {"action": "billing.invoice_marked"})
        .json()["results"]
    )
    assert audit[0]["reason"] == "Paid by bank transfer"

    other = console_org()
    owner = login(other["owner"])
    _buy(other, catalog)
    invoice = owner.get(url(other["slug"], "billing/invoices")).json()["results"][0]
    voided = manager.post(
        f"/api/v1/admin/customers/{other['org'].id}/invoices/{invoice['invoice_id']}/void",
        {"reason": "Duplicate customer account", "version": invoice["version"]},
        content_type="application/json",
    )
    assert voided.json()["status"] == "void"


def test_provider_settings_never_expose_a_secret(providers, staff_login):
    viewer, _ = staff_login("platform.staff_payment.view")
    response = viewer.get("/api/v1/admin/payments/providers")
    text = response.content.decode()
    assert (
        STRIPE_KEY not in text and STRIPE_SECRET not in text and RAZORPAY_KEY not in text
    )
    stripe = next(p for p in response.json()["results"] if p["key"] == "stripe-uk")
    assert stripe["credential_configured"] is True
    assert stripe["credential_variable"] == "W360_PAYMENT_CREDENTIAL_STRIPE_TEST"
    assert stripe["webhook_path"] == "/api/v1/public/payments/webhooks/stripe-uk"


def test_providers_are_configured_by_reference_only(staff_login, settings):
    manager, _ = staff_login("platform.staff_payment.manage")
    base = {
        "key": "stripe-eu",
        "adapter": "stripe",
        "display_name": "Stripe EU",
        "mode": "live",
        "webhook_secret_ref": "STRIPE_EU",
        "currencies": ["EUR"],
    }
    leaked = manager.post(
        "/api/v1/admin/payments/providers",
        {**base, "credential_ref": "sk_live_51H8abc$secret"},
        content_type="application/json",
    )
    assert leaked.status_code == 400  # a pasted key is not a variable-name suffix
    created = manager.post(
        "/api/v1/admin/payments/providers",
        {**base, "credential_ref": "STRIPE_EU"},
        content_type="application/json",
    )
    assert created.status_code == 201 and created.json()["status"] == "disabled"
    enabling = manager.patch(
        "/api/v1/admin/payments/providers/stripe-eu",
        {"status": "enabled", "version": 1},
        content_type="application/json",
    )
    assert (enabling.status_code, enabling.json()["code"]) == (
        409,
        "payment_not_allowed",
    )  # secrets absent
    test_provider = manager.post(
        "/api/v1/admin/payments/providers",
        {**base, "key": "fake-1", "adapter": "fake", "credential_ref": "X"},
        content_type="application/json",
    )
    assert test_provider.status_code == 400  # the development provider is refused here


def test_the_development_test_checkout_is_invisible_unless_enabled(client, settings):
    settings.PAYMENTS_FAKE_PROVIDER_ENABLED = False
    response = client.post(
        "/api/v1/public/payments/test-checkout/complete",
        {},
        content_type="application/json",
    )
    assert response.status_code == 404


def test_the_development_provider_takes_the_real_webhook_path(
    paying_org, settings, login
):
    from workeasy360.commercial.payments.models import PaymentProvider

    settings.PAYMENTS_FAKE_PROVIDER_ENABLED = True
    PaymentProvider.objects.create(
        key="test-provider",
        adapter="fake",
        display_name="Test",
        status="enabled",
        credential_ref="TEST",
        webhook_secret_ref="TEST",
        currencies=["GBP"],
    )
    attempt = _pay(paying_org)
    assert _checkout(paying_org, attempt) == "awaiting_payment"
    view = _status(paying_org, attempt)
    assert "/billing/test-checkout#reference=" in view["checkout_url"]
    reference = _reference(paying_org, attempt["attempt_id"])
    done = paying_org["client"].post(
        "/api/v1/public/payments/test-checkout/complete",
        {
            "reference": reference,
            "organization_id": str(paying_org["org"].id),
            "outcome": "succeeded",
        },
        content_type="application/json",
    )
    assert done.json()["outcome"] == "applied"
    assert _invoice(paying_org)["status"] == "paid"


def test_the_staff_dashboard_totals_collections(
    paying_org, providers, client, staff_login
):
    _settle(paying_org, client)
    staff, _ = staff_login("platform.staff_payment.view", "platform.staff_customer.view")
    collections = staff.get("/api/v1/admin/dashboard").json()["collections"]
    assert collections["by_currency"] == [
        {"currency": "GBP", "amount_minor": 4999, "payments": 1}
    ]
    # The month overview draws the same collection on the day it arrived.
    from django.utils import timezone

    revenue = staff.get("/api/v1/admin/dashboard").json()["overview"]["revenue"]
    assert [(r["currency"], r["amount_minor"], r["payments"]) for r in revenue] == [
        ("GBP", 4999, 1)
    ]
    assert revenue[0]["daily"][timezone.now().day - 1] == 4999


# --- races (real threads, real commits) ------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_simultaneous_webhook_redeliveries_settle_once(paying_org, providers):
    from django.test import Client

    attempt = _pay(paying_org)
    _checkout(paying_org, attempt)
    body = _stripe_event(
        paying_org,
        attempt,
        reference=_reference(paying_org, attempt["attempt_id"]),
        event_id="evt_race",
    )
    signature = _stripe_signature(body)
    barrier = threading.Barrier(4)
    outcomes: list[str] = []

    def deliver():
        barrier.wait()
        response = _deliver(Client(), "stripe-uk", body, signature)
        outcomes.append(response.json().get("outcome", response.json().get("code")))
        connection.close()

    threads = [threading.Thread(target=deliver) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["applied", "duplicate", "duplicate", "duplicate"]
    assert _invoice(paying_org)["amount_paid_minor"] == 4999


@pytest.mark.django_db(transaction=True)
def test_simultaneous_pay_clicks_create_one_attempt(paying_org, providers, login):
    from workeasy360.commercial.payments.models import PaymentAttempt
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    clients = [login(paying_org["owner"]) for _ in range(3)]
    barrier = threading.Barrier(3)
    ids: list[str] = []

    def click(c):
        barrier.wait()
        response = c.post(
            url(
                paying_org["slug"],
                f"billing/invoices/{paying_org['invoice']['invoice_id']}/pay",
            ),
            HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
        )
        ids.append(response.json()["attempt_id"])
        connection.close()

    threads = [threading.Thread(target=click, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(ids)) == 1
    with (
        unscoped_access(reason="Test counts payment attempts", module="tests"),
        organization_db_scope(paying_org["org"].id),
    ):
        assert (
            PaymentAttempt.unscoped.filter(organization_id=paying_org["org"].id).count()
            == 1
        )

"""Acceptance data for the Admin Panel browser check, on top of the ecosystem seed.

Everything here goes through the real endpoints a person would use, or the real services
a product would call, so a screen that lists it is listing what the system really wrote.
Prices and rates are test figures for this scratch database only.

Scratch databases only. Every password is ``CHECK_PASSWORD``.
"""

# ruff: noqa: S105, S106

import base64
import os
import uuid

import django

django.setup()

from django.test import Client  # noqa: E402
from django.utils import timezone  # noqa: E402

from workeasy360.commercial.catalog.models import PlanVersion  # noqa: E402
from workeasy360.commercial.payments.checkout import create_checkout  # noqa: E402
from workeasy360.commercial.payments.invoicing import organization_scope  # noqa: E402
from workeasy360.commercial.payments.models import PaymentAttempt  # noqa: E402
from workeasy360.commercial.wallets import usage  # noqa: E402
from workeasy360.platform.authz import staff as grants  # noqa: E402
from workeasy360.platform.identity import lifecycle  # noqa: E402
from workeasy360.platform.identity.models import User  # noqa: E402
from workeasy360.platform.organizations.models import Organization  # noqa: E402
from workeasy360.platform.tenancy.unscoped import unscoped_access  # noqa: E402

PASSWORD = os.environ["CHECK_PASSWORD"]
STAFF = os.environ["PLATFORM_SUPERADMIN_EMAIL"]
BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def signed_in(email: str) -> Client:
    client = Client(HTTP_USER_AGENT=BROWSER, REMOTE_ADDR="127.0.0.1")
    client.get("/api/v1/auth/session")
    response = client.post(
        "/api/v1/auth/login",
        {"email": email, "password": PASSWORD},
        content_type="application/json",
    )
    assert response.status_code == 200, (email, response.content)
    return client


def send(client: Client, url: str, body=None, *, method="post", key=None, want=(200, 201, 202)):
    extra = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
    response = getattr(client, method)(url, body or {}, content_type="application/json", **extra)
    assert response.status_code in want, (url, response.status_code, response.content[:400])
    return response.json() if response.content else {}


def version_of(plan_code: str) -> str:
    return str(PlanVersion.objects.filter(plan__code=plan_code).order_by("-version").first().id)


staff = signed_in(STAFF)
admin = "/api/v1/admin"

# --- settings ---------------------------------------------------------------------------
current = staff.get(f"{admin}/settings")
if current.status_code == 200:
    body = current.json()
    send(
        staff,
        f"{admin}/settings",
        {
            **body,
            "support_email": "support@workeasy360.dev",
            "sales_email": "sales@workeasy360.dev",
            "notifications_email": "ops@workeasy360.dev",
            "company_name": "Workeasy360 Test Pvt Ltd",
            "company_address": "1 Test Street, Pune",
            "company_tax_id": "27ABCDE1234F1Z5",
            "public_catalog_currency": "INR",
            "reason": "Browser check settings",
        },
        method="put",
    )

# --- wallets: the rate card and packs an Admin would enter --------------------------------
for meter, amount, per in (("whatsapp", 90, 1), ("sms", 25, 1), ("email", 5, 1), ("api_trigger", 10, 1), ("webhook", 2, 1)):
    send(
        staff,
        f"{admin}/usage-rates",
        {"meter": meter, "currency": "INR", "unit_amount": amount, "per_units": per, "included_units": 0, "reason": "Browser check rate card"},
    )
send(staff, f"{admin}/usage-rates", {"meter": "ai_tokens", "unit_amount": 1, "per_units": 1000, "included_units": 0, "reason": "Browser check rate card"})
pack = send(staff, f"{admin}/credit-packs", {"name": "Starter credits", "credits": 5000, "price_minor": 99900, "currency": "INR"})

# --- customers: orders in every state ----------------------------------------------------
#: The customers the ecosystem seed made, by their owners.
BUYERS = {"cart@eco.test": "cart-company", "buyer@eco.test": "store-buyer", "trial@eco.test": "trial-company"}
clients = {email: signed_in(email) for email in BUYERS}


def slug_of(email):
    return BUYERS[email]


def id_of(email):
    return str(Organization.objects.get(slug=slug_of(email)).id)


def order(email, plan_code, *, interval="monthly"):
    return send(
        clients[email],
        f"/api/v1/orgs/{slug_of(email)}/orders",
        {"plan_version_id": version_of(plan_code), "billing_interval": interval},
        key=uuid.uuid4().hex,
    )


def mark_paid(email, order_row, *, reference):
    customer = id_of(email)
    invoices = staff.get(f"{admin}/customers/{customer}/billing").json()["invoices"]
    number = next(
        i["number"]
        for i in clients[email].get(f"/api/v1/orgs/{slug_of(email)}/billing/invoices").json()["results"]
        if i["order_id"] == order_row["order_id"]
    )
    invoice = next(i for i in invoices if i["number"] == number)
    send(
        staff,
        f"{admin}/customers/{customer}/invoices/{invoice['invoice_id']}/mark-paid",
        {"reference": reference, "reason": "Bank transfer checked", "version": invoice["version"]},
    )
    return invoice


def fail_payment(email, order_row):
    slug = slug_of(email)
    organization = uuid.UUID(id_of(email))
    started = clients[email].post(
        f"/api/v1/orgs/{slug}/billing/invoices/{order_row['invoice_id']}/pay",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert started.status_code == 202, started.content
    attempt = uuid.UUID(started.json()["attempt_id"])
    create_checkout(organization, attempt)
    with organization_scope(organization, reason="Browser check seed reads the checkout reference"):
        reference = PaymentAttempt.unscoped.get(id=attempt).provider_reference
    send(
        clients[email],
        "/api/v1/public/payments/test-checkout/complete",
        {"reference": reference, "organization_id": str(organization), "outcome": "failed"},
    )


paid = order("buyer@eco.test", "easy_crm_advanced")
mark_paid("buyer@eco.test", paid, reference="NEFT-1001")
paid_cart = order("cart@eco.test", "easybook_basic")
mark_paid("cart@eco.test", paid_cart, reference="NEFT-1002")
print("SEED paid orders", paid["number"], paid_cart["number"])
open_order = order("trial@eco.test", "easy_project_basic")
fail_payment("trial@eco.test", open_order)
print("SEED failed payment on", open_order["number"])

# --- wallets: real money in, real usage out ----------------------------------------------
customer = id_of("buyer@eco.test")
for kind, amount in (("platform", 500000), ("consumption", 200000)):
    top_up = send(
        staff,
        f"{admin}/customers/{customer}/wallet-orders",
        {"wallet_kind": kind, "amount_minor": amount, "currency": "INR"},
        key=uuid.uuid4().hex,
    )
    mark_paid("buyer@eco.test", top_up, reference=f"NEFT-{kind}")
credits_order = send(staff, f"{admin}/customers/{customer}/wallet-orders", {"pack_code": pack["code"]}, key=uuid.uuid4().hex)
mark_paid("buyer@eco.test", credits_order, reference="NEFT-credits")
organization_id = uuid.UUID(customer)
for meter, quantity in (("whatsapp", 40), ("sms", 25), ("api_trigger", 100), ("webhook", 60), ("email", 18)):
    usage.record(
        organization_id=organization_id,
        product_code="easy_crm",
        meter=meter,
        quantity=quantity,
        idempotency_key=f"seed-{meter}",
        currency="INR",
        occurred_at=timezone.now(),
    )
usage.record(
    organization_id=organization_id,
    product_code="easy_crm",
    meter="ai_tokens",
    quantity=320000,
    idempotency_key="seed-ai",
    currency="INR",
    occurred_at=timezone.now(),
)
# One that cannot be paid for, and one sent twice: what Transaction Controls exists for.
usage.record(
    organization_id=uuid.UUID(id_of("cart@eco.test")),
    product_code="easybook",
    meter="sms",
    quantity=5,
    idempotency_key="seed-unfunded",
    currency="INR",
    occurred_at=timezone.now(),
)
usage.record(
    organization_id=organization_id,
    product_code="easy_crm",
    meter="sms",
    quantity=25,
    idempotency_key="seed-sms",
    currency="INR",
    occurred_at=timezone.now(),
)
wallets = staff.get(f"{admin}/wallets", {"kind": "platform"}).json()["results"]
for wallet in wallets:
    if wallet["owner_id"] == customer:
        send(staff, f"{admin}/wallets/{wallet['id']}", {"low_balance_threshold": 600000}, method="put")
print("SEED wallets", staff.get(f"{admin}/wallets/reconcile").json().get("balanced"))

# --- KYC: requirements and applications at every step -------------------------------------
requirement = send(staff, f"{admin}/kyc/requirements", {"label": "Company registration certificate", "help_text": "PDF or image, under 5 MB.", "is_required": True})
send(staff, f"{admin}/kyc/requirements", {"label": "Address proof", "is_required": False})
print("SEED requirement", requirement["key"])


def applicant(business, first, last, email):
    response = Client(HTTP_USER_AGENT=BROWSER).post(
        "/api/v1/public/partner-applications",
        {
            "business_name": business,
            "first_name": first,
            "last_name": last,
            "email": email,
            "password": PASSWORD,
            "country": "IN",
            "phone": "+91 99999 00000",
            "interest": "reseller",
        },
        content_type="application/json",
    )
    assert response.status_code == 202, response.content
    with unscoped_access(reason="browser check seed", module="seed"):
        user = User.objects.get(email=email)
        user.status = "active"
        user.email_verified_at = timezone.now()
        user.save(update_fields=["status", "email_verified_at"])
    client = signed_in(email)
    from workeasy360.commercial.partners.models import PartnerAccount, PartnerMember

    partner = PartnerAccount.objects.get(id=PartnerMember.objects.get(user_id=user.id).partner_id)
    print("SEED applicant", business, partner.id)
    return client, partner


def fill_and_submit(client, partner, business):
    base = f"/api/v1/partner/{partner.id}/kyc"
    send(client, base, {"details": {"legal_name": f"{business} Pvt Ltd", "country": "IN", "city": "Pune", "address": "2 Trade Road", "tax_id": "27ABCDE1234F1Z5"}}, method="put")
    upload = client.post(f"{base}/documents?requirement={requirement['key']}&filename=registration.png", PNG, content_type="image/png")
    assert upload.status_code in (200, 201), upload.content
    send(client, f"{base}/submit")


applicant("Kiran Traders", "Kiran", "Rao", "kiran.apply@eco.test")
review_client, review_partner = applicant("Meera Consulting", "Meera", "Shah", "meera.apply@eco.test")
fill_and_submit(review_client, review_partner, "Meera Consulting")
second_client, second_partner = applicant("Ravi Digital", "Ravi", "Iyer", "ravi.apply@eco.test")
fill_and_submit(second_client, second_partner, "Ravi Digital")
send(staff, f"{admin}/kyc/applications/{second_partner.id}/decision", {"decision": "request_correction", "reason": "The certificate is unreadable. Please send a clearer copy."})
third_client, third_partner = applicant("Sunil Media", "Sunil", "Das", "sunil.apply@eco.test")
fill_and_submit(third_client, third_partner, "Sunil Media")
send(staff, f"{admin}/kyc/applications/{third_partner.id}/decision", {"decision": "reject", "reason": "We could not verify this business."})

# --- support and help --------------------------------------------------------------------
ticket = send(
    clients["buyer@eco.test"],
    f"/api/v1/orgs/{slug_of('buyer@eco.test')}/support-tickets",
    {"subject": "How do I add seats?", "message": "We have five more people joining next week."},
)
send(
    clients["cart@eco.test"],
    f"/api/v1/orgs/{slug_of('cart@eco.test')}/support-tickets",
    {"subject": "Invoice address is wrong", "message": "Please change the city to Mumbai."},
)
print("SEED ticket", ticket["id"])
article = send(staff, f"{admin}/help-content", {"title": "How do I export my data?", "body": "Open Reports, choose one, then Export CSV.", "kind": "faq", "audience": "customer"})
send(staff, f"{admin}/help-content/{article['id']}/status", {"status": "published"})
send(staff, f"{admin}/help-content", {"title": "Setting up your first project", "body": "Draft guide.", "kind": "setup_guide", "audience": "customer"})

# --- product setup ------------------------------------------------------------------------
send(staff, f"{admin}/setup/requirements", {"product_code": "easy_crm", "label": "Your company's GST number"})

# --- a colleague with narrow grants, for the permission journey ----------------------------
with unscoped_access(reason="browser check seed", module="seed"):
    agent = User.objects.create_user(email="agent@eco.test", password=None)
    lifecycle.complete_with_password(agent, PASSWORD, full_name="Asha Agent", activate=True)
    agent.is_staff = True
    agent.save(update_fields=["is_staff"])
root = User.objects.get(email=STAFF)
for permission in ("platform.staff_customer.view", "platform.staff_support.view", "platform.staff_support.manage"):
    grants.create_grant(user_id=agent.id, permission=permission, granted_by_id=root.id, reason="Browser check colleague")
print("SEED agent", agent.email)
print("SEED done")

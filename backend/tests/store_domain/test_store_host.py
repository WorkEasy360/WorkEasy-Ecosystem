"""The Digital Store on its own host (ADR-065): store.workeasy360.com signs its
customers in with the same accounts on a session of its own, serves exactly the
organization endpoints its checkout and Recharge use (nothing else of an
organization), refuses a write from any other origin, sends the payment provider's
return - and every emailed account link it asks for - back to itself, and hands a
signed-in customer to User Admin through a single-use pass only its own origin can
spend."""

from __future__ import annotations

import json
import uuid

import pytest
from django.core import mail
from django.test import Client

from tests.portals.conftest import ORIGINS as FIVE
from tests.portals.conftest import PASSWORD

pytestmark = pytest.mark.django_db

ORIGINS = {**FIVE, "store": "https://store.workeasy360.com"}
HOST = {name: origin.removeprefix("https://") for name, origin in ORIGINS.items()}


@pytest.fixture
def store_on(settings):
    settings.PORTALS_ENABLED = True
    settings.W360_PORTAL_ORIGINS = dict(ORIGINS)
    settings.ALLOWED_HOSTS = list(HOST.values())
    settings.CSRF_TRUSTED_ORIGINS = [o for n, o in ORIGINS.items() if n != "api"]
    settings.W360_FRONTEND_URL = ORIGINS["app"]
    return settings


class On:
    """One browser's view of one host (its own cookie jar)."""

    def __init__(self, portal: str):
        self.host = HOST[portal]
        self.client = Client()

    def get(self, path, **extra):
        return self.client.get(path, HTTP_HOST=self.host, secure=True, **extra)

    def post(self, path, body=None, **extra):
        return self.client.post(
            path,
            json.dumps(body or {}),
            content_type="application/json",
            HTTP_HOST=self.host,
            secure=True,
            **extra,
        )

    def form(self, path, data, **extra):
        return self.client.post(path, data, HTTP_HOST=self.host, secure=True, **extra)

    def sign_in(self, user):
        self.get("/api/v1/auth/session")
        done = self.post(
            "/api/v1/auth/login", {"email": user.email, "password": PASSWORD}
        )
        assert done.status_code == 200, done.content
        return self


@pytest.fixture
def customer(organization_factory):
    org, membership, user = organization_factory()
    user.set_password(PASSWORD)
    user.save(update_fields=["password"])
    return {"org": org, "slug": org.slug, "user": user}


# --- one portal of its own ------------------------------------------------------------


def test_the_store_host_signs_in_with_the_same_account_on_its_own_session(
    store_on, customer
):
    store = On("store").sign_in(customer["user"])
    assert store.get("/api/v1/auth/session").json()["authenticated"] is True
    # Host-only: the customer portal has no session from it.
    assert On("app").get("/api/v1/auth/session").json()["authenticated"] is False


@pytest.mark.parametrize(
    "suffix",
    [
        "access",
        "orders",
        "orders/quote",
        f"orders/{uuid.uuid4()}",
        "billing-account",
        f"billing/invoices/{uuid.uuid4()}",
        f"billing/payments/{uuid.uuid4()}",
        "wallets",
        "trials",
        "notifications",
    ],
)
def test_the_store_serves_its_checkout_and_recharge_endpoints(store_on, customer, suffix):
    response = (
        On("store")
        .sign_in(customer["user"])
        .get(f"/api/v1/orgs/{customer['slug']}/{suffix}")
    )
    refused = (
        response.status_code == 404
        and response.get("Content-Type", "").startswith("application/json")
        and response.json() == {"code": "not_found"}
    )
    assert not refused, suffix


@pytest.mark.parametrize(
    "suffix",
    [
        "members",
        "apps",
        "settings/organization",
        "security/policy",
        "audit",
        "wallets/transactions",
        "billing/invoices",
        "commercial/catalog",
    ],
)
def test_nothing_else_of_an_organization_is_served_on_the_store(
    store_on, customer, suffix
):
    response = (
        On("store")
        .sign_in(customer["user"])
        .get(f"/api/v1/orgs/{customer['slug']}/{suffix}")
    )
    assert response.status_code == 404 and response.json() == {"code": "not_found"}, (
        suffix
    )


@pytest.mark.parametrize("path", ["/api/v1/admin/session", "/api/v1/partner/portal"])
def test_the_consoles_apis_are_never_on_the_store(store_on, path):
    response = On("store").get(path)
    assert response.status_code == 404 and response.json() == {"code": "not_found"}


def test_a_write_naming_another_origin_is_refused_on_the_store(store_on, customer):
    store = On("store").sign_in(customer["user"])
    refused = store.post(
        f"/api/v1/orgs/{customer['slug']}/orders/quote",
        {},
        HTTP_ORIGIN=ORIGINS["app"],
    )
    assert refused.status_code == 403 and refused.json()["code"] == "csrf_failed"


def test_without_its_own_origin_the_store_is_the_customer_portals(settings, customer):
    """Unset (today's production), nothing changes: no host is the Store's, and
    every Store link is on the customer portal."""
    from workeasy360.platform.identity import portals

    settings.PORTALS_ENABLED = True
    settings.W360_PORTAL_ORIGINS = {**FIVE, "store": ""}
    settings.W360_FRONTEND_URL = FIVE["app"]
    assert portals.store_enabled() is False
    assert portals.store_url() == FIVE["app"]
    assert portals.account_url("store") == FIVE["app"]


# --- links back to the Store -------------------------------------------------------


def test_a_signup_on_the_store_emails_a_link_that_opens_on_the_store(store_on):
    from tests.phase9.conftest import deliver_account_mail

    store = On("store")
    store.get("/api/v1/auth/session")
    sent = store.post(
        "/api/v1/public/signup",
        {
            "first_name": "Sam",
            "last_name": "Store",
            "email": "sam.store@example.com",
            "password": "a long enough passphrase 9",  # pragma: allowlist secret
        },
    )
    assert sent.status_code == 202, sent.content
    messages = [m for m in deliver_account_mail() if "Confirm your email" in m.subject]
    assert messages, [m.subject for m in mail.outbox]
    assert f"{ORIGINS['store']}/verify-email#token=" in messages[-1].body


def test_a_password_reset_asked_on_the_store_opens_on_the_store(store_on, customer):
    from tests.phase9.conftest import deliver_account_mail

    store = On("store")
    store.get("/api/v1/auth/session")
    store.post("/api/v1/public/password/forgot", {"email": customer["user"].email})
    bodies = [m.body for m in deliver_account_mail()]
    assert any(f"{ORIGINS['store']}/reset-password#token=" in b for b in bodies), bodies


def test_the_payment_provider_returns_a_store_payer_to_the_store(store_on, customer):
    from workeasy360.commercial.payments import checkout

    class Attempt:
        id = uuid.uuid4()
        organization_id = customer["org"].id
        return_portal = "store"

    url = checkout._return_url(customer["org"].id, Attempt, "success")
    assert url == (
        f"{ORIGINS['store']}/store/checkout/complete?payment={Attempt.id}"
        f"&org={customer['slug']}&outcome=success"
    )
    Attempt.return_portal = ""
    assert checkout._return_url(customer["org"].id, Attempt, "success").startswith(
        f"{ORIGINS['app']}/o/{customer['slug']}/settings/billing?payment="
    )


def test_a_payment_started_on_the_store_is_marked_to_return_there(
    store_on, console_org, login, inr_catalog, fake_payments
):
    from tests.store_domain.conftest import place
    from workeasy360.commercial.payments.invoicing import organization_scope
    from workeasy360.commercial.payments.models import PaymentAttempt

    org = console_org()
    store = On("store").sign_in(org["owner"])
    opened = store.post(
        f"/api/v1/orgs/{org['slug']}/billing-account",
        {"currency": "INR", "billing_email": "billing@example.com"},
    )
    assert opened.status_code == 200, opened.content

    shop = {"client": _Bound(store), "slug": org["slug"], "catalog": inr_catalog}
    order = place(shop)
    started = store.post(
        f"/api/v1/orgs/{org['slug']}/billing/invoices/{order['invoice_id']}/pay",
        {},
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert started.status_code == 202, started.content
    with organization_scope(org["org"].id, reason="Test reads the attempt"):
        attempt = PaymentAttempt.unscoped.get(id=started.json()["attempt_id"])
    assert attempt.return_portal == "store"
    from workeasy360.commercial.payments.checkout import hosted_page_url

    page = hosted_page_url(attempt, "ref-1")
    assert page.startswith(f"{ORIGINS['store']}/billing/test-checkout#reference=ref-1")
    assert (
        "&return=https%3A%2F%2Fstore.workeasy360.com%2Fstore%2Fcheckout%2Fcomplete"
        in page
    )


class _Bound:
    """``place`` posts with ``client.post(url, body, content_type=..., **extra)``."""

    def __init__(self, on: On):
        self.on = on

    def post(self, path, body, content_type=None, **extra):
        return self.on.post(path, body, **extra)


# --- "My Account": a pass from the Store to User Admin ------------------------------


def _pass(store, next_path):
    issued = store.post("/api/v1/auth/account-pass", {"next": next_path})
    assert issued.status_code == 200, issued.content
    assert issued["Cache-Control"] == "no-store"
    return issued.json()


def test_my_account_opens_user_admin_signed_in_as_the_same_person(store_on, customer):
    store = On("store").sign_in(customer["user"])
    issued = _pass(store, f"/o/{customer['slug']}/wallets")
    assert issued["action"] == f"{ORIGINS['app']}/api/v1/auth/account-pass/enter"
    app = On("app")
    entered = app.form(
        "/api/v1/auth/account-pass/enter",
        {"code": issued["code"]},
        HTTP_ORIGIN=ORIGINS["store"],
    )
    assert entered.status_code == 303, entered.content
    assert entered["Location"] == f"{ORIGINS['app']}/o/{customer['slug']}/wallets"
    session = app.get("/api/v1/auth/session").json()
    assert session["authenticated"] is True
    assert session["email"] == customer["user"].email


def test_a_pass_is_spent_once(store_on, customer):
    store = On("store").sign_in(customer["user"])
    code = _pass(store, "/dashboard")["code"]
    first = On("app").form(
        "/api/v1/auth/account-pass/enter", {"code": code}, HTTP_ORIGIN=ORIGINS["store"]
    )
    again = On("app").form(
        "/api/v1/auth/account-pass/enter", {"code": code}, HTTP_ORIGIN=ORIGINS["store"]
    )
    assert first.status_code == again.status_code == 303
    assert again["Location"] == f"{ORIGINS['app']}/login"


@pytest.mark.parametrize(
    "origin", [None, "https://evil.example", FIVE["app"], FIVE["admin"]]
)
def test_only_the_stores_own_origin_may_spend_a_pass(store_on, customer, origin):
    """Login CSRF: another site cannot post a pass of its own account into a
    visitor's browser."""
    store = On("store").sign_in(customer["user"])
    code = _pass(store, "/dashboard")["code"]
    extra = {"HTTP_ORIGIN": origin} if origin else {}
    app = On("app")
    refused = app.form("/api/v1/auth/account-pass/enter", {"code": code}, **extra)
    assert refused.status_code == 403
    assert app.get("/api/v1/auth/session").json()["authenticated"] is False


def test_a_pass_lands_only_on_a_customer_portal_page(store_on, customer):
    store = On("store").sign_in(customer["user"])
    for asked in ("https://evil.example/", "//evil.example", "/admin", "/partner"):
        code = _pass(store, asked)["code"]
        entered = On("app").form(
            "/api/v1/auth/account-pass/enter",
            {"code": code},
            HTTP_ORIGIN=ORIGINS["store"],
        )
        assert entered["Location"] == f"{ORIGINS['app']}/dashboard", asked


def test_a_pass_is_issued_only_on_the_store_and_spent_only_on_user_admin(
    store_on, customer
):
    app = On("app").sign_in(customer["user"])
    assert (
        app.post("/api/v1/auth/account-pass", {"next": "/dashboard"}).status_code == 404
    )
    store = On("store").sign_in(customer["user"])
    code = _pass(store, "/dashboard")["code"]
    assert (
        store.form(
            "/api/v1/auth/account-pass/enter",
            {"code": code},
            HTTP_ORIGIN=ORIGINS["store"],
        ).status_code
        == 404
    )
    assert On("store").post("/api/v1/auth/account-pass", {}).status_code == 401


def test_a_pass_is_never_a_hand_off_code(store_on, customer):
    from workeasy360.platform.identity import handoff

    store = On("store").sign_in(customer["user"])
    code = _pass(store, "/dashboard")["code"]
    assert handoff.redeem(code=code, state="x" * 43, host=HOST["app"]) is None


def test_signing_out_of_the_store_voids_an_unspent_pass(store_on, customer):
    store = On("store").sign_in(customer["user"])
    code = _pass(store, "/dashboard")["code"]
    store.post("/api/v1/auth/logout")
    entered = On("app").form(
        "/api/v1/auth/account-pass/enter", {"code": code}, HTTP_ORIGIN=ORIGINS["store"]
    )
    assert entered["Location"] == f"{ORIGINS['app']}/login"


def test_without_the_store_host_there_is_no_pass(settings, customer):
    settings.PORTALS_ENABLED = True
    settings.W360_PORTAL_ORIGINS = {**FIVE, "store": ""}
    settings.ALLOWED_HOSTS = [o.removeprefix("https://") for o in FIVE.values()]
    client = Client()
    response = client.post(
        "/api/v1/auth/account-pass/enter",
        {"code": "x"},
        HTTP_HOST=HOST["app"],
        secure=True,
        HTTP_ORIGIN=ORIGINS["store"],
    )
    assert response.status_code in (403, 404)


# --- ADR-073: the Store signs in through WorkEasy360 Accounts --------------------------
#
# With ``W360_ACCOUNTS_IDENTITY`` on (ADR-071) the Store's own sign-in becomes
# Accounts' hand-off - the customer portal's, with ``mode=store`` - so after a
# Store sign-in the central session exists and the first product opened asks for
# no password. Off (production today) nothing above changes; the last test here
# proves it.


@pytest.fixture
def through_accounts(store_on):
    store_on.W360_ACCOUNTS_IDENTITY = True
    return store_on


def _hand_to_store(accounts, *, next_path=None, redeem_on="store"):
    """What the browser does: the Store's proxy sets its binding cookie and sends
    the person to Accounts; Accounts, signed in, issues the hand-off; the Store's
    ``/auth/continue`` spends it for the Store's own session."""
    import secrets

    from django.conf import settings

    state = secrets.token_urlsafe(32)
    issued = accounts.post(
        "/api/v1/auth/handoff", {"mode": "store", "next": next_path, "state": state}
    )
    if issued.status_code != 200:
        return None, issued, None
    target = On(redeem_on)
    target.client.cookies[settings.PORTAL_STATE_COOKIE_NAME] = state
    target.get("/api/v1/auth/session")
    code = issued.json()["redirect_url"].split("#code=", 1)[1]
    return target, issued, target.post("/api/v1/auth/handoff/redeem", {"code": code})


def _sign_in_ids(user) -> set:
    from workeasy360.platform.identity.models import UserSession

    return set(UserSession.objects.filter(user=user).values_list("sign_in_id", flat=True))


def test_with_accounts_identity_a_store_sign_in_is_an_accounts_sign_in(
    through_accounts, customer
):
    accounts = On("auth").sign_in(customer["user"])
    store, issued, redeemed = _hand_to_store(accounts, next_path="/store/checkout?plan=x")
    assert issued.json()["redirect_url"].startswith(
        f"{ORIGINS['store']}/auth/continue#code="
    )
    assert issued["Cache-Control"] == "no-store"
    assert redeemed.status_code == 200, redeemed.content
    assert redeemed.json() == {"next": "/store/checkout?plan=x"}

    # The same person, on a session of the Store's own - and Accounts stays signed in.
    mine = store.get("/api/v1/auth/session").json()
    assert mine["authenticated"] is True and mine["email"] == customer["user"].email
    assert accounts.get("/api/v1/auth/session").json()["authenticated"] is True
    # One sign-in behind both sessions, so signing out of either ends the other.
    assert len(_sign_in_ids(customer["user"])) == 1
    # And the Store's own API calls work on that session (its checkout endpoints).
    access = store.get(f"/api/v1/orgs/{customer['slug']}/access")
    assert access.status_code == 200, access.content


def test_signing_out_of_the_store_ends_the_accounts_sign_in_too(
    through_accounts, customer
):
    accounts = On("auth").sign_in(customer["user"])
    store, _issued, redeemed = _hand_to_store(accounts)
    assert redeemed.status_code == 200
    store.post("/api/v1/auth/logout")
    assert accounts.get("/api/v1/auth/session").json()["authenticated"] is False


def test_the_first_product_opened_after_a_store_sign_in_needs_no_password(
    through_accounts, customer, register_product, entitled
):
    """The journey's gap (G3): bought in the Store, then opened - the product's
    authorization request on Accounts finds the sign-in and returns a code."""
    from urllib.parse import parse_qs, urlencode, urlsplit

    from tests.universal_sso.conftest import AUTHORIZE, authorize_query

    registration = register_product("books")  # organization-wide: no seat to assign
    entitled(customer["org"].id, "books")
    from workeasy360.platform.ecosystem.models import UserSignInPreference

    # The person's own choice: open products with their WorkEasy360 account.
    UserSignInPreference.objects.update_or_create(
        user=customer["user"], product_code="", defaults={"mode": "auto_sso"}
    )

    accounts = On("auth").sign_in(customer["user"])
    _store, _issued, redeemed = _hand_to_store(accounts, next_path="/store")
    assert redeemed.status_code == 200
    _verifier, query = authorize_query(registration, str(customer["org"].id))
    answer = accounts.get(f"{AUTHORIZE}?{urlencode(query)}")
    assert answer.status_code == 302, answer.content
    location = urlsplit(answer["Location"])
    assert (
        f"{location.scheme}://{location.netloc}{location.path}"
        == registration["redirect_uri"]
    )
    assert parse_qs(location.query)["code"]


def test_a_store_hand_off_lands_only_on_a_store_page(through_accounts, customer):
    accounts = On("auth").sign_in(customer["user"])
    for asked in (
        "/o/acme/billing",
        "https://evil.example/store",
        "//evil.example",
        "/admin",
    ):
        _store, _issued, redeemed = _hand_to_store(accounts, next_path=asked)
        assert redeemed.json() == {"next": "/store"}, asked


def test_a_store_code_is_spent_only_on_the_store(through_accounts, customer):
    accounts = On("auth").sign_in(customer["user"])
    app, _issued, redeemed = _hand_to_store(accounts, redeem_on="app")
    assert redeemed.status_code == 400
    assert app.get("/api/v1/auth/session").json()["authenticated"] is False


def test_a_store_sign_up_with_accounts_identity_emails_an_accounts_link(through_accounts):
    from tests.phase9.conftest import deliver_account_mail
    from workeasy360.platform.identity import portals

    assert portals.identity_url("store") == ORIGINS["auth"]
    store = On("store")
    store.get("/api/v1/auth/session")
    sent = store.post(
        "/api/v1/public/signup",
        {
            "first_name": "Sam",
            "last_name": "Accounts",
            "email": "sam.accounts@example.com",
            "password": "a long enough passphrase 9",  # pragma: allowlist secret
        },
    )
    assert sent.status_code == 202, sent.content
    messages = [m for m in deliver_account_mail() if "Confirm your email" in m.subject]
    assert f"{ORIGINS['auth']}/verify-email#token=" in messages[-1].body


def test_without_accounts_identity_nothing_hands_a_sign_in_to_the_store(
    store_on, customer
):
    """Production today: the Store signs in itself (ADR-065), exactly as before."""
    from workeasy360.platform.identity import portals

    accounts = On("auth").sign_in(customer["user"])
    _store, issued, _redeemed = _hand_to_store(accounts)
    assert issued.status_code == 400 and issued.json() == {"code": "handoff_invalid"}
    refused = On("store").post("/api/v1/auth/handoff/redeem", {"code": "x" * 43})
    assert refused.status_code == 404 and refused.json() == {"code": "not_found"}
    assert portals.identity_url("store") == ORIGINS["store"]
    # ...and its own sign-in still answers on its own host.
    store = On("store").sign_in(customer["user"])
    assert store.get("/api/v1/auth/session").json()["authenticated"] is True

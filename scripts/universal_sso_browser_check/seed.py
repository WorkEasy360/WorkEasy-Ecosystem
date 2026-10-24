"""Seed the scratch Workeasy360 for the universal-sign-in browser check (ADR-044).

Everything goes through the existing commands and services:

- the Super Admin (``bootstrap_superadmin``) with an authenticator;
- the four products from ``infra/ecosystem/products.json``
  (``register_products --activate-integrations``), all four OIDC clients;
- one TEST plan and price per product, and each 7-day trial switched on for it
  (no plan or price exists outside this scratch database);
- the four products pointed at their LOCAL instances through the Product
  Manager's own integration service - Easy CRM ``sso_optional`` with its
  sign-in page (it runs here with its password sign-in on), EasyBook, Easy
  Project and Easy Help Desk ``sso_only`` - each with a public-key credential
  (the private halves stay with the products, in ``$USSO_WORK``) and a
  post-logout URI;
- "Customer Company": Ana (owner) and Ben (member, invited and accepted
  through the real invitation service), both on trials of all four products
  with seats;
- "Trial Company": Cara (owner), holding nothing - the expired-trial journey
  starts her EasyBook trial in the browser.

Writes ``$USSO_WORK/seed.json``. Scratch databases only - see README.md.
"""

import json
import os
import uuid
from pathlib import Path

import django

django.setup()

from django.core.management import call_command  # noqa: E402

from workeasy360.commercial.billing import services as billing  # noqa: E402
from workeasy360.commercial.catalog import selectors as catalog_selectors  # noqa: E402
from workeasy360.commercial.catalog import services as catalog  # noqa: E402
from workeasy360.commercial.controlplane import products as console  # noqa: E402
from workeasy360.commercial.pricing import services as pricing  # noqa: E402
from workeasy360.commercial.subscriptions import trials  # noqa: E402
from workeasy360.contracts.enums import (  # noqa: E402
    BillingInterval,
    CatalogStatus,
    OrganizationStatus,
    PriceBookChannel,
)
from workeasy360.contracts.values.money import CurrencyCode  # noqa: E402
from workeasy360.contracts.values.tenant import TenantContext  # noqa: E402
from workeasy360.platform.administration import invitations  # noqa: E402
from workeasy360.platform.authz.control_plane import (  # noqa: E402
    acting_scope,
    platform_context,
)
from workeasy360.platform.ecosystem import selectors as ecosystem_selectors  # noqa: E402
from workeasy360.platform.ecosystem import services as ecosystem  # noqa: E402
from workeasy360.platform.identity import lifecycle  # noqa: E402
from workeasy360.platform.identity import services as identity  # noqa: E402
from workeasy360.platform.identity.models import User  # noqa: E402
from workeasy360.platform.launcher import services as launcher  # noqa: E402
from workeasy360.platform.onboarding.services import create_organization  # noqa: E402
from workeasy360.platform.tenancy.context import tenant_context  # noqa: E402
from workeasy360.platform.tenancy.db import tenant_db_scope  # noqa: E402
from workeasy360.platform.tenancy.unscoped import unscoped_access  # noqa: E402

PASSWORD = "Browser-Test-Password-1!"  # noqa: S105 - scratch test users only
ROOT = Path(__file__).resolve().parents[2]
WORK = Path(os.environ["USSO_WORK"])
PRODUCTS = json.loads(
    (ROOT / "infra" / "ecosystem" / "products.json").read_text(encoding="utf-8")
)["products"]
CRM = "http://localhost:4022"
BOOK = "http://127.0.0.2:3023"
#: Easy Project: one jar serves the SPA and the API.
PROJECT = "http://127.0.0.4:8024"
#: Easy Help Desk: the Next frontend and the NestJS API on one host, two ports
#: (production puts both behind one nginx origin; cookies are per host, so the
#: API's refresh cookie reaches the frontend's API calls exactly as there).
HELPDESK = "http://127.0.0.5:3025"
HELPDESK_API = "http://127.0.0.5:4025"
SCOPES = ["openid", "email", "profile", "organization"]

os.environ["PLATFORM_SUPERADMIN_EMAIL"] = "staff@usso.test"
os.environ["PLATFORM_SUPERADMIN_PASSWORD"] = PASSWORD
call_command("bootstrap_superadmin")
admin = User.objects.get(email="staff@usso.test")
sctx = platform_context(user_id=admin.id, aal=2)


def enroll_totp(user):
    """No second factor exists any more (ADR-050): nothing to enrol. The harnesses
    still read a ``STAFF_TOTP`` line, so this returns an empty one."""
    return ""


def person(email, name):
    with unscoped_access(reason="universal sign-in browser seed", module="seed"):
        user = User.objects.create_user(email=email, password=None)
        lifecycle.complete_with_password(user, PASSWORD, full_name=name, activate=True)
        return user


def owner_ctx(created, user):
    return TenantContext(
        organization_id=created.organization_id,
        membership_id=created.membership_id,
        user_id=user.id,
        organization_status=OrganizationStatus.ACTIVE,
        aal=2,
    )


staff_totp = enroll_totp(admin)
call_command("register_products", actor=admin.email, activate_integrations=True)

with acting_scope(sctx):
    book = pricing.create_price_book(
        sctx,
        code="direct_gbp",
        name="Direct GBP",
        currency=CurrencyCode.GBP,
        channel=PriceBookChannel.DIRECT,
    )
    by_code = {p["code"]: p for p in catalog_selectors.visible_products()}
    for entry in PRODUCTS:
        code = entry["code"]
        plan = catalog.create_plan(
            sctx,
            product_id=by_code[code]["id"],
            code=f"{code}_team",
            name="Team",
            caption="TEST plan",
        )
        catalog.set_plan_status(sctx, plan_id=plan.id, status=CatalogStatus.AVAILABLE)
        version = catalog.draft_plan_version(
            sctx,
            plan_id=plan.id,
            features={feature["key"]: True for feature in entry["features"]},
            included_seats=5,
            trial_days=0,
        )
        catalog.publish_plan_version(sctx, plan_version_id=version.id)
        pricing.publish_price(
            sctx,
            price_book_id=book.id,
            plan_version_id=version.id,
            interval=BillingInterval.MONTHLY,
            amount_minor=1900,
        )
        offer = catalog_selectors.trial_offer(code)
        catalog.configure_product_trial(
            sctx,
            product_id=by_code[code]["id"],
            enabled=True,
            duration_days=offer.duration_days,
            eligible_plan_codes=[f"{code}_team"],
        )

    # The offline test payment provider, configured by staff exactly as a real one is (adapter "fake":
    # PAYMENTS_FAKE_PROVIDER_ENABLED is on in this scratch backend's settings only), so journey 10 can
    # buy a product after its trial.
    from workeasy360.commercial.payments import staff as payments_staff

    provider = payments_staff.create_provider(
        sctx,
        {
            "key": "test-gbp",
            "adapter": "fake",
            "display_name": "Test provider",
            "credential_ref": "TEST_GBP",
            "webhook_secret_ref": "TEST_GBP",
            "currencies": ["GBP"],
        },
    )
    payments_staff.update_provider(
        sctx, "test-gbp", {"status": "enabled", "version": provider["version"]}
    )

    # The two local products' sign-in, exactly as staff set it in the console.
    console.update_integration(
        sctx,
        product_code="easy_crm",
        changes={
            "launch_url": f"{CRM}/",
            "redirect_uris": [f"{CRM}/api/auth/callback/workeasy360"],
            "sign_in_mode": "sso_optional",
            "login_url": f"{CRM}/login",
            "post_logout_redirect_uris": [f"{CRM}/login"],
            "scopes": SCOPES,
        },
    )
    console.update_integration(
        sctx,
        product_code="easybook",
        changes={
            "launch_url": f"{BOOK}/",
            "redirect_uris": [f"{BOOK}/api/auth/callback/workeasy360"],
            "sign_in_mode": "sso_only",
            "post_logout_redirect_uris": [f"{BOOK}/login"],
            "scopes": SCOPES,
        },
    )
    console.update_integration(
        sctx,
        product_code="easy_project",
        changes={
            "launch_url": f"{PROJECT}/",
            "redirect_uris": [f"{PROJECT}/api/auth/callback/workeasy360"],
            "sign_in_mode": "sso_only",
            "post_logout_redirect_uris": [f"{PROJECT}/"],
            "scopes": SCOPES,
        },
    )
    console.update_integration(
        sctx,
        product_code="easy_helpdesk",
        changes={
            "launch_url": f"{HELPDESK}/",
            "redirect_uris": [f"{HELPDESK_API}/api/auth/callback/workeasy360"],
            "sign_in_mode": "sso_only",
            "post_logout_redirect_uris": [f"{HELPDESK}/login"],
            "scopes": SCOPES,
        },
    )
    credentials = {}
    for code, key in (
        ("easy_crm", "crm"),
        ("easybook", "easybook"),
        ("easy_project", "project"),
        ("easy_helpdesk", "helpdesk"),
    ):
        client_id = ecosystem_selectors.integration_for_product(
            sctx, product_code=code
        ).client_id
        issued = ecosystem.create_credential(
            sctx,
            client_id=client_id,
            kind="public_key",
            public_key_pem=(WORK / f"{key}.pub").read_text(encoding="utf-8"),
            algorithm="RS256",
            label="browser check",
        )
        credentials[code] = {"client_id": client_id, "key_id": issued.credential_id}

# Customer Company: Ana (owner) and Ben (invited member).
ana = person("ana@usso.test", "Ana Owner")
company = create_organization(user_id=ana.id, name="Customer Company")
actx = owner_ctx(company, ana)
captured = {}
_new_token = invitations._new_token
invitations._new_token = lambda org: captured.setdefault("token", _new_token(org))
with tenant_context(actx), tenant_db_scope(actx):
    billing.open_billing_account(
        actx, currency=CurrencyCode.GBP, billing_email="billing@usso.test"
    )
    invitations.invite(actx, email="ben@usso.test", role_key="member")
invitations._new_token = _new_token
accepted = invitations.accept(
    captured["token"], password=PASSWORD, full_name="Ben Member"
)
with tenant_context(actx), tenant_db_scope(actx):
    for code in ("easy_crm", "easybook", "easy_project", "easy_helpdesk"):
        trials.start_product_trial(
            actx,
            product_code=code,
            plan_code=f"{code}_team",
            idempotency_key=uuid.uuid4().hex,
        )
        launcher.assign_seat(
            actx, product_code=code, membership_id=accepted.membership_id
        )

# Trial Company: Cara, holding nothing yet.
cara = person("cara@usso.test", "Cara Trial")
trial_company = create_organization(user_id=cara.id, name="Trial Company")
cctx = owner_ctx(trial_company, cara)
with tenant_context(cctx), tenant_db_scope(cctx):
    billing.open_billing_account(
        cctx, currency=CurrencyCode.GBP, billing_email="cara@usso.test"
    )

seed = {
    "password": PASSWORD,
    "staff_email": admin.email,
    "staff_totp": staff_totp,
    "company_slug": company.slug,
    "company_id": str(company.organization_id),
    "trial_company_slug": trial_company.slug,
    "trial_company_id": str(trial_company.organization_id),
    "credentials": credentials,
}
(WORK / "seed.json").write_text(json.dumps(seed, indent=2), encoding="utf-8")
print(json.dumps(seed, indent=2))

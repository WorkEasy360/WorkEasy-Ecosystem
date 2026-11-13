"""Unified-login browser check data (ADR-066), after the User Admin and Store seeds,
written by the working tree's own services:

- Easy CRM and EasyBook connected for single sign-on (``connect_product_sso``)
  with a throwaway public key and their registered callback addresses - the
  browser check intercepts those callbacks, so nothing leaves the machine;
- "UAP Beta Labs" (the owner's second organization) holding both products, with a
  seat each for the owner (Manoj Kumar) and a second person, Priya Patel, a member;
- a change-of-sign-in-email link for Neha Sharma (member of UAP Acme), minted the
  way ``request_email_change`` mints it, so the confirmation page can be opened.

Prints ``ULOGIN {json}``. Scratch databases only. Every password is ``CHECK_PASSWORD``.
"""

# ruff: noqa: S105, S106, E402

import json
import os
import tempfile
from datetime import timedelta

import django

django.setup()

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.management import call_command
from django.utils import timezone

from workeasy360.contracts.enums import (
    EntitlementSourceKind,
    EntitlementStatus,
    LicensingMode,
    OrganizationStatus,
)
from workeasy360.contracts.ports.catalog import get_product_directory
from workeasy360.contracts.values.identifiers import ProductCode
from workeasy360.contracts.values.tenant import TenantContext
from workeasy360.platform.authz import services as authz
from workeasy360.platform.authz.control_plane import acting_scope, platform_context
from workeasy360.platform.ecosystem import selectors as ecosystem_selectors
from workeasy360.platform.entitlements.services.seats import assign_seat
from workeasy360.platform.entitlements.services.write import (
    ComputedEntitlements,
    ComputedProduct,
    ComputedSeatPool,
    apply_entitlements,
)
from workeasy360.platform.identity import lifecycle, tokens
from workeasy360.platform.identity.models import AccountToken, User
from workeasy360.platform.organizations import projection
from workeasy360.platform.organizations.models import Membership, Organization
from workeasy360.platform.tenancy.context import tenant_context
from workeasy360.platform.tenancy.db import organization_db_scope, tenant_db_scope
from workeasy360.platform.tenancy.unscoped import unscoped_access

PASSWORD = os.environ["CHECK_PASSWORD"]
STAFF = os.environ["PLATFORM_SUPERADMIN_EMAIL"]
REASON = "unified login acceptance seed"
PRODUCTS = ("easy_crm", "easybook")


def person(email: str, name: str) -> User:
    with unscoped_access(reason=REASON, module="seed"):
        user = User.objects.filter(email=email).first()
        if user is None:
            user = User.objects.create_user(email=email, password=None)
            lifecycle.complete_with_password(user, PASSWORD, full_name=name, activate=True)
        return user


def membership_of(org_id, user_id):
    with unscoped_access(reason=REASON, module="seed"), organization_db_scope(org_id):
        return Membership.unscoped.filter(organization_id=org_id, user_id=user_id).first()


def add_member(org_id, user, role):
    with unscoped_access(reason=REASON, module="seed"), organization_db_scope(org_id):
        organization = Organization.objects.get(id=org_id)
        membership = Membership(
            organization_id=org_id, user_id=user.id, status="active", member_type="internal"
        )
        membership.save()
        with acting_scope(
            TenantContext(organization_id=org_id, membership_id=membership.id, user_id=user.id)
        ):
            authz.replace_system_role(
                organization_id=org_id, membership_id=membership.id, role_key=role
            )
        projection.sync_access_for_membership(
            user_id=user.id, membership=membership, organization=organization
        )
    return membership


# --- both products connected for single sign-on --------------------------------

private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
public_pem = private_key.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
)
with tempfile.NamedTemporaryFile("wb", suffix=".pem", delete=False) as handle:
    handle.write(public_pem)
    key_file = handle.name

staff = User.objects.get(email=STAFF)
sctx = platform_context(user_id=staff.id)
products = {}
for code in PRODUCTS:
    with acting_scope(sctx):
        integration = ecosystem_selectors.integration_for_product(sctx, product_code=code)
    redirect = integration.manifest["redirect_uris"][0]
    call_command(
        "connect_product_sso",
        "--actor", STAFF,
        "--product", code,
        "--redirect-uri", redirect,
        "--public-key-file", key_file,
    )  # fmt: skip
    products[code] = {"client_id": integration.client_id, "redirect_uri": redirect}
os.unlink(key_file)

# --- UAP Beta Labs holds both, with seats for two people ------------------------

owner = User.objects.get(email="manoj@uap-acme.dev")
with unscoped_access(reason=REASON, module="seed"):
    beta = Organization.objects.get(slug="uap-beta-labs")
priya = person("priya@uap-beta.dev", "Priya Patel")
if membership_of(beta.id, priya.id) is None:
    add_member(beta.id, priya, "member")

directory = get_product_directory()
per_seat = {
    code
    for code in PRODUCTS
    if (d := directory.get(ProductCode(code))) is not None
    and d.licensing_mode == LicensingMode.PER_SEAT
}
now = timezone.now()
apply_entitlements(
    ComputedEntitlements(
        organization_id=beta.id,
        products=tuple(
            ComputedProduct(
                product_code=code,
                status=EntitlementStatus.ACTIVE,
                source_kind=EntitlementSourceKind.SUBSCRIPTION,
                source_ref=f"bootstrap:{code}",
                seats_total=5 if code in per_seat else None,
                effective_from=now,
            )
            for code in PRODUCTS
        ),
        seats=tuple(
            ComputedSeatPool(pool_key=code, seats_total=5, source_ref=f"bootstrap:{code}")
            for code in sorted(per_seat)
        ),
    )
)
owner_membership = membership_of(beta.id, owner.id)
ctx = TenantContext(
    organization_id=beta.id,
    membership_id=owner_membership.id,
    user_id=owner.id,
    organization_status=OrganizationStatus.ACTIVE,
)
with tenant_context(ctx), tenant_db_scope(ctx):
    for code in sorted(per_seat):
        for member in (owner, priya):
            assign_seat(beta.id, pool_key=code, membership_id=membership_of(beta.id, member.id).id)

# --- a change-of-sign-in-email link for Neha -----------------------------------

neha = User.objects.get(email="member@uap-acme.dev")
email_token = tokens.issue(
    neha.id,
    AccountToken.Purpose.CHANGE_EMAIL,
    ttl=timedelta(hours=24),
    new_email="Neha.New@uap-acme.dev",
)

print(
    "ULOGIN "
    + json.dumps(
        {
            "products": products,
            "beta_id": str(beta.id),
            "beta_slug": beta.slug,
            "owner": owner.email,
            "other": priya.email,
            "email_token": email_token,
            "per_seat": sorted(per_seat),
        }
    )
)

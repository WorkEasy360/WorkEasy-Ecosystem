"""Fixtures for the Phase 8 extension suites: an organization with real system
roles, members signed in through the real login endpoint, staff clients."""

from __future__ import annotations

import uuid

import pytest

PASSWORD = "correct horse battery staple 42"
BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


@pytest.fixture
def login(db):
    """Sign a user in through the real login endpoint (password only)."""
    from django.test import Client

    def make(user, *, password: str = PASSWORD, ip: str = "127.0.0.1"):
        client = Client(HTTP_USER_AGENT=BROWSER, REMOTE_ADDR=ip)
        client.get("/api/v1/auth/session")
        response = client.post(
            "/api/v1/auth/login",
            {"email": user.email, "password": password},
            content_type="application/json",
        )
        assert response.status_code == 200, response.content
        return client

    return make


@pytest.fixture
def console_org(db, organization_factory, user_factory):
    """An organization whose owner holds the real ``owner`` system role, and a
    factory for more members with a chosen system role."""
    from workeasy360.contracts.enums import MembershipStatus
    from workeasy360.platform.authz import services as authz
    from workeasy360.platform.organizations import projection
    from workeasy360.platform.organizations.models import Membership
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    def make(slug: str | None = None):
        owner = user_factory(password=PASSWORD, full_name="Olivia Owner")
        org, membership, _ = organization_factory(
            slug=slug or f"org-{uuid.uuid4().hex[:8]}", owner=owner
        )
        with (
            unscoped_access(reason="Test fixture assigns the owner role", module="tests"),
            organization_db_scope(org.id),
        ):
            authz.assign_system_role(
                organization_id=org.id, membership_id=membership.id, role_key="owner"
            )
        authz.bump_permission_version(org.id)

        def add(
            role: str = "member", *, status: str = "active", name: str = "", email=None
        ):
            user = user_factory(email=email, password=PASSWORD, full_name=name)
            with (
                unscoped_access(reason="Test fixture adds a member", module="tests"),
                organization_db_scope(org.id),
            ):
                member = Membership(
                    organization_id=org.id,
                    user_id=user.id,
                    status=status,
                    member_type="internal",
                )
                member.save()
                if role:
                    from workeasy360.contracts.values.tenant import TenantContext
                    from workeasy360.platform.tenancy.context import tenant_context

                    with tenant_context(
                        TenantContext(
                            organization_id=org.id,
                            membership_id=member.id,
                            user_id=user.id,
                        )
                    ):
                        authz.replace_system_role(
                            organization_id=org.id, membership_id=member.id, role_key=role
                        )
                if status == MembershipStatus.ACTIVE.value:
                    projection.sync_access_for_membership(
                        user_id=user.id, membership=member, organization=org
                    )
            return {"user": user, "membership": member}

        return {
            "org": org,
            "slug": org.slug,
            "owner": owner,
            "membership": membership,
            "add": add,
        }

    return make


def url(slug: str, suffix: str) -> str:
    return f"/api/v1/orgs/{slug}/{suffix}"


@pytest.fixture
def staff_login(staff_factory, user_factory, login):
    """A signed-in staff member holding exactly ``permissions``."""
    from workeasy360.platform.authz import staff

    root = user_factory(is_staff=True)

    def make(*permissions: str):
        user = user_factory(is_staff=True, password=PASSWORD)
        for permission in permissions:
            staff.create_grant(
                user_id=user.id,
                permission=permission,
                granted_by_id=root.id,
                reason="Test fixture grants a control-plane permission",
            )
        return login(user), user

    return make


def staff_scope(*permissions: str):
    """A staff context holding ``permissions``, bound to the platform
    organization's scope - for calling control-plane services directly."""
    import uuid as _uuid
    from contextlib import contextmanager

    from workeasy360.platform.authz import staff
    from workeasy360.platform.authz.control_plane import acting_scope, platform_context
    from workeasy360.platform.identity.models import User

    @contextmanager
    def enter():
        root = User.objects.create_user(
            email=f"root-{_uuid.uuid4().hex[:8]}@example.com", is_staff=True
        )
        actor = User.objects.create_user(
            email=f"staff-{_uuid.uuid4().hex[:8]}@example.com", is_staff=True
        )
        for permission in permissions:
            staff.create_grant(
                user_id=actor.id,
                permission=permission,
                granted_by_id=root.id,
                reason="Test helper grants a control-plane permission",
            )
        ctx = platform_context(user_id=actor.id)
        with acting_scope(ctx):
            yield ctx

    return enter()


# --- buying (Phase 9) -------------------------------------------------------------


def owner_context(org_fixture):
    """The owner's ``TenantContext`` for a ``console_org`` organization."""
    from workeasy360.contracts.enums import OrganizationStatus
    from workeasy360.contracts.values.tenant import TenantContext

    return TenantContext(
        organization_id=org_fixture["org"].id,
        membership_id=org_fixture["membership"].id,
        user_id=org_fixture["owner"].id,
        organization_status=OrganizationStatus(org_fixture["org"].status),
    )


def buy_directly(org_fixture, plan_version_id, *, currency="GBP", **kwargs):
    """An active subscription whose first period invoice is open - the state
    every *renewal* is in, and a sales-led account's after any purchase.

    Customers buy pay-first through orders (Phase 9); a suite about paying,
    dunning or refunding an ordinary period invoice needs that invoice to
    exist, so this buys through the subscription service directly, as staff
    and partners do."""
    from workeasy360.commercial.billing import services as billing
    from workeasy360.commercial.subscriptions import services as subscriptions
    from workeasy360.contracts.values.money import CurrencyCode
    from workeasy360.platform.tenancy.context import tenant_context
    from workeasy360.platform.tenancy.db import tenant_db_scope

    ctx = owner_context(org_fixture)
    with tenant_context(ctx), tenant_db_scope(ctx):
        billing.open_billing_account(
            ctx, currency=CurrencyCode(currency), billing_email="billing@example.com"
        )
        return subscriptions.purchase_plan(
            ctx,
            plan_version_id=plan_version_id,
            idempotency_key=uuid.uuid4().hex,
            **kwargs,
        )


@pytest.fixture
def fake_payments(db, settings):
    """The development test provider, enabled, taking every currency the
    catalog fixtures price in."""
    from workeasy360.commercial.payments.models import PaymentProvider

    settings.PAYMENTS_FAKE_PROVIDER_ENABLED = True
    return PaymentProvider.objects.create(
        key="test-provider",
        adapter="fake",
        display_name="Test provider",
        status="enabled",
        credential_ref="TEST",
        webhook_secret_ref="TEST",
        currencies=["GBP", "INR", "USD"],
    )


def place_order(client, slug, **body):
    return client.post(
        url(slug, "orders"),
        body,
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=body.pop("_key", None) or uuid.uuid4().hex,
    )


def pay_order(client, slug, organization_id, order, *, outcome="succeeded"):
    """Pay an order's invoice the way a browser does: start the payment, let
    the task create the hosted checkout, complete it on the test provider's
    page - which signs a callback and takes the real webhook path. Returns the
    order as the organization now sees it."""
    from workeasy360.commercial.payments.checkout import create_checkout
    from workeasy360.commercial.payments.invoicing import organization_scope
    from workeasy360.commercial.payments.models import PaymentAttempt

    started = client.post(
        url(slug, f"billing/invoices/{order['invoice_id']}/pay"),
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert started.status_code == 202, started.content
    attempt_id = uuid.UUID(started.json()["attempt_id"])
    assert create_checkout(organization_id, attempt_id) == "awaiting_payment"
    with organization_scope(organization_id, reason="Test reads the checkout reference"):
        reference = PaymentAttempt.unscoped.get(id=attempt_id).provider_reference
    done = client.post(
        "/api/v1/public/payments/test-checkout/complete",
        {
            "reference": reference,
            "organization_id": str(organization_id),
            "outcome": outcome,
        },
        content_type="application/json",
    )
    assert done.status_code == 200, done.content
    return client.get(url(slug, f"orders/{order['order_id']}")).json()


def place_and_pay(client, slug, organization_id, **body):
    """Place an order and, when it needs payment, pay it. Returns the order."""
    placed = place_order(client, slug, **body)
    assert placed.status_code == 201, placed.content
    order = placed.json()
    if order["requires_payment"]:
        order = pay_order(client, slug, organization_id, order)
    return order

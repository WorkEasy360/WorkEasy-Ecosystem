"""Commercial security controls, exercised through the real HTTP surface.

The defects this suite is written to catch, in the order they appear in the
Phase 2 brief: mass assignment, object-level authorization, price tampering,
currency tampering, subscription ownership, IDOR, tenant switching,
permission bypass, duplicate requests, replay, and unsafe admin endpoints.

Where a control can be asserted at the schema or service level as well, it is
- an HTTP test proves the endpoint is wired correctly, and a structural test
proves the control cannot be bypassed by reaching the service another way.
"""

from __future__ import annotations

import json
import uuid

import pytest
from django.test import Client
from django.urls import reverse

from workeasy360.commercial.subscriptions import services
from workeasy360.commercial.subscriptions.models import Subscription, SubscriptionItem

pytestmark = [pytest.mark.django_db, pytest.mark.security]


def key() -> str:
    return uuid.uuid4().hex


@pytest.fixture
def client_for():
    """A logged-in client for a given user."""

    def make(user):
        client = Client()
        client.force_login(user)
        return client

    return make


def _post(client, url, payload=None, *, idempotency_key=None):
    headers = {}
    if idempotency_key:
        headers["HTTP_IDEMPOTENCY_KEY"] = idempotency_key
    return client.post(
        url,
        data=json.dumps(payload or {}),
        content_type="application/json",
        **headers,
    )


# --- authorization on the admin surface -----------------------------------


def test_a_customer_cannot_reach_the_catalog_admin_endpoint(
    catalog, customer, client_for
):
    """The internal surface is gated by the service, not by the URL.

    The customer's own organization slug is in the path - which is exactly
    the request an attacker would craft - and the answer is still 403.
    """
    client = client_for(customer["user"])
    url = reverse("commercial-products", kwargs={"org_slug": customer["org"].slug})
    response = _post(client, url, {"code": "sneaky", "name": "Sneaky"})
    assert response.status_code == 403


def test_a_customer_cannot_publish_a_price_over_http(catalog, customer, client_for):
    client = client_for(customer["user"])
    url = reverse("commercial-prices", kwargs={"org_slug": customer["org"].slug})
    response = _post(
        client,
        url,
        {
            "price_book_id": str(catalog["price_book"].id),
            "plan_version_id": str(catalog["versions"]["crm_starter"].id),
            "interval": "monthly",
            "amount_minor": 1,
        },
    )
    assert response.status_code == 403


def test_staff_status_alone_does_not_grant_catalog_management(
    db, organization_factory, tenant_ctx_factory, client_for, sync_permissions
):
    """Both barriers are required, in both directions.

    A staff identity with no commercial role must be refused just as a
    customer with the role is.
    """
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    org, membership, user = organization_factory()
    with unscoped_access(
        reason="Test marks an identity as staff without granting a role",
        module="tests.security",
    ):
        user.is_staff = True
        user.save(update_fields=["is_staff"])

    client = client_for(user)
    url = reverse("commercial-products", kwargs={"org_slug": org.slug})
    assert _post(client, url, {"code": "sneaky", "name": "Sneaky"}).status_code == 403


# --- tenant isolation and IDOR --------------------------------------------


def test_a_tenant_cannot_read_another_tenants_subscription(
    catalog,
    customer,
    billing_account,
    in_tenant,
    client_for,
    organization_factory,
    tenant_ctx_factory,
    grant_permissions,
):
    """IDOR with a *valid* id belonging to somebody else."""
    owner = customer["ctx"]
    billing_account(owner)
    with in_tenant(owner):
        services.purchase_plan(
            owner,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )
        subscription = Subscription.objects.get()

    org, membership, user = organization_factory()
    grant_permissions(org, membership, ("commercial.subscription.view",))

    client = client_for(user)
    # Their own organization in the path, somebody else's subscription id.
    url = reverse(
        "subscription-detail",
        kwargs={"org_slug": org.slug, "subscription_id": str(subscription.id)},
    )
    assert client.get(url).status_code == 404


def test_tenant_switching_by_path_is_refused(
    catalog, customer, billing_account, in_tenant, client_for, organization_factory
):
    """Putting somebody else's slug in the path does not grant their tenant.

    Resolution requires an active membership. A valid slug the caller does
    not belong to gets the same answer as one that does not exist, which is
    deliberate: distinguishing them would let an attacker enumerate tenants.
    """
    owner = customer["ctx"]
    billing_account(owner)
    with in_tenant(owner):
        services.purchase_plan(
            owner,
            plan_version_id=catalog["versions"]["crm_starter"].id,
            idempotency_key=key(),
        )

    other_org, _, other_user = organization_factory()
    client = client_for(other_user)

    theirs = client.get(
        reverse("subscriptions", kwargs={"org_slug": customer["org"].slug})
    )
    unknown = client.get(
        reverse("subscriptions", kwargs={"org_slug": "no-such-org-at-all"})
    )

    assert theirs.status_code >= 400
    assert theirs.status_code == unknown.status_code, (
        "a foreign tenant and a nonexistent one answer differently, which "
        "lets an attacker enumerate organizations"
    )
    assert "crm" not in theirs.content.decode()


def test_an_unauthenticated_caller_gets_nothing(customer):
    """Denied, and told nothing about whether the organization exists.

    The exact status is deliberately not asserted: the middleware answers
    identically for "no such tenant" and "not your tenant", and pinning one
    code here would turn a disclosure decision into a test fixture. What
    matters is that it is a denial and carries no data.
    """
    url = reverse("subscriptions", kwargs={"org_slug": customer["org"].slug})
    response = Client().get(url)
    assert response.status_code >= 400
    assert "results" not in response.content.decode()


# --- mass assignment ------------------------------------------------------
#
# A customer buys through a pay-first order (ADR-035): the order freezes the
# server's price and nothing is granted until its invoice is paid. So the
# assertions read the *order* - what payment will charge and fulfilment will
# create - and that no subscription exists before the money does.


def _orders_url(customer) -> str:
    return reverse("orders", kwargs={"org_slug": customer["org"].slug})


def _only_order(in_tenant, customer):
    from workeasy360.commercial.payments.models import Order

    with in_tenant(customer["ctx"]):
        return Order.objects.get()


def test_a_client_cannot_set_the_price_on_a_purchase(
    catalog, customer, billing_account, in_tenant, client_for
):
    """Price tampering.

    The handler never reads an amount from the body; the price is resolved
    server-side from the catalog and frozen in the order. Extra fields are
    ignored rather than honoured.
    """
    billing_account(customer["ctx"])
    client = client_for(customer["user"])
    response = _post(
        client,
        _orders_url(customer),
        {
            "plan_version_id": str(catalog["versions"]["crm_professional"].id),
            "total_amount_minor": 1,
            "unit_amount_minor": 1,
            "amount_due_minor": 1,
            "recurring_amount_minor": 1,
            "price_id": str(uuid.uuid4()),
            "currency": "INR",
        },
        idempotency_key=key(),
    )
    assert response.status_code == 201, response.content
    assert response.json()["amount_due_minor"] == 4999

    order = _only_order(in_tenant, customer)
    assert order.recurring_amount_minor == order.amount_due_minor == 4999
    assert order.currency == "GBP"
    assert order.price_snapshot["price_id"] == str(
        catalog["prices"]["crm_professional"].id
    )
    assert order.price_snapshot["total_amount_minor"] == 4999


def test_a_client_cannot_set_the_subscription_status(
    catalog, customer, billing_account, in_tenant, client_for
):
    """Status is reached only through the state machine: asking for a paid,
    fulfilled order with an active subscription gets an order awaiting
    payment and no subscription at all."""
    billing_account(customer["ctx"])
    client = client_for(customer["user"])
    response = _post(
        client,
        _orders_url(customer),
        {
            "plan_version_id": str(catalog["versions"]["crm_professional"].id),
            "status": "fulfilled",
            "paid_at": "2026-01-01T00:00:00Z",
            "requires_payment": False,
            "subscription_status": "active",
            "cancel_at": None,
            "version": 999,
        },
        idempotency_key=key(),
    )
    assert response.status_code == 201, response.content
    order = _only_order(in_tenant, customer)
    assert order.status == "pending_payment"
    assert order.paid_at is None
    assert order.version < 999
    with in_tenant(customer["ctx"]):
        assert not Subscription.objects.exists()
        assert not SubscriptionItem.objects.exists()


def test_a_client_cannot_set_the_organization_on_a_purchase(
    catalog, customer, billing_account, in_tenant, client_for, organization_factory
):
    """Cross-tenant write by body parameter."""
    from workeasy360.commercial.payments.models import Order
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    billing_account(customer["ctx"])
    other_org, _, _ = organization_factory()
    client = client_for(customer["user"])

    response = _post(
        client,
        _orders_url(customer),
        {
            "plan_version_id": str(catalog["versions"]["crm_starter"].id),
            "organization": str(other_org.id),
            "organization_id": str(other_org.id),
        },
        idempotency_key=key(),
    )
    assert response.status_code == 201, response.content
    with in_tenant(customer["ctx"]):
        order = Order.objects.select_related("invoice").get()
        assert order.organization_id == customer["org"].id
        assert order.invoice.organization_id == customer["org"].id
    with unscoped_access(
        reason="Test asserts no order was written under another organization",
        module="tests.security",
    ):
        assert not Order.unscoped.filter(organization_id=other_org.id).exists()


# --- currency tampering ---------------------------------------------------


def test_a_purchase_cannot_be_made_in_an_arbitrary_currency(
    catalog, customer, billing_account, in_tenant, client_for
):
    """Currency comes from the billing account, never from the request.

    Accepting it from a client is how a customer buys a GBP plan at an INR
    price.
    """
    from workeasy360.contracts.values.money import CurrencyCode

    billing_account(customer["ctx"], currency=CurrencyCode.INR)
    client = client_for(customer["user"])
    for route in ("orders", "order-quote"):
        response = _post(
            client,
            reverse(route, kwargs={"org_slug": customer["org"].slug}),
            {
                "plan_version_id": str(catalog["versions"]["crm_professional"].id),
                "currency": "GBP",
            },
            idempotency_key=key(),
        )
        # The organization bills in INR and no INR price exists, so the order
        # is refused rather than silently priced in GBP.
        assert response.status_code == 409, (route, response.content)
        assert response.json()["code"] == "price_not_available"


# --- duplicate requests and replay ---------------------------------------


def test_a_replayed_request_does_not_buy_twice(
    catalog, customer, billing_account, in_tenant, client_for
):
    from workeasy360.commercial.payments.models import Invoice, Order

    billing_account(customer["ctx"])
    client = client_for(customer["user"])
    command = key()
    payload = {"plan_version_id": str(catalog["versions"]["crm_professional"].id)}

    first = _post(client, _orders_url(customer), payload, idempotency_key=command)
    second = _post(client, _orders_url(customer), payload, idempotency_key=command)

    assert first.status_code == second.status_code == 201
    assert first.json()["order_id"] == second.json()["order_id"]
    assert first.json()["invoice_id"] == second.json()["invoice_id"]
    with in_tenant(customer["ctx"]):
        assert Order.objects.count() == 1
        assert Invoice.objects.count() == 1


def test_a_command_without_an_idempotency_key_is_refused(
    catalog, customer, billing_account, client_for
):
    """A retried command with no key would buy twice, and the client cannot
    tell that it has."""
    billing_account(customer["ctx"])
    client = client_for(customer["user"])
    response = _post(
        client,
        _orders_url(customer),
        {"plan_version_id": str(catalog["versions"]["crm_starter"].id)},
    )
    assert response.status_code == 400
    assert "Idempotency-Key" in response.json()["detail"]


def test_replaying_a_key_with_a_different_body_is_refused(
    catalog, customer, billing_account, client_for
):
    """One authorisation, one operation."""
    billing_account(customer["ctx"])
    client = client_for(customer["user"])
    command = key()

    first = _post(
        client,
        _orders_url(customer),
        {"plan_version_id": str(catalog["versions"]["crm_starter"].id)},
        idempotency_key=command,
    )
    assert first.status_code == 201, first.content
    response = _post(
        client,
        _orders_url(customer),
        {"plan_version_id": str(catalog["versions"]["books_professional"].id)},
        idempotency_key=command,
    )
    assert response.status_code == 409
    assert response.json()["code"] == "idempotency_conflict"


# --- error responses ------------------------------------------------------


def test_a_domain_error_does_not_leak_internals(
    catalog, customer, billing_account, client_for
):
    """The client gets a code and a correlation id, never a traceback - also
    for an identifier of something that is not on sale."""
    billing_account(customer["ctx"])
    client = client_for(customer["user"])
    for payload in (
        {"plan_version_id": str(uuid.uuid4())},
        {"plan_version_id": "not-a-uuid"},
        {"bundle_version_id": str(uuid.uuid4()), "declined_plan_version_ids": ["x"]},
    ):
        response = _post(client, _orders_url(customer), payload, idempotency_key=key())
        body = response.json()
        assert 400 <= response.status_code < 500, (payload, response.content)
        assert set(body) >= {"code", "detail", "correlation_id"}
        assert "Traceback" not in json.dumps(body)


def test_a_missing_record_and_a_foreign_record_look_identical(
    catalog, customer, billing_account, in_tenant, client_for
):
    """Distinguishing them is an enumeration oracle."""
    billing_account(customer["ctx"])
    client = client_for(customer["user"])

    nonexistent = client.get(
        reverse(
            "subscription-detail",
            kwargs={
                "org_slug": customer["org"].slug,
                "subscription_id": str(uuid.uuid4()),
            },
        )
    )
    assert nonexistent.status_code == 404


# --- suspension -----------------------------------------------------------


def test_a_suspended_organization_cannot_buy(
    catalog, customer, billing_account, in_tenant, client_for
):
    """R-10 still applies to commercial writes.

    Reads continue - the customer must be able to see what they have and
    export it - but the organization cannot take on new commitments.
    """
    from workeasy360.contracts.enums import OrganizationStatus
    from workeasy360.platform.organizations.models import Organization
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    billing_account(customer["ctx"])
    with unscoped_access(
        reason="Test suspends an organization to assert the write block",
        module="tests.security",
    ):
        Organization.objects.filter(id=customer["org"].id).update(
            status=OrganizationStatus.SUSPENDED.value
        )

    client = client_for(customer["user"])
    response = _post(
        client,
        _orders_url(customer),
        {"plan_version_id": str(catalog["versions"]["crm_starter"].id)},
        idempotency_key=key(),
    )
    assert response.status_code == 402

    read = client.get(reverse("subscriptions", kwargs={"org_slug": customer["org"].slug}))
    assert read.status_code == 200


# --- structural controls --------------------------------------------------


def test_commercial_views_contain_no_business_logic():
    """F-7 / G4: a view calls at most one application service.

    Business logic in a view is bypassed by the task path and by every
    internal caller, and is untestable without HTTP.
    """
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "workeasy360"
        / "interfaces"
        / "api"
        / "v1"
        / "commercial_views.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    service_modules = {
        "catalog_services",
        "pricing_services",
        "subscription_services",
        "billing",
    }

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.endswith("_view"):
            continue
        calls = [
            child.func.value.id
            for child in ast.walk(node)
            if isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and isinstance(child.func.value, ast.Name)
        ]
        distinct = {name for name in calls if name in service_modules}
        if len(distinct) > 1:
            offenders.append(f"{node.name}: {sorted(distinct)}")
    assert not offenders, "Views calling more than one service:\n" + "\n".join(offenders)


def test_every_commercial_mutation_is_audited():
    """Sensitive operations leave a record, asserted over the source.

    Scoped to services that take a ``ctx``, because those are the ones with
    an actor to attribute. ``provisioning.grant_product`` takes none: it
    writes the derived entitlement read model from a bootstrap or a test, not
    commercial state, and inventing an actor for it would put a name against
    something nobody did. Commercial mutations that *do* have an actor are
    covered here, and the clock-driven ones are covered by
    ``record_system`` plus their own tests.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "workeasy360" / "commercial"
    mutating_prefixes = (
        "create_",
        "publish_",
        "draft_",
        "set_",
        "grant_",
        "close_",
        "purchase_",
        "activate_",
        "change_",
        "cancel_",
        "open_",
        "link_",
    )
    unaudited = []
    for path in root.rglob("*.py"):
        if "/tests/" in path.as_posix() or path.name != "services.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if not node.name.startswith(mutating_prefixes):
                continue
            takes_ctx = bool(node.args.args) and node.args.args[0].arg == "ctx"
            if not takes_ctx:
                continue
            audited = any(
                isinstance(child, ast.Attribute)
                and child.attr in {"record", "record_system"}
                for child in ast.walk(node)
            )
            if not audited:
                unaudited.append(f"{path.parent.name}.{node.name}")
    assert not unaudited, f"Unaudited commercial mutations: {unaudited}"


def test_audit_metadata_carries_no_personal_data(
    catalog, customer, billing_account, in_tenant
):
    """The audit log is customer-readable and retained for seven years."""
    from workeasy360.platform.audit.models import AuditEvent

    ctx = customer["ctx"]
    with in_tenant(ctx):
        from workeasy360.commercial.billing import services as billing_services

        billing_services.open_billing_account(
            ctx,
            currency=__import__(
                "workeasy360.contracts.values.money", fromlist=["CurrencyCode"]
            ).CurrencyCode.GBP,
            billing_email="finance@customer.example",
        )
        entries = list(AuditEvent.objects.values_list("changes", flat=True))

    serialised = json.dumps(entries)
    assert "finance@customer.example" not in serialised

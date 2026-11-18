"""Admin UX Phase 3: the staff lists filter on the server.

Every list below takes ``from`` and ``to``: whole UTC days (``YYYY-MM-DD``), both
included, either end open. The filter is applied by the database, composes with the
list's other filters, and the keyset cursor pages within the filtered rows. Without
the parameters every list answers exactly as before. The user directory also takes
``product`` (people in an organization holding it now) and the order list ``q`` (the
order number or the customer's name). Through the real endpoints on real PostgreSQL.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest import mock

import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db

UTC = dt_timezone.utc

#: The window every test asks for: 1 to 10 March 2026, both days included.
WINDOW = {"from": "2026-03-01", "to": "2026-03-10"}

#: One row at each edge of the window: the last second before it, its first
#: instant, the last second of ``to`` and the first instant after it.
MOMENTS = {
    "before": datetime(2026, 2, 28, 23, 59, 59, tzinfo=UTC),
    "first": datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC),
    "last": datetime(2026, 3, 10, 23, 59, 59, tzinfo=UTC),
    "after": datetime(2026, 3, 11, 0, 0, 0, tzinfo=UTC),
}

PAYMENT_VIEW = "platform.staff_payment.view"

#: Every list that takes the date filter, and the grant it is read with.
LISTS = [
    ("/api/v1/admin/partners/resellers", "platform.staff_partner.view"),
    ("/api/v1/admin/customers", "platform.staff_customer.view"),
    ("/api/v1/admin/users", "platform.staff_user.view"),
    ("/api/v1/admin/orders", PAYMENT_VIEW),
    ("/api/v1/admin/subscriptions", "platform.staff_customer.view"),
    ("/api/v1/admin/invoices", PAYMENT_VIEW),
    ("/api/v1/admin/refunds", PAYMENT_VIEW),
    ("/api/v1/admin/credit-notes", PAYMENT_VIEW),
    ("/api/v1/admin/tickets", "platform.staff_support.view"),
    ("/api/v1/admin/kyc/applications", "platform.staff_kyc.view"),
    ("/api/v1/admin/commission/accruals", "platform.staff_commission.view"),
    ("/api/v1/admin/payouts", "platform.staff_commission.view"),
]


def _backdate(model, pk, field: str, when: datetime) -> None:
    """Move one row's date. ``auto_now_add`` sets it on create, so a test that needs
    a past date moves it afterwards."""
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with unscoped_access(
        reason="Test moves a row into the date filter's window", module="tests"
    ):
        assert model.objects.filter(pk=pk).update(**{field: when}) == 1


def _walk(client, path: str, params: dict, key: str) -> list[str]:
    """Every row of a keyset list, one row per page: what the cursor pages over."""
    seen: list[str] = []
    cursor = None
    for _ in range(20):
        query = {**params, "limit": 1, **({"cursor": cursor} if cursor else {})}
        page = client.get(path, query)
        assert page.status_code == 200, page.content
        body = page.json()
        seen += [row[key] for row in body["results"]]
        if not body["has_more"]:
            return seen
        cursor = body["next_cursor"]
    raise AssertionError("the cursor never ended")


def _refused(client, path: str, params: dict, field: str) -> str:
    response = client.get(path, params)
    assert response.status_code == 400, response.content
    assert response.json()["field"] == field
    return response.json()["detail"]


# --- the window itself ------------------------------------------------------------------


class TestTheWindow:
    def test_whole_days_with_to_included_and_either_end_open(self):
        from workeasy360.commercial.controlplane.overview import optional_window

        assert optional_window("2026-03-01", "2026-03-10") == (
            MOMENTS["first"],
            MOMENTS["after"],
        )
        assert optional_window("2026-03-01", None) == (MOMENTS["first"], None)
        assert optional_window(None, "2026-03-10") == (None, MOMENTS["after"])
        assert optional_window(None, None) == (None, None)
        assert optional_window("", "") == (None, None)
        # One day is a range of its own.
        assert optional_window("2026-03-10", "2026-03-10") == (
            datetime(2026, 3, 10, tzinfo=UTC),
            MOMENTS["after"],
        )

    def test_a_reversed_range_is_refused_not_answered_with_nothing(self):
        from workeasy360.commercial.controlplane.overview import optional_window
        from workeasy360.contracts.errors import CatalogValidationError

        with pytest.raises(CatalogValidationError) as refused:
            optional_window("2026-03-10", "2026-03-01")
        assert refused.value.message == "The date range ends before it starts."
        assert refused.value.context["field"] == "from"

    @pytest.mark.parametrize(
        ("raw_from", "raw_to", "field"),
        [
            ("yesterday", None, "from"),
            ("2026-13-01", None, "from"),
            (None, "10/03/2026", "to"),
            # The last representable day has no day after it: a 400, never a 500.
            (None, "9999-12-31", "to"),
        ],
    )
    def test_a_bad_date_names_its_field(self, raw_from, raw_to, field):
        from workeasy360.commercial.controlplane.overview import optional_window
        from workeasy360.contracts.errors import CatalogValidationError

        with pytest.raises(CatalogValidationError) as refused:
            optional_window(raw_from, raw_to)
        assert refused.value.message == "Use a date like 2026-11-09."
        assert refused.value.context["field"] == field

    def test_the_audit_lists_that_already_used_it_still_answer(self, staff_login):
        staff, _ = staff_login("platform.staff_audit.view")
        for path in ("/api/v1/admin/audit", "/api/v1/admin/security/sign-ins"):
            assert staff.get(path, WINDOW).status_code == 200
            assert staff.get(path, {"from": "2026-03-01"}).status_code == 200
            detail = _refused(
                staff, path, {"from": "2026-03-10", "to": "2026-03-01"}, "from"
            )
            assert detail == "The date range ends before it starts."


@pytest.mark.parametrize(("path", "grant"), LISTS)
def test_every_list_refuses_a_bad_or_reversed_range(staff_login, path, grant):
    staff, _ = staff_login(grant)
    assert staff.get(path).status_code == 200
    assert staff.get(path, WINDOW).status_code == 200
    assert _refused(staff, path, {"from": "March"}, "from") == (
        "Use a date like 2026-11-09."
    )
    assert _refused(staff, path, {"to": "2026-02-30"}, "to") == (
        "Use a date like 2026-11-09."
    )
    assert _refused(staff, path, {"from": "2026-03-10", "to": "2026-03-09"}, "from") == (
        "The date range ends before it starts."
    )


# --- partners -----------------------------------------------------------------------------


class TestPartnerList:
    PATH = "/api/v1/admin/partners/resellers"

    def test_the_window_keeps_partners_created_in_it(self, staff_login, partner_factory):
        from workeasy360.commercial.partners.models import PartnerAccount

        staff, _ = staff_login("platform.staff_partner.view")
        code = {}
        for name, when in MOMENTS.items():
            partner = partner_factory(code=f"df-{name}-{uuid.uuid4().hex[:6]}")["partner"]
            _backdate(PartnerAccount, partner.id, "created_at", when)
            code[name] = partner.code

        def codes(**params):
            query = {"scope": "all", "q": "df-", "limit": 100, **params}
            rows = staff.get(self.PATH, query).json()["results"]
            return {row["code"] for row in rows}

        assert codes() == set(code.values())
        assert codes(**WINDOW) == {code["first"], code["last"]}
        assert codes(**{"from": "2026-03-01"}) == {
            code["first"],
            code["last"],
            code["after"],
        }
        assert codes(to="2026-03-10") == {code["before"], code["first"], code["last"]}
        # It composes with the search.
        assert codes(q=code["first"], **WINDOW) == {code["first"]}
        assert codes(q=code["after"], **WINDOW) == set()
        # The cursor pages inside the window, newest first.
        assert _walk(
            staff, self.PATH, {"scope": "all", "q": "df-", **WINDOW}, "code"
        ) == [code["last"], code["first"]]


# --- customers ----------------------------------------------------------------------------


def _summary(org, *products: str) -> None:
    """What the organization holds now (the provisioning projection)."""
    from workeasy360.commercial.provisioning.models import CustomerSummary

    CustomerSummary.objects.create(
        organization=org,
        subscription_status="active",
        product_codes=list(products),
        seat_capacity=5,
        access_class="customer",
        computed_at=timezone.now(),
    )


class TestCustomerDirectory:
    PATH = "/api/v1/admin/customers"

    def test_the_window_keeps_customers_created_in_it(
        self, staff_login, organization_factory
    ):
        from workeasy360.platform.organizations.models import Organization

        staff, _ = staff_login("platform.staff_customer.view")
        slug = {}
        for name, when in MOMENTS.items():
            org, _, _ = organization_factory(slug=f"dfc-{name}-{uuid.uuid4().hex[:6]}")
            _backdate(Organization, org.id, "created_at", when)
            slug[name] = org.slug
            if name in ("first", "after"):
                _summary(org, "crm")

        def slugs(**params):
            query = {"q": "dfc-", "limit": 100, **params}
            rows = staff.get(self.PATH, query).json()["results"]
            return {row["slug"] for row in rows}

        assert slugs() == set(slug.values())
        assert slugs(**WINDOW) == {slug["first"], slug["last"]}
        assert slugs(**{"from": "2026-03-01"}) == {
            slug["first"],
            slug["last"],
            slug["after"],
        }
        assert slugs(to="2026-03-10") == {slug["before"], slug["first"], slug["last"]}
        # It composes with the product filter (a subquery of what is held).
        assert slugs(product="crm") == {slug["first"], slug["after"]}
        assert slugs(product="crm", **WINDOW) == {slug["first"]}
        assert _walk(staff, self.PATH, {"q": "dfc-", **WINDOW}, "slug") == [
            slug["last"],
            slug["first"],
        ]


# --- users --------------------------------------------------------------------------------


class TestUserDirectory:
    PATH = "/api/v1/admin/users"

    def _emails(self, staff, **params):
        query = {"limit": 100, **params}
        response = staff.get(self.PATH, query)
        assert response.status_code == 200, response.content
        return {row["email"] for row in response.json()["results"]}

    def test_the_window_keeps_accounts_created_in_it(self, staff_login, user_factory):
        from workeasy360.platform.identity.models import User

        staff, _ = staff_login("platform.staff_user.view")
        email = {}
        for name, when in MOMENTS.items():
            user = user_factory(
                email=f"dfu-{name}-{uuid.uuid4().hex[:6]}@example.com",
                # The last one never verified its address.
                **({"status": "pending"} if name == "last" else {}),
            )
            _backdate(User, user.id, "created_at", when)
            email[name] = user.email

        assert self._emails(staff, q="dfu-") == set(email.values())
        assert self._emails(staff, q="dfu-", **WINDOW) == {email["first"], email["last"]}
        assert self._emails(staff, q="dfu-", **{"from": "2026-03-01"}) == {
            email["first"],
            email["last"],
            email["after"],
        }
        assert self._emails(staff, q="dfu-", to="2026-03-10") == {
            email["before"],
            email["first"],
            email["last"],
        }
        # It composes with a segment.
        assert self._emails(staff, q="dfu-", segment="unverified", **WINDOW) == {
            email["last"]
        }
        assert _walk(staff, self.PATH, {"q": "dfu-", **WINDOW}, "email") == [
            email["last"],
            email["first"],
        ]

    def test_product_keeps_people_whose_organization_holds_it_now(
        self, staff_login, organization_factory, user_factory
    ):
        staff, _ = staff_login("platform.staff_user.view")
        owners = {}
        for name, products in (("crm", ["crm"]), ("books", ["books"]), ("none", None)):
            owner = user_factory(email=f"dfp-{name}-{uuid.uuid4().hex[:6]}@example.com")
            org, _, _ = organization_factory(owner=owner)
            if products:
                _summary(org, *products)
            owners[name] = owner.email

        assert self._emails(staff, q="dfp-") == set(owners.values())
        assert self._emails(staff, q="dfp-", product="crm") == {owners["crm"]}
        assert self._emails(staff, q="dfp-", product="books") == {owners["books"]}
        assert self._emails(staff, q="dfp-", product="helpdesk") == set()
        # It composes with the date: these accounts were created today, not in March.
        assert self._emails(staff, q="dfp-", product="crm", **WINDOW) == set()
        today = timezone.now().date().isoformat()
        assert self._emails(staff, q="dfp-", product="crm", **{"from": today}) == {
            owners["crm"]
        }
        # A value that cannot be a product code is refused, as on the customer list.
        for bad in ("CRM!", "x", "a" * 40):
            assert _refused(staff, self.PATH, {"product": bad}, "product") == (
                "Unknown product filter."
            )


# --- orders -------------------------------------------------------------------------------


def _order(org, number: str, when: datetime, *, status: str = "paid"):
    from workeasy360.commercial.payments.models import OrderSummary

    return OrderSummary.objects.create(
        order_id=uuid.uuid4(),
        organization_id=org.id,
        number=number,
        kind="plan",
        status=status,
        description="Easy CRM",
        currency="GBP",
        amount_due_minor=1000,
        created_at=when,
    )


class TestOrderList:
    PATH = "/api/v1/admin/orders"

    def _numbers(self, staff, **params):
        response = staff.get(self.PATH, {"limit": 100, **params})
        assert response.status_code == 200, response.content
        return {row["number"] for row in response.json()["results"]}

    def test_the_window_keeps_orders_placed_in_it(
        self, staff_login, organization_factory
    ):
        staff, _ = staff_login(PAYMENT_VIEW)
        org, _, _ = organization_factory()
        for name, when in MOMENTS.items():
            _order(
                org,
                f"ORD-{name.upper()}",
                when,
                status="cancelled" if name == "first" else "paid",
            )

        assert self._numbers(staff, **WINDOW) == {"ORD-FIRST", "ORD-LAST"}
        assert self._numbers(staff, **{"from": "2026-03-01"}) == {
            "ORD-FIRST",
            "ORD-LAST",
            "ORD-AFTER",
        }
        assert self._numbers(staff, to="2026-03-10") == {
            "ORD-BEFORE",
            "ORD-FIRST",
            "ORD-LAST",
        }
        # It composes with the status filter.
        assert self._numbers(staff, status="paid", **WINDOW) == {"ORD-LAST"}
        assert _walk(staff, self.PATH, WINDOW, "number") == ["ORD-LAST", "ORD-FIRST"]

    def test_q_matches_the_order_number_or_the_customers_name(
        self, staff_login, organization_factory
    ):
        staff, _ = staff_login(PAYMENT_VIEW)
        northwind, _, _ = organization_factory(display_name="Northwind Traders")
        globex, _, _ = organization_factory(display_name="Globex Corporation")
        _order(northwind, "ORD-000101", MOMENTS["first"])
        _order(globex, "ORD-000202", MOMENTS["after"])

        assert self._numbers(staff, q="ORD-000101") == {"ORD-000101"}
        assert self._numbers(staff, q="000202") == {"ORD-000202"}
        assert self._numbers(staff, q="northwind") == {"ORD-000101"}
        assert self._numbers(staff, q="  GLOBEX  ") == {"ORD-000202"}
        assert self._numbers(staff, q="ORD-0") == {"ORD-000101", "ORD-000202"}
        assert self._numbers(staff, q="Initech") == set()
        # It composes with the window and the cursor.
        assert self._numbers(staff, q="globex", **WINDOW) == set()
        assert self._numbers(staff, q="ORD-0", **WINDOW) == {"ORD-000101"}
        assert _walk(staff, self.PATH, {"q": "ORD-0"}, "number") == [
            "ORD-000202",
            "ORD-000101",
        ]
        # A long search is bounded, never an error.
        assert self._numbers(staff, q="ORD-000101" + "x" * 200) == set()

    def test_the_row_shape_is_unchanged(self, staff_login, organization_factory):
        staff, _ = staff_login(PAYMENT_VIEW)
        org, _, _ = organization_factory(display_name="Northwind Traders")
        _order(org, "ORD-000303", MOMENTS["first"])
        plain = staff.get(self.PATH).json()
        filtered = staff.get(self.PATH, {"q": "northwind", **WINDOW}).json()
        assert set(plain) == set(filtered) == {"results", "next_cursor", "has_more"}
        assert plain["results"] == filtered["results"]
        assert filtered["results"][0]["organization_name"] == "Northwind Traders"


# --- subscriptions --------------------------------------------------------------------------


class TestSubscriptionList:
    PATH = "/api/v1/admin/subscriptions"

    def test_the_window_keeps_subscriptions_created_in_it(
        self, staff_login, organization_factory
    ):
        from workeasy360.commercial.subscriptions.models import SubscriptionSummary

        staff, _ = staff_login("platform.staff_customer.view")
        org, _, _ = organization_factory()
        ident = {}
        for name, when in MOMENTS.items():
            row = SubscriptionSummary.objects.create(
                subscription_id=uuid.uuid4(),
                organization_id=org.id,
                status="cancelled" if name == "first" else "active",
                currency="GBP",
                billing_interval="monthly",
                renewal_policy="auto",
                current_period_start=when,
                current_period_end=when + timedelta(days=30),
                activated_at=when,
                created_at=when,
            )
            ident[str(row.subscription_id)] = name

        def names(**params):
            body = staff.get(self.PATH, {"limit": 100, **params}).json()
            return {ident[row["subscription_id"]] for row in body["results"]}

        assert names(**WINDOW) == {"first", "last"}
        assert names(**{"from": "2026-03-01"}) == {"first", "last", "after"}
        assert names(to="2026-03-10") == {"before", "first", "last"}
        assert names(status="active", **WINDOW) == {"last"}
        walked = _walk(staff, self.PATH, WINDOW, "subscription_id")
        assert [ident[i] for i in walked] == ["last", "first"]
        # The status counts still cover every subscription.
        counts = staff.get(self.PATH, WINDOW).json()["counts"]
        assert counts == {"active": 3, "cancelled": 1}


# --- invoices, refunds, credit notes ----------------------------------------------------------


class TestBillingLists:
    def test_invoices_by_the_day_they_were_issued(
        self, staff_login, organization_factory
    ):
        from workeasy360.commercial.payments.models import InvoiceSummary

        staff, _ = staff_login(PAYMENT_VIEW)
        org, _, _ = organization_factory()
        for name, when in MOMENTS.items():
            InvoiceSummary.objects.create(
                invoice_id=uuid.uuid4(),
                organization_id=org.id,
                subscription_id=uuid.uuid4(),
                number=f"INV-{name.upper()}",
                status="void" if name == "first" else "paid",
                currency="GBP",
                total_minor=1000,
                collection_mode="automatic",
                issued_at=when,
                due_at=when,
            )
        path = "/api/v1/admin/invoices"

        def numbers(**params):
            rows = staff.get(path, {"limit": 100, **params}).json()["results"]
            return {row["number"] for row in rows}

        assert numbers(**WINDOW) == {"INV-FIRST", "INV-LAST"}
        assert numbers(**{"from": "2026-03-01"}) == {"INV-FIRST", "INV-LAST", "INV-AFTER"}
        assert numbers(to="2026-03-10") == {"INV-BEFORE", "INV-FIRST", "INV-LAST"}
        assert numbers(status="paid", **WINDOW) == {"INV-LAST"}
        assert numbers(q="INV-L", **WINDOW) == {"INV-LAST"}
        assert _walk(staff, path, WINDOW, "number") == ["INV-LAST", "INV-FIRST"]

    def test_refunds_by_the_day_they_were_asked_for(
        self, staff_login, organization_factory
    ):
        from workeasy360.commercial.payments.models import RefundSummary

        staff, _ = staff_login(PAYMENT_VIEW)
        org, _, owner = organization_factory()
        ident = {}
        for name, when in MOMENTS.items():
            row = RefundSummary.objects.create(
                refund_id=uuid.uuid4(),
                organization_id=org.id,
                attempt_id=uuid.uuid4(),
                amount_minor=500,
                currency="GBP",
                reason="Order could not be fulfilled",
                status="failed" if name == "first" else "succeeded",
                requested_by_user_id=owner.id,
                created_at=when,
            )
            ident[str(row.refund_id)] = name
        path = "/api/v1/admin/refunds"

        def names(**params):
            rows = staff.get(path, {"limit": 100, **params}).json()["results"]
            return {ident[row["refund_id"]] for row in rows}

        assert names(**WINDOW) == {"first", "last"}
        assert names(**{"from": "2026-03-01"}) == {"first", "last", "after"}
        assert names(to="2026-03-10") == {"before", "first", "last"}
        assert names(status="succeeded", **WINDOW) == {"last"}
        # The "waiting for someone" view composes too: only the failed one waits.
        assert names(waiting="1", **WINDOW) == {"first"}
        assert names(waiting="1", **{"from": "2026-03-02"}) == set()
        walked = _walk(staff, path, WINDOW, "refund_id")
        assert [ident[i] for i in walked] == ["last", "first"]

    def test_credit_notes_by_the_day_they_were_issued(
        self, staff_login, organization_factory
    ):
        from workeasy360.commercial.payments.models import CreditNote

        staff, _ = staff_login(PAYMENT_VIEW)
        org, _, _ = organization_factory()
        for name, when in MOMENTS.items():
            CreditNote.objects.create(
                number=f"CN-{name.upper()}-{uuid.uuid4().hex[:4]}",
                organization_id=org.id,
                refund_id=uuid.uuid4(),
                attempt_id=uuid.uuid4(),
                invoice_id=uuid.uuid4(),
                invoice_number=f"INV-{name.upper()}",
                amount_minor=500,
                currency="GBP",
                reason="Refund confirmed",
                issued_at=when,
            )
        path = "/api/v1/admin/credit-notes"

        def invoices(**params):
            rows = staff.get(path, {"limit": 100, **params}).json()["results"]
            return {row["invoice_number"] for row in rows}

        assert invoices(**WINDOW) == {"INV-FIRST", "INV-LAST"}
        assert invoices(**{"from": "2026-03-01"}) == {
            "INV-FIRST",
            "INV-LAST",
            "INV-AFTER",
        }
        assert invoices(to="2026-03-10") == {"INV-BEFORE", "INV-FIRST", "INV-LAST"}
        assert invoices(q="INV-F", **WINDOW) == {"INV-FIRST"}
        assert invoices(q="INV-A", **WINDOW) == set()
        assert _walk(staff, path, WINDOW, "invoice_number") == ["INV-LAST", "INV-FIRST"]


# --- support tickets (offset paged) -----------------------------------------------------------


class TestSupportTickets:
    PATH = "/api/v1/admin/tickets"

    def test_the_window_keeps_tickets_opened_in_it(
        self, staff_login, organization_factory, user_factory
    ):
        from workeasy360.commercial.partners.models import PartnerTicket

        staff, _ = staff_login("platform.staff_support.view")
        org, _, owner = organization_factory()
        for n, (name, when) in enumerate(MOMENTS.items(), start=1):
            PartnerTicket.objects.create(
                number=n,
                direction="direct",
                organization_id=org.id,
                subject=f"Ticket {name}",
                status="closed" if name == "first" else "open",
                opened_by=owner,
                opened_at=when,
                updated_at=when,
                first_response_due_at=when + timedelta(hours=4),
                resolve_due_at=when + timedelta(days=2),
            )

        def page(**params):
            response = staff.get(self.PATH, {"status": "all", **params})
            assert response.status_code == 200, response.content
            return response.json()

        def subjects(**params):
            return {item["subject"] for item in page(**params)["items"]}

        assert subjects(**WINDOW) == {"Ticket first", "Ticket last"}
        assert page(**WINDOW)["total"] == 2
        assert subjects(**{"from": "2026-03-01"}) == {
            "Ticket first",
            "Ticket last",
            "Ticket after",
        }
        assert subjects(to="2026-03-10") == {
            "Ticket before",
            "Ticket first",
            "Ticket last",
        }
        # It composes with the status (the default is the open ones) and the search,
        # and the status counts follow the window as they follow the search.
        assert subjects(status="open", **WINDOW) == {"Ticket last"}
        assert subjects(q="last", **WINDOW) == {"Ticket last"}
        assert page(**WINDOW)["counts"]["closed"] == 1
        assert page(**WINDOW)["counts"]["open"] == 1
        # Offset paging stays inside the window.
        first_page = page(limit=1, offset=0, **WINDOW)
        second_page = page(limit=1, offset=1, **WINDOW)
        assert first_page["total"] == second_page["total"] == 2
        assert [i["subject"] for i in first_page["items"] + second_page["items"]] == [
            "Ticket last",
            "Ticket first",
        ]


# --- the KYC queue (offset paged) -------------------------------------------------------------


class TestKycQueue:
    PATH = "/api/v1/admin/kyc/applications"

    def test_the_window_keeps_applications_submitted_in_it(
        self, staff_login, partner_factory
    ):
        from workeasy360.commercial.partners.models import PartnerKyc

        staff, _ = staff_login("platform.staff_kyc.view")
        code = {}
        for name, when in [*MOMENTS.items(), ("never", None)]:
            partner = partner_factory(code=f"dfk-{name}-{uuid.uuid4().hex[:6]}")[
                "partner"
            ]
            status = {"last": "under_review", "never": "draft"}.get(name, "submitted")
            PartnerKyc.objects.update_or_create(
                partner=partner, defaults={"status": status, "submitted_at": when}
            )
            code[name] = partner.code

        def page(**params):
            response = staff.get(self.PATH, {"q": "dfk-", **params})
            assert response.status_code == 200, response.content
            return response.json()

        def codes(**params):
            return {row["partner_code"] for row in page(**params)["results"]}

        # The queue's default is what waits for a reviewer.
        assert codes() == {code[n] for n in ("before", "first", "last", "after")}
        assert codes(**WINDOW) == {code["first"], code["last"]}
        assert page(**WINDOW)["total"] == 2
        assert codes(**{"from": "2026-03-01"}) == {
            code["first"],
            code["last"],
            code["after"],
        }
        assert codes(to="2026-03-10") == {code["before"], code["first"], code["last"]}
        # It composes with the status filter the page's chips use.
        assert codes(status="submitted", **WINDOW) == {code["first"]}
        assert codes(status="under_review", **WINDOW) == {code["last"]}
        # Never submitted: no date, so a date filter leaves it out.
        assert code["never"] in codes(status="all")
        assert codes(status="all", **WINDOW) == {code["first"], code["last"]}
        assert codes(status="draft", **WINDOW) == set()
        assert _refused(staff, self.PATH, {"status": "pending"}, "status")
        # Offset paging stays inside the window, oldest submission first.
        first_page = page(limit=1, offset=0, **WINDOW)
        second_page = page(limit=1, offset=1, **WINDOW)
        assert [
            r["partner_code"] for r in first_page["results"] + second_page["results"]
        ] == [code["first"], code["last"]]


# --- commission entries and payouts -----------------------------------------------------------


class TestCommissionLists:
    def test_entries_by_the_day_they_were_earned(
        self, staff_login, partner_factory, user_factory
    ):
        from workeasy360.commercial.commissions.models import CommissionAccrual

        staff, _ = staff_login("platform.staff_commission.view")
        ours = partner_factory()["partner"]
        theirs = partner_factory()["partner"]
        creator = user_factory(is_staff=True)
        ident = {}

        def entry(partner, name, when):
            row = CommissionAccrual.objects.create(
                partner=partner,
                kind="adjustment",
                amount_minor=1000,
                currency="GBP",
                source_ref="manual",
                event_at=when,
                clears_at=when + timedelta(days=30),
                reason="Date filter test",
                created_by=creator,
                idempotency_key=f"df-{uuid.uuid4().hex}",
            )
            ident[str(row.id)] = name

        for name, when in MOMENTS.items():
            entry(ours, name, when)
        entry(theirs, "theirs", MOMENTS["first"])
        path = "/api/v1/admin/commission/accruals"

        def names(**params):
            rows = staff.get(path, {"limit": 100, **params}).json()["results"]
            return {ident[row["id"]] for row in rows}

        assert names(**WINDOW) == {"first", "last", "theirs"}
        assert names(**{"from": "2026-03-01"}) == {"first", "last", "after", "theirs"}
        assert names(to="2026-03-10") == {"before", "first", "last", "theirs"}
        # It composes with the partner filter.
        assert names(partner_id=str(ours.id), **WINDOW) == {"first", "last"}
        assert names(partner_id=str(theirs.id), to="2026-02-28") == set()
        walked = _walk(staff, path, {"partner_id": str(ours.id), **WINDOW}, "id")
        assert [ident[i] for i in walked] == ["last", "first"]

    def test_payouts_by_the_day_they_were_prepared(
        self, staff_login, partner_factory, user_factory
    ):
        from workeasy360.commercial.commissions.models import Payout

        staff, _ = staff_login("platform.staff_commission.view")
        partner = partner_factory()["partner"]
        creator = user_factory(is_staff=True)
        ident = {}
        for name, when in MOMENTS.items():
            # ``created_at`` is set on insert and frozen afterwards, so the clock moves.
            with mock.patch.object(timezone, "now", return_value=when):
                row = Payout.objects.create(
                    partner=partner,
                    currency="GBP",
                    period_end=when.date(),
                    status="ready" if name == "first" else "draft",
                    created_by=creator,
                    idempotency_key=f"df-{uuid.uuid4().hex}",
                )
            assert row.created_at == when
            ident[str(row.id)] = name
        path = "/api/v1/admin/payouts"

        def names(**params):
            rows = staff.get(path, {"limit": 100, **params}).json()["results"]
            return {ident[row["id"]] for row in rows}

        assert names(**WINDOW) == {"first", "last"}
        assert names(**{"from": "2026-03-01"}) == {"first", "last", "after"}
        assert names(to="2026-03-10") == {"before", "first", "last"}
        # It composes with the status filter.
        assert names(status="draft", **WINDOW) == {"last"}
        assert names(status="open", **WINDOW) == {"first", "last"}
        walked = _walk(staff, path, WINDOW, "id")
        assert [ident[i] for i in walked] == ["last", "first"]

"""The Super Admin dashboard's month overview and the console's notification
bell (admin console redesign, 2026-10-26): counts per month, a value per day
for the charts, and nothing beyond the caller's grants."""

from __future__ import annotations

import calendar
from datetime import date, datetime
from datetime import timezone as dt_timezone

import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db

WIDE = (
    "platform.staff_customer.view",
    "platform.staff_user.view",
    "platform.staff_partner.view",
    "platform.staff_catalog.view",
    "platform.staff_payment.view",
)


def test_the_overview_covers_the_month_and_only_what_the_caller_may_see(staff_client):
    narrow, _u = staff_client("platform.staff_customer.view")
    overview = narrow.get("/api/v1/admin/dashboard").json()["overview"]
    today = timezone.now().date()
    assert overview["month"] == f"{today.year:04d}-{today.month:02d}"
    assert overview["first_day"] == date(today.year, today.month, 1).isoformat()
    for hidden in ("users", "partners", "products", "orders", "revenue"):
        assert hidden not in overview

    wide, _u = staff_client(*WIDE)
    overview = wide.get("/api/v1/admin/dashboard").json()["overview"]
    users = overview["users"]
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    assert len(users["daily"]) == days_in_month
    # The days still to come carry no value, so a chart draws no false zero.
    assert all(v is None for v in users["daily"][today.day :])
    assert users["new"] == sum(v for v in users["daily"] if v is not None)
    # Both staff members were created today.
    assert users["daily"][today.day - 1] >= 2 and users["total"] >= users["new"]
    assert {"total", "new"} <= set(overview["partners"])
    assert {"total", "available", "new"} <= set(overview["products"])
    assert overview["orders"] == {"paid": 0, "previous": 0, "by_product": []}
    assert overview["revenue"] == []


def test_a_past_month_is_compared_with_the_whole_month_before(staff_client):
    wide, _u = staff_client(*WIDE)
    today = timezone.now().date()
    index = today.year * 12 + today.month - 2
    month = f"{index // 12:04d}-{index % 12 + 1:02d}"
    overview = wide.get("/api/v1/admin/dashboard", {"month": month}).json()["overview"]
    assert overview["month"] == month
    assert None not in overview["users"]["daily"]
    first = date.fromisoformat(overview["compared_with"]["first_day"])
    last = date.fromisoformat(overview["compared_with"]["last_day"])
    assert first.day == 1 and last.day == calendar.monthrange(first.year, first.month)[1]


@pytest.mark.parametrize("month", ["2999-01", "1999-01", "2026-13", "October", "2026"])
def test_a_month_outside_the_picker_is_refused(staff_client, month):
    staff, _u = staff_client("platform.staff_customer.view")
    response = staff.get("/api/v1/admin/dashboard", {"month": month})
    assert response.status_code == 400
    assert response.json()["field"] == "month"


def test_a_running_month_is_compared_with_the_same_days_of_the_month_before():
    from workeasy360.commercial.controlplane.dashboard import month_window

    window = month_window(None, now=datetime(2026, 10, 26, 9, tzinfo=dt_timezone.utc))
    assert (window["start"], window["end"]) == (date(2026, 10, 1), date(2026, 11, 1))
    assert window["last_shown"] == date(2026, 10, 26)
    assert (window["compared_start"], window["compared_end"]) == (
        date(2026, 9, 1),
        date(2026, 9, 27),
    )
    # 31 March compared with a February that has no 31st: the whole of February.
    window = month_window(None, now=datetime(2026, 3, 31, tzinfo=dt_timezone.utc))
    assert window["compared_end"] == date(2026, 3, 1)
    window = month_window("2026-01", now=datetime(2026, 10, 26, tzinfo=dt_timezone.utc))
    assert (window["compared_start"], window["compared_end"]) == (
        date(2025, 12, 1),
        date(2026, 1, 1),
    )


def test_the_bell_lists_only_what_waits_and_only_within_the_grants(
    staff_client, partner_factory
):
    from workeasy360.commercial.partners.models import PartnerAccount

    fx = partner_factory()
    PartnerAccount.objects.filter(id=fx["partner"].id).update(status="draft")

    unrelated, _u = staff_client("platform.staff_customer.view")
    assert unrelated.get("/api/v1/admin/attention").json() == {"items": []}

    partners, _u = staff_client("platform.staff_partner.view")
    items = partners.get("/api/v1/admin/attention").json()["items"]
    assert {"key": "draft_partners", "count": 1} in items
    assert all(item["count"] > 0 for item in items)


def test_the_bell_is_staff_only(client, user_factory, http_login):
    assert client.get("/api/v1/admin/attention").status_code in (401, 403)
    from tests.phase7.conftest import PASSWORD

    signed_in = http_login(user_factory(password=PASSWORD))
    assert signed_in.get("/api/v1/admin/attention").status_code == 403

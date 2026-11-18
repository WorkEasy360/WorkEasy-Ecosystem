"""ADR-063 (the specification's 1.3, item 8): a guest view's actions are in the
audit log - every API request it makes, a refused write included - beside its
start and end, with the reason the Admin gave."""

from __future__ import annotations

import pytest

from tests.guest_login import test_guest_entry as _entry
from tests.guest_login.conftest import REASON

entered = _entry.entered
on_admin = _entry.on_admin  # the fixture

pytestmark = pytest.mark.django_db


def _events(partner_id):
    from workeasy360.contracts.values.control_plane import PLATFORM_ORGANIZATION_ID
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with (
        unscoped_access(reason="Test reads the guest view's audit trail", module="tests"),
        organization_db_scope(PLATFORM_ORGANIZATION_ID),
    ):
        return list(
            AuditEvent.unscoped.filter(
                action="partner.guest_view_request", resource_id=str(partner_id)
            )
            .order_by("id")
            .values("changes", "reason", "actor_kind")
        )


def test_each_request_of_a_guest_view_is_audited_with_the_reason(
    catalog, reseller, on_admin
):
    _owner, partner_id = reseller
    started, partner, _response = entered(on_admin, partner_id)
    view_id = started.json()["id"]
    base = f"/api/v1/partner/{partner_id}"
    assert partner.get(f"{base}/dashboard").status_code == 200
    refused = partner.post(f"{base}/platform-wallet/top-up", {"amount_minor": 10_000})
    assert refused.status_code == 403
    assert refused.json()["reason"] == "guest_read_only"

    events = _events(partner_id)
    paths = {(e["changes"]["method"], e["changes"]["path"]) for e in events}
    assert ("GET", "/api/v1/partner/{id}/dashboard") in paths
    assert ("POST", "/api/v1/partner/{id}/platform-wallet/top-up") in paths
    staff = {e["changes"]["staff_user_id"] for e in events}
    assert len(staff) == 1
    for event in events:
        assert event["reason"] == REASON
        assert event["changes"]["guest_view_id"] == view_id
    write = next(e for e in events if e["changes"]["method"] == "POST")
    assert write["changes"]["refused"] is True
    assert write["changes"]["status"] == 403

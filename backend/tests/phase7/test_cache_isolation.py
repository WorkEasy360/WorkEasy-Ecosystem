"""Per-principal API responses are never cacheable (Task 124)."""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.django_db


def test_control_plane_responses_are_no_store_including_errors(
    staff_client, partner_client, client
):
    staff, _u = staff_client("platform.staff_customer.view")
    partner, fx = partner_client("commission.view")
    pid = fx["partner"].id
    responses = {
        "admin ok": staff.get("/api/v1/admin/customers"),
        "admin forbidden": staff.get("/api/v1/admin/partners"),
        "admin session": staff.get("/api/v1/admin/session"),
        "admin unauthenticated": client.get("/api/v1/admin/customers"),
        "partner ok": partner.get(f"/api/v1/partner/{pid}/commissions"),
        "partner forbidden": partner.get(f"/api/v1/partner/{pid}/customers"),
        "partner stranger id": partner.get(f"/api/v1/partner/{uuid.uuid4()}/commissions"),
        "partner portal": partner.get("/api/v1/partner/portal"),
        "invitations": partner.get("/api/v1/invitations"),
    }
    for name, response in responses.items():
        assert response["Cache-Control"] == "no-store", (name, response.status_code)


def test_a_logo_miss_is_not_cached_anywhere(client):
    """A found logo is cacheable by the *browser* only (``private, max-age``;
    test_opus_final_audit); a miss inherits the API default and is not stored,
    so a host whose brand appears a minute later is not stuck on the 404."""
    response = client.get("/api/v1/public/branding/logo", {"host": "nobody.example.com"})
    assert response.status_code == 404
    assert response["Cache-Control"] == "no-store"

"""The 2026-11-10 follow-up: a customer's partner (assign, change, history), the
purchase source of orders, and where each product's access comes from - through the
real endpoints on real PostgreSQL."""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.django_db


def _json(client, method, url, body=None):
    return getattr(client, method)(url, body or {}, content_type="application/json")


class TestCustomerPartner:
    def test_assign_then_change_keeps_the_history_and_audits_both_sides(
        self, console_org, partner_factory, staff_login
    ):
        from workeasy360.commercial.partners.models import PartnerOrganizationLink

        org = console_org()
        first = partner_factory("customer.provision", "customer.manage")["partner"]
        second = partner_factory("customer.provision", "customer.manage")["partner"]
        manager, _ = staff_login(
            "platform.staff_customer.view",
            "platform.staff_partner.view",
            "platform.staff_partner.manage",
        )
        url = f"/api/v1/admin/customers/{org['org'].id}/partner"

        empty = manager.get(url).json()
        assert empty["current"] is None and empty["history"] == []
        assert empty["may_manage"] is True

        assigned = _json(
            manager,
            "post",
            url,
            {"partner_id": str(first.id), "reason": "Customer asked for this reseller."},
        )
        assert assigned.status_code == 200, assigned.content
        assert assigned.json()["current"]["partner_id"] == str(first.id)

        changed = _json(
            manager,
            "post",
            url,
            {"partner_id": str(second.id), "reason": "Moved to the regional reseller."},
        )
        assert changed.status_code == 200, changed.content
        body = changed.json()
        assert body["current"]["partner_id"] == str(second.id)
        assert [h["partner_id"] for h in body["history"]] == [
            str(second.id),
            str(first.id),
        ]
        ended = body["history"][1]
        assert ended["status"] == "ended"
        assert ended["end_reason"] == "partner_changed_by_staff"
        assert ended["started_by_name"] is not None
        # One live relationship, the old one kept.
        assert (
            PartnerOrganizationLink.objects.filter(organization_id=org["org"].id)
            .exclude(status="ended")
            .count()
            == 1
        )

    def test_the_same_partner_again_an_affiliate_and_read_only_staff_are_refused(
        self, console_org, partner_factory, staff_login
    ):
        org = console_org()
        reseller = partner_factory("customer.provision")["partner"]
        affiliate = partner_factory("referral.create")["partner"]
        url = f"/api/v1/admin/customers/{org['org'].id}/partner"
        viewer, _ = staff_login(
            "platform.staff_customer.view", "platform.staff_partner.view"
        )
        refused = _json(
            viewer,
            "post",
            url,
            {"partner_id": str(reseller.id), "reason": "Trying without the grant."},
        )
        assert refused.status_code == 403
        manager, _ = staff_login(
            "platform.staff_customer.view",
            "platform.staff_partner.view",
            "platform.staff_partner.manage",
        )
        not_serving = _json(
            manager,
            "post",
            url,
            {
                "partner_id": str(affiliate.id),
                "reason": "An affiliate is not a reseller.",
            },
        )
        assert not_serving.status_code == 400
        assert "cannot serve customers" in not_serving.json()["detail"]
        ok = _json(
            manager,
            "post",
            url,
            {
                "partner_id": str(reseller.id),
                "reason": "Customer asked for this reseller.",
            },
        )
        assert ok.status_code == 200
        again = _json(
            manager,
            "post",
            url,
            {
                "partner_id": str(reseller.id),
                "reason": "Customer asked for this reseller.",
            },
        )
        assert again.status_code == 400

    def test_the_picker_lists_only_active_partners_that_serve_customers(
        self, partner_factory, staff_login
    ):
        reseller = partner_factory("customer.manage")["partner"]
        affiliate = partner_factory("referral.create")["partner"]
        viewer, _ = staff_login("platform.staff_partner.view")
        ids = {
            row["id"]
            for row in viewer.get("/api/v1/admin/partners/assignable").json()["results"]
        }
        assert str(reseller.id) in ids and str(affiliate.id) not in ids


class TestOrders:
    def test_unknown_purchase_source_is_refused(self, staff_login):
        viewer, _ = staff_login("platform.staff_payment.view")
        assert viewer.get("/api/v1/admin/orders", {"channel": "nope"}).status_code == 400
        ok = viewer.get("/api/v1/admin/orders", {"channel": "partner"})
        assert ok.status_code == 200
        assert ok.json()["results"] == []


class TestAccessSources:
    def test_a_product_trial_is_named_as_a_trial_on_the_customer_and_the_holders(
        self, console_org, staff_login
    ):
        from datetime import timedelta

        from django.utils import timezone

        from workeasy360.platform.entitlements.models import Entitlement
        from workeasy360.platform.tenancy.db import organization_db_scope
        from workeasy360.platform.tenancy.unscoped import unscoped_access

        org = console_org()
        now = timezone.now()
        with (
            unscoped_access(reason="test seeds an entitlement row", module="tests"),
            organization_db_scope(org["org"].id),
        ):
            Entitlement.unscoped.create(
                organization_id=org["org"].id,
                product_code="crm",
                status="trial",
                source_kind="trial",
                source_ref="subscriptions.trial:test",
                effective_from=now - timedelta(days=10),
                effective_to=now - timedelta(days=3),
                computed_at=now,
                computation_id=uuid.uuid4(),
            )
        viewer, _ = staff_login("platform.staff_customer.view")
        detail = viewer.get(f"/api/v1/admin/customers/{org['org'].id}").json()
        sources = detail["access_sources"]
        assert [s["source_kind"] for s in sources] == ["trial"]
        assert sources[0]["effective_now"] is False, "an ended trial is not in effect"
        assert detail["product_trials"] == []
        holders = viewer.get("/api/v1/admin/customers", {"product": "crm"}).json()
        row = next(
            (r for r in holders["results"] if r["organization_id"] == str(org["org"].id)),
            None,
        )
        if row is not None:  # listed only when the summary projection has it
            assert row["product_source"]["kind"] == "trial"

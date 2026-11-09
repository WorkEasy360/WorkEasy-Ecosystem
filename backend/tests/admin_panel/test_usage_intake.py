"""A product reporting usage, and AI usage reaching AI Credits."""

from __future__ import annotations

import base64
import json
import uuid
from unittest import mock

import pytest
from django.test import Client

pytestmark = pytest.mark.django_db

URL = "/api/v1/integrations/usage/events"


def _rate(meter, currency="INR", unit=10, per=1):
    from workeasy360.commercial.wallets.models import UsageRate

    return UsageRate.objects.create(
        meter=meter, currency=currency, unit_amount=unit, per_units=per, created_by_user_id=uuid.uuid4()
    )


def _fund(org_id, kind="consumption", currency="INR", amount=1000):
    from workeasy360.commercial.wallets import ledger

    wallet = ledger.ensure(owner_type="organization", owner_id=org_id, kind=kind, currency=currency)
    ledger.post(wallet.id, amount=amount, entry_type="top_up", reason="t", idempotency_key=f"f:{org_id}:{kind}")
    return wallet


class TestService:
    def test_a_report_is_charged_once_and_says_what_happened(self):
        from workeasy360.commercial.controlplane.usage_intake import report

        org = uuid.uuid4()
        wallet = _fund(org)
        _rate("whatsapp")
        first = report(product_code="p", organization_id=org, meter="whatsapp", quantity=3, idempotency_key="k1")
        again = report(product_code="p", organization_id=org, meter="whatsapp", quantity=3, idempotency_key="k1")
        assert first["outcome"] == "charged" and first["amount"] == 30 and first["duplicate"] is False
        assert again["duplicate"] is True and again["amount"] == 30
        wallet.refresh_from_db()
        assert wallet.balance == 970

    def test_a_product_cannot_claim_ai_tokens(self):
        from workeasy360.commercial.controlplane.usage_intake import report
        from workeasy360.contracts.errors import CatalogValidationError

        with pytest.raises(CatalogValidationError):
            report(product_code="p", organization_id=uuid.uuid4(), meter="ai_tokens", quantity=1, idempotency_key="k2")

    def test_ai_tokens_come_off_credits_once_per_request(self):
        from workeasy360.commercial.controlplane.usage_intake import charge_ai_tokens

        org = uuid.uuid4()
        wallet = _fund(org, kind="ai_credits", currency="", amount=100)
        _rate("ai_tokens", currency="", unit=1, per=1000)
        charge_ai_tokens(product_code="p", organization_id=org, request_id="r1", tokens=2100)
        charge_ai_tokens(product_code="p", organization_id=org, request_id="r1", tokens=2100)
        wallet.refresh_from_db()
        assert wallet.balance == 97

    def test_zero_tokens_charge_nothing(self):
        from workeasy360.commercial.controlplane.usage_intake import charge_ai_tokens
        from workeasy360.commercial.wallets.models import UsageEvent

        charge_ai_tokens(product_code="p", organization_id=uuid.uuid4(), request_id="r2", tokens=0)
        assert not UsageEvent.objects.filter(idempotency_key="ai:r2").exists()


class TestEndpoint:
    def _client(self, integration_client):
        return integration_client

    def test_it_needs_a_product_credential_not_a_session(self):
        response = Client().post(URL, {"organization_id": str(uuid.uuid4()), "event": "{}"})
        assert response.status_code in (400, 401)

    def test_a_product_may_charge_only_an_organization_it_serves(self):
        from workeasy360.interfaces.api.v1 import usage_product_views

        fake = mock.Mock(product_code="crm", client_id="c")
        org = uuid.uuid4()
        with (
            mock.patch.object(usage_product_views.clientauth, "authenticate", return_value=fake),
            mock.patch.object(
                usage_product_views.verification,
                "verify",
                return_value=mock.Mock(allowed=False),
            ),
        ):
            response = Client().post(
                URL,
                {
                    "organization_id": str(org),
                    "event": json.dumps({"meter": "sms", "quantity": 1, "idempotency_key": "x"}),
                },
            )
        assert response.status_code == 403
        from workeasy360.commercial.wallets.models import UsageEvent

        assert not UsageEvent.objects.filter(organization_id=org).exists()

    def test_an_entitled_product_is_charged_and_told(self):
        from workeasy360.interfaces.api.v1 import usage_product_views

        fake = mock.Mock(product_code="crm", client_id="c")
        org = uuid.uuid4()
        wallet = _fund(org)
        _rate("sms", unit=5)
        with (
            mock.patch.object(usage_product_views.clientauth, "authenticate", return_value=fake),
            mock.patch.object(
                usage_product_views.verification, "verify", return_value=mock.Mock(allowed=True)
            ),
        ):
            response = Client().post(
                URL,
                {
                    "organization_id": str(org),
                    "event": json.dumps({"meter": "sms", "quantity": 2, "idempotency_key": "y"}),
                },
            )
        assert response.status_code == 200, response.content
        assert response.json()["outcome"] == "charged" and response.json()["amount"] == 10
        wallet.refresh_from_db()
        assert wallet.balance == 990

    def test_ai_invoke_is_refused_when_credits_have_run_out_and_charged_when_it_ran(self):
        from workeasy360.interfaces.api.v1 import intelligence_product_views as views
        from workeasy360.intelligence.gateway import service

        fake = mock.Mock(product_code="crm", client_id="c")
        org = uuid.uuid4()
        _rate("ai_tokens", currency="", unit=1, per=1000)
        answer = mock.Mock(
            status=200,
            body={"request_id": "req-9", "usage": {"input_tokens": 700, "output_tokens": 800}},
        )
        with (
            mock.patch.object(views.clientauth, "authenticate", return_value=fake),
            mock.patch.object(service, "invoke", return_value=answer) as invoked,
        ):
            refused = Client().post("/api/v1/integrations/ai/invoke", {"organization_id": str(org), "request": "{}"})
            assert refused.status_code == 402
            assert refused.json()["error"] == "ai_credits_exhausted"
            assert invoked.call_count == 0

            wallet = _fund(org, kind="ai_credits", currency="", amount=10)
            done = Client().post("/api/v1/integrations/ai/invoke", {"organization_id": str(org), "request": "{}"})
            assert done.status_code == 200 and invoked.call_count == 1
        wallet.refresh_from_db()
        assert wallet.balance == 8

    def test_no_rate_means_ai_is_neither_refused_nor_charged(self):
        from workeasy360.interfaces.api.v1 import intelligence_product_views as views
        from workeasy360.commercial.wallets.models import UsageEvent
        from workeasy360.intelligence.gateway import service

        fake = mock.Mock(product_code="crm", client_id="c")
        org = uuid.uuid4()
        answer = mock.Mock(status=200, body={"request_id": "req-1", "usage": {"input_tokens": 5, "output_tokens": 5}})
        with (
            mock.patch.object(views.clientauth, "authenticate", return_value=fake),
            mock.patch.object(service, "invoke", return_value=answer),
        ):
            done = Client().post("/api/v1/integrations/ai/invoke", {"organization_id": str(org), "request": "{}"})
        assert done.status_code == 200
        row = UsageEvent.objects.get(organization_id=org)
        assert row.outcome == "not_rated" and row.amount == 0


_ = base64

"""Automatic product onboarding (2026-11-18): the integration kit, the evidence
that a product's sign-in and logout work, and the publish gate on it.

- The kit turns a registration into a framework's configuration and holds no
  secret; every generation is audited; only integration viewers get one.
- Every redemption records the issuer and, to the hour, when - for every
  product, back-channel logout or not - without a write per sign-in.
- Readiness warns until a sign-in has completed and a logout been acknowledged;
  a product that signs people in with WorkEasy360 cannot be published until a
  sign-in has completed.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from tests.phase11.conftest import CATALOG_STAFF, audit_of
from tests.phase11.test_product_domains import (  # noqa: F401 - fixtures
    ORIGIN,
    READINESS,
    _edit,
    dns,
    draft,
    live,
    staff,
)

pytestmark = pytest.mark.django_db

KIT = "/api/v1/admin/products/crm/integration/kit"
FRAMEWORKS = ("django", "spring", "node", "other")


def _kit(client, framework: str):
    return client.get(KIT, {"framework": framework})


@pytest.mark.parametrize("framework", FRAMEWORKS)
def test_a_kit_carries_the_registration_and_no_secret(
    staff_login,
    draft,  # noqa: F811
    framework,
):
    client, _ = staff_login(*CATALOG_STAFF, "platform.staff_integration.view")
    response = _kit(client, framework)
    assert response.status_code == 200, response.content
    assert response["Cache-Control"] == "no-store"
    kit = response.json()
    env = next(f["content"] for f in kit["files"] if f["path"] == ".env.example")
    assert f"WORKEASY360_CLIENT_ID={draft['client_id']}" in env
    assert "WORKEASY360_PRODUCT_CODE=crm" in env
    assert f"WORKEASY360_ISSUER={kit['facts']['issuer']}" in env
    key_line = "WORKEASY360_PRIVATE_KEY_FILE=/run/secrets/"  # pragma: allowlist secret
    assert key_line in env  # a path to the secret, never the secret
    everything = "\n".join(f["content"] for f in kit["files"])
    # The product's private key exists in this test (the fixture made it); it is
    # never in a kit, and nothing resembling key material is.
    assert draft["private_key_pem"] and draft["private_key_pem"] not in everything
    assert (
        "PRIVATE KEY-----" not in everything
        and "client_secret=" not in everything.lower()
    )


def test_the_django_kit_pins_the_private_connector_by_commit(
    staff_login,
    draft,  # noqa: F811
):
    from workeasy360.commercial.controlplane import integration_kit

    client, _ = staff_login(*CATALOG_STAFF, "platform.staff_integration.view")
    kit = _kit(client, "django").json()
    requirements = next(
        f["content"] for f in kit["files"] if f["path"] == "requirements.txt"
    )
    assert integration_kit.CONNECTOR_REQUIREMENT in requirements
    assert requirements.count("@" + integration_kit.CONNECTOR_COMMIT) == 1
    assert ".whl" not in requirements  # never a vendored wheel


def test_the_node_kit_names_the_token_endpoint_as_the_assertion_audience(
    staff_login,
    draft,  # noqa: F811
):
    """Found by the first products built from it (2026-11-18): openid-client's
    PrivateKeyJwt names the issuer as aud, which the platform's client
    authentication refuses (token endpoint only). The starter sets it, and asks
    for the ID token's signature to be verified."""
    client, _ = staff_login(*CATALOG_STAFF, "platform.staff_integration.view")
    kit = _kit(client, "node").json()
    starter = next(
        f["content"]
        for f in kit["files"]
        if f["path"].startswith("src/workeasy360/client.ts")
    )
    assert (
        "[oidc.modifyAssertion]" in starter and "payload.aud = tokenEndpoint" in starter
    )
    assert "token_endpoint" in starter and "enableNonRepudiationChecks" in starter


def test_a_kit_names_what_the_registration_still_lacks(
    staff_login,
    draft,  # noqa: F811
):
    client, _ = staff_login(*CATALOG_STAFF, "platform.staff_integration.view")
    kit = _kit(client, "node").json()
    assert any("back-channel" in item for item in kit["missing"])


def test_each_generation_is_audited_without_the_kit(
    staff_login,
    draft,  # noqa: F811
):
    client, _ = staff_login(*CATALOG_STAFF, "platform.staff_integration.view")
    _kit(client, "spring")
    rows = [
        r
        for r in audit_of(draft["client_id"], "action", "changes")
        if r["action"] == "ecosystem.integration.kit_generated"
    ]
    assert rows == [
        {
            "action": "ecosystem.integration.kit_generated",
            "changes": {"product_code": "crm", "framework": "spring"},
        }
    ]


def test_an_unknown_framework_and_an_unregistered_product_are_refused(
    staff_login,
    draft,  # noqa: F811
):
    client, _ = staff_login(*CATALOG_STAFF, "platform.staff_integration.view")
    assert _kit(client, "cobol").status_code in (400, 422)
    missing = client.get(
        "/api/v1/admin/products/books/integration/kit", {"framework": "django"}
    )
    assert missing.status_code in (400, 404, 422)


def test_only_integration_viewers_get_a_kit(
    staff_login,
    draft,  # noqa: F811
):
    client, _ = staff_login(*CATALOG_STAFF)
    assert _kit(client, "django").status_code == 403


def test_a_new_product_is_told_to_trust_accounts_once_it_is_an_issuer(settings):
    from workeasy360.commercial.controlplane.integration_kit import recommended_issuer

    accounts, auth = "https://accounts.workeasy360.com", "https://auth.workeasy360.com"
    settings.PORTALS_ENABLED = True
    settings.W360_PORTAL_ORIGINS = {**settings.W360_PORTAL_ORIGINS, "auth": accounts}
    settings.OIDC_ISSUER = auth
    settings.OIDC_TRANSITION_ISSUERS = []
    assert recommended_issuer() == auth  # stage a: accounts. serves but is no issuer yet
    settings.OIDC_TRANSITION_ISSUERS = [accounts]
    assert recommended_issuer() == accounts  # stage b
    settings.OIDC_ISSUER, settings.OIDC_TRANSITION_ISSUERS = accounts, [auth]
    assert recommended_issuer() == accounts  # stage c


# --- the evidence ---------------------------------------------------------------------------


def _row(registration):
    from workeasy360.platform.ecosystem.models import ProductIntegration

    return ProductIntegration.objects.get(pk=registration["integration"].pk)


def test_every_redemption_records_issuer_and_hour_even_without_backchannel(
    live,  # noqa: F811
    django_assert_num_queries,
):
    import uuid

    from workeasy360.platform.ecosystem import backchannel, tokens

    integration = _row(live)
    assert not backchannel.takes_backchannel_logout(integration)
    backchannel.record_product_sign_in(
        integration=integration, user_id=uuid.uuid4(), sign_in_id=None
    )
    first = _row(live)
    assert first.last_token_issuer == tokens.issuer() and first.last_token_at is not None

    # Within the hour: two conditional updates that match nothing - no write.
    with django_assert_num_queries(2):
        backchannel.record_product_sign_in(
            integration=first, user_id=uuid.uuid4(), sign_in_id=None
        )
    assert _row(live).last_token_at == first.last_token_at

    # Older than an hour: refreshed.
    from workeasy360.platform.ecosystem.models import ProductIntegration

    ProductIntegration.objects.filter(pk=first.pk).update(
        last_token_at=timezone.now() - timedelta(hours=2)
    )
    backchannel.record_product_sign_in(
        integration=first, user_id=uuid.uuid4(), sign_in_id=None
    )
    assert _row(live).last_token_at > timezone.now() - timedelta(minutes=1)


def _states(body):
    return {c["key"]: c["state"] for c in body["checks"]}


def test_readiness_warns_until_a_sign_in_and_a_logout_are_proven(
    staff_login,
    staff,  # noqa: F811
    live,  # noqa: F811
):
    from workeasy360.platform.ecosystem.models import (
        BackchannelLogout,
        ProductIntegration,
    )

    _edit(
        staff,
        live,
        manifest_version="2026-11-15",
        backchannel_logout_uri=f"{ORIGIN}/auth/backchannel-logout",
    )
    ProductIntegration.objects.filter(pk=live["integration"].pk).update(
        health_status="healthy"
    )
    client, _ = staff_login(*CATALOG_STAFF, "platform.staff_integration.view")

    body = client.get(READINESS).json()
    assert _states(body)["sign_in_verified"] == "warning"
    assert _states(body)["logout_verified"] == "warning"
    assert body["verdict"] == "attention"

    ProductIntegration.objects.filter(pk=live["integration"].pk).update(
        last_token_at=timezone.now(), last_token_issuer="https://auth.workeasy360.com"
    )
    BackchannelLogout.objects.create(
        integration_id=live["integration"].pk,
        user_id=live["integration"].pk,
        reason="signed_out",
        status="delivered",
        finished_at=timezone.now(),
    )
    body = client.get(READINESS).json()
    assert (
        _states(body)["sign_in_verified"] == "ok"
        and _states(body)["logout_verified"] == "ok"
    )
    assert body["verdict"] == "ready", body


# --- the publish gate -----------------------------------------------------------------------


def _checklist(**overrides):
    from workeasy360.commercial.catalog.domain import ProductStatus, publish_checklist

    kwargs = {
        "name": "CRM",
        "slug": "crm",
        "category": "sales",
        "tagline": "t",
        "has_logo": True,
        "application_url": "https://crm.example.com",
        "launch_url": "",
        "status": ProductStatus.DRAFT,
        "return_destinations": True,
        "has_active_plan": True,
        "channel_open": True,
    }
    kwargs.update(overrides)
    return {item["key"]: item["ok"] for item in publish_checklist(**kwargs)}


def test_a_workeasy360_sign_in_product_publishes_only_after_a_completed_sign_in():
    assert _checklist(sign_in_verified=False)["sign_in_verified"] is False
    assert _checklist(sign_in_verified=True)["sign_in_verified"] is True
    # No connected sign-in (no integration, or local_only): nothing to prove.
    assert _checklist(sign_in_verified=None)["sign_in_verified"] is True


def test_the_publish_checklist_reads_the_recorded_sign_in(
    live,  # noqa: F811
):
    from workeasy360.platform.ecosystem import selectors
    from workeasy360.platform.ecosystem.models import ProductIntegration

    assert selectors.sign_in_proven("crm") is False
    ProductIntegration.objects.filter(pk=live["integration"].pk).update(
        last_token_at=timezone.now()
    )
    assert selectors.sign_in_proven("crm") is True
    assert selectors.sign_in_proven("no_such_product") is None

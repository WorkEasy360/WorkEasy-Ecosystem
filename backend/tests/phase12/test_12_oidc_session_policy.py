"""Product sign-in under an organization's access policy (Phase 12 audit, P1).

Before Phase 12 the token endpoint re-resolved the subject with *no* session
facts, so the live access policy judged a least-trusted session: an
organization that restricted sign-in to an IP allow-list or limited session
age got ``invalid_grant`` on every product sign-in - after the
authorization endpoint had already allowed the same session. The code now
records what the authorizing session vouched for and the token endpoint
re-judges the *live* policy against it: a policy tightened inside the code's
lifetime still refuses.
"""

from __future__ import annotations

import time
from datetime import timedelta

import pytest
from django.utils import timezone

from tests.factories.reference_product_client import IntegrationError
from workeasy360.platform.ecosystem import protocol
from workeasy360.platform.ecosystem.models import AuthorizationCode
from workeasy360.platform.organizations.models import Organization
from workeasy360.platform.organizations.services import cut_off_member_sessions
from workeasy360.platform.tenancy.context import tenant_context
from workeasy360.platform.tenancy.db import tenant_db_scope
from workeasy360.platform.tenancy.resolution import SESSION_AUTHENTICATED_AT_KEY

pytestmark = pytest.mark.django_db


def _sign_in(client, user, *, authenticated_at: float | None = None):
    from workeasy360.platform.ecosystem.models import UserSignInPreference

    # The silent path, as the root ``signed_in`` fixture sets up (ADR-044): this suite is about the
    # access policy, not the person's sign-in preference, whose default would ask first.
    UserSignInPreference.objects.update_or_create(
        user=user, product_code="", defaults={"mode": "auto_sso"}
    )
    client.force_login(user)
    session = client.session
    session[SESSION_AUTHENTICATED_AT_KEY] = authenticated_at or time.time()
    session.save()


def _authorize(product, client, org):
    request = product.begin_authorization(organization_id=org["organization_id"])
    response = client.get(request["url"], request["query"])
    return request, response


@pytest.fixture
def policy_org(client, signing_key, register_product, entitled_org, product_client):
    """An organization that only admits sign-ins from the loopback network."""
    product = product_client(register_product("crm"))
    org = entitled_org("crm")
    Organization.objects.filter(id=org["organization_id"]).update(
        ip_allowlist_enabled=True, ip_allowlist=["127.0.0.0/8"]
    )
    return product, org


def test_a_member_inside_the_policy_completes_product_sign_in(client, policy_org):
    product, org = policy_org
    _sign_in(client, org["user"])
    request, response = _authorize(product, client, org)
    assert response.status_code == 302
    code = product.handle_callback(response["Location"], expected_state=request["state"])
    tokens = product.exchange_code(code=code, code_verifier=request["code_verifier"])
    claims = product.verify_id_token(tokens["id_token"], expected_nonce=request["nonce"])
    assert claims["organization_id"] == str(org["organization_id"])
    row = AuthorizationCode.objects.get()
    assert row.session_facts["client_ip"] and row.session_facts["authenticated_at"]
    assert "mfa_verified" not in row.session_facts


def test_a_member_outside_the_policy_is_refused_at_the_authorization_endpoint(
    client, policy_org
):
    product, org = policy_org
    Organization.objects.filter(id=org["organization_id"]).update(
        ip_allowlist=["10.0.0.0/8"]
    )
    _sign_in(client, org["user"])
    request, response = _authorize(product, client, org)
    assert response.status_code == 302
    assert f"error={protocol.ACCESS_POLICY_DENIED}" in response["Location"]
    assert not AuthorizationCode.objects.exists()


def test_a_policy_tightened_after_the_code_was_issued_still_refuses(client, policy_org):
    """The facts are recorded; the policy is re-read live at redemption."""
    product, org = policy_org
    _sign_in(client, org["user"])
    request, response = _authorize(product, client, org)
    code = product.handle_callback(response["Location"], expected_state=request["state"])
    Organization.objects.filter(id=org["organization_id"]).update(
        ip_allowlist=["10.0.0.0/8"]
    )
    with pytest.raises(IntegrationError) as refused:
        product.exchange_code(code=code, code_verifier=request["code_verifier"])
    assert refused.value.error == "invalid_grant"


def test_a_member_signed_out_of_the_organization_after_authorizing_is_refused(
    client, policy_org
):
    product, org = policy_org
    _sign_in(client, org["user"], authenticated_at=time.time() - 60)
    request, response = _authorize(product, client, org)
    code = product.handle_callback(response["Location"], expected_state=request["state"])
    # The console's own "sign this member out of the organization".
    ctx = org["ctx"]
    with tenant_context(ctx), tenant_db_scope(ctx):
        cut_off_member_sessions(
            org["org"].id,
            org["membership"].id,
            actor_membership_id=org["membership"].id,
            at=timezone.now(),
        )
    with pytest.raises(IntegrationError) as refused:
        product.exchange_code(code=code, code_verifier=request["code_verifier"])
    assert refused.value.error == "invalid_grant"


def test_a_code_without_recorded_facts_is_judged_least_trusted(client, policy_org):
    """A code minted by the previous release (no column) cannot pass a policy
    that needs any fact - fail closed, never open."""
    product, org = policy_org
    _sign_in(client, org["user"])
    request, response = _authorize(product, client, org)
    code = product.handle_callback(response["Location"], expected_state=request["state"])
    AuthorizationCode.objects.update(session_facts=None)
    with pytest.raises(IntegrationError) as refused:
        product.exchange_code(code=code, code_verifier=request["code_verifier"])
    assert refused.value.error == "invalid_grant"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "not a dict",
        {"client_ip": 7, "authenticated_at": 1.0},
        {"client_ip": "10.0.0.1", "authenticated_at": "yesterday"},
        {"client_ip": "10.0.0.1", "authenticated_at": True},
    ],
)
def test_malformed_recorded_facts_are_least_trusted(raw):
    from workeasy360.platform.ecosystem.authorization import facts_from_record

    assert facts_from_record(raw) is None


def test_recorded_facts_round_trip():
    from workeasy360.contracts.values.access_policy import SessionFacts
    from workeasy360.platform.ecosystem.authorization import (
        facts_from_record,
        facts_record,
    )

    facts = SessionFacts(
        client_ip="203.0.113.9",
        authenticated_at=time.time() - timedelta(minutes=3).total_seconds(),
        password_noncompliant=False,
    )
    assert facts_from_record(facts_record(facts)) == facts

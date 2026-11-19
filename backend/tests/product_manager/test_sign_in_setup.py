"""Automatic product sign-in setup (ADR-074).

Super Admin adds a product with an application URL; WorkEasy360 prepares its
one OIDC client at once, reads the product's compatibility document
(``/.well-known/workeasy360-sso``), proves its domain, registers its addresses
and public key, and activates its sign-in - only when every mandatory check
passes. These tests play the product with a real key pair and a real signed
proof; the document fetch and the DNS answer are the only stand-ins (the
network).
"""

from __future__ import annotations

import json
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.product_manager.conftest import API, SUPER, body, post
from workeasy360.commercial.controlplane import sso_setup

pytestmark = pytest.mark.django_db

APP = "https://orchard.workeasy360.com"
SETUP = "/api/v1/admin/products/{code}/integration/sign-in-setup"


def _issuer() -> str:
    from workeasy360.platform.ecosystem import tokens

    return tokens.canonical_issuer()


class FakeProduct:
    """A product that serves its compatibility document, signed with its own key."""

    def __init__(self, *, kid: str = "orchard-key-1") -> None:
        self.keys = {kid: rsa.generate_private_key(public_exponent=65537, key_size=2048)}
        self.signing_kid = kid
        self.client_id = ""
        self.product_code = "orchard_planner"
        self.outcome = "ok"
        self.overrides: dict = {}
        self.proof_overrides: dict = {}
        self.proof_header: dict = {}
        self.fetched: list[str] = []

    def add_key(self, kid: str) -> None:
        self.keys[kid] = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def jwk(self, kid: str) -> dict:
        public = json.loads(
            jwt.algorithms.RSAAlgorithm.to_jwk(self.keys[kid].public_key())
        )
        return {**public, "kid": kid, "use": "sig", "alg": "RS256"}

    def document(self, challenge: str) -> dict:
        now = int(time.time())
        claims = {
            "iss": self.client_id,
            "sub": self.client_id,
            "aud": f"{_issuer()}/sso-setup",
            "iat": now,
            "exp": now + 120,
            "jti": str(uuid.uuid4()),
            "nonce": challenge,
            "product_code": self.product_code,
            **self.proof_overrides,
        }
        proof = jwt.encode(
            claims,
            self.keys[self.signing_kid],
            algorithm="RS256",
            headers={
                "kid": self.signing_kid,
                "typ": "w360-sso-setup+jwt",
                **self.proof_header,
            },
        )
        doc = {
            "schema": "workeasy360-sso/1",
            "product_code": self.product_code,
            "client_id": self.client_id,
            "issuer": _issuer(),
            "connector": {
                "name": "workeasy360-sso",
                "version": "0.4.0",
                "framework": "django",
            },
            "token_endpoint_auth_method": "private_key_jwt",
            "launch_url": f"{APP}/auth/workeasy360/start/",
            "redirect_uris": [f"{APP}/auth/workeasy360/callback/"],
            "post_logout_redirect_uris": [f"{APP}/"],
            "backchannel_logout_uri": f"{APP}/auth/workeasy360/backchannel-logout/",
            "jwks": {"keys": [self.jwk(k) for k in self.keys]},
            "proof": proof,
        }
        doc.update(self.overrides)
        return doc

    def fetch(self, url: str):
        self.fetched.append(url)
        if self.outcome != "ok":
            return self.outcome, b"", "answered HTTP 404"
        assert urlsplit(url).path == "/.well-known/workeasy360-sso"
        challenge = parse_qs(urlsplit(url).query)["challenge"][0]
        return "ok", json.dumps(self.document(challenge)).encode(), ""


class Dns:
    """The DNS answer, through the same port partner domains use."""

    def __init__(self) -> None:
        self.records: dict[str, list[str]] = {}

    def txt_values(self, name: str) -> list[str]:
        return self.records.get(name, [])

    def publish_for(self, code: str) -> None:
        from workeasy360.platform.ecosystem import domains

        integration = domains.integration_by_code(code)
        for entry in domains.domains_json(integration)["domains"]:
            if entry.get("record_value"):
                self.records[entry["record_name"]] = [entry["record_value"]]


@pytest.fixture
def product(monkeypatch):
    fake = FakeProduct()
    monkeypatch.setattr(sso_setup, "fetch_document", fake.fetch)
    monkeypatch.setattr(sso_setup, "_enqueue", lambda code: None)
    return fake


@pytest.fixture
def dns():
    from workeasy360.commercial.partners import domains as partner_domains

    resolver = Dns()
    previous = partner_domains.set_txt_resolver(resolver)
    yield resolver
    partner_domains.set_txt_resolver(previous)


@pytest.fixture
def admin(staff_login):
    return staff_login(*SUPER)


def _add(client, **overrides):
    payload = body(**overrides)
    payload.pop("integration", None)  # the automatic path: an application URL only
    response = post(client, API, payload)
    assert response.status_code in (200, 201), response.content
    return response.json()["product"]


def _check(client, code="orchard_planner"):
    response = post(client, SETUP.format(code=code) + "/check")
    assert response.status_code == 200, response.content
    return response.json()


def _integration(code="orchard_planner"):
    from workeasy360.platform.ecosystem.models import ProductIntegration

    return ProductIntegration.objects.get(product_code=code)


def _keys(code="orchard_planner"):
    from workeasy360.platform.ecosystem.models import IntegrationCredential

    return {
        c.credential_id: c.status
        for c in IntegrationCredential.objects.filter(integration__product_code=code)
    }


def _by_key(setup: dict) -> dict:
    return {c["key"]: c for c in setup["checks"]}


# --- the whole path ---------------------------------------------------------------------


def test_add_product_prepares_one_draft_client_at_once(admin, product):
    client, _user = admin
    created = _add(client)
    assert created["code"] == "orchard_planner"
    integration = _integration()
    assert integration.status == "draft"
    assert integration.manifest["launch_url"] == APP
    shown = client.get(SETUP.format(code="orchard_planner")).json()
    assert shown["automatic"] is True
    assert shown["client_id"] == integration.client_id
    assert shown["integration_status"] == "draft"
    assert shown["document_url"] == f"{APP}/.well-known/workeasy360-sso"


def test_a_compatible_product_becomes_active_only_after_its_domain_is_proven(
    admin, product, dns
):
    client, _user = admin
    _add(client)
    product.client_id = _integration().client_id

    first = _check(client)
    assert first["state"] == "verification_required", json.dumps(
        first["checks"], indent=1
    )
    checks = _by_key(first)
    assert checks["identity"]["status"] == "passed"
    assert checks["proof"]["status"] == "passed"
    assert checks["domain"]["status"] == "pending"
    assert (
        "TXT _w360-product-verify.orchard.workeasy360.com" in checks["domain"]["detail"]
    )
    assert _integration().status == "draft"  # never active on an unproven domain
    # The registration already matches the document (a draft accepts it).
    manifest = _integration().manifest
    assert manifest["redirect_uris"] == [f"{APP}/auth/workeasy360/callback/"]
    assert manifest["backchannel_logout_uri"].endswith("/backchannel-logout/")
    assert _keys() == {"orchard-key-1": "active"}

    dns.publish_for("orchard_planner")
    second = _check(client)
    assert second["state"] == "active", second["checks"]
    assert second["activated_at"]
    assert _integration().status == "active"
    assert all(c["status"] == "passed" for c in second["checks"])
    assert second["connector"] == {"name": "workeasy360-sso", "version": "0.4.0"}


def test_retries_never_make_a_second_client_or_a_second_key(admin, product, dns):
    from workeasy360.platform.ecosystem.models import (
        ProductIntegration,
        ProductSignInSetup,
    )

    client, user = admin
    _add(client)
    product.client_id = _integration().client_id
    dns.publish_for("orchard_planner")
    for _ in range(3):
        _check(client)
        sso_setup.check(product_code="orchard_planner")  # the scheduled run
    assert ProductIntegration.objects.filter(product_code="orchard_planner").count() == 1
    assert ProductSignInSetup.objects.filter(product_code="orchard_planner").count() == 1
    assert _keys() == {"orchard-key-1": "active"}
    # Preparing again (an application URL saved twice) changes nothing either.
    from workeasy360.platform.authz.control_plane import acting_scope, platform_context

    ctx = platform_context(user_id=user.id)
    from django.db import transaction

    with acting_scope(ctx), transaction.atomic():
        sso_setup.prepare(ctx, product_code="orchard_planner")
    assert ProductIntegration.objects.filter(product_code="orchard_planner").count() == 1


def test_without_an_application_url_it_waits_and_says_what_is_missing(admin, product):
    client, _user = admin
    _add(client, application_url="")
    shown = client.get(SETUP.format(code="orchard_planner")).json()
    assert shown["state"] == "pending_configuration"
    assert shown["client_id"] == ""
    assert "application URL" in shown["checks"][0]["detail"]


def test_a_product_without_the_connector_is_pending_integration_with_the_exact_requirement(
    admin, product
):
    client, _user = admin
    _add(client)
    product.outcome = "missing"
    result = _check(client)
    assert result["state"] == "pending_configuration"
    detail = _by_key(result)["document"]["detail"]
    assert detail.startswith(
        "Pending integration: https://orchard.workeasy360.com/.well-known/workeasy360-sso"
    )
    assert "workeasy360-sso 0.4.0" in detail and "*well_known()" in detail
    assert _integration().status == "draft"
    assert _keys() == {}
    assert result["next_check_at"]  # retried with back-off


# --- invalid configuration fails safely ------------------------------------------------------


def _wrong_key(product: FakeProduct) -> None:
    product.add_key("other-key")
    product.signing_kid = "other-key"
    product.keys.pop("orchard-key-1")
    product.keys["orchard-key-1"] = rsa.generate_private_key(
        public_exponent=65537, key_size=2048
    )
    product.overrides["jwks"] = {"keys": [product.jwk("orchard-key-1")]}


@pytest.mark.parametrize(
    ("change", "failed_check"),
    [
        (lambda p: p.overrides.update(client_id="someone-elses-client"), "identity"),
        (lambda p: p.overrides.update(product_code="easy_crm"), "identity"),
        (lambda p: p.overrides.update(issuer="https://evil.example"), "identity"),
        (
            lambda p: p.overrides.update(
                token_endpoint_auth_method="client_secret_basic"
            ),
            "identity",
        ),
        (
            lambda p: p.overrides.update(redirect_uris=["https://attacker.example/cb"]),
            "addresses",
        ),
        (
            lambda p: p.overrides.update(
                redirect_uris=["http://orchard.workeasy360.com/cb"]
            ),
            "addresses",
        ),
        (
            lambda p: p.overrides.update(
                backchannel_logout_uri="https://other.example/bc"
            ),
            "addresses",
        ),
        (
            lambda p: p.overrides.update(
                launch_url="https://user:pw@orchard.workeasy360.com/"
            ),
            "addresses",
        ),
        (lambda p: p.overrides.update(jwks={"keys": []}), "keys"),
        (
            lambda p: p.overrides.update(
                jwks={"keys": [{**p.jwk("orchard-key-1"), "d": "private-part"}]}
            ),
            "keys",
        ),
        (
            lambda p: p.overrides.update(
                jwks={"keys": [{**p.jwk("orchard-key-1"), "alg": "HS256"}]}
            ),
            "keys",
        ),
        (
            lambda p: p.proof_overrides.update(nonce="not-the-challenge-" + "x" * 30),
            "proof",
        ),
        (
            lambda p: p.proof_overrides.update(
                aud=f"{_issuer()}/api/v1/integrations/oauth/token"
            ),
            "proof",
        ),
        (lambda p: p.proof_overrides.update(exp=int(time.time()) + 3600), "proof"),
        (
            lambda p: p.proof_overrides.update(
                exp=int(time.time()) - 600, iat=int(time.time()) - 700
            ),
            "proof",
        ),
        (lambda p: p.proof_header.update(typ="JWT"), "proof"),
        (lambda p: p.overrides.update(proof=None), "proof"),
        (_wrong_key, "proof"),
        (lambda p: p.overrides.update(schema="something-else/9"), "document"),
        (
            lambda p: p.overrides.update(
                connector={
                    "name": "workeasy360-sso",
                    "version": "0.3.2",
                    "framework": "django",
                }
            ),
            "document",
        ),
    ],
)
def test_an_invalid_document_fails_safely_and_activates_nothing(
    admin, product, dns, change, failed_check
):
    client, _user = admin
    _add(client)
    product.client_id = _integration().client_id
    dns.publish_for("orchard_planner")
    change(product)
    result = _check(client)
    assert result["state"] == "failed", result["checks"]
    assert _by_key(result)[failed_check]["status"] == "failed", result["checks"]
    assert _integration().status == "draft"
    assert _keys() == {}
    assert "private-part" not in json.dumps(result)


def test_a_published_private_key_is_called_out(admin, product, dns):
    client, _user = admin
    _add(client)
    product.client_id = _integration().client_id
    product.overrides["jwks"] = {
        "keys": [{**product.jwk("orchard-key-1"), "d": "x", "p": "y"}]
    }
    detail = _by_key(_check(client))["keys"]["detail"]
    assert "PRIVATE KEY MATERIAL IS PUBLISHED" in detail


def test_the_setup_proof_can_never_be_a_client_assertion():
    """The token endpoint's accepted audiences never include the proof's."""
    from workeasy360.platform.ecosystem import clientauth, tokens

    audiences = clientauth._assertion_audience()
    for issuer in tokens.issuers():
        assert f"{issuer.rstrip('/')}/sso-setup" not in audiences


# --- never undoing what works ------------------------------------------------------------


def _activate(client, product, dns):
    _add(client)
    product.client_id = _integration().client_id
    dns.publish_for("orchard_planner")
    assert _check(client)["state"] == "active"


def test_an_active_product_is_never_deactivated_by_a_failing_check(admin, product, dns):
    client, _user = admin
    _activate(client, product, dns)
    product.outcome = "missing"
    result = _check(client)
    assert result["state"] == "active"
    assert _by_key(result)["document"]["status"] == "pending"
    assert _integration().status == "active"
    product.outcome = "ok"
    product.overrides["client_id"] = "changed"
    assert _check(client)["state"] == "active"
    assert _integration().status == "active"


def test_a_suspended_integration_is_never_resumed(admin, product, dns):
    from workeasy360.platform.ecosystem.models import ProductIntegration

    client, _user = admin
    _activate(client, product, dns)
    ProductIntegration.objects.filter(product_code="orchard_planner").update(
        status="suspended"
    )
    result = _check(client)
    assert result["state"] == "configured"
    assert "never resumes" in _by_key(result)["activation"]["detail"]
    assert _integration().status == "suspended"


# --- rotation -------------------------------------------------------------------------------


def test_a_rotated_key_is_added_at_once_and_the_old_one_revoked_only_after_repeated_absence(
    admin, product, dns
):
    client, _user = admin
    _activate(client, product, dns)
    product.add_key("orchard-key-2")
    product.signing_kid = "orchard-key-2"
    _check(client)
    assert _keys() == {"orchard-key-1": "active", "orchard-key-2": "active"}

    product.keys.pop("orchard-key-1")  # the product stopped publishing the old key
    for _ in range(sso_setup.KEY_ABSENCES_BEFORE_REVOKE - 1):
        _check(client)
        assert (
            _keys()["orchard-key-1"] == "active"
        )  # a rolling deploy may still sign with it
    result = _check(client)
    assert _keys() == {"orchard-key-1": "revoked", "orchard-key-2": "active"}
    assert (
        "Revoked (no longer published): orchard-key-1"
        in _by_key(result)["credentials"]["detail"]
    )
    assert result["state"] == "active"


def test_a_key_id_reused_for_a_different_key_fails(admin, product, dns):
    client, _user = admin
    _activate(client, product, dns)
    product.keys["orchard-key-1"] = rsa.generate_private_key(
        public_exponent=65537, key_size=2048
    )
    result = _check(client)
    assert _by_key(result)["credentials"]["status"] == "failed"
    assert "new key id" in _by_key(result)["credentials"]["detail"]
    assert result["state"] == "active"  # still live on the registered key


def test_a_manually_registered_credential_is_never_revoked_by_setup(admin, product, dns):
    from workeasy360.platform.authz.control_plane import acting_scope, platform_context
    from workeasy360.platform.ecosystem import services as ecosystem

    client, user = admin
    _activate(client, product, dns)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    from cryptography.hazmat.primitives import serialization

    pem = (
        other.public_key()
        .public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        .decode()
    )
    ctx = platform_context(user_id=user.id)
    with acting_scope(ctx):
        issued = ecosystem.create_credential(
            ctx,
            client_id=_integration().client_id,
            kind="public_key",
            public_key_pem=pem,
            algorithm="RS256",
        )
    for _ in range(sso_setup.KEY_ABSENCES_BEFORE_REVOKE + 1):
        _check(client)
    assert _keys()[issued.credential_id] == "active"


# --- who may, and who acts ------------------------------------------------------------------


def test_a_catalog_only_admin_can_add_the_product_and_an_integration_admin_finishes_it(
    staff_login, product, dns
):
    catalog_only, _ = staff_login(
        "platform.staff_catalog.manage", "platform.staff_audit.view"
    )
    _add(catalog_only)  # adding never fails for want of the integration grant
    from workeasy360.platform.ecosystem.models import ProductIntegration

    assert not ProductIntegration.objects.filter(product_code="orchard_planner").exists()
    assert sso_setup.check(product_code="orchard_planner")["state"] == "failed"
    integration_admin, _ = staff_login(*SUPER)
    first = _check(integration_admin)
    product.client_id = _integration().client_id
    assert first["client_id"] == product.client_id
    dns.publish_for("orchard_planner")
    assert _check(integration_admin)["state"] == "active"


def test_the_setup_api_is_for_integration_staff_only(
    admin, staff_login, product, login, console_org
):
    client, _user = admin
    _add(client)
    outsider, _ = staff_login("platform.staff_catalog.manage")
    assert outsider.get(SETUP.format(code="orchard_planner")).status_code == 403
    assert (
        post(outsider, SETUP.format(code="orchard_planner") + "/check").status_code == 403
    )
    customer = login(console_org()["owner"])
    assert customer.get(SETUP.format(code="orchard_planner")).status_code in (
        401,
        403,
        404,
    )
    assert post(client, SETUP.format(code="nosuch") + "/check").status_code == 404


def test_every_check_is_audited_as_the_person_it_acts_for(admin, product, dns):
    from tests.phase11.conftest import audit_of

    client, user = admin
    _activate(client, product, dns)
    rows = [
        r
        for r in audit_of(_integration().id, "action", "actor_user_id", "changes")
        if r["action"] == "ecosystem.integration.sign_in_setup_checked"
    ]
    assert rows
    assert {str(r["actor_user_id"]) for r in rows} == {str(user.id)}
    assert any(
        r["changes"]["state"] == "active" and r["changes"]["activated"] for r in rows
    )


# --- the document, judged on its own ---------------------------------------------------------


def test_evaluate_document_names_every_problem_at_once():
    result = sso_setup.evaluate_document(
        json.dumps(
            {
                "schema": "workeasy360-sso/1",
                "product_code": "x_product",
                "client_id": "wrong",
                "issuer": "https://elsewhere.example",
                "token_endpoint_auth_method": "private_key_jwt",
                "launch_url": "https://a.example/",
                "redirect_uris": ["https://b.example/cb"],
                "jwks": {"keys": []},
            }
        ).encode(),
        product_code="orchard_planner",
        client_id="right",
        issuers=("https://accounts.workeasy360.com",),
        application_url=APP,
        challenge="c" * 43,
    )
    by_key = {c["key"]: c for c in result.checks}
    assert not result.ok
    assert by_key["identity"]["status"] == "failed"
    assert (
        "x_product" in by_key["identity"]["detail"]
        and "issuer" in by_key["identity"]["detail"]
    )
    assert by_key["addresses"]["status"] == "failed"
    assert by_key["keys"]["status"] == "failed"
    assert by_key["proof"]["status"] == "pending"


def test_evaluate_document_refuses_what_is_not_a_document():
    for raw in (b"<html>", b"[]", json.dumps({"schema": "other"}).encode(), b"\xff\xfe"):
        result = sso_setup.evaluate_document(
            raw,
            product_code="p_x",
            client_id="c",
            issuers=("https://i.example",),
            application_url=APP,
            challenge="c" * 43,
        )
        assert not result.ok and result.checks[0]["status"] == "failed"

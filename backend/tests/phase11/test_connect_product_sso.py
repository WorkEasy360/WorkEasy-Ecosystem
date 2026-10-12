"""``connect_product_sso``: the operator's step that turns a registered
product's placeholder integration into a working single sign-on client."""

from io import StringIO

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from django.core.management import CommandError, call_command

from tests.phase11.conftest import audit_of

REDIRECT = "https://book.workeasy360.com/api/auth/callback/workeasy360"


def _actor(staff_factory):
    user, _ = staff_factory(
        "platform.staff_catalog.manage", "platform.staff_integration.manage"
    )
    return user.email


def _public_key_file(tmp_path, key=None):
    key = key or rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "product.pub"
    path.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    return str(path)


def _connect(actor, tmp_path, **options):
    out = StringIO()
    call_command(
        "connect_product_sso",
        actor=actor,
        product=options.pop("product", "easybook"),
        redirect_uris=options.pop("redirect_uris", [REDIRECT]),
        public_key_file=options.pop("public_key_file", None)
        or _public_key_file(tmp_path),
        stdout=out,
        **options,
    )
    return out.getvalue()


def test_connecting_sets_the_manifest_registers_the_key_and_activates(
    staff_factory, tmp_path
):
    from workeasy360.platform.ecosystem.models import ProductIntegration

    actor = _actor(staff_factory)
    call_command("register_products", actor=actor, stdout=StringIO())
    printed = _connect(actor, tmp_path)

    integration = ProductIntegration.objects.get(product_code="easybook")
    assert integration.status == "active"
    assert integration.manifest["redirect_uris"] == [REDIRECT]
    # Stored in the platform's canonical order, whatever order was given.
    assert set(integration.manifest["scopes"]) == {
        "openid",
        "email",
        "profile",
        "organization",
    }
    assert integration.manifest["token_endpoint_auth_method"] == "private_key_jwt"
    # What staff set elsewhere is kept.
    assert integration.manifest["launch_url"] == "https://book.workeasy360.com/"

    credential = integration.credentials.get()
    assert (credential.kind, credential.algorithm) == ("public_key", "RS256")
    assert f"WORKEASY360_CLIENT_ID={integration.client_id}" in printed
    assert f"WORKEASY360_KEY_ID={credential.credential_id}" in printed
    assert "WORKEASY360_ISSUER=" in printed
    # The other products are untouched.
    assert set(
        ProductIntegration.objects.exclude(product_code="easybook").values_list(
            "status", flat=True
        )
    ) == {"draft"}


def test_the_change_is_audited_with_the_redirect_uris(staff_factory, tmp_path):
    from workeasy360.platform.ecosystem.models import ProductIntegration

    actor = _actor(staff_factory)
    call_command("register_products", actor=actor, stdout=StringIO())
    _connect(actor, tmp_path)
    integration = ProductIntegration.objects.get(product_code="easybook")
    rows = audit_of(integration.id, "action", "changes")
    updated = next(
        row for row in rows if row["action"] == "ecosystem.integration.manifest_updated"
    )
    assert updated["changes"]["redirect_uris_after"] == [REDIRECT]
    credential = integration.credentials.get()
    assert [row["action"] for row in audit_of(credential.id, "action")] == [
        "ecosystem.credential.created"
    ]


def test_an_ec_key_is_registered_as_es256_and_rotation_keeps_the_old_key(
    staff_factory, tmp_path
):
    from workeasy360.platform.ecosystem.models import ProductIntegration

    actor = _actor(staff_factory)
    call_command("register_products", actor=actor, stdout=StringIO())
    _connect(actor, tmp_path)
    _connect(
        actor,
        tmp_path,
        public_key_file=_public_key_file(
            tmp_path, ec.generate_private_key(ec.SECP256R1())
        ),
    )
    algorithms = sorted(
        ProductIntegration.objects.get(product_code="easybook").credentials.values_list(
            "algorithm", flat=True
        )
    )
    assert algorithms == ["ES256", "RS256"]


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"scopes": ["openid", "offline_access"]}, "Unsupported scopes"),
        ({"scopes": ["email"]}, "must include openid"),
        ({"product": "no_such_product"}, "no integration"),
    ],
)
def test_bad_requests_are_refused_before_anything_changes(
    staff_factory, tmp_path, options, message
):
    from workeasy360.platform.ecosystem.models import IntegrationCredential

    actor = _actor(staff_factory)
    call_command("register_products", actor=actor, stdout=StringIO())
    with pytest.raises(CommandError, match=message):
        _connect(actor, tmp_path, **options)
    assert not IntegrationCredential.objects.exists()


def test_a_private_key_or_garbage_is_not_accepted_as_the_public_key(
    staff_factory, tmp_path
):
    actor = _actor(staff_factory)
    call_command("register_products", actor=actor, stdout=StringIO())
    garbage = tmp_path / "not-a-key.pem"
    garbage.write_text("hello")
    with pytest.raises(CommandError, match="not a PEM public key"):
        _connect(actor, tmp_path, public_key_file=str(garbage))


def test_only_an_existing_staff_identity_may_act(staff_factory, tmp_path):
    call_command("register_products", actor=_actor(staff_factory), stdout=StringIO())
    with pytest.raises(CommandError, match="No staff identity"):
        _connect("nobody@example.com", tmp_path)


# --- ADR-044: the sign-in mode, the product's own page, post-logout URIs ---

PROJECT_REDIRECT = "https://ops.workeasy360.com/api/auth/callback/workeasy360"


def _store_mode(product_code, mode):
    """A registration made before the product had a client (Easy Project and
    Easy Help Desk until 2026-10-12) was stored local_only."""
    from workeasy360.platform.ecosystem.models import ProductIntegration
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    with unscoped_access(reason="Test stores a pre-client registration", module="tests"):
        integration = ProductIntegration.objects.get(product_code=product_code)
        integration.manifest = {**integration.manifest, "sign_in_mode": mode}
        integration.save(update_fields=["manifest"])


def test_a_product_stored_local_only_is_connected_as_sso_only(staff_factory, tmp_path):
    from workeasy360.platform.ecosystem.models import ProductIntegration

    actor = _actor(staff_factory)
    call_command("register_products", actor=actor, stdout=StringIO())
    _store_mode("easy_project", "local_only")

    printed = _connect(
        actor,
        tmp_path,
        product="easy_project",
        redirect_uris=[PROJECT_REDIRECT],
        post_logout_redirect_uris=["https://ops.workeasy360.com/"],
    )

    manifest = ProductIntegration.objects.get(product_code="easy_project").manifest
    assert manifest["sign_in_mode"] == "sso_only"
    assert manifest["post_logout_redirect_uris"] == ["https://ops.workeasy360.com/"]
    assert "sign-in mode sso_only" in printed


def test_a_stored_mode_is_kept_unless_asked(staff_factory, tmp_path):
    from workeasy360.platform.ecosystem.models import ProductIntegration

    actor = _actor(staff_factory)
    call_command("register_products", actor=actor, stdout=StringIO())
    _connect(actor, tmp_path)
    assert (
        ProductIntegration.objects.get(product_code="easybook").manifest.get(
            "sign_in_mode", "sso_only"
        )
        == "sso_only"
    )

    _connect(
        actor,
        tmp_path,
        sign_in_mode="sso_optional",
        login_url="https://book.workeasy360.com/login",
    )
    manifest = ProductIntegration.objects.get(product_code="easybook").manifest
    assert manifest["sign_in_mode"] == "sso_optional"
    assert manifest["login_url"] == "https://book.workeasy360.com/login"


def test_sso_optional_without_its_sign_in_page_is_refused_before_anything_changes(
    staff_factory, tmp_path
):
    from workeasy360.platform.ecosystem.models import IntegrationCredential

    actor = _actor(staff_factory)
    call_command("register_products", actor=actor, stdout=StringIO())

    with pytest.raises(CommandError, match="manifest was refused"):
        _connect(actor, tmp_path, sign_in_mode="sso_optional")
    assert not IntegrationCredential.objects.exists()

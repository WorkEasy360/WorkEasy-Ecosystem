"""The product integration registry: registration, lifecycle, credentials.

What these assert, beyond "the happy path works":

- an integration is **not** the commercial product, and the two are joined by
  code rather than by a foreign key;
- nothing reaches ``active`` by accident;
- suspension stops authentication without touching commercial history;
- every control-plane write leaves an audit entry naming what changed;
- a credential secret exists exactly once, in the response that created it.
"""

from __future__ import annotations

import pytest

from tests.factories.manifests import manifest_for
from workeasy360.contracts.errors import (
    ManifestRejected,
    PermissionDenied,
    ProductIntegrationError,
    ProductIntegrationNotFound,
)
from workeasy360.platform.ecosystem import selectors, services
from workeasy360.platform.ecosystem.models import (
    IntegrationStatus,
    ProductIntegration,
)

pytestmark = pytest.mark.django_db


# --- registration ---------------------------------------------------------


def test_registration_starts_in_draft(platform_admin, catalog, in_tenant):
    """Never active on creation.

    A registration that could authenticate the moment it was written would
    mean a mistyped redirect URI is live before anyone has looked at it.
    """
    ctx = platform_admin["ctx"]
    with in_tenant(ctx):
        integration = services.register_integration(ctx, manifest=manifest_for("crm"))

    assert integration.status == IntegrationStatus.DRAFT.value
    assert not integration.status_enum.can_authenticate


def test_the_integration_is_separate_from_the_commercial_product(
    platform_admin, catalog, in_tenant
):
    """Two records, two lifecycles, joined by code and by nothing else.

    The commercial ``Product`` answers "what can we sell?"; the integration
    answers "how does this application connect?". If these were one row, a
    product could not be sold before its application existed - which is how
    launches actually work.
    """
    ctx = platform_admin["ctx"]
    with in_tenant(ctx):
        integration = services.register_integration(ctx, manifest=manifest_for("crm"))

    product = catalog["products"]["crm"]
    assert integration.product_code == product.code
    # No foreign key in either direction, and no field on either that names
    # the other's primary key.
    field_names = {f.name for f in ProductIntegration._meta.get_fields()}
    assert "product" not in field_names
    assert not any(
        getattr(f, "related_model", None) is type(product)
        for f in ProductIntegration._meta.get_fields()
    )


def test_a_product_code_the_catalog_does_not_know_is_refused(
    platform_admin, catalog, in_tenant
):
    """Reached through contracts/ports/catalog.py, never by importing
    commercial.

    Without this, ``crmm`` registers happily and then denies every entitlement
    check forever, producing a ticket that says "SSO is broken".
    """
    ctx = platform_admin["ctx"]
    with in_tenant(ctx), pytest.raises(ManifestRejected) as exc:
        services.register_integration(ctx, manifest=manifest_for("crmm"))
    assert exc.value.context["field"] == "product_code"


def test_one_integration_per_product(platform_admin, catalog, in_tenant):
    """Two client ids both claiming to be CRM would make the entitlement
    answer depend on which one asked."""
    ctx = platform_admin["ctx"]
    with in_tenant(ctx):
        services.register_integration(ctx, manifest=manifest_for("crm"))
        with pytest.raises(ProductIntegrationError, match="already has an integration"):
            services.register_integration(
                ctx, manifest=manifest_for("crm", origin="http://localhost:9999")
            )


def test_client_ids_are_random_and_unguessable(platform_admin, catalog, in_tenant):
    """A client id derived from the product code would let anyone construct
    another product's id - and it is also the ``aud`` claim."""
    ctx = platform_admin["ctx"]
    with in_tenant(ctx):
        crm = services.register_integration(ctx, manifest=manifest_for("crm"))
        books = services.register_integration(ctx, manifest=manifest_for("books"))

    assert "crm" not in crm.client_id
    assert "books" not in books.client_id
    assert crm.client_id != books.client_id
    assert len(crm.client_id) >= 24


# --- lifecycle ------------------------------------------------------------


def test_only_active_integrations_can_authenticate(register_product):
    registration = register_product("crm", activate=False)
    assert not registration["integration"].status_enum.can_authenticate


@pytest.mark.parametrize(
    "start,target,permitted",
    [
        ("draft", "active", True),
        ("draft", "retired", True),
        ("draft", "suspended", False),
        ("active", "suspended", True),
        ("active", "retired", True),
        ("suspended", "active", True),
        ("suspended", "retired", True),
        ("retired", "active", False),
        ("retired", "suspended", False),
        ("retired", "draft", False),
    ],
)
def test_the_lifecycle_state_machine_is_enforced(
    register_product, platform_admin, in_tenant, start, target, permitted
):
    """Retirement is terminal, and nothing skips states.

    An illegal transition is refused, never silently ignored: an activation
    that quietly does nothing is indistinguishable from one that worked, and
    the difference is whether a customer can sign in.
    """
    ctx = platform_admin["ctx"]
    registration = register_product("crm", activate=False)
    client_id = registration["client_id"]

    with in_tenant(ctx):
        for step in _path_to(start):
            services.transition(ctx, client_id=client_id, to_status=step, reason="setup")

        if permitted:
            result = services.transition(
                ctx, client_id=client_id, to_status=target, reason="test"
            )
            assert result.status == target
        else:
            with pytest.raises(ProductIntegrationError, match="not a permitted"):
                services.transition(
                    ctx, client_id=client_id, to_status=target, reason="test"
                )


def _path_to(status: str) -> list[str]:
    """The transitions that reach ``status`` from ``draft``."""
    return {
        "draft": [],
        "active": ["active"],
        "suspended": ["active", "suspended"],
        "retired": ["retired"],
    }[status]


def test_suspension_requires_a_reason(register_product, platform_admin, in_tenant):
    """A suspension stops every user of that product signing in. An
    unexplained one is an outage nobody can attribute."""
    ctx = platform_admin["ctx"]
    registration = register_product("crm")
    with (
        in_tenant(ctx),
        pytest.raises(ProductIntegrationError, match="requires a reason"),
    ):
        services.transition(
            ctx, client_id=registration["client_id"], to_status="suspended"
        )


def test_suspension_does_not_disturb_commercial_history(
    register_product, platform_admin, in_tenant, entitled_org
):
    """The registration is an ecosystem record; the entitlement is a
    commercial one. Suspending the first must leave the second untouched, or
    a suspension would look like a cancellation on the customer's invoice."""
    from workeasy360.platform.entitlements.services import read as entitlements

    ctx = platform_admin["ctx"]
    registration = register_product("crm")
    org = entitled_org("crm")

    with in_tenant(org["ctx"]):
        before = entitlements.check(org["org"].id, "crm")
    with in_tenant(ctx):
        services.transition(
            ctx,
            client_id=registration["client_id"],
            to_status="suspended",
            reason="security review",
        )
    with in_tenant(org["ctx"]):
        after = entitlements.check(org["org"].id, "crm")

    assert before.allowed and after.allowed
    assert before.status == after.status


def test_reactivation_clears_the_suspension(register_product, platform_admin, in_tenant):
    ctx = platform_admin["ctx"]
    registration = register_product("crm")
    with in_tenant(ctx):
        services.transition(
            ctx,
            client_id=registration["client_id"],
            to_status="suspended",
            reason="review",
        )
        result = services.transition(
            ctx, client_id=registration["client_id"], to_status="active"
        )
    assert result.suspended_at is None
    assert result.suspension_reason == ""
    assert result.status_enum.can_authenticate


def test_an_unknown_status_is_refused_by_the_service_not_the_view(
    register_product, platform_admin, in_tenant
):
    """Views pass the string through; the service owns the validation, so the
    task path and every internal caller get the same check."""
    ctx = platform_admin["ctx"]
    registration = register_product("crm")
    with in_tenant(ctx), pytest.raises(ProductIntegrationError, match="Unknown status"):
        services.transition(ctx, client_id=registration["client_id"], to_status="deleted")


# --- manifest updates -----------------------------------------------------


def test_a_manifest_update_cannot_change_the_product(
    register_product, platform_admin, in_tenant
):
    """One client id must not mean CRM on Monday and Books on Tuesday, while
    every token ever issued under it still says CRM."""
    ctx = platform_admin["ctx"]
    registration = register_product("crm")
    with in_tenant(ctx), pytest.raises(ProductIntegrationError, match="product code"):
        services.update_manifest(
            ctx, client_id=registration["client_id"], manifest=manifest_for("books")
        )


def test_a_retired_integration_cannot_be_modified(
    register_product, platform_admin, in_tenant
):
    ctx = platform_admin["ctx"]
    registration = register_product("crm")
    with in_tenant(ctx):
        services.transition(
            ctx, client_id=registration["client_id"], to_status="retired", reason="eol"
        )
        with pytest.raises(ProductIntegrationError, match="retired"):
            services.update_manifest(
                ctx,
                client_id=registration["client_id"],
                manifest=manifest_for("crm", origin="https://new.example.com"),
            )


def test_a_redirect_uri_change_records_both_sides_in_audit(
    register_product, platform_admin, in_tenant
):
    """The highest-risk write in the module. The audit entry must make an
    unauthorised change *visible*, not merely implied - which means recording
    what the set was as well as what it became."""
    from workeasy360.platform.audit.models import AuditEvent

    ctx = platform_admin["ctx"]
    registration = register_product("crm")

    with in_tenant(ctx):
        services.update_manifest(
            ctx,
            client_id=registration["client_id"],
            manifest=manifest_for("crm", origin="https://crm.example.com"),
        )
        entry = AuditEvent.objects.filter(
            action="ecosystem.integration.manifest_updated"
        ).first()

    assert entry is not None
    assert entry.changes["redirect_uris_before"] == [registration["redirect_uri"]]
    assert entry.changes["redirect_uris_after"] == [
        "https://crm.example.com/auth/callback"
    ]


# --- credentials ----------------------------------------------------------


def test_a_client_secret_is_returned_once_and_never_stored(
    register_product, platform_admin, in_tenant
):
    """A credential API that can re-read a secret leaks every secret the day
    it is compromised."""
    from workeasy360.platform.ecosystem import clientauth
    from workeasy360.platform.ecosystem.models import IntegrationCredential

    ctx = platform_admin["ctx"]
    registration = register_product("crm", credential_kind="client_secret")
    secret = registration["client_secret"]
    assert secret

    row = IntegrationCredential.objects.get(credential_id=registration["credential_id"])
    # Only a digest is stored, and the digest is of the secret we were given.
    assert secret not in row.secret_hash
    assert row.secret_hash == clientauth.hash_secret(secret)

    # No read path returns it.
    with in_tenant(ctx):
        listed = selectors.credentials(ctx, client_id=registration["client_id"])
    assert all(not hasattr(c, "secret") for c in listed)
    assert secret not in str([c.as_dict() for c in listed])


def test_creating_a_credential_does_not_revoke_the_existing_one(
    register_product, platform_admin, in_tenant
):
    """Overlap is the point: a product rotates by adding, deploying, then
    revoking. A rotation requiring an atomic fleet-wide swap is a rotation
    nobody performs."""
    from workeasy360.platform.ecosystem import keys

    ctx = platform_admin["ctx"]
    registration = register_product("crm")

    with in_tenant(ctx):
        _, public_pem = keys.generate_key_pair("RS256")
        services.create_credential(
            ctx,
            client_id=registration["client_id"],
            kind="public_key",
            public_key_pem=public_pem,
            algorithm="RS256",
        )
        listed = selectors.credentials(ctx, client_id=registration["client_id"])

    assert len(listed) == 2
    assert all(c.status == "active" for c in listed)


def test_an_unusable_public_key_is_refused_at_registration(
    register_product, platform_admin, in_tenant
):
    """Checked now rather than discovered at the first token exchange, when
    nobody will connect the outage to this action."""
    ctx = platform_admin["ctx"]
    registration = register_product("crm")
    with in_tenant(ctx), pytest.raises(ProductIntegrationError, match="not a usable"):
        services.create_credential(
            ctx,
            client_id=registration["client_id"],
            kind="public_key",
            public_key_pem="-----BEGIN PUBLIC KEY-----\nnonsense\n-----END PUBLIC KEY-----",
            algorithm="RS256",
        )


def test_an_unsupported_credential_algorithm_is_refused(
    register_product, platform_admin, in_tenant
):
    """The algorithm is pinned at registration and used at verify time instead
    of the assertion's own header. Accepting an arbitrary value here puts the
    choice back in the attacker's hands."""
    from workeasy360.platform.ecosystem import keys

    ctx = platform_admin["ctx"]
    registration = register_product("crm")
    _, public_pem = keys.generate_key_pair("RS256")
    with in_tenant(ctx), pytest.raises(ProductIntegrationError, match="Unsupported"):
        services.create_credential(
            ctx,
            client_id=registration["client_id"],
            kind="public_key",
            public_key_pem=public_pem,
            algorithm="HS256",
        )


def test_revocation_requires_a_reason_and_is_idempotent(
    register_product, platform_admin, in_tenant
):
    """Idempotent because an operator responding to an incident must not be
    stopped by an error saying the thing they wanted has already happened."""
    ctx = platform_admin["ctx"]
    registration = register_product("crm")

    with in_tenant(ctx):
        with pytest.raises(ProductIntegrationError, match="requires a reason"):
            services.revoke_credential(
                ctx,
                client_id=registration["client_id"],
                credential_id=registration["credential_id"],
                reason="",
            )

        first = services.revoke_credential(
            ctx,
            client_id=registration["client_id"],
            credential_id=registration["credential_id"],
            reason="leaked",
        )
        second = services.revoke_credential(
            ctx,
            client_id=registration["client_id"],
            credential_id=registration["credential_id"],
            reason="leaked again",
        )

    assert first.revoked_at == second.revoked_at
    assert second.status == "revoked"


def test_revoking_an_unknown_credential_is_a_404_not_a_500(
    register_product, platform_admin, in_tenant
):
    ctx = platform_admin["ctx"]
    registration = register_product("crm")
    with in_tenant(ctx), pytest.raises(ProductIntegrationNotFound):
        services.revoke_credential(
            ctx,
            client_id=registration["client_id"],
            credential_id="nope",
            reason="test",
        )


# --- authorization on the control plane ----------------------------------


def test_every_registry_mutation_requires_platform_staff(
    customer, catalog, in_tenant, grant_permissions
):
    """A customer administrator with the permission but without staff status
    must still be refused.

    Either control alone is one misconfiguration away from a customer being
    able to register an application against the platform.
    """
    ctx = customer["ctx"]
    grant_permissions(
        customer["org"], customer["membership"], ("platform.integration.register",)
    )
    with in_tenant(ctx), pytest.raises(PermissionDenied) as exc:
        services.register_integration(ctx, manifest=manifest_for("crm"))
    assert "internal platform identity" in str(exc.value)


def test_registry_mutations_are_blocked_under_impersonation(
    platform_admin, catalog, in_tenant
):
    """Break-glass may not register an application. The UI is not a security
    boundary, so this is enforced in the decision function."""
    from dataclasses import replace

    ctx = replace(
        platform_admin["ctx"], is_break_glass=True, reason="support investigation"
    )
    with in_tenant(ctx), pytest.raises(PermissionDenied) as exc:
        services.register_integration(ctx, manifest=manifest_for("crm"))
    # The reason, not the message: the message is deliberately uniform across
    # denial causes, and the machine-readable reason is what a caller acts on.
    assert exc.value.context["reason"] == "blocked_under_impersonation"


def test_reading_the_registry_does_not_require_step_up(
    register_product, platform_admin, in_tenant
):
    """``platform.integration.view`` is deliberately not dangerous: client ids
    and redirect URIs are public by construction, and gating the read would
    push operators toward keeping a copy somewhere worse."""
    from dataclasses import replace

    register_product("crm")
    ctx = replace(platform_admin["ctx"])
    with in_tenant(ctx):
        assert selectors.integrations(ctx)


@pytest.mark.parametrize(
    "action",
    [
        "ecosystem.integration.registered",
        "ecosystem.integration.active",
        "ecosystem.credential.created",
    ],
)
def test_control_plane_actions_are_audited(register_product, platform_admin, action):
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    register_product("crm")
    org_id = platform_admin["org"].id

    with (
        unscoped_access(reason="test reads the audit log", module="tests"),
        organization_db_scope(org_id),
    ):
        assert AuditEvent.unscoped.filter(
            organization_id=org_id, action=action
        ).exists(), f"{action} left no audit entry"


def test_no_credential_secret_ever_reaches_the_audit_log(
    register_product, platform_admin
):
    """``audit.redact`` strips secret-shaped keys, and nothing here writes one
    anyway. Asserted against the created secret itself, not against a pattern."""
    from workeasy360.platform.audit.models import AuditEvent
    from workeasy360.platform.tenancy.db import organization_db_scope
    from workeasy360.platform.tenancy.unscoped import unscoped_access

    registration = register_product("crm", credential_kind="client_secret")
    org_id = platform_admin["org"].id

    with (
        unscoped_access(reason="test reads the audit log", module="tests"),
        organization_db_scope(org_id),
    ):
        entries = list(
            AuditEvent.unscoped.filter(organization_id=org_id).values_list(
                "changes", flat=True
            )
        )
    assert registration["client_secret"] not in str(entries)

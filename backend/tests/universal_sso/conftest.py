"""Universal, user-controlled product sign-in (ADR-044): shared fixtures.

Everything here registers products and people through the same services
production uses - the registry, the sole entitlement writer, the real login
endpoint where a password matters - so the suites below exercise states the
system can actually reach. ``signed_in(..., sign_in_preference=None)`` is the
platform default ("ask me each time"); the older OIDC suites pass
``auto_sso`` (see the root ``signed_in`` fixture).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.factories.manifests import PRODUCT_ORIGIN, manifest_for

PASSWORD = "correct horse battery staple 42"

AUTHORIZE = "/api/v1/integrations/oauth/authorize"
RESUME = "/api/v1/integrations/oauth/resume"
PENDING = "/api/v1/integrations/oauth/pending"
CHOOSE = "/api/v1/integrations/oauth/pending/choose"
TOKEN = "/api/v1/integrations/oauth/token"
PREFS = "/api/v1/auth/sign-in-preferences"
END_SESSION = "/api/v1/integrations/oauth/logout"
END_SESSION_REQUEST = "/api/v1/integrations/oauth/logout/request"
END_SESSION_CONFIRM = "/api/v1/integrations/oauth/logout/confirm"
LOGIN_URL = f"{PRODUCT_ORIGIN}/login"
SIGNED_OUT_URL = f"{PRODUCT_ORIGIN}/signed-out"


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorize_query(
    registration: dict, organization_id: str | None, **overrides
) -> tuple[str, dict]:
    """A product's authorization request: (code_verifier, query)."""
    verifier, challenge = pkce()
    query = {
        "response_type": "code",
        "client_id": registration["client_id"],
        "redirect_uri": registration["redirect_uri"],
        "scope": "openid",
        "state": "client-state",
        "nonce": "client-nonce",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if organization_id is not None:
        query["organization"] = organization_id
    query.update(overrides)
    return verifier, {k: v for k, v in query.items() if v is not None}


def query_of(response) -> dict[str, list[str]]:
    return parse_qs(urlsplit(response["Location"]).query)


def path_of(response) -> str:
    return urlsplit(response["Location"]).path


def resume_token_of(response) -> str:
    return query_of(response)["resume"][0]


@pytest.fixture
def sso_product(register_product):
    """Register a product with an ADR-044 sign-in declaration.

    ``mode`` is the product's own ``sign_in_mode``; an ``sso_optional`` or
    ``local_only`` product gets ``login_url`` unless ``login_url=False``.
    """

    def make(
        code: str = "crm",
        *,
        mode: str = "sso_only",
        login_url: bool = True,
        post_logout: tuple[str, ...] = (),
        scopes: tuple[str, ...] = ("openid",),
        activate: bool = True,
        credential_kind: str = "public_key",
    ) -> dict:
        extra: dict = {"manifest_version": "2026-10-12", "sign_in_mode": mode}
        if login_url and mode != "sso_only":
            extra["login_url"] = LOGIN_URL
        if post_logout:
            extra["post_logout_redirect_uris"] = list(post_logout)
        return register_product(
            code,
            manifest=manifest_for(code, scopes=scopes, **extra),
            activate=activate,
            credential_kind=credential_kind,
        )

    return make


@pytest.fixture
def prefer(db):
    """Store a person's preference directly (``inherit`` removes a row)."""
    from workeasy360.platform.ecosystem.models import UserSignInPreference

    def make(user, mode: str, product_code: str = "") -> None:
        if mode == "inherit":
            UserSignInPreference.objects.filter(
                user=user, product_code=product_code
            ).delete()
            return
        UserSignInPreference.objects.update_or_create(
            user=user, product_code=product_code, defaults={"mode": mode}
        )

    return make


@pytest.fixture
def with_password(db):
    """Give a person a real password, for flows that sign in through the
    actual login endpoint (``prompt=login``, account switching)."""

    def make(user):
        user.set_password(PASSWORD)
        user.save(update_fields=["password"])
        return user

    return make


@pytest.fixture
def password_login(db):
    """Sign in through the real login endpoint on ``client`` (a Django test
    client), as the login page does: session first (CSRF cookie), then POST."""

    def make(client, user):
        client.get("/api/v1/auth/session")
        response = client.post(
            "/api/v1/auth/login",
            {"email": user.email, "password": PASSWORD},
            content_type="application/json",
        )
        assert response.status_code == 200, response.content
        return client

    return make


@pytest.fixture
def second_org_for(organization_factory, entitled):
    """Another organization for the same person, entitled to ``product``
    (an organization-licensed one needs no seat)."""

    def make(user, product: str = "books"):
        org, membership, _ = organization_factory(owner=user)
        entitled(org.id, product)
        return {"org": org, "membership": membership, "organization_id": str(org.id)}

    return make


@pytest.fixture
def trial_org(organization_factory):
    """An organization on a product trial: a TRIAL entitlement whose window
    ends at ``ends_at`` - the state ``provisioning`` produces for a 7-day
    product trial - written through the sole entitlement writer. ``books`` is
    organization-licensed, so no seat is involved."""
    from datetime import timedelta

    from django.utils import timezone

    from workeasy360.contracts.enums import EntitlementSourceKind, EntitlementStatus
    from workeasy360.platform.entitlements.services.write import (
        ComputedEntitlements,
        ComputedProduct,
        apply_entitlements,
    )

    def make(product: str = "books", *, days: int = 7):
        org, membership, user = organization_factory()
        now = timezone.now()
        ends_at = now + timedelta(days=days)
        apply_entitlements(
            ComputedEntitlements(
                organization_id=org.id,
                products=(
                    ComputedProduct(
                        product_code=product,
                        status=EntitlementStatus.TRIAL,
                        source_kind=EntitlementSourceKind.TRIAL,
                        source_ref=f"test-trial:{product}",
                        effective_from=now,
                        effective_to=ends_at,
                    ),
                ),
                features=(),
            )
        )
        return {
            "org": org,
            "membership": membership,
            "user": user,
            "organization_id": str(org.id),
            "ends_at": ends_at,
        }

    return make

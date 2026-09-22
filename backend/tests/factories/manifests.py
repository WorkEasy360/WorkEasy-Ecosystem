"""Manifest builders shared by the ecosystem suites.

A module rather than a fixture because the root ``conftest.py`` and the
registry tests both need it, and a fixture that has to be requested in order
to build a dictionary is ceremony. It lives under ``tests/factories`` for the
same reason the reference client does: it is test data, not platform code.
"""

from __future__ import annotations

from typing import Any

#: The loopback origin the fixtures register. ``http://`` on localhost is
#: permitted outside production (contracts/values/manifest.py), which is
#: exactly the case this exercises - production refuses it, and
#: test_production_refuses_insecure_configuration proves that separately.
PRODUCT_ORIGIN = "http://localhost:9100"

#: The issuer the test settings use. Pinned here rather than read back from
#: settings, so a test asserting "the product verifies the issuer" compares
#: against a value it stated rather than one it copied from the code under
#: test.
TEST_ISSUER = "http://testserver"


def manifest_for(
    product_code: str,
    *,
    origin: str = PRODUCT_ORIGIN,
    scopes: tuple[str, ...] = ("openid",),
    auth_method: str = "private_key_jwt",
    **overrides: Any,
) -> dict[str, Any]:
    """A valid manifest for ``product_code``. The happy-path shape."""
    payload: dict[str, Any] = {
        "manifest_version": "2026-09-01",
        "product_code": product_code,
        "display_name": product_code.upper(),
        "launch_url": f"{origin}/launch",
        "redirect_uris": [f"{origin}/auth/callback"],
        "scopes": list(scopes),
        "capabilities": ["offline_token_verification", "entitlement_revalidation"],
        "token_endpoint_auth_method": auth_method,
        "health_url": f"{origin}/healthz",
        "contact_email": f"{product_code}@example.com",
    }
    payload.update(overrides)
    return payload

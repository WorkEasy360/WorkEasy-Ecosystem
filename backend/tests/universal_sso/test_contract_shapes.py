"""The frontend's declared wire types for the Authentication section match
real API responses (ADR-044) - field by field, so a renamed field is a test
failure, not a blank cell in the console."""

from __future__ import annotations

import pytest

from tests.factories.manifests import PRODUCT_ORIGIN
from tests.phase7 import conftest as _phase7
from tests.phase10 import ts_contract as ts

pytestmark = pytest.mark.django_db

# Fixtures shared with the Phase 7 suites: a signed-in staff member at AAL 2.
http_login = _phase7.http_login
staff_client = _phase7.staff_client

ADMIN = "/api/v1/admin"


def test_the_authentication_section_matches_its_types(staff_client, sso_product):
    from workeasy360.platform.ecosystem import keys

    sso_product("crm", mode="sso_optional", post_logout=(f"{PRODUCT_ORIGIN}/signed-out",))
    staff, _user = staff_client(
        "platform.staff_catalog.view",
        "platform.staff_catalog.manage",
        "platform.staff_integration.view",
        "platform.staff_integration.manage",
    )
    console = staff.get(f"{ADMIN}/products/console").json()
    crm = next(p for p in console["products"] if p["code"] == "crm")
    ts.assert_matches(crm["integration"], "ConsoleIntegration", "crm.integration")

    _private, public_pem = keys.generate_key_pair("RS256")
    issued = staff.post(
        f"{ADMIN}/products/crm/integration/credentials",
        {"kind": "public_key", "public_key_pem": public_pem},
        content_type="application/json",
    ).json()
    ts.assert_matches(issued, "IssuedConsoleCredential", "issued")
    listed = staff.get(f"{ADMIN}/products/crm/integration/credentials").json()
    ts.assert_matches(listed["credentials"][0], "ConsoleCredential", "credentials[0]")

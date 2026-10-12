"""The return-URL allowlist and origin parsing (``contracts.values.portals``).

Pure: no database, no settings. ``next`` is only ever a path the portal
serves; everything else falls back to the portal's root, so it can never be an
open redirect.
"""

from __future__ import annotations

import pytest

from workeasy360.contracts.values.portals import (
    PORTAL_ROOT,
    host_of,
    parse_origin,
    portal_for_host,
    safe_next,
)

ORIGINS = {
    "admin": "https://admin.workeasy360.com",
    "partner": "https://partner.workeasy360.com",
    "app": "https://app.workeasy360.com",
    "auth": "https://auth.workeasy360.com",
    "api": "https://api.workeasy360.com",
}


@pytest.mark.parametrize(
    "portal,raw,expected",
    [
        ("admin", "/admin", "/admin"),
        ("admin", "/admin/customers?q=acme&page=2", "/admin/customers?q=acme&page=2"),
        ("admin", "/admin/partners/resellers#top", "/admin/partners/resellers#top"),
        ("partner", "/partner/7b1f/customers", "/partner/7b1f/customers"),
        ("app", "/o/acme/apps", "/o/acme/apps"),
        ("app", "/dashboard", "/dashboard"),
        ("app", "/organizations/select", "/organizations/select"),
        ("app", "/onboarding", "/onboarding"),
        ("app", "/billing/test-checkout", "/billing/test-checkout"),
    ],
)
def test_a_page_the_portal_serves_is_kept(portal, raw, expected):
    assert safe_next(portal, raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        7,
        "https://evil.example.com/admin",
        "//evil.example.com/admin",
        "/\\evil.example.com",
        "\\\\evil.example.com",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "JaVaScRiPt:alert(1)",
        "/admin/../o/acme",
        "/admin/./x",
        "/admin/%2e%2e/o/acme",
        "/admin%2f..%2fo",
        "/admin%5c",
        "/admin\n/x",
        "/admin\t",
        "/admin\x00",
        "/administrator",  # a prefix match is on whole segments only
        "/o/acme",  # another portal's page
        "/partner/x",
        "http://localhost/admin",
        "https://admin.workeasy360.com.evil.com/admin",
        "/admin" + "x" * 600,
        " /admin",
    ],
)
def test_anything_else_falls_back_to_the_root(raw):
    assert safe_next("admin", raw) == PORTAL_ROOT["admin"]


def test_another_portals_page_is_not_a_destination():
    assert safe_next("app", "/admin") == "/dashboard"
    assert safe_next("partner", "/o/acme") == "/partner"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://app.workeasy360.com", ("https", "app.workeasy360.com")),
        ("https://App.Workeasy360.com/", ("https", "app.workeasy360.com")),
        ("http://127.0.0.1:3999", ("http", "127.0.0.1:3999")),
        ("https://app.workeasy360.com/path", None),
        ("https://app.workeasy360.com?x=1", None),
        ("https://user:pw@app.workeasy360.com", None),
        ("https://*.workeasy360.com", None),
        ("ftp://app.workeasy360.com", None),
        ("app.workeasy360.com", None),
        ("", None),
    ],
)
def test_an_origin_is_exactly_scheme_and_host(raw, expected):
    assert parse_origin(raw) == expected


def test_a_host_maps_to_exactly_one_portal():
    assert portal_for_host("admin.workeasy360.com", ORIGINS) == "admin"
    assert portal_for_host("AUTH.workeasy360.com.", ORIGINS) == "auth"
    assert portal_for_host("api.workeasy360.com", ORIGINS) == "api"
    assert portal_for_host("crm.workeasy360.com", ORIGINS) is None
    assert portal_for_host("admin.workeasy360.com:8443", ORIGINS) is None
    assert portal_for_host("", ORIGINS) is None
    assert portal_for_host("app.workeasy360.com", {}) is None
    assert host_of("https://app.workeasy360.com:8443") == "app.workeasy360.com:8443"

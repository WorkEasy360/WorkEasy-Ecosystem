"""Phase 7 fitness rules: the properties the control plane is only safe
because of, checked structurally so they cannot erode quietly.

These are the checks an import graph cannot see: that the frontend calls only
routes that exist, that it names only capabilities that exist, that the API
grew no product-domain surface, and that the list of tables exempt from
tenant scoping is exactly the list the debt register (D-019) justifies.
"""

from __future__ import annotations

import pathlib
import re
import uuid

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
FRONTEND = REPO_ROOT / "frontend" / "src"
API_CLIENT = FRONTEND / "lib" / "api" / "controlPlane.ts"
_SAMPLE_ID = str(uuid.uuid4())


def _actions_before(source: str, position: int) -> list[str]:
    """The string-literal union of the nearest ``action: "a" | "b"`` parameter
    declared before ``position`` - the values a ``${action}`` segment can take."""
    declared = list(
        re.finditer(r'action: ((?:"[a-z_]+"(?:\s*\|\s*)?)+)', source[:position])
    )
    assert declared, "a ${action} path with no visible action union"
    return re.findall(r'"([a-z_]+)"', declared[-1].group(1))


def _client_paths() -> list[str]:
    """Every API path the Phase 7 frontend client can request, with ids filled
    in and every ``${action}`` expanded to each value its type allows."""
    source = API_CLIENT.read_text(encoding="utf-8")
    found: list[str] = []
    for match in re.finditer(r"`([^`]*)`|\"(/api/v1/[^\"]*)\"", source):
        raw = match.group(1) if match.group(1) is not None else match.group(2)
        raw = raw.replace("${admin}", "/api/v1/admin")
        raw = re.sub(r"\$\{partner\([^)]*\)\}", f"/api/v1/partner/{_SAMPLE_ID}", raw)
        variants = [raw]
        if "${action}" in raw:
            variants = [
                raw.replace("${action}", word)
                for word in _actions_before(source, match.start())
            ]
        for variant in variants:
            variant = re.sub(r"\$\{[^}]*\}", _SAMPLE_ID, variant)
            if not variant.startswith("/api/v1/"):
                continue
            # A bare prefix ("/api/v1/admin", "/api/v1/partner/<id>") is a helper.
            if variant == "/api/v1/admin" or re.fullmatch(
                rf"/api/v1/partner/{_SAMPLE_ID}", variant
            ):
                continue
            found.append(variant)
    return found


def test_the_client_finds_the_paths_it_should():
    """Guards the parser: an empty result would make the next test vacuous."""
    paths = _client_paths()
    assert len(paths) > 50
    assert any(p.startswith("/api/v1/admin/payouts") for p in paths)
    assert any("/branding/logo" in p for p in paths)


def test_every_path_the_frontend_client_requests_is_a_real_route():
    from django.urls import Resolver404, resolve

    missing = []
    for path in sorted(set(_client_paths())):
        try:
            resolve(path)
        except Resolver404:
            missing.append(path)
    assert not missing, "The frontend calls routes the API does not serve:\n" + "\n".join(
        missing
    )


def test_the_frontend_only_names_capabilities_the_registry_declares():
    from workeasy360.commercial.partners.capabilities import CAPABILITIES

    used: set[str] = set()
    for path in (FRONTEND / "features" / "controlplane").glob("*.tsx"):
        if path.name.endswith(".test.tsx"):
            continue
        used.update(
            re.findall(r'has\("([a-z]+\.[a-z_]+)"\)', path.read_text(encoding="utf-8"))
        )
    assert used, "no capability checks found - the pattern no longer matches the code"
    unknown = used - set(CAPABILITIES)
    assert not unknown, (
        f"the frontend names capabilities that do not exist: {sorted(unknown)}"
    )


def test_the_frontend_names_only_staff_permissions_that_exist():
    from workeasy360.platform.authz.permissions import STAFF_PERMISSIONS

    declared = {p.key for p in STAFF_PERMISSIONS}
    used: set[str] = set()
    for path in (FRONTEND / "features" / "controlplane").glob("*.tsx"):
        if path.name.endswith(".test.tsx"):
            continue
        used.update(
            re.findall(
                r'"(platform\.staff_[a-z_]+\.[a-z_]+)"', path.read_text(encoding="utf-8")
            )
        )
    assert used
    assert used <= declared, (
        f"unknown staff permissions in the UI: {sorted(used - declared)}"
    )


_TYPE_BRANCH = re.compile(
    r"partner[_.]?(?:type|kind|tier)|partner(?:Type|Kind|Tier)|"
    r"is_?(?:reseller|affiliate|white_?label)|is(?:Reseller|Affiliate|WhiteLabel)",
    re.IGNORECASE,
)


def test_the_partner_portal_has_no_partner_type_branch():
    """ADR-028: capabilities, never a type. No screen, client module or partner,
    commission or control-plane service may branch on one. (The final audit
    widened this from the control-plane ``.tsx`` files alone to every frontend
    file of the admin and partner areas and to the backend services.)"""
    backend = REPO_ROOT / "backend" / "workeasy360" / "commercial"
    paths = [
        *(FRONTEND / "features" / "controlplane").glob("*.ts*"),
        *(FRONTEND / "app" / "partner").rglob("*.ts*"),
        *(FRONTEND / "app" / "admin").rglob("*.ts*"),
        API_CLIENT,
        *(backend / "partners").glob("*.py"),
        *(backend / "commissions").glob("*.py"),
        *(backend / "controlplane").rglob("*.py"),
    ]
    offenders = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in paths
        if ".test." not in path.name
        and _TYPE_BRANCH.search(path.read_text(encoding="utf-8"))
    )
    assert len(paths) > 20
    assert not offenders, offenders


#: The only production modules that may read Django's ``is_staff``, each as a
#: *precondition* or an operator switch - never as a permission. Everything
#: that decides what staff may do reads live ``StaffGrant`` rows.
_IS_STAFF_READERS = {
    # The grant mechanism: a grant on a non-staff account is inert.
    "workeasy360/platform/authz/staff.py",
    # The admin API refuses a non-staff session before building a context.
    "workeasy360/interfaces/api/v1/control_plane_http.py",
    # Operator commands that designate staff identities.
    "workeasy360/commercial/controlplane/management/commands/bootstrap_superadmin.py",
    "workeasy360/commercial/controlplane/management/commands/w360_staff_identity.py",
    # Phase 12 (D-037 residual): break-glass reset; it removes the designation.
    "workeasy360/commercial/controlplane/management/commands/w360_staff_mfa_reset.py",
    # Phase 11: registers the four products' catalog rows as a named,
    # already-staff actor (looked up by email, never created) - the same
    # pattern as the two commands above, not a new authority.
    "workeasy360/commercial/controlplane/management/commands/register_products.py",
    # 2026-10-12: connects a registered product's SSO as the same kind of
    # named, already-staff actor.
    "workeasy360/commercial/controlplane/management/commands/connect_product_sso.py",
    # The field itself, and Django's own manager defaults.
    "workeasy360/platform/identity/models.py",
    # Phase 9: the one writer of the designation (``set_staff``), which the
    # operator commands above call - identity is the only writer of ``User``.
    "workeasy360/platform/identity/lifecycle.py",
    # Phase 12 (D-037): a staff identity enrolling an authenticator is logged
    # as a security event - a read of the flag, never an authority.
    "workeasy360/platform/identity/services.py",
    # The deep health endpoint's detail view (diagnostics, no business data).
    "workeasy360/platform/observability/health.py",
    # Phase 8 extension, navigation and display only - never authority:
    # the session endpoint offers the Super Admin link to a staff identity that
    # also holds a live grant (the admin API re-checks both on every request),
    # and the staff user directory shows who is staff.
    "workeasy360/interfaces/api/v1/auth_views.py",
    "workeasy360/platform/administration/staff_users.py",
}


def test_django_is_staff_is_never_an_authority():
    """Final audit A-01: ``is_staff`` plus any organization's owner role once
    unlocked the global catalog, price books and the integration registry. A
    new reader of the flag must be a deliberate, reviewed addition here."""
    import ast

    root = REPO_ROOT / "backend"
    readers = set()
    for path in (root / "workeasy360").rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if "/tests/" in rel or "/migrations/" in rel:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and node.attr == "is_staff") or (
                isinstance(node, ast.keyword)
                and node.arg is not None
                and node.arg.endswith("is_staff")
            ):
                readers.add(rel)
                break
    assert "workeasy360/platform/authz/staff.py" in readers  # the scan sees reads
    assert readers <= _IS_STAFF_READERS, sorted(readers - _IS_STAFF_READERS)


def test_the_partner_model_has_no_type_column():
    from workeasy360.commercial.partners.models import PartnerAccount

    names = {f.name for f in PartnerAccount._meta.get_fields()}
    assert not {"type", "partner_type", "kind", "tier"} & names


ALLOWED_API_ROOTS = frozenset(
    {
        "admin",
        "partner",
        "public",
        "referrals",
        "invitations",
        "auth",
        "orgs",
        "organizations",
        "integrations",
        "me",
        "health",
        "ready",
        "products",
        "ecosystem",
        ".well-known",
        "oauth",
    }
)


def test_the_api_exposes_no_product_domain_routes():
    """ADR-017 still holds after Phase 7: no CRM/Books/... route may appear,
    and every top-level API segment is one a person reviewed."""
    from django.urls import URLPattern, URLResolver, get_resolver

    roots: set[str] = set()

    def walk(patterns, prefix=""):
        for p in patterns:
            route = prefix + str(p.pattern)
            if isinstance(p, URLResolver):
                walk(p.url_patterns, route)
            elif isinstance(p, URLPattern):
                if route.startswith("api/v1/"):
                    roots.add(route.removeprefix("api/v1/").split("/")[0])

    walk(get_resolver().url_patterns)
    product_words = {
        "crm",
        "books",
        "inventory",
        "payroll",
        "hr",
        "commerce",
        "invoices",
        "contacts",
        "leads",
    }
    assert not roots & product_words, roots & product_words
    unreviewed = {
        r for r in roots if r and r not in ALLOWED_API_ROOTS and not r.startswith("<")
    }
    assert not unreviewed, f"new top-level API segments need review: {sorted(unreviewed)}"


def test_the_frontend_contains_no_product_screens():
    """The frontend is the platform's shell, not a product."""
    app = FRONTEND / "app"
    top = {p.name for p in app.iterdir() if p.is_dir()}
    assert top <= {
        "admin",
        "partner",
        "login",
        "onboarding",
        "organizations",
        "o",
        # Phase 8 extension: the public invitation acceptance page, and the
        # development-only stand-in for a payment provider's hosted checkout.
        "invitations",
        "billing",
        # Phase 9: the public website (a route group: /, /products/<slug>,
        # /pricing, /services, /contact - every product page is data from the
        # catalog, never a screen of its own), the self-service account pages
        # and /dashboard, which only decides where a signed-in person lands.
        "(site)",
        "signup",
        "verify-email",
        "forgot-password",
        "reset-password",
        "dashboard",
        # Phase 10e: the public page a customer's owner opens from the emailed
        # link to activate the account a partner set up (debt D-020).
        "owner-invitations",
        # ADR-043: /auth/continue, where each portal host redeems the central
        # sign-in's one-time hand-off for its own session. Not a product screen.
        "auth",
        # ADR-044: the access chooser (/product-access) and the RP-initiated
        # sign-out confirmation (/sign-out) - generic pages that name whichever
        # registered product asked, from the registry - and /settings/sign-in,
        # the person's own sign-in preferences outside any organization.
        "product-access",
        "sign-out",
        "settings",
    }, top
    assert {p.name for p in (app / "auth").iterdir()} == {"continue"}
    site = {p.name for p in (app / "(site)").iterdir() if p.is_dir()}
    assert site <= {"products", "pricing", "services", "contact"}, site
    # Phase 11: /products (the store index - four cards, server data only)
    # joins the existing /products/<slug> detail page.
    assert {p.name for p in (app / "(site)" / "products").iterdir()} == {
        "[slug]",
        "page.tsx",
    }
    for product in ("crm", "books", "inventory", "payroll"):
        assert not list(app.rglob(product)), product


def test_the_exemptions_from_tenant_scoping_are_exactly_the_ones_D019_justifies():
    from workeasy360.platform.tenancy.checks import TENANT_SCOPE_EXEMPT

    phase7 = {
        label
        for label in TENANT_SCOPE_EXEMPT
        if label.split(".")[0] in {"partners", "commissions", "provisioning"}
    }
    assert phase7 == {
        "partners.PartnerOrganizationLink",
        "partners.PartnerOwnerInvite",
        "provisioning.CustomerSummary",
        "commissions.Referral",
        "commissions.CommissionAccrual",
    }, "adding a control-plane exemption needs an entry in the debt register"
    register = (REPO_ROOT / "docs" / "architecture" / "debt-register.md").read_text(
        encoding="utf-8"
    )
    for label in phase7:
        assert label in register, f"{label} is exempt but not in the debt register"


def test_no_frontend_source_writes_an_inline_style_from_server_data():
    """The Phase 7 screens use CSS modules and the CSSOM only (D-010 not widened)."""
    offenders = []
    for path in (FRONTEND / "features" / "controlplane").glob("*.tsx"):
        if path.name.endswith(".test.tsx"):
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"\bstyle=\{\{|dangerouslySetInnerHTML|\.innerHTML\s*=", text):
            offenders.append(path.name)
    assert not offenders, offenders

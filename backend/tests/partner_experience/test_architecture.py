"""Architecture pins for admin-created partners and the type-specific workspaces (ADR-047).

The brief froze the architecture: ONE PartnerAccount, ONE User model, ONE
authentication backend, ONE entitlement writer, ONE commission ledger - and no
Reseller / White-label / Affiliate model or login. These tests read the source
and the model registry, so a later change that quietly adds one, makes the
landing preference decide something, or lets a password reach a log fails here.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.phase10.test_10f_architecture import (
    COMMERCIAL,
    PACKAGE,
    _classes,
    _imports,
    _python_sources,
    _rel,
)

PARTNERS = COMMERCIAL / "partners"
CONTROLPLANE = COMMERCIAL / "controlplane"
ONBOARDING = CONTROLPLANE / "partner_onboarding.py"
FRONTEND_SRC = Path(__file__).resolve().parents[3] / "frontend" / "src"


# --- one of everything -------------------------------------------------------------


def test_there_is_still_no_partner_type_model_or_column():
    banned = re.compile(
        r"^(Reseller|WhiteLabel|Affiliate|Partner(Type|Kind|Class|Tier|Category))\w*$"
    )
    assert [n.name for _p, n in _classes(PACKAGE) if banned.match(n.name)] == []

    from workeasy360.commercial.partners.models import PartnerAccount

    columns = {f.name for f in PartnerAccount._meta.get_fields()}
    assert (
        not {
            "type",
            "kind",
            "partner_type",
            "category",
            "tier",
            "is_reseller",
            "is_white_label",
            "is_affiliate",
            "experience",
        }
        & columns
    )
    assert "default_experience" in columns


@pytest.mark.django_db
def test_one_user_model_and_one_authentication_backend():
    from django.apps import apps
    from django.conf import settings

    assert [
        m._meta.label
        for m in apps.get_models()
        if any(f.name == "password" for f in m._meta.get_fields())
    ] == ["identity.User"]
    assert list(settings.AUTHENTICATION_BACKENDS) == [
        "django.contrib.auth.backends.ModelBackend"
    ]


def test_the_partner_models_are_unchanged_by_this_work():
    from django.apps import apps

    names = {m.__name__ for m in apps.get_app_config("partners").get_models()}
    assert names == {
        "PartnerAccount",
        "PartnerCapabilityGrant",
        "PartnerMember",
        "PartnerOrganizationLink",
        "PartnerProductGrant",
        "PartnerBrand",
        "PartnerDomain",
        "PartnerOwnerInvite",
        "PartnerLogoUpload",
        "PartnerProfile",
        "PartnerContact",
        "PartnerHierarchyLink",
        "PartnerProductSetting",
        "PartnerApiKey",
        "PartnerTranslation",
        "PartnerNotificationRead",
        "PartnerNotificationState",
    }


def test_migration_0007_adds_one_column_and_one_check_and_rewrites_no_data():
    source = (PARTNERS / "migrations" / "0007_partner_default_experience.py").read_text(
        encoding="utf-8"
    )
    operations = re.findall(r"migrations\.(\w+)\(", source)
    assert operations == ["AddField", "AddConstraint"], operations
    assert "RunPython" not in source and "RunSQL" not in source


# --- the landing preference decides nothing ---------------------------------------------


def test_the_landing_preference_is_read_only_by_presentation_code():
    """``default_experience`` may be written by the creation and transition
    services and read to draw a landing - and by no module that decides what
    anyone may do, pays, prices or entitles."""
    allowed = {
        "commercial/partners/models.py",
        "commercial/partners/selectors.py",
        "commercial/partners/experience.py",
        "commercial/partners/capabilities.py",
        "commercial/controlplane/partner_console.py",
        "commercial/controlplane/reads.py",
        "commercial/controlplane/partner_onboarding.py",
        # The HTTP adapter that answers "what is the landing now" - no decision.
        "interfaces/api/v1/admin_views.py",
    }
    readers = {
        path.relative_to(PACKAGE).as_posix()
        for path in _python_sources()
        if "default_experience" in path.read_text(encoding="utf-8")
    }
    assert readers <= allowed, readers - allowed
    for decider in (
        "commercial/partners/resolver.py",
        "commercial/partners/permissions.py",
        "commercial/partners/access.py",
        "platform/authz/services.py",
        "commercial/commissions/accrual.py",
        "commercial/pricing/resolution.py",
    ):
        text = (PACKAGE / decider).read_text(encoding="utf-8")
        assert "default_experience" not in text and "workspaces" not in text, decider


def test_the_resolver_computes_permissions_from_grants_alone():
    tree = ast.parse((PARTNERS / "resolver.py").read_text(encoding="utf-8"))
    attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "default_experience" not in attributes
    assert "experience_labels" not in attributes and "resolve_landing" not in attributes


@pytest.mark.django_db
def test_changing_the_preference_changes_no_permission(partner_factory):
    """The same grants resolve to the same permissions whatever the preference."""
    from workeasy360.commercial.partners import capabilities, selectors
    from workeasy360.commercial.partners.models import PartnerAccount

    fx = partner_factory(*capabilities.EXPERIENCE_BUNDLES["affiliate"])
    before = selectors.partners_for_user(fx["user"].id)[0]["permissions"]
    for value in ("", "reseller", "white_label", "affiliate"):
        PartnerAccount.objects.filter(id=fx["partner"].id).update(
            default_experience=value
        )
        after = selectors.partners_for_user(fx["user"].id)[0]
        assert after["permissions"] == before
        assert [w["key"] for w in after["workspaces"]] == ["affiliate"]


# --- the onboarding command reuses and does not duplicate ---------------------------------


def test_onboarding_creates_the_user_only_through_the_identity_lifecycle():
    source = ONBOARDING.read_text(encoding="utf-8")
    assert "lifecycle.create_account(" in source
    for forbidden in (
        "set_password(",
        "make_password(",
        "create_user(",
        "User.objects",
        "django_login(",
        "auth.login(",
        "authenticate(",
    ):
        assert forbidden not in source, forbidden


def test_onboarding_writes_neither_entitlements_nor_the_commission_ledger_nor_prices_directly():
    imported = _imports(ONBOARDING)
    offending = [
        m
        for m in imported
        if re.search(
            r"entitlements\.services\.write|commissions\.models|commissions\.ledger|"
            r"pricing\.models|partners\.models|subscriptions|provisioning",
            m,
        )
    ]
    assert offending == [], offending
    # Add Partner creates the account only (ADR-048): prices, commission and
    # product grants are set afterwards in Complete Your Profile, through the
    # commands the staff screens use - never here.
    source = ONBOARDING.read_text(encoding="utf-8")
    for later in (
        "assign_partner_commission(",
        "set_partner_price(",
        "grant_product(",
        "set_partner_status(",
    ):
        assert later not in source, later


def test_the_password_is_read_in_one_function_and_reaches_no_log_audit_or_metric():
    source = ONBOARDING.read_text(encoding="utf-8")
    tree = ast.parse(source)
    secret_names = {"password", "confirm", "_password"}

    def mentions_secret(node: ast.AST) -> bool:
        for child in ast.walk(node):
            if isinstance(child, ast.Attribute) and child.attr in secret_names:
                return True
            if isinstance(child, ast.Name) and child.id in secret_names:
                return True
            if isinstance(child, ast.Constant) and child.value in {
                "password",
                "password_confirm",
            }:
                return True
        return False

    sinks = {
        "record",
        "increment",
        "info",
        "warning",
        "error",
        "debug",
        "exception",
        "print",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", "")
            )
            if target in sinks:
                assert not any(
                    mentions_secret(a)
                    for a in [*node.args, *[k.value for k in node.keywords]]
                ), f"line {node.lineno}: {target}() is handed the password"
    # And exactly one function reads the typed value off the administrator.
    readers = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        if any(
            isinstance(c, ast.Attribute)
            and c.attr == "password"
            and isinstance(c.ctx, ast.Load)
            for c in ast.walk(fn)
        ):
            readers.append(fn.name)
    assert set(readers) <= {"_new_account", "_check_password"}, readers


def test_no_new_route_is_a_login():
    from django.urls import get_resolver

    names: list[str] = []

    def walk(patterns):
        for p in patterns:
            if hasattr(p, "url_patterns"):
                walk(p.url_patterns)
            elif p.name:
                names.append(p.name)

    walk(get_resolver().url_patterns)
    new = [
        n
        for n in names
        if n.startswith(
            (
                "admin-partner-credentials",
                "admin-partner-create",
                "admin-partner-experience",
                "partner-affiliate",
                "partner-reports",
                "partner-white-label-status",
            )
        )
    ]
    assert len(new) >= 6
    assert not [
        n for n in new if re.search(r"(^|[-_])(login|signin|sign-in|sign_in)$", n)
    ]


def test_the_new_staff_routes_are_all_staff_views():
    """Every ``/admin/`` handler added here is a ``staff_view``: staff, a verified
    second factor, then an explicit permission inside the service."""
    text = (PACKAGE / "interfaces" / "api" / "v1" / "admin_views.py").read_text(
        encoding="utf-8"
    )
    for name in (
        "partner_credentials_check_view",
        "partner_create_view",
        "partner_experience_view",
        "partner_member_password_reset_view",
    ):
        # ``staff_view`` is outermost; the redaction decorator below may sit beneath it.
        assert re.search(
            rf"@staff_view\(\[\"POST\"\]\)\n(?:@\w+(?:\(\))?\n)*def {name}\(", text
        ), name
    # The two views that carry the typed password hide their locals (and those of
    # everything they call) from Django's error reports.
    for name in ("partner_credentials_check_view", "partner_create_view"):
        assert re.search(rf"@sensitive_variables\(\)\ndef {name}\(", text), name


def test_the_workspace_reads_are_partner_views_and_authorize_in_the_service():
    views = (PACKAGE / "interfaces" / "api" / "v1" / "partner_panel_views.py").read_text(
        encoding="utf-8"
    )
    for name in ("affiliate_overview_view", "reports_view", "white_label_status_view"):
        assert re.search(rf"@partner_view\(\[\"GET\"\]\)\ndef {name}\(", views), name
    service = (CONTROLPLANE / "partner_workspaces.py").read_text(encoding="utf-8")
    assert service.count("authz.require(") >= 3
    assert "write" not in _rel(CONTROLPLANE / "partner_workspaces.py")
    # Reads only: no model is written from this module.
    for forbidden in (".save(", ".create(", ".update(", ".delete(", "bulk_create"):
        assert forbidden not in service, forbidden


# --- the screens: nothing stored in the browser, routes stay in the portal --------------

PANEL = FRONTEND_SRC / "features" / "partnerpanel"
ADD_PARTNER = FRONTEND_SRC / "features" / "controlplane" / "addpartner"

#: The files this work added to the browser: the Add Partner screen and the three
#: workspaces. They hold a typed password (the first) or a person's session (the
#: rest); none may put either where a script on the page, or the next visitor to
#: the machine, could read it.
SCREEN_FILES = sorted(
    [
        *ADD_PARTNER.glob("*.ts*"),
        *(FRONTEND_SRC / "features" / "controlplane" / "profile").glob("*.ts*"),
        FRONTEND_SRC / "features" / "controlplane" / "AddPartner.tsx",
        FRONTEND_SRC / "features" / "controlplane" / "ExperiencePanel.tsx",
        PANEL / "experiences.ts",
        PANEL / "WorkspaceShell.tsx",
        PANEL / "AffiliateScreens.tsx",
        PANEL / "Reports.tsx",
        PANEL / "WhiteLabelDashboard.tsx",
        PANEL / "workspaceKit.tsx",
        PANEL / "workspacePages.tsx",
    ]
)


def _code(path: Path) -> str:
    """The file without its comments (a comment may say what the code never does)."""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)(^|\s)//[^\n]*", r"\1", text)


def test_the_screens_exist():
    assert len(SCREEN_FILES) >= 12
    assert all(p.is_file() for p in SCREEN_FILES), [
        str(p) for p in SCREEN_FILES if not p.is_file()
    ]


def test_the_add_partner_screen_and_the_workspaces_store_nothing_in_the_browser():
    storage = re.compile(
        r"localStorage|sessionStorage|indexedDB|document\.cookie|cookieStore|caches\.open"
    )
    offenders = [str(p.name) for p in SCREEN_FILES if storage.search(_code(p))]
    assert offenders == [], offenders


def test_every_password_input_is_the_one_new_password_field():
    field = _code(ADD_PARTNER / "PasswordField.tsx")
    assert field.count('autoComplete="new-password"') == 1
    assert '"password"' in field and "visible ?" in field  # hidden until Show is chosen
    for path in ADD_PARTNER.glob("*.ts*"):
        code = _code(path)
        assert 'type="password"' not in code, path.name
        assert 'autoComplete="current-password"' not in code, path.name
    # A password is never an address or a stored draft.
    for path in SCREEN_FILES:
        code = _code(path)
        assert not re.search(
            r"(searchParams|URLSearchParams|router\.(push|replace))\([^)]*password", code
        ), path.name


def test_the_password_is_sent_only_to_create_an_account():
    """The form's one request carries the password only when an account is to be
    created; linking an existing account sends none (ADR-048)."""
    model = _code(ADD_PARTNER / "model.ts")
    assert re.search(
        r'd\.mode === "create" \? \{ password: d\.password, password_confirm: d\.confirm \} : \{\}',
        model,
    )
    client = _code(FRONTEND_SRC / "lib" / "api" / "partnerConsole.ts")
    assert "create-review" not in client


def test_the_workspaces_live_inside_the_portal_prefix_and_nowhere_else():
    app = FRONTEND_SRC / "app"
    for slug in ("reseller", "white-label", "affiliate"):
        assert (app / "partner" / slug / "[[...slug]]" / "page.tsx").is_file(), slug
        # The short address is a redirect, never a second route tree.
        assert not (app / slug).exists(), slug
    # Nothing in the app tree is a login for a partner type.
    types = {"reseller", "affiliate", "white-label"}
    assert not [
        p
        for p in app.rglob("page.tsx")
        if types & set(p.parts) and {"login", "sign-in", "signin"} & set(p.parts)
    ]


def test_the_workspace_data_file_is_pure_data():
    """``experiences.ts`` is read by the shell, the tests and ``next.config.ts``; an
    import would make the build config depend on React and the panel."""
    source = (PANEL / "experiences.ts").read_text(encoding="utf-8")
    assert not re.findall(r"(?m)^import\b", source)
    assert "fetch(" not in source and "apiFetch" not in source


def test_the_decision_is_recorded():
    adr = (
        Path(__file__).resolve().parents[3]
        / "docs"
        / "architecture"
        / "adrs"
        / "ADR-047-admin-created-partners-and-type-workspaces.md"
    )
    text = adr.read_text(encoding="utf-8")
    for needle in (
        "EXPERIENCE_BUNDLES",
        "default_experience",
        "partners.0007",
        "link_existing",
        "Alternatives",
        "partner.workeasy360.com",
        "No forced password change",
    ):
        assert needle in text, needle

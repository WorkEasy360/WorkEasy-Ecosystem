"""Partner Control Center + sign-in modes: nothing was built twice.

The brief forbade a partner type, Reseller/White-label/Affiliate models, an
administrator user model and a second authentication system. Phase 10's
architecture tests already pin the partner side for the whole tree
(``tests/phase10/test_10f_architecture.py``: no ``partner_type``, no class
named for a kind of partner, no classifying column, the frozen partner model
set). These add what is new here: the sign-in modes.

They read the source, the URL table, the settings and the model registry, so a
later change that adds a second login, an admin user table or a role the
browser can choose fails here instead of in review.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
PACKAGE = BACKEND / "workeasy360"
FRONTEND_SRC = BACKEND.parent / "frontend" / "src"


def _sources():
    for path in sorted(PACKAGE.rglob("*.py")):
        if {"migrations", "__pycache__", "tests"} & set(path.parts):
            continue
        yield path


def _classes():
    for path in _sources():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef):
                yield path, node


# --- no administrator user, no second authentication ------------------------------------


def test_no_administrator_user_model_or_class():
    pattern = re.compile(
        r"^(Admin|Administrator|Staff|SuperAdmin|Superadmin)"
        r"(User|Account|Login|Credential|Identity|Profile|Session)$"
    )
    hits = [
        f"{p.relative_to(BACKEND)}: {n.name}"
        for p, n in _classes()
        if pattern.match(n.name)
    ]
    assert not hits, hits


def test_people_are_identity_users_only():
    from django.apps import apps

    user_like = [
        f"{m._meta.label}"
        for m in apps.get_models()
        if any(f.name == "password" for f in m._meta.get_fields())
    ]
    assert user_like == ["identity.User"], user_like


def test_one_authentication_backend_and_one_login_route():
    from django.conf import settings
    from django.urls import get_resolver

    assert list(settings.AUTHENTICATION_BACKENDS) == [
        "django.contrib.auth.backends.ModelBackend"
    ]

    names: list[str] = []

    def walk(patterns):
        for p in patterns:
            if hasattr(p, "url_patterns"):
                walk(p.url_patterns)
            elif p.name:
                names.append(p.name)

    walk(get_resolver().url_patterns)
    # A route *named for* signing in (``...-login``, ``...-signin``), not one
    # that lists past sign-ins (``console-sign-ins``, the security history).
    endpoint = re.compile(r"(^|[-_])(login|signin|sign-in|sign_in)$")
    logins = sorted(n for n in names if endpoint.search(n))
    assert logins == ["auth-login"], logins
    # And the one function that establishes a session is called only by the
    # login view and by an accepted invitation (both through establish_session).
    callers = {
        p.relative_to(PACKAGE).as_posix()
        for p in _sources()
        if re.search(r"\bdjango_login\(|\bauth\.login\(", p.read_text(encoding="utf-8"))
    }
    assert callers <= {"interfaces/api/v1/auth_views.py"}, callers


def test_the_login_endpoint_reads_only_an_email_and_a_password():
    """Nothing a browser sends at sign-in can name a mode, a role or a console."""
    source = (PACKAGE / "interfaces" / "api" / "v1" / "auth_views.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    login = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "login_view"
    )
    keys = {
        node.args[0].value
        for node in ast.walk(login)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert keys == {"email", "password"}, keys
    assert "request.GET" not in ast.get_source_segment(source, login)
    assert "COOKIES" not in ast.get_source_segment(source, login)


def test_the_console_gate_reads_no_superuser_flag_and_no_request_input():
    path = PACKAGE / "interfaces" / "api" / "v1" / "control_plane_http.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    gate = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "staff_context"
    )
    attributes = {n.attr for n in ast.walk(gate) if isinstance(n, ast.Attribute)}
    assert "is_superuser" not in attributes
    assert not attributes & {"GET", "POST", "COOKIES", "headers", "META"}, attributes


def test_is_superuser_is_read_only_by_the_identity_model():
    readers = set()
    for path in _sources():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr == "is_superuser":
                readers.add(path.relative_to(PACKAGE).as_posix())
    assert readers <= {"platform/identity/models.py"}, readers


def test_no_new_tables_for_partners_staff_or_identity():
    """The Control Center and the sign-in modes are screens over existing
    tables: the newest migration of each owning app is the one that was
    there before them. (Partners moved on to 0004 when the complete reseller and
    white-label setup added its two tables - tests/partner_setup pins those.
    Identity moved on to 0005/0006 when the multi-portal split added the sign-in
    hand-off table and the session's sign-in id - ADR-043, tests/portals - and to
    0007 for universal sign-in, which adds no table: an account-event kind and a
    wider reason column - ADR-044.)"""
    expected = {
        "commercial/partners": "0005_partner_panel_hierarchy_and_product_settings.py",
        "commercial/commissions": "0003_payout_list_keyset_index.py",
        "platform/authz": "0009_billing_manage_not_dangerous.py",
        "platform/identity": "0008_password_change_required.py",
    }
    for app, newest in expected.items():
        found = sorted(p.name for p in (PACKAGE / app / "migrations").glob("0*.py"))[-1]
        assert found == newest, (app, found)


# --- the frontend chooses a destination, never a privilege ------------------------------------


def _read(relative: str) -> str:
    return (FRONTEND_SRC / relative).read_text(encoding="utf-8")


SIGN_IN_FILES = (
    "features/auth/LoginScreen.tsx",
    "features/auth/LoginForm.tsx",
    "features/auth/signInMode.ts",
    "features/controlplane/ControlShell.tsx",
    "app/dashboard/page.tsx",
    "lib/api/auth.ts",
)


def test_the_sign_in_mode_is_never_stored_in_the_browser():
    for relative in SIGN_IN_FILES:
        text = _read(relative)
        for store in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
            assert store not in text, (relative, store)


def test_the_login_request_sends_only_an_email_and_a_password():
    source = _read("lib/api/auth.ts")
    body = re.search(r"export function login\(.*?\n\}", source, re.S)
    assert body, "login() not found"
    assert "JSON.stringify({ email, password })" in body.group(0)
    assert "mode" not in body.group(0) and "role" not in body.group(0)


def test_the_admin_shell_decides_from_the_server_answer():
    """The console opens on ``/api/v1/admin/session``'s answer - not on the
    mode in the address bar, and not on the session's ``consoles`` hint."""
    shell = _read("features/controlplane/ControlShell.tsx")
    admin = shell.split("export function AdminShell", 1)[1].split(
        "// --- Partner portal", 1
    )[0]
    assert "adminApi" in admin and ".session()" in admin
    assert "consoles" not in admin.split("adminApi", 1)[0]
    assert "useSearchParams" not in admin

"""Fitness tests for universal sign-in (ADR-044) - the brief's "ARCHITECTURE
TESTS", each a structural property of the source rather than a scenario."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend" / "workeasy360"
FRONTEND = ROOT / "frontend" / "src"
PRODUCTS = json.loads(
    (ROOT / "infra" / "ecosystem" / "products.json").read_text(encoding="utf-8")
)["products"]

#: Every way a product could be named in a branch: its code, its slug, its
#: host label, and the test-only future product.
PRODUCT_NAMES = sorted(
    {p["code"] for p in PRODUCTS}
    | {p["slug"] for p in PRODUCTS}
    | {p["integration"]["launch_url"].split("//")[1].split(".")[0] for p in PRODUCTS}
    | {"test_future_product", "test-future-product"}
)

#: The sign-in machinery. Nothing here may name a product.
SIGN_IN_SOURCES = [
    BACKEND / "contracts" / "values" / "sign_in.py",
    BACKEND / "contracts" / "values" / "manifest.py",
    BACKEND / "platform" / "ecosystem" / "authorization.py",
    BACKEND / "platform" / "ecosystem" / "chooser.py",
    BACKEND / "platform" / "ecosystem" / "sign_in.py",
    BACKEND / "platform" / "ecosystem" / "selectors.py",
    BACKEND / "platform" / "ecosystem" / "tokens.py",
    BACKEND / "platform" / "ecosystem" / "verification.py",
    BACKEND / "platform" / "launcher" / "access.py",
    BACKEND / "platform" / "launcher" / "services.py",
    BACKEND / "interfaces" / "api" / "v1" / "ecosystem_views.py",
    BACKEND / "interfaces" / "api" / "v1" / "launcher_views.py",
    BACKEND / "interfaces" / "api" / "v1" / "account_views.py",
    BACKEND / "commercial" / "controlplane" / "products.py",
]
FRONTEND_SIGN_IN = [
    *sorted((FRONTEND / "features" / "signin").glob("*.ts*")),
    FRONTEND / "features" / "launcher" / "AppCard.tsx",
    FRONTEND / "features" / "launcher" / "LaunchButton.tsx",
    FRONTEND / "features" / "controlplane" / "IntegrationAuthentication.tsx",
    FRONTEND / "lib" / "api" / "signIn.ts",
]


def _strings(path: Path) -> list[str]:
    """Every string literal that could steer a branch - docstrings and other
    bare-statement strings (prose, examples) excluded."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    prose = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in prose
    ]


@pytest.mark.parametrize("path", SIGN_IN_SOURCES, ids=lambda p: p.name)
def test_no_product_specific_authentication_branch_in_the_backend(path):
    named = [
        s
        for s in _strings(path)
        for name in PRODUCT_NAMES
        if re.search(rf"\b{re.escape(name)}\b", s)
    ]
    assert not named, f"{path.name} names a product: {named}"


@pytest.mark.parametrize(
    "path",
    [
        p
        for p in FRONTEND_SIGN_IN
        if not p.name.endswith(".test.tsx") and not p.name.endswith(".test.ts")
    ],
    ids=lambda p: p.name,
)
def test_no_product_specific_authentication_branch_in_the_frontend(path):
    text = path.read_text(encoding="utf-8")
    named = [
        name
        for name in PRODUCT_NAMES
        if re.search(rf"['\"`]{re.escape(name)}['\"`]", text)
    ]
    assert not named, f"{path.name} names a product: {named}"


def test_passwords_never_cross_the_product_boundary_from_the_backend():
    """No runtime source sends anything to a product's host: the only
    outbound HTTP is the egress client, and no caller of it handles a
    password. Products receive a code, then exchange it themselves."""
    product_hosts = {
        p["integration"]["launch_url"].split("//")[1].rstrip("/") for p in PRODUCTS
    }
    offenders = []
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if "/tests/" in rel or "/migrations/" in rel:
            continue
        text = path.read_text(encoding="utf-8")
        for host in product_hosts:
            if host in text:
                offenders.append(f"{rel} names {host}")
        tree = ast.parse(text)
        sends = any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr in {"post", "put", "request", "urlopen"}
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id in {"requests", "httpx", "urllib", "egress", "transport"}
            for n in ast.walk(tree)
        )
        if sends and re.search(r"\bpassword\b", text, re.IGNORECASE):
            offenders.append(f"{rel} sends HTTP and handles a password")
    assert not offenders, offenders


def test_passwords_never_cross_the_product_boundary_from_the_frontend():
    """The browser sends a password only to Workeasy360's own sign-in, account
    and invitation endpoints - same-origin ``/api/v1/...`` - and never calls
    another origin at all."""
    allowed = {
        "/api/v1/auth/login",
        "/api/v1/auth/password",
        "/api/v1/public/signup",
        "/api/v1/public/password/reset",
        "/api/v1/public/invitations/accept",
        "/api/v1/public/owner-invitations/accept",
        "/api/v1/invitations",
    }
    offenders = []
    for path in FRONTEND.rglob("*.ts*"):
        if ".test." in path.name:
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"fetch\(\s*[`'\"](https?://[^`'\"]+)", text):
            offenders.append(f"{path.name}: fetch to {match.group(1)}")
        if "password" not in text.lower():
            continue
        for match in re.finditer(
            r"apiFetch(?:<[^>]*>)?\(\s*[`'\"]([^`'\"]+)[`'\"][^)]*password",
            text,
            re.DOTALL,
        ):
            target = match.group(1).split("?")[0]
            if not any(target.startswith(a) for a in allowed):
                offenders.append(f"{path.name}: password to {target}")
    assert not offenders, offenders


def test_there_is_one_identity_provider():
    """One issuer setting, one authorization endpoint, one place tokens are
    signed - and only the token endpoint mints an ID token."""
    from workeasy360.interfaces.api.v1 import urls
    from workeasy360.platform.ecosystem import discovery

    authorize_routes = [
        p for p in urls.urlpatterns if str(p.pattern).endswith("oauth/authorize")
    ]
    assert len(authorize_routes) == 1
    assert discovery.PATHS["authorization"] == "/api/v1/integrations/oauth/authorize"
    minters = []
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if "/tests/" in rel:
            continue
        if "issue_id_token(" in path.read_text(encoding="utf-8") and not rel.endswith(
            "ecosystem/tokens.py"
        ):
            minters.append(rel)
    assert minters == ["interfaces/api/v1/ecosystem_views.py"]


def test_the_preference_is_read_only_after_every_access_check():
    """In ``authorize``, entitlement and seat are decided before the
    preference is read: a preference can add a question, never remove a
    refusal."""
    tree = ast.parse(
        (BACKEND / "interfaces" / "api" / "v1" / "ecosystem_views.py").read_text(
            encoding="utf-8"
        )
    )
    authorize = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "authorize"
    )
    order = [
        n.func.attr
        for n in ast.walk(authorize)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr
        in {
            "resolve_subject",
            "require_entitlement",
            "require_seat",
            "decision_at_authorize",
            "issue_code",
        }
    ]
    lines = {
        n.func.attr: n.lineno
        for n in ast.walk(authorize)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert set(order) >= {
        "resolve_subject",
        "require_entitlement",
        "require_seat",
        "decision_at_authorize",
        "issue_code",
    }
    assert (
        lines["resolve_subject"]
        < lines["require_entitlement"]
        < lines["require_seat"]
        < lines["decision_at_authorize"]
        < lines["issue_code"]
    )


def test_the_hostname_grants_nothing_in_the_sign_in_machinery():
    for path in SIGN_IN_SOURCES:
        text = path.read_text(encoding="utf-8")
        assert "get_host(" not in text and "HTTP_HOST" not in text, path.name


def test_a_preference_is_written_only_on_the_signed_in_persons_behalf():
    """``set_preferences`` / ``remember_choice`` / ``reset_preferences`` are
    called only by the account endpoint (the session's user) and the access
    chooser (the session's user, bound to the parked request)."""
    callers = set()
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if "/tests/" in rel or rel.endswith("ecosystem/sign_in.py"):
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"\b(set_preferences|remember_choice|reset_preferences)\(", text):
            callers.add(rel)
    assert callers == {
        "interfaces/api/v1/account_views.py",
        "platform/ecosystem/chooser.py",
    }
    account = (BACKEND / "interfaces" / "api" / "v1" / "account_views.py").read_text(
        encoding="utf-8"
    )
    assert (
        "user_id="
        not in account.split("def sign_in_preferences_view", 1)[1].split(
            "def _sign_in_preferences", 1
        )[0]
    )


def test_every_destination_comes_from_the_registry_and_is_exact():
    """The product sign-in page, the post-logout URI and the error redirect
    are each a registered value matched exactly - never a request field."""
    chooser = (BACKEND / "platform" / "ecosystem" / "chooser.py").read_text(
        encoding="utf-8"
    )
    assert "manifest_obj.product_login_url" in chooser
    assert "permits_post_logout_redirect(uri)" in chooser
    manifest = (BACKEND / "contracts" / "values" / "manifest.py").read_text(
        encoding="utf-8"
    )
    assert "return candidate in self.post_logout_redirect_uris" in manifest
    assert "return candidate in self.redirect_uris" in manifest
    views = (BACKEND / "interfaces" / "api" / "v1" / "ecosystem_views.py").read_text(
        encoding="utf-8"
    )
    choose = views.split("def pending_choose_view", 1)[1].split("def _sign_out", 1)[0]
    assert 'body.get("redirect' not in choose and 'GET.get("next' not in choose


def test_products_are_oidc_clients_and_future_ones_use_the_registry():
    """The launcher and the identity provider read sign-in facts only from
    registered integrations - ``ProductIntegration`` rows and their manifests -
    so a product registered tomorrow is read exactly like today's."""
    sign_in = (BACKEND / "platform" / "ecosystem" / "sign_in.py").read_text(
        encoding="utf-8"
    )
    assert "ProductIntegration.objects" in sign_in and "from_stored_manifest" in sign_in
    launcher = (BACKEND / "platform" / "launcher" / "services.py").read_text(
        encoding="utf-8"
    )
    assert "sign_in.product_sign_in(" in launcher

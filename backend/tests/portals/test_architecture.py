"""What the multi-portal split must not have created (brief Part 38).

One identity model, one authentication backend, one trial system, one
entitlement writer, and no authority derived from the host or from the
sign-in page's mode.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.apps import apps
from django.conf import settings

pytestmark = pytest.mark.architecture

BACKEND = Path(__file__).resolve().parents[2] / "workeasy360"
FRONTEND = Path(__file__).resolve().parents[3] / "frontend" / "src"


def _source(root: Path, *suffixes: str):
    for path in root.rglob("*"):
        if path.suffix in suffixes and "node_modules" not in path.parts:
            yield path, path.read_text(encoding="utf-8")


def test_there_is_one_identity_model():
    from django.contrib.auth.base_user import AbstractBaseUser

    users = [m for m in apps.get_models() if issubclass(m, AbstractBaseUser)]
    assert [f"{m._meta.app_label}.{m.__name__}" for m in users] == ["identity.User"]
    assert settings.AUTH_USER_MODEL == "identity.User"
    names = {m.__name__.lower() for m in apps.get_models()}
    assert not names & {"adminuser", "partneruser", "customeruser", "portaluser"}


def test_there_is_one_authentication_backend():
    assert settings.AUTHENTICATION_BACKENDS == [
        "django.contrib.auth.backends.ModelBackend"
    ] or list(settings.AUTHENTICATION_BACKENDS) == [
        "django.contrib.auth.backends.ModelBackend"
    ]


def test_there_is_one_session_engine_and_one_sign_in_endpoint():
    from django.urls import get_resolver

    assert settings.SESSION_ENGINE == "workeasy360.platform.identity.sessions"
    logins = [
        str(p.pattern)
        for p in get_resolver().url_patterns[0].url_patterns  # type: ignore[attr-defined]
        if "login" in str(getattr(p, "pattern", ""))
    ]
    assert logins == [] or all("auth/login" in x for x in logins)


def test_there_is_one_product_trial_system():
    trials = sorted(
        f"{m._meta.app_label}.{m.__name__}"
        for m in apps.get_models()
        if "trial" in m.__name__.lower()
    )
    assert trials == ["catalog.ProductTrialConfig", "subscriptions.ProductTrial"]


def test_the_portal_code_writes_no_entitlement_and_reads_no_product_database():
    """The new portal modules touch identity only: no entitlement row is
    written outside provisioning, and nothing reaches a product's data."""
    new_modules = [
        BACKEND / "contracts" / "values" / "portals.py",
        BACKEND / "platform" / "identity" / "handoff.py",
        BACKEND / "platform" / "identity" / "portals.py",
        BACKEND / "interfaces" / "api" / "portal_surface.py",
    ]
    for path in new_modules:
        text = path.read_text(encoding="utf-8")
        assert "entitlements" not in text, path
        assert "requests" not in re.findall(r"^import (\w+)", text, re.M), path
        assert "httpx" not in text and "urllib.request" not in text, path


def test_the_hand_off_decides_eligibility_from_identity_not_from_the_mode():
    """``mode`` only picks which portal to try; the refusal comes from
    ``_consoles`` (live StaffGrants, partner membership)."""
    text = (BACKEND / "interfaces" / "api" / "v1" / "auth_views.py").read_text(
        encoding="utf-8"
    )
    view = text[text.index("def handoff_view") : text.index("def handoff_redeem_view")]
    assert "consoles = _consoles(user)" in view
    assert 'consoles["staff"]' in view and 'consoles["partner"]' in view


def test_the_frontend_never_derives_authority_from_the_host_or_the_mode():
    """The host-routing table and the sign-in mode are display only: neither
    is sent anywhere as a privilege, and no permission check reads them."""
    offenders = []
    for path, text in _source(FRONTEND, ".ts", ".tsx"):
        if ".test." in path.name:
            continue
        # A permission decision made from the portal or host in the client.
        if re.search(r"(permissions|isStaff|is_staff)\s*=.*(portalFor|host)", text):
            offenders.append(str(path))
        # The mode or portal stored where a later request could pick it up.
        if re.search(
            r"(localStorage|sessionStorage|document\.cookie)\s*.*\b(mode|portal)\b", text
        ):
            offenders.append(str(path))
    assert offenders == []


def test_no_shared_parent_domain_cookie_is_configured():
    """Host-only cookies everywhere: no ``Domain`` attribute, in any settings."""
    for name in ("development", "test", "production", "staging", "base"):
        text = (BACKEND.parent / "config" / "settings" / f"{name}.py").read_text(
            encoding="utf-8"
        )
        for setting in ("SESSION_COOKIE_DOMAIN", "CSRF_COOKIE_DOMAIN"):
            for match in re.finditer(rf"^{setting}\s*=\s*(.+)$", text, re.M):
                assert match.group(1).strip() == "None", (name, setting)
        assert '.workeasy360.com"' not in text.replace("W360_PLATFORM_DOMAINS", "")

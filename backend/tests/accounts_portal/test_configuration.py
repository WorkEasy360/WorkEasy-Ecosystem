"""The move's configuration (ADR-067): what production accepts and refuses,
and that every stage ``make_env.py accounts`` writes is one it accepts.

Production refuses to start on a mix of two stages, so the tool and the
settings checks must agree exactly - proven here by feeding the tool's output
to the real production settings module.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path

import pytest

from config.env import Env, ImproperlyConfigured
from tests.accounts_portal.conftest import STAGES
from tests.security.test_security_baseline import _production_env

REPO = Path(__file__).resolve().parents[3]
MAKE_ENV = REPO / "deploy" / "single-host" / "make_env.py"

ADMIN = "https://admin.workeasy360.com"
PARTNER = "https://partner.workeasy360.com"
APP = "https://app.workeasy360.com"
STORE = "https://store.workeasy360.com"
ACCOUNTS = "https://accounts.workeasy360.com"
AUTH = "https://auth.workeasy360.com"


def _load_make_env():
    spec = importlib.util.spec_from_file_location("w360_make_env", MAKE_ENV)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _import_production(monkeypatch, env: dict[str, str]):
    for key in list(os.environ):
        if key.startswith("W360_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings.production")
    monkeypatch.setattr(Env, "load_dotenv", lambda self, path: None)
    sys.modules.pop("config.settings.production", None)
    sys.modules.pop("config.settings.base", None)
    try:
        return importlib.import_module("config.settings.production")
    finally:
        sys.modules.pop("config.settings.production", None)
        sys.modules.pop("config.settings.base", None)


def _live_env_file(tmp_path: Path) -> Path:
    """The live deployment's shape today: five portals plus the Store's host,
    the central sign-in on auth. (every other value as production needs it)."""
    values = _production_env(
        W360_ALLOWED_HOSTS=(
            "admin.workeasy360.com,partner.workeasy360.com,app.workeasy360.com,"
            "auth.workeasy360.com,api.workeasy360.com,store.workeasy360.com"
        ),
        W360_CSRF_TRUSTED_ORIGINS=f"{ADMIN},{PARTNER},{APP},{AUTH},{STORE}",
        W360_STORE_ORIGIN=STORE,
    )
    env_file = tmp_path / "production.env"
    env_file.write_text(
        "".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8"
    )
    return env_file


def _read(env_file: Path) -> dict[str, str]:
    return {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in env_file.read_text(encoding="utf-8").splitlines()
        if "=" in line
    }


# --- every stage the tool writes boots -----------------------------------------


@pytest.mark.parametrize("stage", list(STAGES))
def test_every_stage_make_env_writes_is_accepted_by_production(
    stage, tmp_path, monkeypatch
):
    make_env = _load_make_env()
    env_file = _live_env_file(tmp_path)
    make_env.accounts(env_file, stage=stage)
    values = _read(env_file)

    current, previous, issuer, transition = STAGES[stage]
    assert values["W360_AUTH_ORIGIN"] == current
    assert values.get("W360_AUTH_LEGACY_ORIGIN", "") == previous
    assert values.get("W360_AUTH_LEGACY_EDGE", "off") == ("on" if previous else "off")
    assert values["W360_OIDC_ISSUER"] == issuer
    assert values.get("W360_OIDC_TRANSITION_ISSUERS", "") == transition
    hosts = set(values["W360_ALLOWED_HOSTS"].split(","))
    csrf = set(values["W360_CSRF_TRUSTED_ORIGINS"].split(","))
    # The Store's host and the other portals are left exactly as they were.
    assert {
        "admin.workeasy360.com",
        "store.workeasy360.com",
        "api.workeasy360.com",
    } <= hosts
    assert {ADMIN, PARTNER, APP, STORE} <= csrf
    # The current sign-in is trusted for writes; its previous name never is.
    assert current in csrf and (not previous or previous not in csrf)
    assert (previous.removeprefix("https://") in hosts) == bool(previous)

    module = _import_production(monkeypatch, values)
    assert module.OIDC_ISSUER == issuer
    assert module.W360_AUTH_LEGACY_ORIGIN == previous
    assert module.OIDC_TRANSITION_ISSUERS == ([transition] if transition else [])


def test_each_stage_is_a_complete_state_so_going_back_is_the_rollback(tmp_path):
    make_env = _load_make_env()
    env_file = _live_env_file(tmp_path)
    before = _read(env_file)
    for stage in ("a", "b", "c", "d", "e", "d", "c", "b", "a", "off"):
        make_env.accounts(env_file, stage=stage)
    after = _read(env_file)
    for key in (
        "W360_AUTH_ORIGIN",
        "W360_OIDC_ISSUER",
        "W360_STORE_ORIGIN",
        "W360_SECRET_KEY",
        "W360_DB_APP_PASSWORD",
    ):
        assert after[key] == before[key], key
    for key in ("W360_ALLOWED_HOSTS", "W360_CSRF_TRUSTED_ORIGINS"):
        assert set(after[key].split(",")) == set(before[key].split(",")), key
    for key in (
        "W360_AUTH_LEGACY_ORIGIN",
        "W360_AUTH_LEGACY_EDGE",
        "W360_OIDC_TRANSITION_ISSUERS",
    ):
        assert key not in after, key


def test_running_a_stage_twice_changes_nothing(tmp_path):
    make_env = _load_make_env()
    env_file = _live_env_file(tmp_path)
    make_env.accounts(env_file, stage="b")
    once = env_file.read_text(encoding="utf-8")
    make_env.accounts(env_file, stage="b")
    assert env_file.read_text(encoding="utf-8") == once


def test_a_new_deployment_is_rendered_with_accounts(tmp_path):
    make_env = _load_make_env()
    make_env.render(
        tmp_path / "env", "2026.11.16-test", "workeasy360.com", "no-reply@workeasy360.com"
    )
    values = _read(tmp_path / "env" / "production.env")
    assert values["W360_AUTH_ORIGIN"] == ACCOUNTS
    assert values["W360_OIDC_ISSUER"] == ACCOUNTS
    assert "accounts.workeasy360.com" in values["W360_ALLOWED_HOSTS"].split(",")
    assert "auth.workeasy360.com" not in values["W360_ALLOWED_HOSTS"]
    assert ACCOUNTS in values["W360_CSRF_TRUSTED_ORIGINS"].split(",")


# --- what production refuses ------------------------------------------------------

STAGE_A = {
    "W360_AUTH_ORIGIN": ACCOUNTS,
    "W360_AUTH_LEGACY_ORIGIN": AUTH,
    "W360_OIDC_ISSUER": AUTH,
    "W360_ALLOWED_HOSTS": (
        "admin.workeasy360.com,partner.workeasy360.com,app.workeasy360.com,"
        "accounts.workeasy360.com,api.workeasy360.com,auth.workeasy360.com"
    ),
    "W360_CSRF_TRUSTED_ORIGINS": f"{ADMIN},{PARTNER},{APP},{ACCOUNTS}",
}


def test_stage_a_is_accepted(monkeypatch):
    module = _import_production(monkeypatch, _production_env(**STAGE_A))
    assert module.OIDC_ISSUER == AUTH
    assert module.W360_PORTAL_ORIGINS["auth"] == ACCOUNTS


@pytest.mark.parametrize(
    "override,expected",
    [
        # The issuer is one of the sign-in's two origins - nothing else.
        ({"W360_OIDC_ISSUER": APP}, "must be W360_AUTH_ORIGIN"),
        ({"W360_OIDC_ISSUER": "https://idp.example.net"}, "must be W360_AUTH_ORIGIN"),
        # The window's other issuer: ours, once, never the canonical one.
        ({"W360_OIDC_TRANSITION_ISSUERS": APP}, "W360_OIDC_TRANSITION_ISSUERS"),
        ({"W360_OIDC_TRANSITION_ISSUERS": AUTH}, "W360_OIDC_TRANSITION_ISSUERS"),
        (
            {"W360_OIDC_TRANSITION_ISSUERS": f"{ACCOUNTS},{ACCOUNTS}"},
            "W360_OIDC_TRANSITION_ISSUERS",
        ),
        (
            {"W360_OIDC_TRANSITION_ISSUERS": "https://*.workeasy360.com"},
            "W360_OIDC_TRANSITION_ISSUERS",
        ),
        # The previous name: never trusted for a browser write...
        (
            {"W360_CSRF_TRUSTED_ORIGINS": f"{ADMIN},{PARTNER},{APP},{ACCOUNTS},{AUTH}"},
            "exactly the four portal origins",
        ),
        # ...but it must be an allowed host, and nothing else may be.
        (
            {
                "W360_ALLOWED_HOSTS": (
                    "admin.workeasy360.com,partner.workeasy360.com,app.workeasy360.com,"
                    "accounts.workeasy360.com,api.workeasy360.com"
                )
            },
            "exactly the five portal hosts",
        ),
        # A bare https origin of its own.
        (
            {"W360_AUTH_LEGACY_ORIGIN": "http://auth.workeasy360.com"},
            "W360_AUTH_LEGACY_ORIGIN must",
        ),
        (
            {"W360_AUTH_LEGACY_ORIGIN": "https://auth.workeasy360.com/x"},
            "W360_AUTH_LEGACY_ORIGIN must",
        ),
        ({"W360_AUTH_LEGACY_ORIGIN": APP}, "W360_AUTH_LEGACY_ORIGIN must"),
    ],
)
def test_production_refuses_an_unsafe_move(override, expected, monkeypatch):
    with pytest.raises(ImproperlyConfigured, match=expected):
        _import_production(monkeypatch, _production_env(**{**STAGE_A, **override}))


def test_the_previous_name_needs_multi_portal_mode(monkeypatch):
    """Outside production too: a previous name with no portals is refused."""
    for key in list(os.environ):
        if key.startswith("W360_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("W360_AUTH_LEGACY_ORIGIN", AUTH)
    monkeypatch.setattr(Env, "load_dotenv", lambda self, path: None)
    sys.modules.pop("config.settings.base", None)
    try:
        module = importlib.import_module("config.settings.base")
        with pytest.raises(ImproperlyConfigured, match="W360_AUTH_LEGACY_ORIGIN needs"):
            module.env.require_all(context="test")
    finally:
        sys.modules.pop("config.settings.base", None)

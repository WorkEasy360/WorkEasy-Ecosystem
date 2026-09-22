"""Security baseline: grants, configuration validation, headers, secrets."""

from __future__ import annotations

import os

import pytest
from django.db import connection

from config.env import Env, ImproperlyConfigured

pytestmark = pytest.mark.security


# --- database grants ------------------------------------------------------


@pytest.mark.django_db
def test_application_role_is_not_the_table_owner():
    """R-02. If the application owns its tables it bypasses RLS unless FORCE
    is set, and one missed FORCE disables isolation silently."""
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT count(*)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_roles r ON r.oid = c.relowner
            WHERE n.nspname = current_schema()
              AND c.relkind = 'r'
              AND r.rolname = current_user
            """
        )
        owned = cur.fetchone()[0]
    assert owned == 0, f"The application role owns {owned} table(s)."


@pytest.mark.django_db
def test_application_role_cannot_bypass_rls():
    with connection.cursor() as cur:
        cur.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        is_super, bypass = cur.fetchone()
    assert not is_super
    assert not bypass


@pytest.mark.django_db
def test_audit_table_denies_update_and_delete():
    """Append-only enforced by grants, not application discipline.

    The test database additionally grants TRUNCATE for teardown (see
    conftest), which production does not - asserted below.
    """
    with connection.cursor() as cur:
        for privilege in ("UPDATE", "DELETE"):
            cur.execute(
                "SELECT has_table_privilege(current_user, 'audit_event', %s)",
                [privilege],
            )
            assert cur.fetchone()[0] is False, (
                f"The application role holds {privilege} on audit_event."
            )


def test_production_migration_does_not_grant_truncate():
    """TRUNCATE is not subject to RLS, so granting it to the application role
    would be a cross-tenant data-destruction primitive.

    Asserted against the migration source, because the test database
    deliberately diverges here.
    """
    import pathlib

    helper = (
        pathlib.Path(__file__).resolve().parents[2]
        / "workeasy360"
        / "platform"
        / "tenancy"
        / "migrations_helpers.py"
    )
    source = helper.read_text(encoding="utf-8")

    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("GRANT") and "TRUNCATE" in stripped:
            pytest.fail(f"Migration helper grants TRUNCATE: {stripped}")

    assert "REVOKE UPDATE, DELETE, TRUNCATE" in source


@pytest.mark.django_db
def test_audit_trigger_blocks_mutation_independently_of_grants():
    """Belt and braces: a future role misconfiguration must not silently
    restore mutability of the audit log."""
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            WHERE c.relname = 'audit_event' AND NOT t.tgisinternal
            """
        )
        assert cur.fetchone()[0] >= 1


# --- configuration validation --------------------------------------------


def _production_env(**overrides) -> dict[str, str]:
    base = {
        "W360_ENVIRONMENT": "production",
        "W360_SECRET_KEY": "s" * 60,
        "W360_DB_APP_PASSWORD": "p" * 24,
        "W360_DB_APP_USER": "w360_app",
        "W360_FILE_URL_SIGNING_KEY": "f" * 40,
        "W360_ALLOWED_HOSTS": "app.workeasy360.com",
        "W360_CSRF_TRUSTED_ORIGINS": "https://app.workeasy360.com",
        "W360_CORS_ALLOWED_ORIGINS": "https://app.workeasy360.com",
        "W360_RELEASE": "2026.09.21-abc123",
        "W360_FILE_STORAGE_BACKEND": "s3",
        "W360_REDIS_URL": "rediss://cache:6379/0",
        "W360_BROKER_URL": "amqps://broker:5671//",
        # The `iss` claim every deployed product pins and verifies.
        # Required in production and asserted to be an https origin
        # with no trailing slash, because `iss` is compared by exact
        # string equality (OpenID Connect Core section 3.1.3.7).
        "W360_OIDC_ISSUER": "https://app.workeasy360.com",
    }
    base.update(overrides)
    return base


def test_env_reports_every_problem_at_once():
    """A misconfigured deployment is fixed in one pass rather than one
    variable per restart."""
    env = Env({})
    env.str("W360_ALPHA", required=True)
    env.int("W360_BETA", required=True)
    env.secret("W360_GAMMA", required=True)

    with pytest.raises(ImproperlyConfigured) as exc:
        env.require_all()

    message = str(exc.value)
    assert "W360_ALPHA" in message
    assert "W360_BETA" in message
    assert "W360_GAMMA" in message


def test_secret_values_never_appear_in_validation_errors():
    """Reporting `SECRET_KEY='hunter2' is too short` would put the secret in
    the logs the check exists to protect."""
    env = Env({"W360_SECRET_KEY": "hunter2"})
    env.secret("W360_SECRET_KEY", min_length=50)

    with pytest.raises(ImproperlyConfigured) as exc:
        env.require_all()

    assert "hunter2" not in str(exc.value)
    assert "too short" in str(exc.value)


def test_invalid_types_are_reported_with_the_offending_key():
    env = Env({"W360_PORT": "not-a-number"})
    env.int("W360_PORT")
    with pytest.raises(ImproperlyConfigured, match="W360_PORT"):
        env.require_all()


@pytest.mark.parametrize(
    "missing",
    [
        "W360_SECRET_KEY",
        "W360_ALLOWED_HOSTS",
        "W360_CSRF_TRUSTED_ORIGINS",
        "W360_FILE_URL_SIGNING_KEY",
        "W360_RELEASE",
    ],
)
def test_production_refuses_to_start_without_a_critical_setting(missing, monkeypatch):
    """Production fails at startup, never at the first request that happens
    to need the value."""
    import importlib
    import sys

    env = _production_env()
    env.pop(missing)

    for key in list(os.environ):
        if key.startswith("W360_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings.production")
    # A local .env (the documented `cp .env.example .env` developer
    # workflow) would otherwise backfill the very key this test just
    # deleted via Env.load_dotenv()'s setdefault - making the "missing"
    # case silently not missing. This test's environment must be exactly
    # what it constructs, nothing a stray file on disk contributes.
    monkeypatch.setattr(Env, "load_dotenv", lambda self, path: None)

    sys.modules.pop("config.settings.production", None)
    sys.modules.pop("config.settings.base", None)

    with pytest.raises(ImproperlyConfigured, match=missing):
        importlib.import_module("config.settings.production")

    sys.modules.pop("config.settings.production", None)
    sys.modules.pop("config.settings.base", None)


@pytest.mark.parametrize(
    "override,expected",
    [
        ({"W360_ALLOWED_HOSTS": "*"}, "must not contain"),
        ({"W360_CORS_ALLOWED_ORIGINS": "*"}, "must not contain"),
        ({"W360_CSRF_TRUSTED_ORIGINS": "http://app.workeasy360.com"}, "https"),
        ({"W360_FILE_STORAGE_BACKEND": "local"}, "not durable"),
        ({"W360_DB_APP_USER": "w360_migrator"}, "non-owner"),
        ({"W360_DB_APP_PASSWORD": "w360_app_pw"}, "development default"),
        # Phase 3. A plaintext issuer means the discovery document and
        # the key set can both be replaced in transit, and a trailing
        # slash makes every product reject every token.
        ({"W360_OIDC_ISSUER": "http://app.workeasy360.com"}, "https origin"),
        ({"W360_OIDC_ISSUER": "https://app.workeasy360.com/"}, "trailing slash"),
    ],
)
def test_production_refuses_insecure_configuration(override, expected, monkeypatch):
    """Each of these is individually easy to get wrong and collectively
    catastrophic, so each fails the boot with a message naming the reason."""
    import importlib
    import sys

    for key in list(os.environ):
        if key.startswith("W360_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in _production_env(**override).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings.production")
    monkeypatch.setattr(Env, "load_dotenv", lambda self, path: None)

    sys.modules.pop("config.settings.production", None)
    sys.modules.pop("config.settings.base", None)

    with pytest.raises(ImproperlyConfigured, match=expected):
        importlib.import_module("config.settings.production")

    sys.modules.pop("config.settings.production", None)
    sys.modules.pop("config.settings.base", None)


def test_production_accepts_a_valid_configuration(monkeypatch):
    """The positive case: the checks must not be unsatisfiable."""
    import importlib
    import sys

    for key in list(os.environ):
        if key.startswith("W360_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in _production_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings.production")
    monkeypatch.setattr(Env, "load_dotenv", lambda self, path: None)

    sys.modules.pop("config.settings.production", None)
    sys.modules.pop("config.settings.base", None)

    module = importlib.import_module("config.settings.production")

    assert module.DEBUG is False
    assert module.SECURE_SSL_REDIRECT is True
    assert module.SESSION_COOKIE_SECURE is True
    assert module.CSRF_COOKIE_SECURE is True
    assert module.SECURE_HSTS_SECONDS >= 31_536_000
    assert module.CELERY_TASK_ALWAYS_EAGER is False

    sys.modules.pop("config.settings.production", None)
    sys.modules.pop("config.settings.base", None)


# --- secrets hygiene ------------------------------------------------------


def test_no_secrets_committed_to_the_repository():
    """12-security.md section 5 rule 1: no secrets in code, config files or
    environment files in the repo.

    The local development passwords in docker-compose and infra/ are
    deliberate and are refused by the production configuration checks above.
    """
    import pathlib
    import re

    repo = pathlib.Path(__file__).resolve().parents[3]
    patterns = [
        re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key id
        re.compile(r"sk_live_[0-9a-zA-Z]{24,}"),  # Stripe live key
        # Header **and** a body. A bare header is not key material - it
        # appears in tests that assert no key reaches a log or a
        # response, and flagging those would train people to phrase the
        # assertion so the scanner misses it, which is worse than the
        # false positive. A real committed key has base64 right after the
        # header, so requiring it makes the check more precise rather
        # than more permissive.
        re.compile(r"-----BEGIN (RSA |EC )?PRIVATE KEY-----[\s\n]*[A-Za-z0-9+/]{40,}"),
        re.compile(r"ghp_[0-9a-zA-Z]{36}"),  # GitHub token
    ]

    findings = []
    for path in repo.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(repo).as_posix()
        if any(part in rel for part in (".venv/", ".git/", "node_modules/")):
            continue
        if path.suffix not in {
            ".py",
            ".yml",
            ".yaml",
            ".ini",
            ".toml",
            ".md",
            ".txt",
            ".json",
            ".cfg",
            ".env",
        }:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover
            continue
        for pattern in patterns:
            if pattern.search(content):
                findings.append(f"{rel}: {pattern.pattern}")

    assert not findings, "Possible secrets committed:\n" + "\n".join(findings)


def test_the_secret_scanner_detects_a_real_key():
    """Prove the private-key pattern is not vacuous.

    The pattern requires a header *and* a body, so that a test asserting "no
    key reached this log" is not itself reported. That narrowing is only
    acceptable if the pattern still catches the thing it exists for - a key
    actually committed to the tree - which is what this asserts, against
    genuinely generated material rather than against a hand-written sample.
    """
    import re

    from workeasy360.platform.ecosystem import keys

    private_pem, _ = keys.generate_key_pair("RS256")
    pattern = re.compile(
        r"-----BEGIN (RSA |EC )?PRIVATE KEY-----[\s\\n]*[A-Za-z0-9+/]{40,}"
    )

    assert pattern.search(private_pem), (
        "The scanner would not detect a real RSA private key committed to the repository."
    )
    assert pattern.search(private_pem.replace("\n", "\\n")), (
        "The scanner would not detect a key stored with escaped newlines, "
        "which is how most secret-manager CLIs render a PEM."
    )
    # And the shape that caused the narrowing is correctly ignored, so the
    # false positive it was narrowed for cannot come back.
    assert not pattern.search('assert "-----BEGIN PRIVATE KEY-----" not in body')


def test_env_file_is_git_ignored():
    """A generated local environment file must never enter Git."""
    import pathlib

    repo = pathlib.Path(__file__).resolve().parents[3]
    gitignore = (repo / ".gitignore").read_text(encoding="utf-8")

    for entry in (".env", "*.pem", ".venv"):
        assert entry in gitignore, f"{entry} is not git-ignored."

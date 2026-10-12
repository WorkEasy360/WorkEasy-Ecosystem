"""Create, migrate and seed the scratch databases for the universal-sign-in
browser check (ADR-044), and generate each product's key pair.

    USSO_WORK=<scratch dir> python scripts/universal_sso_browser_check/prepare.py
    USSO_WORK=<scratch dir> python scripts/universal_sso_browser_check/prepare.py --drop

Scratch only:
- Workeasy360  ``w360_usso_fresh`` on the local Docker PostgreSQL (5442);
- Easy CRM     ``keel_usso`` on Keel's PostgreSQL (5433), owned by ``crm_app``;
- EasyBook     ``easybook_usso`` on EasyBook's PostgreSQL (5472), owned by
  ``easybook_app``;
- Easy Project ``project_usso`` on this check's own PostgreSQL 17 container
  ``usso-project-pg`` (127.0.0.1:5492) - its Flyway migrations run when it
  starts;
- Easy Help Desk ``helpdesk_usso`` on this check's own PostgreSQL 16 container
  ``usso-helpdesk-pg`` (127.0.0.1:5493) and Redis container
  ``usso-helpdesk-redis`` (127.0.0.1:6493).
Never a development or test database. The three containers are created here
when missing; ``--drop`` removes them. The journeys start trials, change
preferences and register a product, so re-create before every run.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rehearse_phase8_upgrade import (
    HOST,
    PORT,
    PY,
    ROOT,
    create_database,
    create_vector_extension,
    drop_database,
    manage,
)

DB = "w360_usso_fresh"
REDIS_URL = "redis://127.0.0.1:6392/8"
CRM = Path("C:/Users/aqw03/OneDrive/Desktop/AI CRM/backend")
EASYBOOK = Path("C:/Users/aqw03/OneDrive/Desktop/EasyBook/backend")
PROJECT_REPO = Path("C:/Users/aqw03/OneDrive/Desktop/Project-Module")
HELPDESK_REPO = Path("C:/Users/aqw03/OneDrive/Desktop/helpdesk-workeasy")
PROJECT_DB_URL = "jdbc:postgresql://127.0.0.1:5492/project_usso"
HELPDESK_DB_URL = "postgresql://digisoft:digisoft@127.0.0.1:5493/helpdesk_usso"
HELPDESK_REDIS_URL = "redis://127.0.0.1:6493/3"
#: This check's own containers: name -> docker run arguments.
CONTAINERS = {
    "usso-project-pg": [
        "-e",
        "POSTGRES_PASSWORD=pmtest",
        "-p",
        "127.0.0.1:5492:5432",
        "postgres:17",
    ],
    "usso-helpdesk-pg": [
        "-e",
        "POSTGRES_USER=digisoft",
        "-e",
        "POSTGRES_PASSWORD=digisoft",
        "-p",
        "127.0.0.1:5493:5432",
        "postgres:16",
    ],
    "usso-helpdesk-redis": ["-p", "127.0.0.1:6493:6379", "redis:7"],
}
WORK = Path(os.environ.get("USSO_WORK", ROOT / ".usso_work"))


def backend_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        PYTHONPATH=f"{ROOT / 'backend'}{os.pathsep}{ROOT / 'scripts' / 'universal_sso_browser_check'}",
        DJANGO_SETTINGS_MODULE="w360_usso_browser_settings",
        W360_DB_NAME=DB,
        W360_DB_HOST=HOST,
        W360_DB_PORT=str(PORT),
        W360_DB_DIRECT_HOST=HOST,
        W360_DB_DIRECT_PORT=str(PORT),
        W360_DB_POOL_MODE="direct",
        W360_REDIS_URL=REDIS_URL,
        USSO_WORK=str(WORK),
    )
    signing = WORK / "signing.pem"
    if signing.exists():
        # The private half of the scratch issuer's signing key, resolved from
        # the environment exactly as production resolves it from the secret
        # manager (private_key_ref "usso" -> W360_OIDC_SIGNING_KEY_USSO).
        env["W360_OIDC_SIGNING_KEY_USSO"] = signing.read_text(encoding="utf-8")
    return env


def _signing_key() -> None:
    """Generate and promote the scratch issuer's token-signing key (without
    one the token endpoint answers 503)."""

    def run(*args: str) -> str:
        result = subprocess.run(  # noqa: S603 - this repository's own interpreter
            [PY, str(ROOT / "backend" / "manage.py"), "w360_signing_key", *args],
            env=backend_env(),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode:
            raise SystemExit(result.stderr[-2000:])
        return result.stdout

    out = run("generate", "--ref", "usso")
    begin, end = out.index("-----BEGIN"), out.index("-----END")
    pem = out[begin:end] + out[end:].split(chr(10))[0] + chr(10)
    (WORK / "signing.pem").write_text(pem, encoding="utf-8")
    kid = out.split("Created pending key ")[1].split()[0]
    run("promote", "--kid", kid)
    print("signing key", kid, "active")


def crm_env(seed: dict | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        DJANGO_SETTINGS_MODULE="config.settings.dev",
        DATABASE_URL="postgres://crm_app:crm_app_dev_password@localhost:5433/keel_usso",
        REDIS_URL="redis://localhost:6379/5",
        CELERY_BROKER_URL="redis://localhost:6379/6",
        FRONTEND_ORIGIN="http://localhost:4022",
        CSRF_TRUSTED_ORIGINS="http://localhost:4022",
        ALLOWED_HOSTS="localhost,127.0.0.1",
        AUTO_LOGIN_ENABLED="0",
        WORKEASY360_SSO_ONLY="0",
        WORKEASY360_ISSUER="http://127.0.0.1:8021",
        WORKEASY360_REDIRECT_URI="http://localhost:4022/api/auth/callback/workeasy360",
        WORKEASY360_PRIVATE_KEY_FILE=str(WORK / "crm.key"),
        WORKEASY360_POST_LOGOUT_REDIRECT_URI="http://localhost:4022/login",
    )
    if seed:
        env.update(
            WORKEASY360_CLIENT_ID=seed["credentials"]["easy_crm"]["client_id"],
            WORKEASY360_KEY_ID=seed["credentials"]["easy_crm"]["key_id"],
        )
    return env


def bff_proxy_secret() -> str:
    """The secret EasyBook's BFF and API share (its production frontend will
    not start without one). Scratch only: generated once per work directory."""
    path = WORK / "bff_proxy_secret"
    if not path.exists():
        WORK.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def easybook_env(seed: dict | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        DJANGO_SETTINGS_MODULE="config.settings.dev",
        DB_NAME="easybook_usso",
        DB_USER="easybook_app",
        DB_PASSWORD="easybook_app",  # noqa: S106 - the local development database role
        DB_HOST="127.0.0.1",
        DB_PORT="5472",
        REDIS_URL="redis://127.0.0.1:6390/5",
        WORKEASY360_SSO_ONLY="1",
        WORKEASY360_ISSUER="http://127.0.0.1:8021",
        WORKEASY360_REDIRECT_URI="http://127.0.0.2:3023/api/auth/callback/workeasy360",
        WORKEASY360_PRIVATE_KEY_FILE=str(WORK / "easybook.key"),
        WORKEASY360_POST_LOGOUT_REDIRECT_URI="http://127.0.0.2:3023/login",
        BFF_PROXY_SECRET=bff_proxy_secret(),
    )
    if seed:
        env.update(
            WORKEASY360_CLIENT_ID=seed["credentials"]["easybook"]["client_id"],
            WORKEASY360_KEY_ID=seed["credentials"]["easybook"]["key_id"],
        )
    return env


def project_env(seed: dict | None = None) -> dict[str, str]:
    """Easy Project (Spring Boot): production-shaped, with its sign-in settings
    pointing at the scratch issuer. ``SERVER_SECURE_COOKIES`` stays on: loopback
    is a secure context to the browser."""
    env = dict(os.environ)
    env.update(
        # The ``local`` profile only lets the jar start with plain-http
        # issuer and redirect URIs (loopback); everything that matters is set
        # explicitly below, Secure cookies included (loopback is a secure
        # context to the browser).
        SPRING_PROFILES_ACTIVE="local",
        PROJECTMODULE_SESSION_COOKIE_SECURE="true",
        # postgres:17 refuses the JVM's default zone name on this machine
        # (Asia/Calcutta); production runs in UTC anyway.
        JAVA_TOOL_OPTIONS="-Duser.timezone=UTC",
        SERVER_ADDRESS="127.0.0.4",
        SERVER_PORT="8024",
        PROJECTMODULE_DB_URL=PROJECT_DB_URL,
        PROJECTMODULE_DB_USERNAME="postgres",
        PROJECTMODULE_DB_PASSWORD="pmtest",  # noqa: S106 - scratch container
        WORKEASY360_ISSUER="http://127.0.0.1:8021",
        WORKEASY360_REDIRECT_URI="http://127.0.0.4:8024/api/auth/callback/workeasy360",
        WORKEASY360_PRIVATE_KEY_FILE=str(WORK / "project.key"),
        WORKEASY360_POST_LOGOUT_REDIRECT_URI="http://127.0.0.4:8024/",
        WORKEASY360_PRODUCT_CODE="easy_project",
    )
    if seed:
        env.update(
            WORKEASY360_CLIENT_ID=seed["credentials"]["easy_project"]["client_id"],
            WORKEASY360_KEY_ID=seed["credentials"]["easy_project"]["key_id"],
        )
    return env


def helpdesk_env(seed: dict | None = None) -> dict[str, str]:
    """Easy Help Desk API and worker, SSO-only as in production. Development mode
    only because production refuses plain-http issuer and redirect URIs (this run
    is loopback http); SSO-only and Secure cookies are set explicitly."""
    env = dict(os.environ)
    env.update(
        NODE_ENV="development",
        PORT="4025",
        PUBLIC_API_URL="http://127.0.0.5:4025/api/v1",
        EMAIL_PROVIDER="console",
        THROTTLE_ENABLED="true",
        DATABASE_URL=HELPDESK_DB_URL,
        REDIS_URL=HELPDESK_REDIS_URL,
        FRONTEND_URL="http://127.0.0.5:3025",
        BACKEND_URL="http://127.0.0.5:4025",
        COOKIE_SECURE="true",
        WORKEASY360_SSO_ONLY="true",
        WORKEASY360_ISSUER="http://127.0.0.1:8021",
        WORKEASY360_REDIRECT_URI="http://127.0.0.5:4025/api/auth/callback/workeasy360",
        WORKEASY360_PRIVATE_KEY_FILE=str(WORK / "helpdesk.key"),
        WORKEASY360_POST_LOGOUT_REDIRECT_URI="http://127.0.0.5:3025/login",
        WORKEASY360_PRODUCT_CODE="easy_helpdesk",
    )
    secrets_file = WORK / "helpdesk_secrets.json"
    if not secrets_file.exists():
        WORK.mkdir(parents=True, exist_ok=True)
        secrets_file.write_text(
            json.dumps(
                {
                    "JWT_SECRET": secrets.token_urlsafe(48),
                    "JWT_REFRESH_SECRET": secrets.token_urlsafe(48),
                    "CHANNEL_ENCRYPTION_KEY": secrets.token_hex(32),
                }
            ),
            encoding="utf-8",
        )
    env.update(json.loads(secrets_file.read_text(encoding="utf-8")))
    if seed:
        env.update(
            WORKEASY360_CLIENT_ID=seed["credentials"]["easy_helpdesk"]["client_id"],
            WORKEASY360_KEY_ID=seed["credentials"]["easy_helpdesk"]["key_id"],
        )
    return env


def _psql(container: str, user: str, password: str, sql: str) -> None:
    subprocess.run(  # noqa: S603 - local Docker only
        [  # noqa: S607 - local Docker CLI from PATH, scratch databases only
            "docker",
            "exec",
            "-e",
            f"PGPASSWORD={password}",
            container,
            "psql",
            "-U",
            user,
            "-d",
            "postgres",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _keys() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    WORK.mkdir(parents=True, exist_ok=True)
    for name in ("crm", "easybook", "project", "helpdesk", "future"):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        (WORK / f"{name}.key").write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        (WORK / f"{name}.pub").write_bytes(
            key.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )


def _containers() -> None:
    """Start this check's own scratch containers, fresh, and wait until each
    PostgreSQL accepts connections."""
    import time

    for name, args in CONTAINERS.items():
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)  # noqa: S603,S607
        subprocess.run(  # noqa: S603 - local Docker only
            ["docker", "run", "-d", "--name", name, *args],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        )
    for name, user in (("usso-project-pg", "postgres"), ("usso-helpdesk-pg", "digisoft")):
        for _ in range(60):
            ready = subprocess.run(  # noqa: S603 - local Docker only
                ["docker", "exec", name, "pg_isready", "-U", user],  # noqa: S607
                capture_output=True,
                text=True,
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise SystemExit(f"{name} did not start")
        time.sleep(2)


def _products_drop() -> None:
    for name in CONTAINERS:
        subprocess.run(  # noqa: S603 - local Docker only
            ["docker", "rm", "-f", name],  # noqa: S607
            capture_output=True,
            text=True,
        )
    _psql(
        "keel-postgres-1",
        "postgres",
        "postgres_dev_password",
        "DROP DATABASE IF EXISTS keel_usso WITH (FORCE)",
    )
    _psql(
        "easybook-postgres-1",
        "easybook",
        "easybook",
        "DROP DATABASE IF EXISTS easybook_usso WITH (FORCE)",
    )


def _products_create() -> None:
    _products_drop()
    _psql(
        "keel-postgres-1",
        "postgres",
        "postgres_dev_password",
        "CREATE DATABASE keel_usso OWNER crm_app",
    )
    _psql(
        "easybook-postgres-1",
        "easybook",
        "easybook",
        "CREATE DATABASE easybook_usso OWNER easybook_app",
    )
    _containers()
    _psql("usso-project-pg", "postgres", "pmtest", "CREATE DATABASE project_usso")
    _psql("usso-helpdesk-pg", "digisoft", "digisoft", "CREATE DATABASE helpdesk_usso")
    migrated = subprocess.run(  # noqa: S603 - the product's own Prisma CLI
        ["pnpm", "--filter", "@digisoft/db", "exec", "prisma", "migrate", "deploy"],  # noqa: S607
        cwd=HELPDESK_REPO / "backend",
        env={**helpdesk_env(), "DATABASE_URL": HELPDESK_DB_URL},
        capture_output=True,
        text=True,
        timeout=900,
        shell=os.name == "nt",
    )
    print(
        "Easy Help Desk migrate:",
        "ok"
        if migrated.returncode == 0
        else migrated.stderr[-3000:] + migrated.stdout[-2000:],
    )
    if migrated.returncode:
        raise SystemExit(migrated.returncode)
    for name, cwd, env in (
        ("Easy CRM", CRM, crm_env()),
        ("EasyBook", EASYBOOK, easybook_env()),
    ):
        result = subprocess.run(  # noqa: S603 - the product's own interpreter
            [
                str(cwd / ".venv" / "Scripts" / "python.exe"),
                "manage.py",
                "migrate",
                "--noinput",
            ],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        print(name, "migrate:", "ok" if result.returncode == 0 else result.stderr[-3000:])
        if result.returncode:
            raise SystemExit(result.returncode)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--drop", action="store_true")
    args = parser.parse_args()
    if args.drop:
        drop_database(DB)
        _products_drop()
        print(
            "dropped", DB, "keel_usso", "easybook_usso", "project_usso", "helpdesk_usso"
        )
        return 0
    _keys()
    (WORK / "signing.pem").unlink(missing_ok=True)
    drop_database(DB)
    create_database(DB)
    create_vector_extension(DB)
    rc, _out, err = manage(DB, "migrate", "--noinput")
    if rc:
        print(err[-2000:])
        return rc
    _signing_key()
    seeded = subprocess.run(  # noqa: S603 - this repository's own interpreter and script
        [PY, str(Path(__file__).with_name("seed.py"))],
        env=backend_env(),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=900,
    )
    print(seeded.stdout)
    if seeded.returncode:
        print(seeded.stderr[-4000:])
        return seeded.returncode
    _products_create()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

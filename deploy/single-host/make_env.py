"""Production environment for the single-host deployment - rendered ON the host.

    sudo python3 make_env.py render --out /opt/workeasy360/env --release <tag>
    sudo python3 make_env.py set W360_EMAIL_HOST_PASSWORD --env /opt/workeasy360/env/production.env

``render`` writes ``production.env`` and ``seaweedfs-s3.json`` (mode 0600), every
secret freshly random and never printed (deploy/env/production.env.example
lists the variables). It refuses to overwrite either file: new secrets would no
longer open the existing database, broker and object-store volumes.

``set`` replaces (or adds) one variable. The value is read from the terminal
without echo, or from stdin when piped, so it never appears in a command line,
shell history or process list. Multi-line values (a PEM) are stored with
literal ``\\n``, which the platform accepts.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import sys
from pathlib import Path

PORTALS = ("admin", "partner", "app", "auth", "api")
WEB = ("admin", "partner", "app", "auth")
BUCKET = "w360-files"
SAFE_UNQUOTED = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.@+/=:,"
)


def token(n: int = 48) -> str:
    return secrets.token_urlsafe(n)


def write_private(path: Path, text: str) -> None:
    """Create ``path`` with mode 0600 from the first byte; never overwrite."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


def render(out: Path, release: str, domain: str, smtp_user: str) -> None:
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    env_file, s3_file = out / "production.env", out / "seaweedfs-s3.json"
    for f in (env_file, s3_file):
        if f.exists():
            raise SystemExit(f"{f} exists - refusing to replace live secrets.")

    hosts = {name: f"{name}.{domain}" for name in PORTALS}
    app_pw, mig_pw, redis_pw, rabbit_pw = token(24), token(24), token(24), token(24)
    s3_access, s3_secret = "w360" + secrets.token_hex(8), token(32)
    values = {
        "W360_ENVIRONMENT": "production",
        "W360_RELEASE": release,
        "W360_ENV_FILE": str(env_file),
        # --- hosts (ADR-043) ---
        **{f"W360_{n.upper()}_ORIGIN": f"https://{h}" for n, h in hosts.items()},
        "W360_EDGE_UPSTREAM_HOST": hosts["api"],
        "W360_ALLOWED_HOSTS": ",".join(hosts.values()),
        "W360_CSRF_TRUSTED_ORIGINS": ",".join(f"https://{hosts[n]}" for n in WEB),
        "W360_FRONTEND_URL": f"https://{hosts['app']}",
        "W360_OIDC_ISSUER": f"https://{hosts['auth']}",
        # DNS-only records: nothing sits between the client and the edge.
        "W360_TRUSTED_PROXY_COUNT": "1",
        "W360_TLS_ISSUER": "acme",
        # --- platform keys ---
        "W360_SECRET_KEY": token(64),
        "W360_FILE_URL_SIGNING_KEY": token(),
        "W360_MFA_ENCRYPTION_KEY": token(),
        "W360_INTEGRATION_SECRETS_KEYS": token(),
        "W360_NOTIFICATION_SEAL_KEYS": token(),
        # --- database ---
        "W360_DB_NAME": "workeasy360",
        "W360_DB_HOST": "pgbouncer",
        "W360_DB_PORT": "6432",
        "W360_DB_DIRECT_HOST": "postgres",
        "W360_DB_DIRECT_PORT": "5432",
        "W360_DB_APP_USER": "w360_app",
        "W360_DB_APP_PASSWORD": app_pw,
        "W360_DB_MIGRATOR_USER": "w360_migrator",
        "W360_DB_MIGRATOR_PASSWORD": mig_pw,
        "POSTGRES_PASSWORD": token(24),
        # --- Redis and broker: a private Docker network, never published ---
        "W360_REDIS_PASSWORD": redis_pw,
        "W360_REDIS_URL": f"redis://:{redis_pw}@redis:6379/0",
        "W360_ALLOW_PLAINTEXT_REDIS": "true",
        "RABBITMQ_DEFAULT_USER": "w360",
        "RABBITMQ_DEFAULT_PASS": rabbit_pw,
        "W360_BROKER_URL": f"amqp://w360:{rabbit_pw}@rabbitmq:5672//",
        "W360_ALLOW_PLAINTEXT_BROKER": "true",
        # --- files: SeaweedFS on the same private network ---
        "W360_FILE_STORAGE_BACKEND": "s3",
        "W360_S3_BUCKET": BUCKET,
        "W360_S3_REGION": "us-east-1",
        "W360_S3_ENDPOINT_URL": "http://seaweedfs:8333",
        "W360_ALLOW_PLAINTEXT_S3": "true",
        "W360_S3_SSE": "AES256",
        "W360_SEAWEED_SSE_KEY": secrets.token_hex(32),
        "W360_SEAWEED_S3_CONFIG": str(s3_file),
        "AWS_ACCESS_KEY_ID": s3_access,
        "AWS_SECRET_ACCESS_KEY": s3_secret,
        # --- email: Hostinger SMTP, implicit TLS; the password is `set` later ---
        "W360_EMAIL_HOST": "smtp.hostinger.com",
        "W360_EMAIL_PORT": "465",
        "W360_EMAIL_USE_SSL": "true",
        "W360_EMAIL_USE_TLS": "false",
        "W360_EMAIL_HOST_USER": smtp_user,
        "W360_EMAIL_HOST_PASSWORD": "",
        "W360_DEFAULT_FROM_EMAIL": smtp_user,
        # --- observability ---
        "W360_LOG_LEVEL": "INFO",
        "W360_METRICS_TOKEN": token(32),
        # --- sizing for 1 GiB RAM (compose.single-host.yml) ---
        "GUNICORN_WORKERS": "1",
        "GUNICORN_THREADS": "4",
        "W360_PASSWORD_HASH_CONCURRENCY": "1",
        "W360_WORKER_CONCURRENCY": "2",
    }
    identities = {
        "identities": [
            {
                "name": "w360",
                "credentials": [{"accessKey": s3_access, "secretKey": s3_secret}],
                "actions": ["Admin", "Read", "Write", "List", "Tagging"],
            }
        ]
    }
    write_private(s3_file, json.dumps(identities, indent=2) + "\n")
    write_private(env_file, "".join(f"{k}={v}\n" for k, v in values.items()))
    print(f"wrote {env_file} and {s3_file} (mode 0600, secrets not shown)")


def set_value(env_file: Path, name: str) -> None:
    if sys.stdin.isatty():
        value = getpass.getpass(f"{name} (input hidden): ")
    else:
        value = sys.stdin.read()
    value = value.strip("\r\n").replace("\r\n", "\n").replace("\n", "\\n")
    if not value:
        raise SystemExit("Empty value - nothing changed.")
    # Unquoted is read the same by compose and by `docker run --env-file`
    # (restore_verify.sh); anything else is single-quoted, which compose reads
    # literally (no $-interpolation, no inline comment).
    if not set(value) <= SAFE_UNQUOTED:
        if "'" in value:
            raise SystemExit("A single quote cannot be stored in the env file - nothing changed.")
        value = f"'{value}'"
    lines = env_file.read_text(encoding="utf-8").splitlines()
    line, done = f"{name}={value}", False
    for i, existing in enumerate(lines):
        if existing.split("=", 1)[0] == name:
            lines[i], done = line, True
    if not done:
        lines.append(line)
    tmp = env_file.with_name(env_file.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    write_private(tmp, "\n".join(lines) + "\n")
    os.replace(tmp, env_file)
    print(f"{name} {'replaced' if done else 'added'} in {env_file} (value not shown)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--release", required=True)
    r.add_argument("--domain", default="workeasy360.com")
    r.add_argument("--smtp-user", default="no-reply@workeasy360.com")
    s = sub.add_parser("set")
    s.add_argument("name")
    s.add_argument("--env", type=Path, required=True)
    a = p.parse_args()
    if a.cmd == "render":
        render(a.out.resolve(), a.release, a.domain, a.smtp_user)
    else:
        set_value(a.env.resolve(), a.name)


if __name__ == "__main__":
    main()

"""Render a staging environment for the Phase 12 rehearsal - OUTSIDE the repo.

    python deploy/staging/make_env.py <out-dir>

Writes ``<out-dir>/staging.env`` (every secret freshly random, never printed)
and ``<out-dir>/prometheus/`` (scrape config, alert rules, scrape token).
Production renders the same variables from its secret store instead
(deploy/env/production.env.example). Refuses to write inside the repository.
"""

from __future__ import annotations

import secrets
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
#: The five portal hosts (ADR-043), each its own origin.
PORTALS = {
    name: f"{name}.staging.workeasy360.com"
    for name in ("admin", "partner", "app", "auth", "api")
}
WEB = ("admin", "partner", "app", "auth")


def token(n: int = 48) -> str:
    return secrets.token_urlsafe(n)


def main(out: Path) -> None:
    out = out.resolve()
    if out == REPO or REPO in out.parents:
        raise SystemExit("Refusing to write secrets inside the repository.")
    out.mkdir(parents=True, exist_ok=True)

    app_pw, mig_pw, redis_pw, rabbit_pw = token(24), token(24), token(24), token(24)
    metrics_token = token(32)
    values = {
        "W360_ENVIRONMENT": "staging",
        "W360_RELEASE": "p12-rc1",
        **{f"W360_{name.upper()}_ORIGIN": f"https://{host}" for name, host in PORTALS.items()},
        "W360_EDGE_UPSTREAM_HOST": PORTALS["api"],
        "W360_ALLOWED_HOSTS": ",".join(PORTALS.values()),
        "W360_CSRF_TRUSTED_ORIGINS": ",".join(f"https://{PORTALS[n]}" for n in WEB),
        "W360_FRONTEND_URL": f"https://{PORTALS['app']}",
        "W360_OIDC_ISSUER": f"https://{PORTALS['auth']}",
        "W360_TRUSTED_PROXY_COUNT": "1",
        "W360_TLS_ISSUER": "internal",
        "W360_SECRET_KEY": token(64),
        "W360_FILE_URL_SIGNING_KEY": token(),
        "W360_INTEGRATION_SECRETS_KEYS": token(),
        "W360_NOTIFICATION_SEAL_KEYS": token(),
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
        "W360_REDIS_PASSWORD": redis_pw,
        "W360_REDIS_URL": f"redis://:{redis_pw}@redis:6379/0",
        "W360_ALLOW_PLAINTEXT_REDIS": "true",
        "RABBITMQ_DEFAULT_USER": "w360",
        "RABBITMQ_DEFAULT_PASS": rabbit_pw,
        "W360_BROKER_URL": f"amqp://w360:{rabbit_pw}@rabbitmq:5672//",
        "W360_ALLOW_PLAINTEXT_BROKER": "true",
        "W360_FILE_STORAGE_BACKEND": "s3",
        "W360_S3_BUCKET": "w360-files",
        "W360_S3_REGION": "us-east-1",
        "W360_S3_ENDPOINT_URL": "http://seaweedfs:8333",
        "W360_ALLOW_PLAINTEXT_S3": "true",
        "W360_S3_SSE": "AES256",
        # The stand-in object store's own SSE-S3 key (compose.staging.yml).
        "W360_STG_S3_SSE_KEY": secrets.token_hex(32),
        "AWS_ACCESS_KEY_ID": "staging",
        "AWS_SECRET_ACCESS_KEY": token(16),
        "W360_EMAIL_HOST": "mailpit",
        "W360_EMAIL_PORT": "1025",
        "W360_ALLOW_PLAINTEXT_SMTP": "true",
        "W360_DEFAULT_FROM_EMAIL": "no-reply@workeasy360.com",
        "W360_PAYMENTS_FAKE_PROVIDER": "true",
        "W360_DNS_RESOLVERS": "172.31.240.53",
        "W360_LOG_LEVEL": "INFO",
        "W360_METRICS_TOKEN": metrics_token,
        "GUNICORN_WORKERS": "3",
        "GUNICORN_THREADS": "4",
    }
    env_file = out / "staging.env"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")

    prom = out / "prometheus"
    prom.mkdir(exist_ok=True)
    for name in ("prometheus.yml", "alerts.yml"):
        shutil.copyfile(REPO / "deploy" / "observability" / name, prom / name)
    (prom / "metrics_token").write_text(metrics_token, encoding="utf-8")
    print(f"wrote {env_file} and {prom} (secrets not shown)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]))

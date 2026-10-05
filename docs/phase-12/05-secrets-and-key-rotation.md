# Secrets, key rotation and credential-incident procedure (Phase 12, Steps 14, 15, 52)

No real credential appears in this document, in the repository, in an image or in CI output.

## 1. Where every secret lives

| Secret | Variable | Used by (code) | Never in |
|---|---|---|---|
| Django `SECRET_KEY` | `W360_SECRET_KEY` (≥ 50 chars) | api, workers | repo, image, logs |
| File URL signing | `W360_FILE_URL_SIGNING_KEY` | api | ″ |
| MFA encryption (Fernet over TOTP secrets) | `W360_MFA_ENCRYPTION_KEY` | api | ″ |
| Integration secret sealing (webhook signing secrets) | `W360_INTEGRATION_SECRETS_KEYS` (list) | api, workers | ″ |
| Notification sealing (links waiting in the queue) | `W360_NOTIFICATION_SEAL_KEYS` (list) | api, workers | ″ |
| OIDC token signing keys | `W360_OIDC_SIGNING_KEY_<KID>` (PEM) | api | ″; the database stores only the public key and the *reference* |
| Database runtime role | `W360_DB_APP_PASSWORD` | api, workers, PgBouncer; PostgreSQL's role script at first start | ″ |
| Database owner role | `W360_DB_MIGRATOR_PASSWORD` | migrate task; PostgreSQL's role script at first start | ″ |
| Database superuser | `POSTGRES_PASSWORD` | PostgreSQL only | ″ |
| Redis | `W360_REDIS_PASSWORD` / `W360_REDIS_URL` | api, workers | ″ |
| RabbitMQ | `RABBITMQ_DEFAULT_PASS` / `W360_BROKER_URL` | broker, api, workers | ″ |
| SMTP | `W360_EMAIL_HOST_PASSWORD` | workers | ″ |
| Payment provider | `W360_PAYMENT_CREDENTIAL_<REF>`, `W360_PAYMENT_WEBHOOK_SECRET_<REF>` | api, workers | ″; the database stores only the reference |
| AI providers | `W360_AI_CREDENTIAL_<REF>` | workers | ″ |
| Object storage | task/instance role (preferred) or `AWS_*` | api | ″ |
| Metrics scrape token | `W360_METRICS_TOKEN` | api, scraper | ″ |
| Super Admin bootstrap | `PLATFORM_SUPERADMIN_*` | one command run | removed after use |

**What each container receives** (`deploy/compose/production.yml`; least privilege since the final audit, G-54; verified inside the running staging containers):

| Container | Receives |
|---|---|
| edge (internet-facing), web | **no secret**: the canonical host, the TLS issuer and the trusted proxy ranges |
| PostgreSQL | its superuser's password and the two role passwords (to create the roles) |
| PgBouncer / Redis / RabbitMQ | its own one credential |
| api, workers, Beat | the application env file, with `POSTGRES_PASSWORD` and `W360_DB_MIGRATOR_PASSWORD` **blanked** |
| migrate task | the application env file with the owner's password; the superuser's blanked |

One image serves the api and the workers, so they share the application file. A worker therefore also receives the MFA key it never reads. Splitting that file per process type is possible, but not done (P3).

**Approved storage:** the deployment's secret store (AWS Secrets Manager / SSM Parameter Store with KMS, or an equivalent vault). It is rendered at deploy time into the process environment (`W360_ENV_FILE`, mode 0600 on the host, or native task-definition secrets on ECS). `.gitignore` refuses `deploy/env/*.env`; `.dockerignore` is an allow-list that cannot copy a `.env` into an image.

**Verified in Phase 12:**
- gitleaks over the full git history and the working tree: no leaks after triage. Every allowlisted match is a documented development default, a variable name or a synthetic test fixture (`.gitleaks.toml`, [11-security-scans.md](11-security-scans.md)).
- Both images inspected: no `.env`, key or test file; trivy's secret scanner is clean.
- The frontend bundle has no `NEXT_PUBLIC_*` variable.
- Logs: stdlib and structlog both pass the redactor (G-14). Tests pin password, authorization, session and CSRF values, MFA codes, OIDC codes and verifiers, client assertions, API keys and card numbers.
- Production refuses to start with any secret missing, too short, equal to another key, or equal to a development default.

## 2. Rotation procedures

| Key | Mechanism | Procedure |
|---|---|---|
| **OIDC signing key** | Several keys published in JWKS; statuses pending → active → retiring → retired. | `w360_signing_key generate --ref k2` prints the PEM **once**. Store it as `W360_OIDC_SIGNING_KEY_K2` and redeploy the api so the key is published. Wait for products' JWKS caches (≥ their cache TTL). Then `promote --kid <k2>`, which starts signing with k2 while k1 goes to `retiring`, still published. Wait `safe_retirement_after()` = max(ID token, access token TTL) + clock skew = **930 s** by default. Then `retire --kid <k1>`. An early retirement is refused unless `--force` (compromise only: it invalidates every unexpired token k1 signed). Exercised live on staging: see phase-12-status §15. |
| **Integration secret key** | Comma list: the first seals, all open. | Prepend a new key and deploy; old ciphertext still opens. Re-seal lazily or via a re-seal job, then remove the old key a release later. |
| **Notification seal key** | Same list rule. | Same. The items it protects expire within days (`INVITATION_TTL_DAYS`), so removal can follow after the longest link lifetime. |
| **MFA encryption key** | **A single key (no list).** | **Do not rotate destructively.** Replacing it makes every enrolled TOTP secret undecryptable, so every user would lose their second factor. A safe rotation needs a migration strategy that is **not built**: MultiFernet with old and new keys plus a re-encrypt job. Recorded as debt D-038 (P2). Until then the key is rotated only by that migration, never by swapping the value. |
| **`SECRET_KEY`** | Django signing (sessions are DB-backed; password reset tokens are platform-hashed, not Django-signed). | Rotating it signs everyone out once (session registry). It no longer breaks MFA or file URLs, which have their own keys. |
| **DB, Redis, broker, SMTP, payment, AI credentials** | Provider-side. | Issue the new credential, update the secret store, roll the processes (§3 order), revoke the old credential. Payment and AI credentials are read per call, so there is no restart. |

## 3. Suspected credential leak (incident)

**Detect.** Sources: a secret-scanner alert (gitleaks in CI, GitHub push protection), a provider's anomaly notice, an unexpected `security.*` or staff audit event, or the `W360*` alerts.

**Contain (first hour).**
1. Identify which secret(s) and since when. Assume use from that moment on.
2. Rotate the leaked secret at its issuer first (database password, broker password, provider key); for an OIDC signing key use `promote` then `retire --force`. The old value stops working even before the application is redeployed.
3. Update the secret store and roll the processes that read it (§1).
4. For `SECRET_KEY` or session compromise: bump every session epoch (the staff console's "sign everyone out", or `User.revoke_sessions` in bulk).
5. For a staff credential: disable the account (`w360_staff_identity --disable`), revoke its grants, and review its audit trail.

**Recover.**
- Re-issue what depended on the secret: webhook secrets (integration key), outstanding invitation links (notification key).
- Check the audit log for actions taken with the credential between leak and rotation.

**Verify.**
- The old credential is refused at its issuer.
- The platform starts under the new one (`/healthz/startup`).
- gitleaks is clean on the offending commit's history. A leaked secret is rotated, never merely deleted from Git.

## 4. Operating rule

Nobody pastes a credential into a ticket, chat, document or command line. The only place a credential is ever displayed is the terminal of the `w360_signing_key generate` operator, once, and it goes straight into the secret store.

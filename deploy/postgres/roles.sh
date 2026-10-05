#!/bin/bash
# Workeasy360 - database roles for a new production cluster (Phase 12).
#
# The production twin of infra/postgres/init.sql, with no literal credential:
# both passwords come from the environment (the deployment's secret store).
# Run once, as the superuser, on an empty cluster - by the postgres image's
# initdb hook here, or by an operator against RDS (`psql -f` equivalent).
#
#   w360_migrator : owns the schema, runs migrations. NOT superuser, NOT bypassrls.
#   w360_app      : the runtime role. Non-owner, NOBYPASSRLS, and carries its
#                   own statement/lock/idle timeouts (Phase 12, G-02: a
#                   transaction-mode pooler drops per-connection options).
#
# The application refuses to start if its role can bypass RLS, owns a table,
# or has no timeouts (W360.E002 / W360.E010 / /healthz/startup).
set -euo pipefail

: "${W360_DB_APP_PASSWORD:?W360_DB_APP_PASSWORD is required}"
: "${W360_DB_MIGRATOR_PASSWORD:?W360_DB_MIGRATOR_PASSWORD is required}"
DB="${POSTGRES_DB:-workeasy360}"

psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER:-postgres}" --dbname "$DB" \
  -v app_pw="$W360_DB_APP_PASSWORD" -v mig_pw="$W360_DB_MIGRATOR_PASSWORD" -v db="$DB" <<'SQL'
SET password_encryption = 'scram-sha-256';
CREATE ROLE w360_migrator WITH LOGIN PASSWORD :'mig_pw' NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB;
CREATE ROLE w360_app WITH LOGIN PASSWORD :'app_pw' NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB;

ALTER ROLE w360_app SET statement_timeout = '10s';
ALTER ROLE w360_app SET lock_timeout = '5s';
ALTER ROLE w360_app SET idle_in_transaction_session_timeout = '15s';

ALTER DATABASE :"db" OWNER TO w360_migrator;
CREATE EXTENSION IF NOT EXISTS vector;
ALTER SCHEMA public OWNER TO w360_migrator;
GRANT USAGE ON SCHEMA public TO w360_app;
ALTER DEFAULT PRIVILEGES FOR ROLE w360_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO w360_app;
ALTER DEFAULT PRIVILEGES FOR ROLE w360_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO w360_app;
ALTER DEFAULT PRIVILEGES FOR ROLE w360_migrator IN SCHEMA public
    GRANT EXECUTE ON FUNCTIONS TO w360_app;
SQL

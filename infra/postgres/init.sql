-- Workeasy360 database bootstrap.
--
-- Two roles, per ADR-009 / 03-multi-tenancy.md §4 Layer 3:
--   w360_migrator : owns the schema, runs migrations. NOT superuser, NOT bypassrls.
--   w360_app      : the runtime application role. Non-owner, NOBYPASSRLS.
--
-- Neither role may bypass RLS. The application asserts this at startup
-- (platform/tenancy/checks.py) and refuses to boot if either flag is set.

CREATE ROLE w360_migrator WITH LOGIN PASSWORD 'w360_migrator_pw'
    NOSUPERUSER NOBYPASSRLS NOCREATEROLE CREATEDB;

CREATE ROLE w360_app WITH LOGIN PASSWORD 'w360_app_pw'
    NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB;

-- Phase 12 (G-02). The runtime role's timeouts live on the ROLE, not only in
-- the libpq `options` the settings module sends: PgBouncer in transaction mode
-- drops that startup parameter (ignore_startup_parameters), so before this no
-- pooled connection had a statement, lock or idle-in-transaction timeout.
-- The application refuses to start without them (W360.E010 / startup probe).
-- Values match config/settings/base.py's defaults.
ALTER ROLE w360_app SET statement_timeout = '10s';
ALTER ROLE w360_app SET lock_timeout = '5s';
ALTER ROLE w360_app SET idle_in_transaction_session_timeout = '15s';

ALTER DATABASE workeasy360 OWNER TO w360_migrator;

-- Phase 8 (ADR-031): pgvector. The extension is not "trusted", so only a
-- superuser may create it; the migrator deliberately is not one. Created in
-- template1 so every database made later (the test database, rehearsal
-- scratch databases) inherits it, and in workeasy360 itself below. On an
-- existing deployment an operator runs the same statement once, as the
-- superuser, before `migrate` (docs/phase-8/09-operations.md).
\connect template1
CREATE EXTENSION IF NOT EXISTS vector;

\connect workeasy360

CREATE EXTENSION IF NOT EXISTS vector;

-- The migrator owns public; the app role only gets DML.
ALTER SCHEMA public OWNER TO w360_migrator;
GRANT USAGE ON SCHEMA public TO w360_app;

-- Tables created later by the migrator must be reachable by the app role.
-- Note: no TRUNCATE, and audit tables have UPDATE/DELETE revoked explicitly
-- by their own migration (platform/audit).
ALTER DEFAULT PRIVILEGES FOR ROLE w360_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO w360_app;
ALTER DEFAULT PRIVILEGES FOR ROLE w360_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO w360_app;

-- Template database so `createdb`-based test databases inherit the same setup.
ALTER DEFAULT PRIVILEGES FOR ROLE w360_migrator IN SCHEMA public
    GRANT EXECUTE ON FUNCTIONS TO w360_app;

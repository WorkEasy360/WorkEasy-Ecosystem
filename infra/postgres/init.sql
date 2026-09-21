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

ALTER DATABASE workeasy360 OWNER TO w360_migrator;

\connect workeasy360

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

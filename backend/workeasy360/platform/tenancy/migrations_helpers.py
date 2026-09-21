"""Migration operations for isolation Layers 3 and 4.

Django generates neither RLS policies nor composite foreign keys, so both are
emitted as ``RunSQL``. Centralising them here means every tenant table gets
*identical*, reviewed SQL - the alternative is hand-written policy SQL in a
dozen migrations, where one missing ``WITH CHECK`` is a silent hole.

Identifiers are validated and quoted rather than interpolated raw. Table names
come from our own models today, but a helper that builds SQL by formatting is
one refactor away from taking a caller-supplied string (ARCHITECTURE.md G7).
"""

from __future__ import annotations

import re
from typing import Final

from django.db import migrations

_IDENT_RE: Final = re.compile(r"^[a-z_][a-z0-9_]*$")

TENANT_SETTING: Final = "app.organization_id"
POLICY_NAME: Final = "tenant_isolation"


def _ident(name: str) -> str:
    """Validate and quote a SQL identifier."""
    if not _IDENT_RE.match(name):
        raise ValueError(
            f"Refusing to build SQL with identifier {name!r}: expected "
            f"lowercase snake_case."
        )
    return f'"{name}"'


def enable_rls(
    table: str,
    *,
    tenant_column: str = "organization_id",
    app_role: str = "w360_app",
) -> migrations.RunSQL:
    """Enable tenant isolation on ``table``.

    Four details, each fatal if missed (03-multi-tenancy.md section 4):

    1. ``FORCE ROW LEVEL SECURITY`` - without it the table *owner* bypasses
       every policy, and Django's migration role usually owns the tables.
    2. ``current_setting(..., true)`` with missing_ok - an unset variable
       yields NULL, ``NULL = uuid`` is NULL, NULL is not TRUE, so **zero rows
       match**. Fail-closed is the default outcome, not an added check.
    3. ``USING`` filters reads and the old row on UPDATE.
    4. ``WITH CHECK`` validates the *new* row on INSERT/UPDATE. Without it,
       code could write a row belonging to another tenant even though it
       could not read one.
    """
    t = _ident(table)
    col = _ident(tenant_column)
    policy = _ident(POLICY_NAME)
    role = _ident(app_role)

    predicate = f"({col} = NULLIF(current_setting('{TENANT_SETTING}', true), '')::uuid)"

    return migrations.RunSQL(
        sql=f"""
        ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {t} FORCE  ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS {policy} ON {t};
        CREATE POLICY {policy} ON {t}
            USING      {predicate}
            WITH CHECK {predicate};

        GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO {role};
        """,
        reverse_sql=f"""
        DROP POLICY IF EXISTS {policy} ON {t};
        ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY;
        ALTER TABLE {t} DISABLE ROW LEVEL SECURITY;
        """,
        elidable=False,
    )


def add_tenant_unique_key(
    table: str, *, column: str = "id", tenant_column: str = "organization_id"
) -> migrations.RunSQL:
    """Add ``UNIQUE (id, organization_id)`` so children can reference both.

    Redundant as a uniqueness claim - ``id`` is already the primary key - but
    required as an FK *target*: PostgreSQL will only accept a composite
    foreign key that points at a unique constraint covering both columns.
    """
    t = _ident(table)
    col = _ident(column)
    tcol = _ident(tenant_column)
    constraint = _ident(f"uq_{table}_{column}_{tenant_column}")

    return migrations.RunSQL(
        sql=f"""
        ALTER TABLE {t}
            ADD CONSTRAINT {constraint} UNIQUE ({col}, {tcol});
        """,
        reverse_sql=f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS {constraint};",
        elidable=False,
    )


def add_tenant_fk(
    *,
    table: str,
    column: str,
    references_table: str,
    references_column: str = "id",
    tenant_column: str = "organization_id",
    on_delete: str = "CASCADE",
) -> migrations.RunSQL:
    """Make a cross-tenant reference structurally impossible (Layer 4).

    RLS stops you *reading* another tenant's row. It does not stop you writing
    a row in your own tenant that *points at* one, if you somehow obtained the
    id (R-05). A composite FK closes that: the child's own ``organization_id``
    must match the parent's, so the reference cannot exist regardless of what
    the application believes.

    The plain single-column FK Django generated is dropped first, otherwise
    both constraints apply and the weaker one is redundant noise in the
    schema.
    """
    if on_delete not in {"CASCADE", "RESTRICT", "NO ACTION", "SET NULL"}:
        raise ValueError(f"Unsupported ON DELETE action {on_delete!r}.")

    t = _ident(table)
    col = _ident(column)
    tcol = _ident(tenant_column)
    ref_t = _ident(references_table)
    ref_col = _ident(references_column)
    constraint = _ident(f"fk_{table}_{column}_tenant")

    return migrations.RunSQL(
        sql=f"""
        DO $$
        DECLARE
            existing text;
        BEGIN
            -- Drop Django's single-column FK on this column, whatever it is named.
            SELECT con.conname INTO existing
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_attribute att
                 ON att.attrelid = rel.oid AND att.attnum = con.conkey[1]
            WHERE rel.relname = '{table}'
              AND con.contype = 'f'
              AND array_length(con.conkey, 1) = 1
              AND att.attname = '{column}';
            IF existing IS NOT NULL THEN
                EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I',
                               '{table}', existing);
            END IF;
        END $$;

        ALTER TABLE {t}
            ADD CONSTRAINT {constraint}
            FOREIGN KEY ({col}, {tcol})
            REFERENCES {ref_t} ({ref_col}, {tcol})
            ON DELETE {on_delete}
            DEFERRABLE INITIALLY DEFERRED;
        """,
        reverse_sql=f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS {constraint};",
        elidable=False,
    )


def make_append_only(table: str, *, app_role: str = "w360_app") -> migrations.RunSQL:
    """Revoke UPDATE and DELETE from the application role.

    Append-only enforced by database grants rather than application
    discipline (11-observability.md section 9). An ORM-level guard is bypassed
    by raw SQL, a data-fix script or the admin; a revoked grant is not.

    A trigger is added as well, so that a future role misconfiguration does
    not silently restore mutability. Belt and braces is proportionate here:
    audit and financial records are the ones where tampering must be
    impossible rather than merely difficult.
    """
    t = _ident(table)
    role = _ident(app_role)
    trigger_fn = _ident(f"{table}_reject_mutation")

    return migrations.RunSQL(
        sql=f"""
        REVOKE UPDATE, DELETE, TRUNCATE ON {t} FROM {role};
        GRANT SELECT, INSERT ON {t} TO {role};

        CREATE OR REPLACE FUNCTION {trigger_fn}() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                '% is append-only: % is not permitted', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS {trigger_fn} ON {t};
        CREATE TRIGGER {trigger_fn}
            BEFORE UPDATE OR DELETE ON {t}
            FOR EACH ROW EXECUTE FUNCTION {trigger_fn}();
        """,
        reverse_sql=f"""
        DROP TRIGGER IF EXISTS {trigger_fn} ON {t};
        DROP FUNCTION IF EXISTS {trigger_fn}();
        GRANT UPDATE, DELETE ON {t} TO {role};
        """,
        elidable=False,
    )


def grant_app_role(table: str, *, app_role: str = "w360_app") -> migrations.RunSQL:
    """Grant DML on a non-tenant (control-plane) table to the app role."""
    t = _ident(table)
    role = _ident(app_role)
    return migrations.RunSQL(
        sql=f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO {role};",
        reverse_sql=f"REVOKE ALL ON {t} FROM {role};",
        elidable=False,
    )


def enable_system_template_rls(
    table: str,
    *,
    tenant_column: str = "organization_id",
    system_flag_column: str = "is_system",
    app_role: str = "w360_app",
) -> migrations.RunSQL:
    """The D-001 exception: a table with a nullable ``organization_id``.

    ``authz.Role`` holds both global system templates (organization_id IS
    NULL) and tenant-owned custom roles. This is the one documented exception
    to the control-plane/data-plane rule, accepted because two parallel tables
    plus a polymorphic join from RoleAssignment is materially worse for every
    query (debt register D-001).

    It is made safe by an asymmetric policy:

    - **read**: own-tenant rows, OR system templates
    - **write**: own-tenant rows only, and never a row with a NULL tenant

    So system roles are readable by every tenant and writable by none. An
    upgrade that adds a permission to a system role cannot be performed by
    the application role at all, which is the property that keeps a customer's
    security posture from being mutated by a runtime bug.
    """
    t = _ident(table)
    col = _ident(tenant_column)
    flag = _ident(system_flag_column)
    role = _ident(app_role)
    read_policy = _ident("tenant_isolation_read")
    write_policy = _ident("tenant_isolation_write")

    own_row = f"({col} = NULLIF(current_setting('{TENANT_SETTING}', true), '')::uuid)"
    system_row = f"({col} IS NULL AND {flag} IS TRUE)"

    return migrations.RunSQL(
        sql=f"""
        ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {t} FORCE  ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS {read_policy} ON {t};
        CREATE POLICY {read_policy} ON {t}
            FOR SELECT
            USING ({own_row} OR {system_row});

        DROP POLICY IF EXISTS {write_policy} ON {t};
        CREATE POLICY {write_policy} ON {t}
            FOR ALL
            USING      {own_row}
            WITH CHECK {own_row};

        GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO {role};
        """,
        reverse_sql=f"""
        DROP POLICY IF EXISTS {read_policy} ON {t};
        DROP POLICY IF EXISTS {write_policy} ON {t};
        ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY;
        ALTER TABLE {t} DISABLE ROW LEVEL SECURITY;
        """,
        elidable=False,
    )

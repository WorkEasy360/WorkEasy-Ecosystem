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
from collections.abc import Sequence
from typing import Final

from django.db import migrations

_IDENT_RE: Final = re.compile(r"^[a-z_][a-z0-9_]*$")

TENANT_SETTING: Final = "app.organization_id"
POLICY_NAME: Final = "tenant_isolation"

#: The parent-publication lookup used by
#: :func:`freeze_children_of_published_parent`, as a **constant**.
#:
#: Nothing is interpolated into it from Python. The table and column names
#: arrive at run time as trigger arguments and are quoted by PostgreSQL's own
#: ``format(%I)``, which is the only safe way to place an identifier into a
#: query - SQL has no parameter form for one.
#:
#: Kept out of the surrounding f-string deliberately, so that the query text
#: and the identifier substitution are visibly separate things. A reader (and
#: a static analyser) can see at a glance that no query here is built by
#: formatting.
_PARENT_PUBLISH_LOOKUP: Final = (
    "format('SELECT %I FROM %I WHERE id = $1', TG_ARGV[2], TG_ARGV[0])"
)


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

    # Two statements, deliberately separate.
    #
    # The first finds and drops Django's single-column FK, whatever it was
    # named (the name carries a hash). It takes the table and column as
    # **bound parameters** rather than interpolating them: they are values
    # here - compared against pg_catalog columns - not identifiers, and
    # values always bind. `%%I` escapes the format() placeholders so psycopg
    # does not mistake them for its own.
    #
    # The second adds the composite FK, which uses identifiers only. Those
    # cannot be bound - SQL has no parameter form for an identifier - so each
    # one goes through _ident(), which validates the shape and quotes it.
    drop_legacy_fk = """
        DO $$
        DECLARE
            existing text;
        BEGIN
            SELECT con.conname INTO existing
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_attribute att
                 ON att.attrelid = rel.oid AND att.attnum = con.conkey[1]
            WHERE rel.relname = %s
              AND con.contype = 'f'
              AND array_length(con.conkey, 1) = 1
              AND att.attname = %s;
            IF existing IS NOT NULL THEN
                EXECUTE format('ALTER TABLE %%I DROP CONSTRAINT %%I',
                               %s, existing);
            END IF;
        END $$;
    """

    add_composite_fk = f"""
        ALTER TABLE {t}
            ADD CONSTRAINT {constraint}
            FOREIGN KEY ({col}, {tcol})
            REFERENCES {ref_t} ({ref_col}, {tcol})
            ON DELETE {on_delete}
            DEFERRABLE INITIALLY DEFERRED;
    """

    return migrations.RunSQL(
        sql=[
            (drop_legacy_fk, [table, column, table]),
            (add_composite_fk, None),
        ],
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


def make_immutable_when_published(
    table: str,
    *,
    columns: Sequence[str],
    publish_column: str = "published_at",
) -> migrations.RunSQL:
    """Refuse changes to ``columns`` once ``publish_column`` is set.

    The generic form of F5 ("published catalog versions and prices are
    immutable"). Editing a published plan or price in place retroactively
    changes what a customer agreed to buy, and the application guard alone is
    bypassed by a data-fix script, the admin or a bulk update.

    A trigger that **raises** rather than the ``DO INSTEAD NOTHING`` rule the
    Phase 0 sketch suggested: a rule silently discards the update, so the
    caller sees success and the operator believes the change landed. A
    refused write is recoverable; a silently ignored one is a support ticket
    six months later.

    ``publish_column`` itself is always protected, so a published record
    cannot be un-published and then edited.
    """
    t = _ident(table)
    flag = _ident(publish_column)
    trigger_fn = _ident(f"{table}_reject_published_change")

    protected = sorted({*columns, publish_column})
    comparisons = " OR ".join(
        f"NEW.{_ident(col)} IS DISTINCT FROM OLD.{_ident(col)}" for col in protected
    )

    return migrations.RunSQL(
        sql=f"""
        CREATE OR REPLACE FUNCTION {trigger_fn}() RETURNS trigger AS $$
        BEGIN
            IF OLD.{flag} IS NOT NULL AND ({comparisons}) THEN
                RAISE EXCEPTION
                    '% row % is published and its commercial terms are immutable',
                    TG_TABLE_NAME, OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS {trigger_fn} ON {t};
        CREATE TRIGGER {trigger_fn}
            BEFORE UPDATE ON {t}
            FOR EACH ROW EXECUTE FUNCTION {trigger_fn}();
        """,
        reverse_sql=f"""
        DROP TRIGGER IF EXISTS {trigger_fn} ON {t};
        DROP FUNCTION IF EXISTS {trigger_fn}();
        """,
        elidable=False,
    )


def freeze_columns(
    table: str,
    *,
    columns: Sequence[str],
) -> migrations.RunSQL:
    """Refuse any change to ``columns`` once the row exists.

    The unconditional sibling of :func:`make_immutable_when_published`, for
    records that have no draft state to be published *from*. A subscription
    line is agreed the moment it is written: there is no later event that
    makes it final, so "immutable after insert" is the whole rule.

    This is what makes the commercial snapshot on a subscription item worth
    having. Its purpose is that historical facts survive a catalog row being
    superseded, closed, or restored from a different backup than the
    subscription - and a snapshot a ``QuerySet.update()`` can rewrite provides
    none of that. The pinned ``PlanVersion`` and ``PlanPrice`` are already
    protected by triggers; leaving the copy mutable made the copy the weakest
    link rather than the belt to the braces.

    Columns that *must* still change are deliberately absent from every call
    site: ``removed_at`` is how a line is closed, and ``updated_at`` is
    bookkeeping.
    """
    t = _ident(table)
    trigger_fn = _ident(f"{table}_reject_history_change")

    comparisons = " OR ".join(
        f"NEW.{_ident(col)} IS DISTINCT FROM OLD.{_ident(col)}"
        for col in sorted(set(columns))
    )

    return migrations.RunSQL(
        sql=f"""
        CREATE OR REPLACE FUNCTION {trigger_fn}() RETURNS trigger AS $$
        BEGIN
            IF ({comparisons}) THEN
                RAISE EXCEPTION
                    '% row % records a commercial fact and cannot be changed',
                    TG_TABLE_NAME, OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS {trigger_fn} ON {t};
        CREATE TRIGGER {trigger_fn}
            BEFORE UPDATE ON {t}
            FOR EACH ROW EXECUTE FUNCTION {trigger_fn}();
        """,
        reverse_sql=f"""
        DROP TRIGGER IF EXISTS {trigger_fn} ON {t};
        DROP FUNCTION IF EXISTS {trigger_fn}();
        """,
        elidable=False,
    )


def freeze_children_of_published_parent(
    table: str,
    *,
    parent_table: str,
    parent_fk_column: str,
    publish_column: str = "published_at",
) -> migrations.RunSQL:
    """Refuse INSERT/UPDATE/DELETE on rows whose parent is published.

    Immutability of a ``PlanVersion`` is worthless if its ``PlanFeature`` rows
    can still be edited - the version's identity is unchanged while what it
    grants is not. The same applies to a ``PlanPrice``'s tiers.

    The parent's table, key column and publish column arrive as **trigger
    arguments**, which are values, and the lookup is built inside PostgreSQL
    with ``format(... %I ...)``. This is the same shape ``add_tenant_fk``
    already uses, and it is the reason the Python string here contains no
    interpolated query: a helper that formats a ``SELECT`` is one refactor
    away from formatting a caller-supplied one.

    ``to_jsonb(NEW)`` reads the foreign-key column by name rather than by
    identifier, so even the column reference is a value.
    """
    t = _ident(table)
    trigger_fn = _ident(f"{table}_reject_published_parent")

    # Validated for shape even though they travel as values, so a malformed
    # name fails when the migration is written rather than when it runs.
    for name in (parent_table, parent_fk_column, publish_column):
        _ident(name)

    return migrations.RunSQL(
        sql=f"""
        CREATE OR REPLACE FUNCTION {trigger_fn}() RETURNS trigger AS $$
        DECLARE
            published timestamptz;
            target uuid;
            row_data jsonb;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                row_data := to_jsonb(OLD);
            ELSE
                row_data := to_jsonb(NEW);
            END IF;
            target := (row_data ->> TG_ARGV[1])::uuid;

            EXECUTE {_PARENT_PUBLISH_LOOKUP}
                INTO published
                USING target;

            IF published IS NOT NULL THEN
                RAISE EXCEPTION
                    '% cannot be modified: its parent % row is published',
                    TG_TABLE_NAME, TG_ARGV[0]
                    USING ERRCODE = 'restrict_violation';
            END IF;

            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS {trigger_fn} ON {t};
        CREATE TRIGGER {trigger_fn}
            BEFORE INSERT OR UPDATE OR DELETE ON {t}
            FOR EACH ROW EXECUTE FUNCTION {trigger_fn}(
                '{parent_table}', '{parent_fk_column}', '{publish_column}'
            );
        """,
        reverse_sql=f"""
        DROP TRIGGER IF EXISTS {trigger_fn} ON {t};
        DROP FUNCTION IF EXISTS {trigger_fn}();
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

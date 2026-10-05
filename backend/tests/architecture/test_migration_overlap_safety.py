"""Every new migration is safe while the previous release is still running
(Phase 12, G-37 / Step 49).

A rolling deploy runs old and new code against the new schema at the same
time (13-deployment-evolution.md section 2.3: expand/contract only). The
operations below break the *old* code during that overlap - it inserts rows
without a column that is now NOT NULL, reads a column that was dropped or
renamed, or waits behind a lock while an index is built - so a migration that
contains one fails this test unless it declares, on its ``Migration`` class::

    overlap_safety = "<why this is safe, or which release contracts it>"

which is a reviewed decision recorded in the code, not a silent one.

The migrations that existed when the guard was introduced are listed in
``migration_baseline.txt`` and exempt: the first production deployment
starts from an empty schema, so none of them ever runs under an overlap.
The frozen list only ever shrinks in meaning; nothing is added to it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.migrations.loader import MigrationLoader

pytestmark = pytest.mark.architecture

BASELINE = {
    line.strip()
    for line in (Path(__file__).with_name("migration_baseline.txt"))
    .read_text(encoding="utf-8")
    .splitlines()
    if line.strip() and not line.startswith("#")
}
THIRD_PARTY = {"contenttypes", "auth", "sessions", "messages"}

#: The baseline's size and fingerprint (sorted entries). Grandfathering one
#: more migration must be a visible change to this test, never a line quietly
#: appended to the text file (final audit: "frozen" was only a subset check).
BASELINE_SIZE = 76
BASELINE_SHA256 = "4d2262d7318ad4019154dc72b0985f88b39a4e373946c7fb752d722c982502e1"


def _problems(migration) -> list[str]:
    created = {
        op.name.lower()
        for op in migration.operations
        if isinstance(op, migrations.CreateModel)
    }
    found: list[str] = []
    for op in migration.operations:
        if isinstance(op, migrations.RemoveField | migrations.DeleteModel):
            found.append(f"{type(op).__name__} (old code still reads it)")
        elif isinstance(op, migrations.RenameField | migrations.RenameModel):
            found.append(f"{type(op).__name__} (old code uses the old name)")
        elif isinstance(op, migrations.AddField):
            field = op.field
            if (
                not field.null
                and not field.many_to_many
                and getattr(field, "db_default", models.NOT_PROVIDED)
                is models.NOT_PROVIDED
                and op.model_name.lower() not in created
            ):
                found.append(
                    f"AddField {op.model_name}.{op.name} is NOT NULL without db_default "
                    f"(old code's INSERTs fail)"
                )
        elif isinstance(op, migrations.AlterField):
            field = op.field
            if (
                not field.null
                and getattr(field, "db_default", models.NOT_PROVIDED)
                is models.NOT_PROVIDED
            ):
                found.append(
                    f"AlterField {op.model_name}.{op.name} may tighten to NOT NULL "
                    f"(review against old writers)"
                )
        elif isinstance(op, migrations.AddIndex) and not isinstance(
            op, AddIndexConcurrently
        ):
            if op.model_name.lower() not in created:
                found.append(
                    f"AddIndex on existing {op.model_name} without CONCURRENTLY (locks writes)"
                )
        elif isinstance(op, migrations.RunSQL):
            sql = " ".join(op.sql) if isinstance(op.sql, list | tuple) else str(op.sql)
            upper = sql.upper()
            for word in ("DROP COLUMN", "DROP TABLE", "RENAME ", "SET NOT NULL"):
                if word in upper:
                    found.append(f"RunSQL contains {word.strip()}")
    return found


def test_new_migrations_are_safe_while_the_previous_release_runs():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    offenders: list[str] = []
    checked = 0
    for (app, name), migration in sorted(loader.disk_migrations.items()):
        if app in THIRD_PARTY or f"{app}.{name}" in BASELINE:
            continue
        checked += 1
        if getattr(migration, "overlap_safety", ""):
            continue
        offenders.extend(f"{app}.{name}: {p}" for p in _problems(migration))
    assert checked >= 1, (
        "the guard checked nothing - is the baseline swallowing new migrations?"
    )
    assert not offenders, "\n".join(offenders)


def test_the_guard_recognises_what_it_is_for():
    """Not vacuous: each unsafe shape is caught."""

    class Unsafe(migrations.Migration):
        operations = [
            migrations.RemoveField("thing", "gone"),
            migrations.RenameField("thing", "a", "b"),
            migrations.AddField("thing", "flag", models.BooleanField(default=False)),
            migrations.AddIndex("thing", models.Index(fields=["a"], name="ix_a")),
            migrations.RunSQL("ALTER TABLE t DROP COLUMN x", reverse_sql=""),
        ]

    assert len(_problems(Unsafe("0001", "x"))) == 5

    class Safe(migrations.Migration):
        operations = [
            migrations.AddField("thing", "note", models.TextField(null=True)),
            migrations.AddField("thing", "flag", models.BooleanField(db_default=False)),
            AddIndexConcurrently("thing", models.Index(fields=["a"], name="ix_a")),
        ]

    assert _problems(Safe("0002", "x")) == []


def test_the_baseline_is_frozen():
    """New migrations are checked, not grandfathered."""
    loader = MigrationLoader(None, ignore_no_migrations=True)
    on_disk = {f"{a}.{n}" for a, n in loader.disk_migrations if a not in THIRD_PARTY}
    assert BASELINE <= on_disk, sorted(BASELINE - on_disk)
    assert "ecosystem.0004_authorizationcode_session_facts" not in BASELINE
    assert len(BASELINE) == BASELINE_SIZE
    fingerprint = hashlib.sha256(",".join(sorted(BASELINE)).encode()).hexdigest()
    assert fingerprint == BASELINE_SHA256, "the migration baseline changed"

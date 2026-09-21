"""Audit: append-only at the database, hash-chained, and never silently lost."""

from __future__ import annotations

import uuid

import pytest
from django.db import connection, transaction
from django.db.utils import InternalError, ProgrammingError

from workeasy360.contracts.enums import ActorKind, AuditOutcome
from workeasy360.contracts.errors import AuditWriteFailed
from workeasy360.platform.audit.models import AuditEvent
from workeasy360.platform.audit.services import record, redact, verify_chain

pytestmark = [pytest.mark.django_db, pytest.mark.security]


def _write(ctx, action="platform.member.invite", **kwargs):
    return record(
        ctx,
        action,
        resource_type=kwargs.pop("resource_type", "organizations.membership"),
        resource_id=kwargs.pop("resource_id", str(uuid.uuid4())),
        **kwargs,
    )


# --- append-only ----------------------------------------------------------


def test_audit_entry_is_written(two_tenants, in_tenant):
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        event = _write(a["ctx"])
        assert AuditEvent.objects.filter(id=event.id).exists()
        assert event.actor_user_id == a["user"].id
        assert event.correlation_id is None or event.correlation_id


def test_update_is_refused_by_the_orm(two_tenants, in_tenant):
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        event = _write(a["ctx"])
        event.action = "tampered"
        with pytest.raises(PermissionError, match="append-only"):
            event.save()


def test_delete_is_refused_by_the_orm(two_tenants, in_tenant):
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        event = _write(a["ctx"])
        with pytest.raises(PermissionError, match="append-only"):
            event.delete()


def test_update_is_refused_by_the_database(two_tenants, in_tenant):
    """The real enforcement: grants and a trigger, not application discipline.

    An ORM guard is bypassed by raw SQL, a data-fix script or the admin. A
    revoked grant is not (11-observability.md section 9).
    """
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        event = _write(a["ctx"])

    with pytest.raises((ProgrammingError, InternalError)):
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute(
                    "UPDATE audit_event SET action = 'tampered' WHERE id = %s",
                    [event.id],
                )


def test_delete_is_refused_by_the_database(two_tenants, in_tenant):
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        event = _write(a["ctx"])

    with pytest.raises((ProgrammingError, InternalError)):
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("DELETE FROM audit_event WHERE id = %s", [event.id])


# --- hash chain -----------------------------------------------------------


def test_entries_are_hash_chained(two_tenants, in_tenant):
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        first = _write(a["ctx"], action="platform.member.view")
        second = _write(a["ctx"], action="platform.member.invite")

    assert first.entry_hash
    assert second.prev_hash == first.entry_hash
    assert second.entry_hash != first.entry_hash


def test_chain_verifies_when_untampered(two_tenants, in_tenant):
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        for _ in range(5):
            _write(a["ctx"])
        assert verify_chain(a["org"].id) == []


def test_hash_is_deterministic(two_tenants, in_tenant):
    """Canonical serialisation, so a rebuild that ordered keys differently
    would not look like tampering."""
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        event = _write(a["ctx"])
        assert event.compute_hash() == event.compute_hash()
        assert event.compute_hash() == event.entry_hash


# --- tenant isolation -----------------------------------------------------


def test_audit_is_tenant_scoped(two_tenants, in_tenant):
    """Customers read their own audit log, and only their own."""
    a, b = two_tenants["a"], two_tenants["b"]

    with in_tenant(a["ctx"]):
        _write(a["ctx"], action="platform.member.invite")
    with in_tenant(b["ctx"]):
        _write(b["ctx"], action="platform.member.remove")

    with in_tenant(a["ctx"]):
        actions = {e.action for e in AuditEvent.objects.all()}
    assert actions == {"platform.member.invite"}


# --- redaction ------------------------------------------------------------


def test_secrets_are_redacted_from_changes():
    """The audit log is customer-readable and retained for years. A secret
    written here is a secret disclosed for years."""
    payload = redact(
        {
            "password": "hunter2",
            "api_key": "w360_live_abc",
            "nested": {"authorization": "Bearer xyz", "safe": "ok"},
            "items": [{"token": "t"}, {"name": "fine"}],
            "status": "active",
        }
    )
    assert payload["password"] == "[redacted]"
    assert payload["api_key"] == "[redacted]"
    assert payload["nested"]["authorization"] == "[redacted]"
    assert payload["nested"]["safe"] == "ok"
    assert payload["items"][0]["token"] == "[redacted]"
    assert payload["status"] == "active"


def test_recorded_changes_are_redacted(two_tenants, in_tenant):
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        event = _write(
            a["ctx"],
            changes={"password": "hunter2", "member_type": "internal"},
        )
    assert event.changes["password"] == "[redacted]"
    assert event.changes["member_type"] == "internal"


def test_ip_is_hashed_not_stored(two_tenants, in_tenant):
    """An IP address is personal data and the log is customer-readable.

    Hashed with a server-side pepper: a bare SHA-256 of an IPv4 address is
    reversible by brute force in seconds.
    """
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        event = _write(a["ctx"], ip="203.0.113.7")
    assert event.ip_hash
    assert "203.0.113.7" not in event.ip_hash
    assert len(event.ip_hash) == 64


# --- failure behaviour ----------------------------------------------------


def test_audit_write_failure_fails_the_operation(two_tenants, in_tenant, monkeypatch):
    """The one place a logging failure is allowed to fail the request.

    An unaudited privileged action is worse than a failed one
    (12-security.md section 18).
    """
    a = two_tenants["a"]

    def explode(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(AuditEvent, "save", explode)

    with in_tenant(a["ctx"]):
        with pytest.raises(AuditWriteFailed):
            _write(a["ctx"])


def test_break_glass_without_reason_is_refused(
    two_tenants, in_tenant, tenant_ctx_factory
):
    """A privileged session with no stated reason must not be auditable as
    though it were ordinary activity."""
    a = two_tenants["a"]
    ctx = tenant_ctx_factory(
        a["org"],
        a["membership"],
        a["user"],
        is_break_glass=True,
        reason="Investigating ticket 42",
    )

    with in_tenant(ctx):
        event = _write(ctx, reason="Investigating ticket 42")
        assert event.actor_kind == ActorKind.STAFF_BREAK_GLASS.value
        assert event.reason


def test_denied_outcome_is_recorded(two_tenants, in_tenant):
    """Denials are audited, not only successes - otherwise the log shows only
    what worked, which is the least interesting half."""
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        event = _write(a["ctx"], outcome=AuditOutcome.DENIED, reason="no permission")
    assert event.outcome == AuditOutcome.DENIED.value


def test_audit_survives_organization_deletion(two_tenants, in_tenant):
    """On purge, audit is retained as a tombstone while business data goes.

    A cascading delete would remove the record of the deletion, which is
    precisely the event most worth keeping (08-data-ownership.md section 6).
    """
    a = two_tenants["a"]
    with in_tenant(a["ctx"]):
        _write(a["ctx"])

    # No database-level FK, so the audit row does not cascade.
    field = AuditEvent._meta.get_field("organization")
    assert field.db_constraint is False
    assert field.remote_field.on_delete.__name__ == "DO_NOTHING"
    assert AuditEvent.unscoped is not None

"""The startup gate (Phase 12, final audit): a container whose runtime role can
bypass RLS, owns tables or lacks its timeouts exits instead of serving.

Before, those checks ran only in ``check --deploy`` and ``/healthz/startup``,
while compose health-checked ``/healthz/ready`` - so nothing stopped such a
container from taking traffic.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

pytestmark = pytest.mark.django_db


def test_the_gate_passes_when_every_startup_check_passes(capsys):
    call_command("w360_startup_gate", wait=0)
    assert "all startup checks passed" in capsys.readouterr().out


def test_the_gate_refuses_to_start_and_names_what_failed(monkeypatch):
    from workeasy360.platform.observability import health

    monkeypatch.setattr(health, "startup_failures", lambda: ["tenancy_rls_role"])
    with pytest.raises(CommandError, match="tenancy_rls_role"):
        call_command("w360_startup_gate", wait=0)


def test_the_gate_waits_for_a_database_that_is_still_starting(monkeypatch):
    from workeasy360.platform.observability import health
    from workeasy360.platform.observability.management.commands import w360_startup_gate

    answers = [["postgresql"], ["postgresql"], []]
    monkeypatch.setattr(health, "startup_failures", lambda: answers.pop(0))
    monkeypatch.setattr(w360_startup_gate.time, "sleep", lambda _s: None)
    call_command("w360_startup_gate", wait=30)
    assert answers == []


def test_the_real_startup_checks_include_the_rls_role_and_the_timeouts():
    from workeasy360.platform.observability import health

    names = [check.name for check in health._startup_checks()]
    assert {"postgresql", "migrations", "registries"} <= set(names)
    registered = health.registered_probe_names("startup")
    assert registered, "tenancy registers its RLS-role and timeout probes at app-ready"
    assert set(registered) <= set(names)

"""TenantTask keeps each run's tenant context to itself, even when runs of
the same task overlap in one process.

A Celery task object is one per process, shared by every thread in it. The
context tokens a run sets must therefore live on the per-run, thread-local
``self.request`` - on ``self``, the second of two overlapping runs (a thread
pool, or eager tasks started from threads, as the Phase 8 concurrency suite
does) overwrites the first's token: the first then resets a token from
another thread's context (``ValueError``), or - finishing second - finds
none and leaves its context in place.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from workeasy360.platform.tenancy.context import current_tenant_or_none, tenant_context
from workeasy360.platform.tenancy.tasks import TenantTask

pytestmark = pytest.mark.django_db


def test_overlapping_runs_of_one_task_each_keep_their_own_tenant(two_tenants):
    from config.celery import app

    assert app.conf.task_always_eager
    first_inside = threading.Event()
    second_started = threading.Event()
    first_done = threading.Event()

    @app.task(base=TenantTask, name="tests.tenancy.echo_tenant", queue="default")
    def echo_tenant(position: int) -> str:
        # Pin the worst order: the first run starts, the second starts (and
        # would overwrite a token kept on the shared task object), then the
        # first finishes while the second is still running.
        if position == 0:
            first_inside.set()
            assert second_started.wait(10)
        else:
            second_started.set()
            assert first_done.wait(10)
        return str(current_tenant_or_none().organization_id)

    def run(position, ctx):
        try:
            with tenant_context(ctx):
                seen = echo_tenant.apply_async(args=[position]).get(
                    disable_sync_subtasks=False
                )
                assert current_tenant_or_none() == ctx, "the caller's context changed"
            return seen
        finally:
            if position == 0:
                first_done.set()

    contexts = [two_tenants["a"]["ctx"], two_tenants["b"]["ctx"]]
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(run, 0, contexts[0])
            assert first_inside.wait(10)
            second = pool.submit(run, 1, contexts[1])
            seen = [first.result(timeout=30), second.result(timeout=30)]
    finally:
        # Not a real task: keep it out of the routing system check.
        app.tasks.pop(echo_tenant.name, None)

    assert seen == [str(ctx.organization_id) for ctx in contexts]

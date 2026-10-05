"""RAG tasks. Queue ``heavy`` (Task 40): embedding waits on a provider, and
must never occupy the ``critical`` workers sign-in and the outbox depend on,
nor the ``default`` ones routing and automation share. Retries are the
pipeline's own durable state (a due-work timer), never Celery's."""

from __future__ import annotations

from celery import shared_task

from workeasy360.platform.tenancy.tasks import TenantTask


@shared_task(
    base=TenantTask,
    queue="heavy",
    # Above the embedding deadline plus two short transactions, below the
    # indexing lease (RAG_INDEX_LEASE_S), so a worker is killed before
    # another could legitimately re-claim its attempt.
    soft_time_limit=90,
    time_limit=120,
    name="intelligence.rag.index_version",
    max_retries=0,
)
def index_version(version_pk: str) -> str:
    from workeasy360.intelligence.rag.indexing import run_index

    return run_index(version_pk)


@shared_task(
    base=TenantTask,
    queue="heavy",
    soft_time_limit=60,
    time_limit=90,
    name="intelligence.rag.purge_source",
    max_retries=0,
)
def purge_source(source_pk: str) -> str:
    from uuid import UUID

    from workeasy360.intelligence.rag.ingestion import purge_source as purge
    from workeasy360.platform.tenancy.context import current_tenant
    from workeasy360.platform.tenancy.db import organization_db_scope

    ctx = current_tenant()
    with organization_db_scope(ctx.organization_id):
        return purge(UUID(str(source_pk)))

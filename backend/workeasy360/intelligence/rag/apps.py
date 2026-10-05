"""App configuration for intelligence/rag."""

from __future__ import annotations

from django.apps import AppConfig


class RagConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workeasy360.intelligence.rag"
    label = "rag"
    verbose_name = "Intelligence | Retrieval"

    def ready(self) -> None:
        from workeasy360.integration.scheduling.services import register_dispatcher
        from workeasy360.intelligence.rag import tasks
        from workeasy360.intelligence.rag.ingestion import INDEX_TIMER, PURGE_TIMER

        register_dispatcher(
            INDEX_TIMER,
            lambda item: tasks.index_version.apply_async(args=[str(item.target_id)]),
        )
        register_dispatcher(
            PURGE_TIMER,
            lambda item: tasks.purge_source.apply_async(args=[str(item.target_id)]),
        )

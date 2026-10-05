"""The AI module's vocabulary: lifecycle states, tiers, capabilities.

Pure (no Django), so other modules - the gateway, retrieval - can name an
execution's status or an operation without importing this module's ORM
models (F-6, ``test_no_cross_module_orm_access``).
"""

from __future__ import annotations

from workeasy360.contracts.enums import StrEnum


class RegistryStatus(StrEnum):
    """Provider and model lifecycle. ``deprecated`` models still answer a
    skill that pins them but are never *selected*; ``disabled`` is the kill
    switch."""

    ACTIVE = "active"
    DEPRECATED = "deprecated"
    DISABLED = "disabled"


class ModelTier(StrEnum):
    """What a model costs relative to its peers. An organization uses
    ``premium`` models only when the platform allows it (Task 88)."""

    STANDARD = "standard"
    PREMIUM = "premium"


class Capability(StrEnum):
    """What a model can do. A skill declares the capabilities it needs and
    the gateway selects among models that have them (Task 5)."""

    TEXT = "text"
    STRUCTURED_OUTPUT = "structured_output"
    EMBEDDINGS = "embeddings"
    LONG_CONTEXT = "long_context"


class SkillStatus(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"


class ExecutionStatus(StrEnum):
    """::

        queued (async) ─┐
                        ├─> running ──> succeeded | failed
        reserved (sync) ┘        └─ lease lapsed ─> expired

    ``expired`` settles a reservation whose caller vanished at its full
    reserved amount - the provider may have been called, so the platform
    assumes it was (fail closed on cost, Task 59).
    """

    QUEUED = "queued"
    RESERVED = "reserved"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"

    @property
    def is_terminal(self) -> bool:
        return self in (
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED,
            ExecutionStatus.EXPIRED,
        )


class Operation(StrEnum):
    #: A skill invocation through the gateway.
    INVOKE = "invoke"
    #: A direct retrieval request (one query embedding).
    RETRIEVE = "retrieve"
    #: Embedding one document version for the index.
    INDEX = "index"


IN_FLIGHT_STATUSES = (
    ExecutionStatus.QUEUED.value,
    ExecutionStatus.RESERVED.value,
    ExecutionStatus.RUNNING.value,
)

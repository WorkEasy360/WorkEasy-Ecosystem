"""The skill and prompt registry (Tasks 13, 14, 17, 18).

A **skill** is a versioned AI contract a product owns: trusted instructions
(the prompt), an input schema, an optional output schema, the model
capabilities and tier it needs, whether it retrieves, and who else may invoke
it. Workeasy360 stores, validates and runs it; what it *means* - what a good
deal summary is - stays with the product (ADR-011).

**Registration is by the owning product, over its own credential**, and the
namespace is structural: the key must be ``product.<authenticated code>.*``,
so Product A cannot create, change or retire anything in Product B's
namespace, and nobody but Workeasy360 can hold ``platform.*`` (Task 14).

**A registered version is immutable** (Task 13). Registration is activation;
re-registering the identical definition is idempotent, a different
definition under the same number is a conflict, and a database trigger
refuses any change to a stored version except its status. To change a
prompt, register the next version.

**Invocation is deny by default** (Task 18): the owner may invoke its skill;
another product only if the owner listed it in ``shared_with``; ``platform.*``
skills are shared with every product by definition. A skill a caller may not
invoke is reported exactly as one that does not exist.

**Platform skills** (``platform.*``) are defined in code below and written to
the registry on first use, so they are versioned and immutable exactly like a
product's - a code change to a published version is refused and logged, not
silently applied.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Final

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from workeasy360.contracts.errors import (
    AIInvalidRequest,
    AIRegistryConflict,
    AISkillNotFound,
)
from workeasy360.contracts.values import intelligence_keys as keys
from workeasy360.intelligence.ai import registry
from workeasy360.intelligence.ai.domain import (
    Capability,
    ModelTier,
    RegistryStatus,
    SkillStatus,
)
from workeasy360.intelligence.ai.models import AISkill, AISkillVersion
from workeasy360.intelligence.ai.safety import redact
from workeasy360.intelligence.ai.schema import SchemaError, check_definition
from workeasy360.platform.observability.logging import get_logger
from workeasy360.platform.observability.metrics import metrics

logger = get_logger(__name__)

MAX_INSTRUCTIONS_CHARS: Final = 16_000
MAX_DESCRIPTION_CHARS: Final = 300
MAX_SHARED_WITH: Final = 20
MAX_SKILLS_PER_OWNER: Final = 200
MAX_VERSIONS_PER_SKILL: Final = 100
#: Capabilities a skill may require beyond text.
SKILL_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {Capability.STRUCTURED_OUTPUT.value, Capability.LONG_CONTEXT.value}
)
_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "key",
        "version",
        "description",
        "instructions",
        "input_schema",
        "output_schema",
        "capabilities",
        "tier",
        "pinned_model",
        "max_output_tokens",
        "retrieval",
        "shared_with",
    }
)


@dataclass(frozen=True, slots=True)
class SkillDTO:
    key: str
    version: int
    owner: str
    status: str
    description: str
    instructions: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    capabilities: frozenset[str]
    tier: str
    pinned_model_key: str
    max_output_tokens: int
    retrieval_enabled: bool
    retrieval_top_k: int
    shared_with: tuple[str, ...]

    @property
    def ref(self) -> str:
        return keys.versioned_ref(self.key, self.version)

    def public(self) -> dict[str, Any]:
        """What the owning product is told about its registration."""
        return {
            "key": self.key,
            "version": self.version,
            "status": self.status,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "capabilities": sorted(self.capabilities),
            "tier": self.tier,
            "pinned_model": self.pinned_model_key,
            "max_output_tokens": self.max_output_tokens,
            "retrieval": {
                "enabled": self.retrieval_enabled,
                "top_k": self.retrieval_top_k,
            },
            "shared_with": list(self.shared_with),
        }


# --- registration ---------------------------------------------------------------------


def register(product_code: str, client_id: str, payload: Any) -> tuple[SkillDTO, bool]:
    """Register one skill version for the calling product. Returns the
    version and whether it was newly created. Validates everything before the
    first write (D-016)."""
    definition = _parse(product_code, payload)
    digest = _hash(definition)
    key, version = definition["key"], definition["version"]

    with transaction.atomic():
        skill = AISkill.objects.filter(key=key).first()
        if skill is None:
            if AISkill.objects.filter(owner=product_code).count() >= MAX_SKILLS_PER_OWNER:
                raise AIRegistryConflict(
                    f"A product may register at most {MAX_SKILLS_PER_OWNER} skills."
                )
            try:
                with transaction.atomic():
                    skill = AISkill.objects.create(key=key, owner=product_code)
            except IntegrityError:
                skill = AISkill.objects.get(key=key)
        existing = AISkillVersion.objects.filter(skill=skill, version=version).first()
        if existing is not None:
            if existing.definition_hash != digest:
                raise AIRegistryConflict(
                    "This skill version is already registered with a different "
                    "definition. Registered versions are immutable; register a "
                    "new version.",
                    key=key,
                    version=version,
                )
            return _dto(skill, existing), False
        if AISkillVersion.objects.filter(skill=skill).count() >= MAX_VERSIONS_PER_SKILL:
            raise AIRegistryConflict(
                f"A skill may have at most {MAX_VERSIONS_PER_SKILL} versions."
            )
        try:
            with transaction.atomic():
                row = _create_version(skill, definition, digest, registered_by=client_id)
        except IntegrityError:
            row = AISkillVersion.objects.get(skill=skill, version=version)
            if row.definition_hash != digest:
                raise AIRegistryConflict(
                    "This skill version is already registered with a different definition.",
                    key=key,
                    version=version,
                ) from None
            return _dto(skill, row), False

    metrics.increment("ai_skills_registered_total")
    logger.info("ai.skill.registered", skill=key, version=version, product=product_code)
    return _dto(skill, row), True


def retire(product_code: str, key: Any, version: Any) -> SkillDTO:
    """The owner stops a version being invoked. Irreversible for that number;
    register a new version to resume. Not the owner = not found."""
    if not isinstance(key, str) or not keys.product_owns_key(product_code, key):
        raise AISkillNotFound("No such skill version.")
    if isinstance(version, bool) or not isinstance(version, int):
        raise AIInvalidRequest("version must be an integer.", field="version")
    with transaction.atomic():
        row = (
            AISkillVersion.objects.select_for_update()
            .select_related("skill")
            .filter(skill__key=key, version=version)
            .first()
        )
        if row is None:
            raise AISkillNotFound("No such skill version.")
        if row.status != SkillStatus.RETIRED.value:
            row.status = SkillStatus.RETIRED.value
            row.retired_at = timezone.now()
            row.save(update_fields=["status", "retired_at"])
    return _dto(row.skill, row)


def owned(product_code: str) -> list[SkillDTO]:
    """Every skill version the calling product registered."""
    rows = (
        AISkillVersion.objects.select_related("skill")
        .filter(skill__owner=product_code)
        .order_by("skill__key", "version")
    )
    return [_dto(r.skill, r) for r in rows[: MAX_SKILLS_PER_OWNER * 5]]


# --- invocation ---------------------------------------------------------------------------


def resolve(caller: str, key: Any, version: Any = None) -> SkillDTO:
    """The active version ``caller`` may invoke, or ``AISkillNotFound``.

    Not-found and not-permitted are one answer (Task 18)."""
    if not isinstance(key, str) or keys.owner_of(key) is None:
        raise AISkillNotFound("No such skill.")
    if version is not None and (
        isinstance(version, bool) or not isinstance(version, int)
    ):
        raise AIInvalidRequest("version must be an integer.", field="version")
    if keys.is_platform_key(key):
        ensure_platform_skills()

    rows = AISkillVersion.objects.select_related("skill").filter(
        skill__key=key, status=SkillStatus.ACTIVE.value
    )
    rows = (
        rows.filter(version=version) if version is not None else rows.order_by("-version")
    )
    row = rows.first()
    if row is None:
        raise AISkillNotFound("No such skill.")
    dto = _dto(row.skill, row)
    if not _may_invoke(caller, dto):
        metrics.increment("ai_access_denied_total", reason="skill_not_shared")
        raise AISkillNotFound("No such skill.")
    return dto


def _may_invoke(caller: str, skill: SkillDTO) -> bool:
    if skill.owner == keys.PLATFORM_ROOT:
        return True
    return skill.owner == caller or caller in skill.shared_with


# --- platform skills --------------------------------------------------------------------

#: Workeasy360's own skills. Generic by construction - no product's business
#: meaning (A14) - and invocable by any product over its *own* knowledge.
PLATFORM_SKILLS: Final[tuple[dict[str, Any], ...]] = (
    {
        "key": "platform.grounded_answer",
        "version": 1,
        "description": "Answer a question from the caller's own indexed knowledge, with citations.",
        "instructions": (
            "Answer the question in the input using only the information in the "
            "provided context. After every statement that relies on the context, "
            "cite the source by its id in square brackets, for example [S1]. If the "
            "context does not contain the answer, say that you do not know. Be "
            "concise."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "minLength": 1, "maxLength": 2000}
            },
            "required": ["question"],
        },
        "output_schema": None,
        "capabilities": [],
        "tier": "standard",
        "pinned_model": "",
        "max_output_tokens": 1024,
        "retrieval": {"enabled": True, "top_k": 5},
        "shared_with": [],
    },
    {
        "key": "platform.summarize",
        "version": 1,
        "description": "Summarise the supplied text.",
        "instructions": (
            "Summarise the text in the input in plain language. Keep every fact "
            "that matters and add none. Be concise."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "minLength": 1, "maxLength": 50000}
            },
            "required": ["text"],
        },
        "output_schema": None,
        "capabilities": [],
        "tier": "standard",
        "pinned_model": "",
        "max_output_tokens": 1024,
        "retrieval": {"enabled": False, "top_k": 5},
        "shared_with": [],
    },
)


def ensure_platform_skills() -> None:
    """Write the code-defined platform skills to the registry. Idempotent and
    race-safe; a published version whose code definition changed is refused
    (logged) rather than rewritten."""
    for payload in PLATFORM_SKILLS:
        definition = _parse(keys.PLATFORM_ROOT, payload, platform=True)
        digest = _hash(definition)
        skill, _ = AISkill.objects.get_or_create(
            key=definition["key"], defaults={"owner": keys.PLATFORM_ROOT}
        )
        existing = AISkillVersion.objects.filter(
            skill=skill, version=definition["version"]
        ).first()
        if existing is not None:
            if existing.definition_hash != digest:
                logger.error(
                    "ai.platform_skill.drift",
                    skill=definition["key"],
                    version=definition["version"],
                )
            continue
        try:
            with transaction.atomic():
                _create_version(skill, definition, digest, registered_by="platform")
        except IntegrityError:
            continue


# --- internals ------------------------------------------------------------------------------


def _parse(owner: str, payload: Any, *, platform: bool = False) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AIInvalidRequest("The skill must be a JSON object.")
    unknown = sorted(set(payload) - _FIELDS)
    if unknown:
        raise AIInvalidRequest(
            f"Unknown field(s): {', '.join(unknown)}.", field=unknown[0]
        )

    key = payload.get("key")
    owns = keys.is_platform_key(key) if platform else keys.product_owns_key(owner, key)
    if not isinstance(key, str) or not owns:
        raise AIInvalidRequest(
            f"The skill key must be 'product.{owner}.<name>' - a product registers "
            "skills only in its own namespace.",
            field="key",
        )
    version = _int(payload.get("version"), "version", 1, keys.MAX_VERSION)
    description = _text(
        payload.get("description", ""), "description", 0, MAX_DESCRIPTION_CHARS
    )
    instructions = _text(
        payload.get("instructions"), "instructions", 1, MAX_INSTRUCTIONS_CHARS
    )
    if redact(instructions)[1]:
        raise AIInvalidRequest(
            "The instructions contain what looks like a credential. Prompts are "
            "sent to AI providers and must never carry secrets.",
            field="instructions",
        )

    input_schema = payload.get("input_schema")
    _schema(input_schema, "input_schema")
    if input_schema.get("type") != "object":
        raise AIInvalidRequest(
            "input_schema must describe an object.", field="input_schema"
        )
    output_schema = payload.get("output_schema")
    if output_schema is not None:
        _schema(output_schema, "output_schema")

    capabilities = payload.get("capabilities", [])
    if (
        not isinstance(capabilities, list)
        or not all(isinstance(c, str) for c in capabilities)
        or not set(capabilities) <= SKILL_CAPABILITIES
    ):
        raise AIInvalidRequest(
            f"capabilities may list only {', '.join(sorted(SKILL_CAPABILITIES))}.",
            field="capabilities",
        )
    tier = payload.get("tier", ModelTier.STANDARD.value)
    if tier not in {t.value for t in ModelTier}:
        raise AIInvalidRequest("tier must be 'standard' or 'premium'.", field="tier")

    pinned = payload.get("pinned_model", "") or ""
    if not isinstance(pinned, str):
        raise AIInvalidRequest("pinned_model must be a model key.", field="pinned_model")
    if pinned:
        model = registry.model(pinned)
        if model is None or model.status == RegistryStatus.DISABLED.value:
            raise AIInvalidRequest(
                "pinned_model is not an approved model.", field="pinned_model"
            )

    max_output = _int(
        payload.get("max_output_tokens", 1024),
        "max_output_tokens",
        1,
        int(settings.AI_MAX_OUTPUT_TOKENS),
    )
    retrieval = payload.get("retrieval") or {}
    if not isinstance(retrieval, dict) or set(retrieval) - {"enabled", "top_k"}:
        raise AIInvalidRequest(
            "retrieval must be {'enabled': bool, 'top_k': int}.", field="retrieval"
        )
    retrieval_enabled = retrieval.get("enabled", False)
    if not isinstance(retrieval_enabled, bool):
        raise AIInvalidRequest("retrieval.enabled must be a boolean.", field="retrieval")
    top_k = _int(
        retrieval.get("top_k", settings.RAG_DEFAULT_TOP_K),
        "retrieval.top_k",
        1,
        int(settings.RAG_MAX_TOP_K),
    )

    shared = payload.get("shared_with", [])
    if (
        not isinstance(shared, list)
        or len(shared) > MAX_SHARED_WITH
        or not all(isinstance(p, str) and keys.is_product_code(p) for p in shared)
        or owner in shared
    ):
        raise AIInvalidRequest(
            f"shared_with must list at most {MAX_SHARED_WITH} other product codes.",
            field="shared_with",
        )

    return {
        "key": key,
        "version": version,
        "description": description,
        "instructions": instructions,
        "input_schema": input_schema,
        "output_schema": output_schema,
        "capabilities": sorted(set(capabilities)),
        "tier": tier,
        "pinned_model": pinned,
        "max_output_tokens": max_output,
        "retrieval_enabled": retrieval_enabled,
        "retrieval_top_k": top_k,
        "shared_with": sorted(set(shared)),
    }


def _create_version(
    skill: AISkill, definition: dict[str, Any], digest: str, *, registered_by: str
) -> AISkillVersion:
    return AISkillVersion.objects.create(
        skill=skill,
        version=definition["version"],
        description=definition["description"],
        instructions=definition["instructions"],
        input_schema=definition["input_schema"],
        output_schema=definition["output_schema"],
        capabilities=definition["capabilities"],
        tier=definition["tier"],
        pinned_model_key=definition["pinned_model"],
        max_output_tokens=definition["max_output_tokens"],
        retrieval_enabled=definition["retrieval_enabled"],
        retrieval_top_k=definition["retrieval_top_k"],
        shared_with=definition["shared_with"],
        definition_hash=digest,
        registered_by=registered_by[:64],
    )


def _hash(definition: dict[str, Any]) -> str:
    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _schema(value: Any, field: str) -> None:
    try:
        check_definition(value)
    except SchemaError as exc:
        raise AIInvalidRequest(f"{field}: {exc.message}", field=field) from None


def _int(value: Any, field: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise AIInvalidRequest(
            f"{field} must be an integer from {low} to {high}.", field=field
        )
    return value


def _text(value: Any, field: str, low: int, high: int) -> str:
    if not isinstance(value, str) or not low <= len(value) <= high:
        raise AIInvalidRequest(f"{field} must be {low}-{high} characters.", field=field)
    return value


def _dto(skill: AISkill, row: AISkillVersion) -> SkillDTO:
    return SkillDTO(
        key=skill.key,
        version=row.version,
        owner=skill.owner,
        status=row.status,
        description=row.description,
        instructions=row.instructions,
        input_schema=row.input_schema,
        output_schema=row.output_schema,
        capabilities=frozenset(row.capabilities or ()),
        tier=row.tier,
        pinned_model_key=row.pinned_model_key,
        max_output_tokens=row.max_output_tokens,
        retrieval_enabled=row.retrieval_enabled,
        retrieval_top_k=row.retrieval_top_k,
        shared_with=tuple(row.shared_with or ()),
    )

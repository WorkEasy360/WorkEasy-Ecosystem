"""Django system checks: W360.E001 - W360.E009.

Rules the CI does not check are rules that do not exist. These run under
``manage.py check --deploy``, in CI, and at container start, so a violation
fails the build or the boot rather than surfacing as a production incident
(02-dependency-rules.md section 6.2).

Each check is written to fail *loudly and specifically*: the message names the
offending model or task and the rule it breaks, because a check that says
"invalid configuration" costs more time than it saves.
"""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.checks import CheckMessage, Error, register
from django.core.checks import Warning as CheckWarning
from django.db import models

#: Cross-module foreign keys are limited to this frozen allowlist
#: (02-dependency-rules.md F-6). Adding to it requires an ADR.
ALLOWED_CROSS_MODULE_FK_TARGETS = frozenset(
    {
        "organizations.Organization",
        "identity.User",
        "files.File",
        # Fourth entry, added by ADR-014. Membership is the junction through
        # which all authorization resolves, and Phase 0's own model
        # definitions require this FK in at least two places
        # (04-identity-authz.md section 4.4 RoleAssignment,
        # 06-entitlements.md section 6 SeatAssignment).
        #
        # Narrowed: only platform/ and commercial/ may hold it. Products use
        # identity.User for attribution, as the original allowlist intends.
        # Enforced by MEMBERSHIP_FK_ALLOWED_MODULES below.
        "organizations.Membership",
    }
)

#: Modules permitted to hold a FK to Membership. All are in the kernel that
#: ADR-001 declares never extractable, so the extractability argument behind
#: F-6 does not apply to them.
MEMBERSHIP_FK_ALLOWED_PACKAGES = ("platform", "commercial")

#: Packages whose modules may hold foreign keys to **each other**, added by
#: ADR-016.
#:
#: F-6 exists because an FK welds the database to another module and makes
#: that module unextractable on its own. Inside ``commercial/`` that argument
#: does not apply: ADR-006 specifies these exact foreign keys
#: (``PlanPrice.plan_version``, ``SubscriptionItem.plan_version``), and
#: ADR-006's own extraction path moves catalog and pricing **together** as one
#: unit. The integrity they buy is load-bearing - ``on_delete=PROTECT`` is
#: what makes it impossible to delete a plan version a subscription pinned,
#: which is the mechanism behind "historical pricing cannot mutate".
#:
#: Narrow on purpose: it permits commercial -> commercial only. ``platform``
#: siblings stay forbidden (F-5 already bans even importing sideways there),
#: products stay forbidden from everything, and no FK may leave ``commercial``
#: except to the frozen allowlist above.
INTRA_PACKAGE_FK_PACKAGES = frozenset({"commercial"})

#: Models that legitimately carry a tenant column without being tenant-scoped,
#: each for a documented reason. Exemptions are explicit rather than inferred:
#: a model that quietly escapes the check is how isolation gaps appear.
TENANT_SCOPE_EXEMPT = frozenset(
    {
        # The tenant itself. An Organization does not belong to an
        # organization; it *is* one.
        "organizations.Organization",
        # D-001: holds both global system templates (organization_id IS NULL)
        # and tenant-owned custom roles. Protected by an asymmetric RLS policy
        # instead - see enable_system_template_rls().
        "authz.Role",
        # Infrastructure tables drained by the relay across every tenant.
        # They carry organization_id as a plain column for routing and
        # filtering, and are never read by application code
        # (07-cross-product-integration.md section 3.4 defines OutboxEvent as
        # a plain models.Model for exactly this reason).
        "events.OutboxEvent",
        "events.DeadLetterEvent",
        # ADR-020. The token endpoint has no tenant context and cannot have
        # one: there is no session, no membership and no organization until
        # the authorization code is found, and the code is what establishes
        # them. A tenant-scoped manager would require the answer in order to
        # ask the question.
        #
        # Protected instead by being reachable only through the SHA-256 of a
        # 256-bit random secret, additionally bound to one client, one
        # redirect URI and one PKCE challenge, and valid for sixty seconds.
        # This is the same class as identity.User - control-plane-adjacent,
        # carrying a tenant reference it does not derive access from.
        "ecosystem.AuthorizationCode",
        # Phase 4. Identical reasoning to AuthorizationCode above - reachable
        # only through the SHA-256 of a random secret, and the organization
        # it names (when it names one) is re-verified against a live
        # membership at resume, not trusted from this row.
        "ecosystem.PendingAuthorization",
    }
)

#: Indexes that intentionally do not lead with organization, with the query
#: each one serves. Every entry is a cross-tenant lookup that happens before
#: tenant context exists.
INDEX_PREFIX_EXEMPT = frozenset(
    {
        # "Which organizations does this user belong to?" runs at login and
        # on the org switcher, before any tenant context is established.
        # Leading with organization would make it useless for that query.
        "ix_membership_user",
    }
)


def _module_of(model: type[models.Model]) -> str:
    """The owning module label, e.g. ``organizations`` for Organization."""
    return model._meta.app_label


def _is_ours(model: type[models.Model]) -> bool:
    """Whether this model is Workeasy360 code.

    Django's own models (auth.Permission, contenttypes.ContentType) are not
    subject to our architecture rules - we do not control them, and flagging
    them would train people to ignore these checks.
    """
    return (model.__module__ or "").startswith("workeasy360.")


def _our_models() -> list[type[models.Model]]:
    return [m for m in apps.get_models() if _is_ours(m) and not m._meta.abstract]


def _tenant_models() -> list[type[models.Model]]:
    from workeasy360.platform.tenancy.models import TenantScopedModel

    return [
        m
        for m in apps.get_models()
        if issubclass(m, TenantScopedModel) and not m._meta.abstract
    ]


@register("w360")
def check_tenant_scoped_models(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """W360.E001 - a model with ``organization`` must inherit TenantScopedModel.

    The failure this prevents: someone adds ``organization = FK(...)`` to a new
    model, it looks tenant-scoped, and it has no scoped manager, no RLS policy
    and no suspension block. It would work perfectly in every test that sets a
    context, and leak in production.
    """
    from workeasy360.platform.tenancy.models import TenantScopedModel

    errors: list[CheckMessage] = []
    for model in _our_models():
        label = f"{model._meta.app_label}.{model.__name__}"
        if label in TENANT_SCOPE_EXEMPT:
            continue

        has_org_field = any(
            f.name == "organization" or f.attname == "organization_id"
            for f in model._meta.get_fields()
            if hasattr(f, "attname")
        )
        if has_org_field and not issubclass(model, TenantScopedModel):
            errors.append(
                Error(
                    f"{label} has an organization field but does not inherit "
                    f"TenantScopedModel.",
                    hint=(
                        "Inherit workeasy360.platform.tenancy.models."
                        "TenantScopedModel. Without it the model has no "
                        "tenant-scoped manager, no RLS policy and no "
                        "suspension write block."
                    ),
                    obj=model,
                    id="W360.E001",
                )
            )
    return errors


@register("w360")
def check_cross_module_foreign_keys(
    app_configs: Any, **kwargs: Any
) -> list[CheckMessage]:
    """W360.E003 - no cross-module FK outside the frozen allowlist.

    A queryset over another module's model welds you to its schema; a foreign
    key welds the *database* to it and makes the module unextractable.
    """
    errors: list[CheckMessage] = []
    for model in _our_models():
        source_module = _module_of(model)
        source_package = (model.__module__ or "").split(".")[1:2]

        for field in model._meta.get_fields():
            if not isinstance(field, models.ForeignKey | models.OneToOneField):
                continue
            target = field.related_model
            if target is None or not _is_ours(target):
                continue
            target_module = _module_of(target)
            if target_module == source_module:
                continue

            target_label = f"{target_module}.{target.__name__}"

            # ADR-016: commercial modules may reference each other.
            target_package = (target.__module__ or "").split(".")[1:2]
            if (
                source_package
                and source_package == target_package
                and source_package[0] in INTRA_PACKAGE_FK_PACKAGES
            ):
                continue

            if target_label not in ALLOWED_CROSS_MODULE_FK_TARGETS:
                errors.append(
                    Error(
                        f"{source_module}.{model.__name__}.{field.name} is a "
                        f"foreign key to {target_label}, which crosses a module "
                        f"boundary and is not on the allowlist.",
                        hint=(
                            f"Allowed cross-module FK targets are "
                            f"{sorted(ALLOWED_CROSS_MODULE_FK_TARGETS)}. Use a "
                            f"soft reference (product, entity, id) instead, or "
                            f"write an ADR to extend the allowlist."
                        ),
                        obj=model,
                        id="W360.E003",
                    )
                )
                continue

            # The Membership entry is narrower than the other three: it is
            # restricted to the never-extracted kernel (ADR-014).
            if (
                target_label == "organizations.Membership"
                and source_package
                and source_package[0] not in MEMBERSHIP_FK_ALLOWED_PACKAGES
            ):
                errors.append(
                    Error(
                        f"{source_module}.{model.__name__}.{field.name} holds a "
                        f"foreign key to organizations.Membership from "
                        f"{source_package[0]}/, which is not permitted.",
                        hint=(
                            f"Only {list(MEMBERSHIP_FK_ALLOWED_PACKAGES)} may "
                            f"reference Membership (ADR-014). Products use "
                            f"identity.User for attribution, or a soft "
                            f"reference."
                        ),
                        obj=model,
                        id="W360.E003",
                    )
                )
    return errors


@register("w360")
def check_tenant_index_prefix(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """B3 - every index on a tenant table leads with ``organization``.

    RLS adds a predicate to every query on a tenant table. That is negligible
    *provided* organization_id leads the relevant index; without it, it is a
    full scan per query (10-performance-concurrency.md section 4).

    A warning rather than an error: a non-leading index is a performance
    defect, not a correctness one, and failing the build would be
    disproportionate.
    """
    warnings: list[CheckMessage] = []
    for model in _tenant_models():
        label = f"{model._meta.app_label}.{model.__name__}"
        for index in model._meta.indexes:
            if not index.fields or index.name in INDEX_PREFIX_EXEMPT:
                continue
            first = index.fields[0].lstrip("-")
            if first not in {"organization", "organization_id"}:
                warnings.append(
                    CheckWarning(
                        f"{label}: index {index.name or index.fields} does not "
                        f"lead with 'organization'.",
                        hint=(
                            "Every index on a tenant table should start with "
                            "organization so the RLS predicate is index-served "
                            "rather than producing a scan."
                        ),
                        obj=model,
                        id="W360.W003",
                    )
                )
    return warnings


@register("w360")
def check_celery_task_routing(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """W360.E007 - every task declares a queue and time limits.

    Bulkheads are meaningless if tasks land in ``default`` by accident, and a
    task with no time limit runs forever holding a worker
    (10-performance-concurrency.md section 6).
    """
    errors: list[CheckMessage] = []
    try:
        from config.celery import app as celery_app
    except Exception:  # pragma: no cover - celery not configured in some tools
        return []

    from django.conf import settings

    valid_queues = set(getattr(settings, "CELERY_QUEUE_GROUPS", ()))

    # Tasks are registered by each AppConfig.ready(), so by the time checks
    # run the registry is complete. This check deliberately does *not* force
    # autodiscovery itself: doing so from inside the check framework nests
    # the import machinery deep enough to exhaust the recursion limit, and
    # Celery swallows that failure in its signal handler - leaving the check
    # passing while seeing a partial registry.
    #
    # An empty registry is reported rather than silently accepted, because a
    # check that verifies nothing is worse than no check at all.
    discovered = [n for n in celery_app.tasks if not n.startswith("celery.")]
    if not discovered:
        return [
            CheckWarning(
                "No application Celery tasks are registered.",
                hint=(
                    "Each module with tasks imports them from its "
                    "AppConfig.ready(). If that import is missing, this "
                    "routing check verifies nothing."
                ),
                id="W360.W007",
            )
        ]

    for name, task in sorted(celery_app.tasks.items()):
        if name.startswith("celery."):
            continue

        queue = getattr(task, "queue", None)
        if not queue:
            errors.append(
                Error(
                    f"Celery task {name!r} does not declare a queue.",
                    hint=(
                        f"Add queue=<one of {sorted(valid_queues)}>. Routing "
                        f"is declared per task from day one so queues can be "
                        f"split later without touching business code (R-12)."
                    ),
                    id="W360.E007",
                )
            )
        elif valid_queues and queue not in valid_queues:
            errors.append(
                Error(
                    f"Celery task {name!r} declares unknown queue {queue!r}.",
                    hint=f"Valid queue groups: {sorted(valid_queues)}.",
                    id="W360.E007",
                )
            )

        if not getattr(task, "soft_time_limit", None):
            errors.append(
                Error(
                    f"Celery task {name!r} has no soft_time_limit.",
                    hint=(
                        "A task without a limit runs forever and holds a "
                        "worker. Set soft_time_limit and time_limit."
                    ),
                    id="W360.E007",
                )
            )
        if not getattr(task, "time_limit", None):
            errors.append(
                Error(
                    f"Celery task {name!r} has no time_limit.",
                    hint="Set a hard time_limit above soft_time_limit.",
                    id="W360.E007",
                )
            )
    return errors


@register("w360")
def check_event_schemas_registered(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """W360.E006 - an event type published must have a registered schema."""
    from workeasy360.contracts.events import platform_events  # noqa: F401
    from workeasy360.contracts.events.registry import registry

    if not registry.all_refs():
        return [
            CheckWarning(
                "No event schemas are registered.",
                hint=(
                    "contracts/events/ declaration modules must be imported "
                    "for the registry to be populated; publishing an "
                    "unregistered type raises UnknownEventType."
                ),
                id="W360.W006",
            )
        ]
    return []


@register("w360")
def check_no_cross_module_signals(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """W360.E009 - no cross-module pre_save/post_save receiver.

    Signals across modules are invisible dependencies with no ordering
    guarantees, they fire inside the sender's transaction, and they break
    silently on bulk operations (F-8).
    """
    from django.db.models import signals

    errors: list[CheckMessage] = []
    watched = (
        ("pre_save", signals.pre_save),
        ("post_save", signals.post_save),
        ("pre_delete", signals.pre_delete),
        ("post_delete", signals.post_delete),
    )

    for signal_name, signal in watched:
        for lookup_key, receiver_ref in signal.receivers:
            _, sender_id = lookup_key
            receiver = receiver_ref() if callable(receiver_ref) else receiver_ref
            if receiver is None:
                continue

            receiver_module = getattr(receiver, "__module__", "") or ""
            if not receiver_module.startswith("workeasy360."):
                continue

            sender = _sender_from_id(sender_id)
            if sender is None:
                continue

            sender_module = _owning_package(sender.__module__)
            handler_module = _owning_package(receiver_module)
            if sender_module and handler_module and sender_module != handler_module:
                errors.append(
                    Error(
                        f"{signal_name} receiver "
                        f"{receiver_module}.{getattr(receiver, '__name__', '?')} "
                        f"listens to {sender.__name__} from another module "
                        f"({sender_module}).",
                        hint=(
                            "Cross-module signals are forbidden (F-8). Use an "
                            "explicit application service call, or publish a "
                            "domain event to the outbox."
                        ),
                        id="W360.E009",
                    )
                )
    return errors


def _sender_from_id(sender_id: Any) -> type | None:
    """Recover the sender class from a signal's weak reference id."""
    for model in apps.get_models():
        if id(model) == sender_id:
            return model
    return None


def _owning_package(module_path: str) -> str:
    """``workeasy360.platform.audit.services`` -> ``platform.audit``."""
    parts = module_path.split(".")
    if len(parts) >= 3 and parts[0] == "workeasy360":
        return f"{parts[1]}.{parts[2]}"
    return ""


@register("w360", deploy=True)
def check_database_role_cannot_bypass_rls(
    app_configs: Any, **kwargs: Any
) -> list[CheckMessage]:
    """R-02 startup assertion - the runtime role must not bypass RLS.

    Deploy-only because it needs a live connection. This is the check that
    turns "every policy exists and none is enforced" from an invisible
    catastrophe into a refused boot.
    """
    from workeasy360.platform.tenancy.db import (
        assert_connection_role_cannot_bypass_rls,
    )

    try:
        problems = assert_connection_role_cannot_bypass_rls()
    except Exception as exc:  # pragma: no cover - DB unreachable
        return [
            CheckWarning(
                f"Could not verify the database role's RLS privileges: {exc}",
                id="W360.W002",
            )
        ]

    return [
        Error(
            problem,
            hint=(
                "Create a dedicated application role with NOSUPERUSER and "
                "NOBYPASSRLS, and run migrations as a separate owner role "
                "(infra/postgres/init.sql)."
            ),
            id="W360.E002",
        )
        for problem in problems
    ]


@register("w360", deploy=True)
def check_tenant_tables_have_rls(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """W360.E002 - every TenantScopedModel table has a complete RLS policy."""
    from workeasy360.platform.tenancy.db import tables_missing_rls

    tables = [m._meta.db_table for m in _tenant_models()]
    try:
        problems = tables_missing_rls(tables)
    except Exception as exc:  # pragma: no cover - DB unreachable
        return [CheckWarning(f"Could not verify RLS policies: {exc}", id="W360.W002")]

    return [
        Error(
            problem,
            hint=(
                "Apply tenancy.migrations_helpers.enable_rls(table) in the "
                "model's migration. RLS is the layer that survives "
                "application bugs; a tenant table without it has only "
                "application filtering."
            ),
            id="W360.E002",
        )
        for problem in problems
    ]

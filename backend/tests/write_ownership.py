"""The suite-wide write-ownership guard (Phase 9 closure, D-032).

Single-writer ownership is asserted statically by the architecture tests; this
proves it at runtime as well. For the whole test session, every write to an
owned table - ``save()``, ``delete()``, and the queryset writes that bypass
both (``update()``, ``bulk_create()``, ``bulk_update()``) - is traced to the
innermost *application* frame that caused it, and that frame must be in the
table's owning module:

- model plumbing (``*.models`` - ``save()`` overrides, managers) is skipped,
  so a caller cannot hide behind the model it writes;
- test code (``tests.*``, ``conftest``, ``*.tests.*``) is allowed to arrange
  state directly, as fixtures do;
- migrations are schema evolution, not application writers;
- what a delete does to *other* tables through their foreign keys' own
  ``on_delete`` (cascade, ``SET_NULL``) is those tables' declared rule, not a
  second writer: the delete that set it off is the write that is checked.

A violation raises at the write *and* is recorded, so it fails the run even if
the code under test swallows the exception (``pytest_sessionfinish`` in the
root conftest). Nothing here runs outside the test suite.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

#: model label -> the module (or package) that alone may write it.
OWNERS: dict[str, str] = {
    "organizations.Organization": "workeasy360.platform.organizations.services",
    "organizations.Membership": "workeasy360.platform.organizations.services",
    "organizations.OrganizationCreationRequest": (
        "workeasy360.platform.organizations.services"
    ),
    "organizations.MemberSignIn": "workeasy360.platform.organizations.services",
    "organizations.UserOrganizationAccess": (
        "workeasy360.platform.organizations.projection"
    ),
    "organizations.Invitation": "workeasy360.platform.administration.invitations",
    "identity.User": "workeasy360.platform.identity",
}

violations: list[str] = []
_expecting: list[list[str]] = []


def _is_test_code(name: str) -> bool:
    return (
        name.startswith("tests")
        or name == "conftest"
        or name.endswith(".conftest")
        or ".tests." in name
        or name == "__main__"
    )


def writer_of(frame: Any) -> str | None:
    """The module that decided this write, or ``None`` when test code,
    a migration or nothing of ours did."""
    while frame is not None:
        name = frame.f_globals.get("__name__", "") or ""
        if name.startswith("workeasy360."):
            if ".migrations." in name:
                return None
            if _is_test_code(name):
                return None
            if not name.endswith(".models"):
                return name
        elif _is_test_code(name):
            return None
        frame = frame.f_back
    return None


def _owns(owner: str, writer: str) -> bool:
    return writer == owner or writer.startswith(owner + ".")


def check(model: Any, operation: str) -> None:
    label = getattr(getattr(model, "_meta", None), "label", "")
    owner = OWNERS.get(label)
    if owner is None:
        return
    writer = writer_of(sys._getframe(2))
    if writer is None or _owns(owner, writer):
        return
    message = f"{label} {operation} from {writer}; only {owner} writes it (single writer)"
    if _expecting:
        _expecting[-1].append(message)
    else:
        violations.append(message)
    raise AssertionError(message)


@contextmanager
def expect_violation() -> Iterator[list[str]]:
    """For the guard's own tests: capture a deliberate violation instead of
    failing the session."""
    caught: list[str] = []
    _expecting.append(caught)
    try:
        yield caught
    finally:
        _expecting.pop()


def install() -> Callable[[], None]:
    """Connect the guard; returns the function that removes it."""
    from django.db.models import QuerySet
    from django.db.models.signals import pre_delete, pre_save

    def on_save(sender: Any, **kwargs: Any) -> None:
        check(sender, "save")

    def on_delete(sender: Any, origin: Any = None, **kwargs: Any) -> None:
        started = getattr(origin, "model", None) or type(origin)
        if origin is not None and started._meta.concrete_model is not (
            sender._meta.concrete_model
        ):
            return  # a cascade another model's foreign key declared
        check(sender, "delete")

    pre_save.connect(on_save, dispatch_uid="w360-write-ownership-save", weak=False)
    pre_delete.connect(on_delete, dispatch_uid="w360-write-ownership-delete", weak=False)

    originals = {
        name: getattr(QuerySet, name) for name in ("update", "bulk_create", "bulk_update")
    }

    def guarded(name: str) -> Callable[..., Any]:
        original = originals[name]

        def method(self: Any, *args: Any, **kwargs: Any) -> Any:
            # Django's delete collector applies ``on_delete=SET_NULL``
            # through ``update()`` on the related model: a declared rule.
            if sys._getframe(1).f_globals.get("__name__") != "django.db.models.deletion":
                check(self.model, name)
            return original(self, *args, **kwargs)

        method.__name__ = name
        return method

    for name in originals:
        setattr(QuerySet, name, guarded(name))

    def uninstall() -> None:
        pre_save.disconnect(dispatch_uid="w360-write-ownership-save")
        pre_delete.disconnect(dispatch_uid="w360-write-ownership-delete")
        for name, original in originals.items():
            setattr(QuerySet, name, original)

    return uninstall

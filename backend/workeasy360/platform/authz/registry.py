"""The permission registry - code-declared, never user-created.

A permission that is not declared in code **cannot be granted**. That single
rule is what makes the full privilege surface of the platform greppable and
reviewable, and it is why asking about an unknown permission raises rather
than quietly denying: a typo in a permission key would otherwise become a
silent, permanent denial that nobody notices until a customer complains.

Naming is mechanical: ``<product>.<resource>.<action>``. A CI check enforces
the pattern and that the product segment matches a registered manifest
(04-identity-authz.md section 4.2).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from workeasy360.contracts.errors import UnknownPermission
from workeasy360.contracts.values.identifiers import PermissionKey


@dataclass(frozen=True, slots=True)
class PermissionDeclaration:
    """One privilege, declared by its owning module."""

    key: PermissionKey
    label: str
    category: str = "general"

    #: Permissions this one subsumes. ``crm.deal.delete`` implies
    #: ``crm.deal.read``. Expanded at **role-compile time**, not at check
    #: time, so a check stays a single set-membership test.
    implies: tuple[str, ...] = ()

    #: Grouped separately in the UI. Blocked outright under impersonation,
    #: and refused while the organization is read-only.
    dangerous: bool = False

    #: Belongs to the control plane (Workeasy360 staff or a partner), not to
    #: a customer organization. **Never granted by a role**: the owner role's
    #: ``"*"`` skips these, so a customer administrator cannot hold one even
    #: through a misconfigured or hand-built role. Staff receive theirs from
    #: ``StaffGrant`` rows and partner users from capability grants (ADR-027).
    control_plane: bool = False

    @property
    def product(self) -> str:
        return self.key.product


class PermissionRegistry:
    """Process-wide registry, populated at app-ready by each module."""

    def __init__(self) -> None:
        self._declarations: dict[str, PermissionDeclaration] = {}
        self._expansion_cache: dict[str, frozenset[str]] = {}

    def register(self, declaration: PermissionDeclaration) -> PermissionDeclaration:
        key = str(declaration.key)
        existing = self._declarations.get(key)
        if existing is not None and existing != declaration:
            raise ValueError(
                f"Permission {key!r} is already declared differently by "
                f"another module. Each permission has exactly one owner."
            )
        self._declarations[key] = declaration
        self._expansion_cache.clear()
        return declaration

    def register_all(
        self, declarations: Iterable[PermissionDeclaration]
    ) -> tuple[PermissionDeclaration, ...]:
        return tuple(self.register(d) for d in declarations)

    def get(self, key: str) -> PermissionDeclaration:
        try:
            return self._declarations[str(key)]
        except KeyError:
            raise UnknownPermission(
                f"Permission {key!r} is not declared in any module's "
                f"permissions.py. A permission that does not exist cannot be "
                f"granted; this is a programming error, not a denial.",
                permission=str(key),
            ) from None

    def exists(self, key: str) -> bool:
        return str(key) in self._declarations

    def all_keys(self) -> list[str]:
        return sorted(self._declarations)

    def all(self) -> list[PermissionDeclaration]:
        return [self._declarations[k] for k in sorted(self._declarations)]

    def tenant_keys(self) -> list[str]:
        """Every permission a customer role may carry - the meaning of ``"*"``."""
        return [str(d.key) for d in self.all() if not d.control_plane]

    def for_product(self, product: str) -> list[PermissionDeclaration]:
        return [d for d in self.all() if d.product == product]

    def expand(self, keys: Iterable[str]) -> frozenset[str]:
        """Close a permission set over ``implies``.

        Done once when a role is compiled, so that a check never walks the
        implication graph. Cycles are tolerated rather than raising: an
        accidental cycle should not take the system down, and the fixed point
        is still correct.
        """
        result: set[str] = set()
        pending = list(keys)
        while pending:
            key = str(pending.pop())
            if key in result:
                continue
            result.add(key)
            declaration = self._declarations.get(key)
            if declaration is not None:
                pending.extend(declaration.implies)
        return frozenset(result)

    def validate_all(self) -> list[str]:
        """Check that every ``implies`` target exists. Called by a fitness test."""
        problems: list[str] = []
        for declaration in self.all():
            for implied in declaration.implies:
                if implied not in self._declarations:
                    problems.append(
                        f"{declaration.key} implies {implied!r}, which is not "
                        f"declared anywhere."
                    )
        return problems

    def reset(self) -> None:
        """Test hook only."""
        self._declarations.clear()
        self._expansion_cache.clear()


permission_registry: Final = PermissionRegistry()

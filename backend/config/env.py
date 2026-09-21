"""Typed environment configuration with fail-early validation.

Two rules from 12-security.md section 5 shape this module:

1. No secrets in code, config files, environment files in the repo, CI logs or
   images. Values come from the process environment, which in production is
   populated from a secret manager.
2. Production must fail at startup on a missing or invalid critical setting,
   never at the first request that happens to need it. A misconfigured
   container that boots and serves errors is harder to diagnose than one that
   refuses to boot and says why.

``Env.require_all()`` collects *every* problem before raising, so a
misconfigured deployment is fixed in one pass rather than one variable per
restart.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")


class ImproperlyConfigured(Exception):
    """Raised at startup. Never caught to fall back to a default."""


class Env:
    """Reads and coerces environment variables, accumulating failures."""

    def __init__(self, environ: dict[str, str] | None = None) -> None:
        self._environ = environ if environ is not None else dict(os.environ)
        self._errors: list[str] = []

    # --- loading ----------------------------------------------------------

    def load_dotenv(self, path: Path) -> None:
        """Load a local .env file if present.

        Development convenience only. The file is git-ignored and never
        contains production secrets; existing process variables always win, so
        a real environment cannot be silently overridden by a stale file.
        """
        if not path.exists():
            return
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            self._environ.setdefault(key, value)

    # --- typed accessors --------------------------------------------------

    def _get(
        self,
        key: str,
        *,
        default: Any,
        required: bool,
        cast: Callable[[str], T],
        type_name: str,
    ) -> Any:
        raw = self._environ.get(key)
        if raw is None or raw == "":
            if required:
                self._errors.append(f"{key} is required but not set.")
                return default
            return default
        try:
            return cast(raw)
        except (ValueError, TypeError):
            self._errors.append(f"{key}={raw!r} is not a valid {type_name}.")
            return default

    def str(self, key: str, default: str | None = None, *, required: bool = False) -> str:
        return self._get(
            key, default=default or "", required=required, cast=str, type_name="string"
        )

    def secret(
        self,
        key: str,
        default: str | None = None,
        *,
        required: bool = False,
        min_length: int = 0,
    ) -> str:
        """A sensitive value.

        Identical to :meth:`str` except that the value is never echoed in an
        error message - reporting ``SECRET_KEY='hunter2' is invalid`` would put
        the secret in the logs that the check exists to protect.
        """
        raw = self._environ.get(key)
        if raw is None or raw == "":
            if required:
                self._errors.append(f"{key} is required but not set.")
            return default or ""
        if min_length and len(raw) < min_length:
            self._errors.append(
                f"{key} is too short: needs at least {min_length} characters."
            )
        return raw

    def int(self, key: str, default: int = 0, *, required: bool = False) -> int:
        return self._get(
            key, default=default, required=required, cast=int, type_name="integer"
        )

    def float(self, key: str, default: float = 0.0, *, required: bool = False) -> float:
        return self._get(
            key, default=default, required=required, cast=float, type_name="number"
        )

    def bool(self, key: str, default: bool = False, *, required: bool = False) -> bool:
        def _cast(raw: str) -> bool:
            lowered = raw.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
            raise ValueError(raw)

        return self._get(
            key, default=default, required=required, cast=_cast, type_name="boolean"
        )

    def list(
        self,
        key: str,
        default: Sequence[str] | None = None,
        *,
        required: bool = False,
        separator: str = ",",
    ) -> list[str]:
        def _cast(raw: str) -> list[str]:
            return [item.strip() for item in raw.split(separator) if item.strip()]

        return self._get(
            key,
            default=list(default or []),
            required=required,
            cast=_cast,
            type_name="comma-separated list",
        )

    def choice(
        self,
        key: str,
        choices: Iterable[str],
        default: str | None = None,
        *,
        required: bool = False,
    ) -> str:
        allowed = tuple(choices)
        value = self.str(key, default or "", required=required)
        if value and value not in allowed:
            self._errors.append(f"{key}={value!r} is not one of {', '.join(allowed)}.")
            return default or allowed[0]
        return value or (default or allowed[0])

    # --- validation -------------------------------------------------------

    def add_error(self, message: str) -> None:
        """Record a cross-field problem discovered by a settings module."""
        self._errors.append(message)

    def check(self, condition: bool, message: str) -> None:
        if not condition:
            self._errors.append(message)

    @property
    def errors(self) -> list[str]:
        return list(self._errors)

    def require_all(self, *, context: str = "configuration") -> None:
        """Raise once, listing every problem found.

        Called at the bottom of each settings module. Reporting all failures
        together turns a sequence of failed deployments into a single fix.
        """
        if not self._errors:
            return
        bullets = "\n".join(f"  - {e}" for e in self._errors)
        raise ImproperlyConfigured(
            f"Invalid {context}:\n{bullets}\n\n"
            f"See .env.example for the expected variables. Secrets must come "
            f"from the environment or a secret manager, never from source "
            f"control (12-security.md section 5)."
        )

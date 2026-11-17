"""Product Publisher fixtures (ADR-068): the earlier phases' signed-in staff,
reused as they are."""

from __future__ import annotations

from tests.extension import conftest as _extension

console_org = _extension.console_org
login = _extension.login
staff_login = _extension.staff_login

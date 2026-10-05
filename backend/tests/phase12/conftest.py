"""Phase 12 fixtures: the Phase 7 HTTP clients (real sign-in, real sessions)."""

from __future__ import annotations

from tests.phase7 import conftest as _phase7

# Re-exported by assignment (not import) so that using a fixture as a test
# argument is not a redefinition of an imported name.
http_login = _phase7.http_login
staff_client = _phase7.staff_client

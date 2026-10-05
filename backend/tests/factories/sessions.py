"""Session states the HTTP suites sign people in with."""

from __future__ import annotations

import time
from typing import Any


def lapsed_step_up(session: Any) -> None:
    """A sign-in that verified a second factor, but longer ago than the step-up
    window: the session reads as AAL 1 while it stays MFA-verified.

    Since Phase 12 (D-037) that is what every staff session at AAL 1 looks
    like - the control plane refuses one that never verified a factor - so the
    staff fixtures use it wherever a test asks for a session *without* AAL 2.
    """
    from django.conf import settings

    from workeasy360.platform.tenancy.resolution import (
        SESSION_STEP_UP_AT_KEY,
        record_step_up,
    )

    record_step_up(session)
    session[SESSION_STEP_UP_AT_KEY] = time.time() - settings.STEP_UP_MAX_AGE_SECONDS - 60

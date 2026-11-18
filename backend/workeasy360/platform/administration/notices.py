"""What staff are told by email, at the address in Platform settings (2026-11-09).

Only that something waits, and where: no customer's answers, documents or message text. An
Admin who leaves the address empty gets no email, and the console's own counts still show
everything. A failed send never fails what the person was doing.
"""

from __future__ import annotations

from typing import Final
from uuid import uuid4

from workeasy360.platform.notifications.templates import Template, register
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

TEMPLATE_KEY: Final = "platform.staff.attention"

STAFF_ATTENTION = register(
    Template(
        key=TEMPLATE_KEY,
        subject="{title}",
        body_text="{title}\n\n{detail}\n\nOpen the Admin console to deal with it.",
    )
)


def tell_staff(title: str, detail: str) -> bool:
    """Queue one email to the notifications address. Returns whether one was queued."""
    from workeasy360.platform.administration.site import notifications_email
    from workeasy360.platform.notifications.services import notify_account

    address = notifications_email()
    if not address:
        return False
    try:
        notify_account(
            template_key=TEMPLATE_KEY,
            recipient_email=address,
            context={"title": title, "detail": detail},
            idempotency_key=f"staff-attention:{uuid4().hex}",
        )
    except Exception as exc:  # pragma: no cover - logged for the on-call, never surfaced
        logger.error("administration.staff_notice_failed", error_type=type(exc).__name__)
        return False
    return True

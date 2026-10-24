"""A partner member's notification read-state (ADR-046).

The only writer of ``PartnerNotificationRead`` and ``PartnerNotificationState``.
The notifications themselves are audit-log entries and are never copied here: a
row holds only "this member has read this event", or "this member has read
everything up to this moment".
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any
from uuid import UUID

from django.utils import timezone


def read_state(
    member: Any, event_ids: Iterable[UUID]
) -> tuple[datetime | None, set[str]]:
    """``(read_until, ids)``: the member's "mark all read" moment, and which of
    ``event_ids`` they have individually read. Two small queries."""
    from workeasy360.commercial.partners.models import (
        PartnerNotificationRead,
        PartnerNotificationState,
    )

    state = PartnerNotificationState.objects.filter(member=member).first()
    seen = {
        str(i)
        for i in PartnerNotificationRead.objects.filter(
            member=member, event_id__in=list(event_ids)
        ).values_list("event_id", flat=True)
    }
    return (state.read_until if state else None), seen


def mark_all_read(member: Any) -> None:
    """Everything up to now counts as read; the per-event rows are redundant."""
    from workeasy360.commercial.partners.models import (
        PartnerNotificationRead,
        PartnerNotificationState,
    )

    PartnerNotificationState.objects.update_or_create(
        member=member, defaults={"read_until": timezone.now()}
    )
    PartnerNotificationRead.objects.filter(member=member).delete()


def mark_read(member: Any, event_ids: Iterable[UUID]) -> None:
    """Idempotent: an event already read is left as it is."""
    from workeasy360.commercial.partners.models import PartnerNotificationRead

    now = timezone.now()
    PartnerNotificationRead.objects.bulk_create(
        [
            PartnerNotificationRead(member=member, event_id=i, read_at=now)
            for i in event_ids
        ],
        ignore_conflicts=True,
    )

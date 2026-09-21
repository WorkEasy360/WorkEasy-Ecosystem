"""Notification and delivery-channel contracts.

``platform/notifications`` owns channels, templates, preferences and delivery.
It does not own the decision that something is notable: modules publish, and
notifications delivers. That split is why ``platform/*`` never needs to import
anything above it to render a message (forbidden edge F-4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from workeasy360.contracts.enums import NotificationChannel


@dataclass(frozen=True, slots=True)
class NotificationRequest:
    """A request to notify someone.

    ``context`` carries data the publisher has *already resolved*. Notifications
    must never call back into a product to render a message: that would be an
    upward dependency and an N+1 across a module boundary.
    """

    organization_id: UUID
    template_key: str
    recipient_membership_id: UUID | None = None
    recipient_email: str | None = None
    channels: tuple[NotificationChannel, ...] = (NotificationChannel.EMAIL,)
    context: dict[str, Any] = field(default_factory=dict)

    #: Stable key for at-most-once semantics. Derived from durable state
    #: (for example "invoice:{id}:issued"), never random: a random key
    #: regenerated on retry is not a retry, it is a second notification.
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        if not self.recipient_membership_id and not self.recipient_email:
            raise ValueError(
                "NotificationRequest needs a recipient membership or address."
            )
        if not self.channels:
            raise ValueError("NotificationRequest needs at least one channel.")


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """Outcome of one delivery attempt on one channel."""

    success: bool
    provider_message_id: str | None = None
    error: str | None = None

    #: Whether a retry could plausibly succeed. Decided by exception type at
    #: the adapter, never by string-matching an error message: retrying a
    #: permanent failure five times is wasted work and alert noise.
    retryable: bool = False


@runtime_checkable
class NotificationChannelAdapter(Protocol):
    """One delivery channel: email, SMS, in-app, webhook.

    Adding a channel means adding an adapter and registering it. No caller
    changes, because callers name channels, not adapters.
    """

    channel: NotificationChannel

    def send(
        self, request: NotificationRequest, rendered: RenderedMessage
    ) -> DeliveryResult:
        """Attempt delivery once.

        Implementations must not retry internally: retries are owned by the
        task layer, at exactly one level, so attempts cannot multiply
        (09-failure-resilience.md section 4.3).
        """
        ...

    def health(self) -> bool:
        """Cheap liveness signal for the deep health endpoint. Never consulted
        by ``/healthz/live`` or ``/healthz/ready``."""
        ...


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    """A message after template rendering, before transport."""

    subject: str
    body_text: str
    body_html: str | None = None


@runtime_checkable
class NotificationService(Protocol):
    """What other modules call.

    ``notify`` persists intent and queues delivery, then returns. It never
    performs transport inline: an email provider outage must not become
    request latency (09-failure-resilience.md section 3.3).
    """

    def notify(self, request: NotificationRequest) -> UUID:
        """Persist the notification and queue delivery. Returns its id."""
        ...

"""Delivery channels behind one interface.

Adding a channel means adding an adapter and registering it; no caller
changes, because callers name channels rather than adapters.

Every adapter obeys two rules:

- **Bounded timeouts.** Python's HTTP and SMTP defaults are *no timeout*,
  which is the single most common cause of worker exhaustion in Django
  systems (R-06).
- **No internal retries.** Retries belong to the task layer, at exactly one
  level. Three layers each retrying three times is 27 requests to a
  struggling provider (09-failure-resilience.md section 4.3).
"""

from __future__ import annotations

from typing import Final

from celery.exceptions import SoftTimeLimitExceeded

from workeasy360.contracts.enums import NotificationChannel
from workeasy360.contracts.ports.notifications import (
    DeliveryResult,
    NotificationRequest,
    RenderedMessage,
)
from workeasy360.platform.observability.logging import get_logger

logger = get_logger(__name__)

_channels: dict[str, object] = {}


def register_channel(adapter: object) -> None:
    _channels[str(adapter.channel)] = adapter


def get_channel(channel: NotificationChannel | str) -> object | None:
    return _channels.get(str(channel))


class EmailChannel:
    """Email via Django's mail backend.

    Django's backend is used rather than a provider SDK so the provider is a
    settings change, matching the ``EmailProvider`` port shape without
    committing to a vendor in Phase 1.
    """

    channel: Final = NotificationChannel.EMAIL

    def send(
        self, request: NotificationRequest, rendered: RenderedMessage
    ) -> DeliveryResult:
        from django.conf import settings
        from django.core.mail import EmailMultiAlternatives, get_connection

        recipient = request.recipient_email
        if not recipient:
            # Permanent, not transient: no number of retries will invent an
            # address, so this must not consume the retry budget.
            return DeliveryResult(
                success=False,
                error="No email address for recipient.",
                retryable=False,
            )

        try:
            from email.utils import formataddr

            connection = get_connection(
                timeout=getattr(settings, "EMAIL_READ_TIMEOUT_S", 10.0)
            )
            # The address is always the deployment's own; only the display
            # name comes from the communication settings (Phase 8 extension).
            from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None)
            if from_email and rendered.sender_name:
                from_email = formataddr((rendered.sender_name, from_email))
            message = EmailMultiAlternatives(
                subject=rendered.subject,
                body=rendered.body_text,
                from_email=from_email,
                to=[recipient],
                reply_to=[rendered.reply_to] if rendered.reply_to else None,
                connection=connection,
            )
            if rendered.body_html:
                message.attach_alternative(rendered.body_html, "text/html")
            sent = message.send(fail_silently=False)
            return DeliveryResult(success=bool(sent))
        except SoftTimeLimitExceeded:
            # The task's budget, not a provider failure: it must end the task
            # (the claimed row's lease then expires and the sweeper retries).
            raise
        except Exception as exc:
            return DeliveryResult(
                success=False,
                error=f"{type(exc).__name__}: {exc}"[:500],
                retryable=_is_transient(exc),
            )

    def health(self) -> bool:
        return True


class InAppChannel:
    """In-app delivery.

    A no-op transport: the ``Notification`` row *is* the in-app notification.
    Succeeds unconditionally, which is why an email provider outage still
    leaves the customer informed - the degradation matrix's "in-app only" row
    (09-failure-resilience.md section 5).
    """

    channel: Final = NotificationChannel.IN_APP

    def send(
        self, request: NotificationRequest, rendered: RenderedMessage
    ) -> DeliveryResult:
        return DeliveryResult(success=True)

    def health(self) -> bool:
        return True


def _is_transient(exc: Exception) -> bool:
    """Decide retryability by exception type, never by message text.

    String-matching an error message is fragile across provider versions and
    silently changes behaviour when a vendor rewords an error.
    """
    import smtplib

    # ``socket.timeout`` is an alias of ``TimeoutError`` since Python 3.10
    # (this project's floor), so it is covered below without importing a
    # socket module outside integration/egress.
    transient_types = (
        smtplib.SMTPConnectError,
        smtplib.SMTPServerDisconnected,
        smtplib.SMTPHeloError,
        ConnectionError,
        TimeoutError,
        OSError,
    )
    if isinstance(exc, smtplib.SMTPResponseException):
        # 4xx is a temporary failure; 5xx is permanent. Retrying a 550
        # "mailbox does not exist" forever is how a queue fills up.
        return 400 <= exc.smtp_code < 500
    if isinstance(exc, smtplib.SMTPRecipientsRefused | smtplib.SMTPSenderRefused):
        return False
    return isinstance(exc, transient_types)


def register_default_channels() -> None:
    """Called from ``NotificationsConfig.ready()``."""
    register_channel(EmailChannel())
    register_channel(InAppChannel())

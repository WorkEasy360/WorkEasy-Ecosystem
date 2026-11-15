"""How identity tells the ecosystem that Workeasy360 sign-ins have ended.

``platform/identity`` ends sessions; ``platform/ecosystem`` knows which
products were signed in to from each of them and owes those products an
OpenID Connect Back-Channel Logout (ADR-067). Identity must not import the
ecosystem, so - exactly like :mod:`workeasy360.contracts.ports.events` - a
narrow protocol is declared here, implemented by the ecosystem and registered
at app-ready. No import edge is created.

The listener runs **inside the caller's transaction**: the session rows go
and the logout deliveries are recorded in one commit, or neither happens.
That is the transactional-outbox property, without a second table to keep in
step; sending them is the integration layer's job, after commit.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable
from uuid import UUID

#: Why sign-ins ended. A closed set, so it is safe as a metric label and in a
#: delivery row; a product is never told which (a logout is a logout).
REASON_SIGNED_OUT = "signed_out"
REASON_SESSION_REVOKED = "session_revoked"
REASON_OTHER_SESSIONS_REVOKED = "other_sessions_revoked"
REASON_SIGNED_OUT_EVERYWHERE = "signed_out_everywhere"
REASON_PASSWORD_CHANGED = "password_changed"  # noqa: S105 - a reason code  # pragma: allowlist secret
REASON_ACCOUNT_RECOVERED = "account_recovered"
REASON_ACCOUNT_SWITCHED = "account_switched"

REASONS = frozenset(
    {
        REASON_SIGNED_OUT,
        REASON_SESSION_REVOKED,
        REASON_OTHER_SESSIONS_REVOKED,
        REASON_SIGNED_OUT_EVERYWHERE,
        REASON_PASSWORD_CHANGED,
        REASON_ACCOUNT_RECOVERED,
        REASON_ACCOUNT_SWITCHED,
    }
)


@runtime_checkable
class SignInsEndedListener(Protocol):
    def sign_ins_ended(
        self,
        *,
        user_id: UUID,
        sign_in_ids: tuple[UUID, ...],
        everywhere: bool,
        reason: str,
    ) -> None:
        """Record what the end of these sign-ins owes to products.

        ``everywhere=True`` means every sign-in of the person, past and
        present (``sign_in_ids`` is then ignored). Must not make a network
        call: it runs inside the caller's transaction.
        """
        ...


_listeners: list[SignInsEndedListener] = []


def register_sign_ins_ended_listener(listener: SignInsEndedListener) -> None:
    """Called once per listener, from its owning app's ``ready()``."""
    if not any(existing is listener for existing in _listeners):
        _listeners.append(listener)


def notify_sign_ins_ended(
    *,
    user_id: object,
    sign_in_ids: Iterable[object] = (),
    everywhere: bool = False,
    reason: str,
) -> None:
    """Tell every registered listener. A no-op when none is registered (a
    deployment without the ecosystem, or a unit test that loads only
    ``platform.identity``)."""
    if reason not in REASONS:
        raise ValueError(f"Unknown sign-in end reason {reason!r}.")
    ids: list[UUID] = []
    for raw in sign_in_ids:
        if raw is None:
            continue
        try:
            ids.append(UUID(str(raw)))
        except (TypeError, ValueError):
            continue
    if not everywhere and not ids:
        return
    uid = UUID(str(user_id))
    for listener in list(_listeners):
        listener.sign_ins_ended(
            user_id=uid,
            sign_in_ids=tuple(dict.fromkeys(ids)),
            everywhere=everywhere,
            reason=reason,
        )


def reset_sign_ins_ended_listeners() -> None:
    """Test hook only."""
    _listeners.clear()

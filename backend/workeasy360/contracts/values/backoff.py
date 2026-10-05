"""Retry delays - the one implementation.

Every retry loop in the platform (the outbox relay, notification delivery,
the task base class, webhook delivery, automation execution) needs the same
two things: an exponentially growing ceiling with a cap, and jitter. Three
copies of that arithmetic had grown up side by side before Phase 6; a fourth
and fifth would have been added by it. One function per jitter shape, here,
is what stops the next reader wondering which copy is authoritative.

Two shapes, because they answer different questions:

- **Full jitter** - ``random(0, ceiling)``. The right default for work that
  merely must not retry in a synchronised herd (09-failure-resilience.md
  section 4.3). A delay near zero is harmless when the thing being retried
  is our own database.
- **Equal jitter** - ``ceiling/2 + random(0, ceiling/2)``. For retries aimed
  at a *third party* that is failing. Full jitter would sometimes retry a
  struggling customer endpoint a fraction of a second after it failed, which
  is exactly the hammering backoff exists to prevent; equal jitter keeps the
  herd-breaking spread while guaranteeing at least half the ceiling.

Pure stdlib, so it lives in ``contracts/`` and every layer can reach it.
"""

from __future__ import annotations

import random
from typing import Final

#: Exponent ceiling. ``2**32`` seconds is far past any cap anyone would set;
#: clamping keeps a pathological attempt count from building a huge integer.
_MAX_EXPONENT: Final = 32


def ceiling(attempt: int, *, base: float, cap: float) -> float:
    """``min(cap, base * 2**attempt)``, with ``attempt`` clamped to [0, 32]."""
    exponent = min(max(0, int(attempt)), _MAX_EXPONENT)
    return float(min(cap, base * (2**exponent)))


def full_jitter(attempt: int, *, base: float, cap: float) -> float:
    """A delay in ``[0, ceiling]``."""
    return random.uniform(0, ceiling(attempt, base=base, cap=cap))  # noqa: S311 - retry jitter, not cryptography


def equal_jitter(attempt: int, *, base: float, cap: float) -> float:
    """A delay in ``[ceiling/2, ceiling]``."""
    top = ceiling(attempt, base=base, cap=cap)
    return top / 2 + random.uniform(0, top / 2)  # noqa: S311 - retry jitter, not cryptography

"""Argon2id with a per-process bulkhead (2026-10 architecture audit).

Django's Argon2 hasher costs 100 MiB of memory and eight lanes per hash, by
design - that cost is the defence. What was unbounded is how many run at once:
every request thread of every gunicorn worker could hash concurrently. On the
staging stack a burst of sign-ins took the api container from ~280 MiB to
750 MiB and over 16 cores, and nothing in the production topology caps a
container's memory. A distributed credential-stuffing run (many addresses, many
accounts, so no rate limit trips) holds that state for as long as it likes.

``BoundedArgon2PasswordHasher`` is Django's hasher unchanged - same algorithm
name, same parameters, so every stored hash verifies and nothing is re-hashed -
with at most ``PASSWORD_HASH_CONCURRENCY`` hashes in flight per process.
Further sign-ins wait their turn; the memory and CPU a burst can take is
``workers x PASSWORD_HASH_CONCURRENCY`` hashes, not ``workers x threads``.
"""

from __future__ import annotations

import threading

from django.conf import settings
from django.contrib.auth.hashers import Argon2PasswordHasher

_slots_lock = threading.Lock()
_slots: threading.BoundedSemaphore | None = None
_slots_size = 0


def _hash_slots() -> threading.BoundedSemaphore:
    """The process-wide semaphore, sized from settings on first use (and
    rebuilt if a test changes the setting)."""
    global _slots, _slots_size
    size = max(1, int(getattr(settings, "PASSWORD_HASH_CONCURRENCY", 2)))
    with _slots_lock:
        if _slots is None or _slots_size != size:
            _slots, _slots_size = threading.BoundedSemaphore(size), size
        return _slots


class BoundedArgon2PasswordHasher(Argon2PasswordHasher):
    """Argon2id, at most ``PASSWORD_HASH_CONCURRENCY`` at a time per process."""

    # Inherited and deliberately unchanged: ``algorithm = "argon2"`` and the
    # time/memory/parallelism costs. A change to either would re-hash every
    # password on its next sign-in, which is a security decision, not this one.

    def encode(self, password: str, salt: str) -> str:
        with _hash_slots():
            return super().encode(password, salt)

    def verify(self, password: str, encoded: str) -> bool:
        # Django's verify() does not call encode(), so one slot per hash.
        with _hash_slots():
            return super().verify(password, encoded)

"""The password-hash bulkhead (2026-10 architecture audit).

``BoundedArgon2PasswordHasher`` must be Django's Argon2 hasher in every way
that matters for security - same algorithm name, same costs, every stored hash
still verifying and none re-hashed - and must never run more than
``PASSWORD_HASH_CONCURRENCY`` hashes at once in one process.
"""

from __future__ import annotations

import threading
import time

from django.contrib.auth.hashers import Argon2PasswordHasher

from workeasy360.platform.identity.hashers import BoundedArgon2PasswordHasher


def test_it_is_django_argon2_with_the_same_costs():
    bounded, plain = BoundedArgon2PasswordHasher(), Argon2PasswordHasher()
    assert bounded.algorithm == plain.algorithm == "argon2"
    assert (bounded.time_cost, bounded.memory_cost, bounded.parallelism) == (
        plain.time_cost,
        plain.memory_cost,
        plain.parallelism,
    )


def test_a_hash_from_either_hasher_verifies_with_the_other_and_is_not_rehashed():
    bounded, plain = BoundedArgon2PasswordHasher(), Argon2PasswordHasher()
    stored = plain.encode("correct horse battery staple", plain.salt())
    assert bounded.verify("correct horse battery staple", stored)
    assert not bounded.verify("wrong horse", stored)
    assert not bounded.must_update(stored)
    assert plain.verify(
        "correct horse battery staple",
        bounded.encode("correct horse battery staple", bounded.salt()),
    )


def test_the_production_hasher_list_leads_with_the_bounded_hasher():
    from config.settings import base

    assert (
        base.PASSWORD_HASHERS[0]
        == "workeasy360.platform.identity.hashers.BoundedArgon2PasswordHasher"
    )


def test_no_more_than_the_configured_number_hash_at_once(settings, monkeypatch):
    settings.PASSWORD_HASH_CONCURRENCY = 2
    lock = threading.Lock()
    state = {"now": 0, "peak": 0}

    def slow_verify(self, password, encoded):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.05)
        with lock:
            state["now"] -= 1
        return True

    monkeypatch.setattr(Argon2PasswordHasher, "verify", slow_verify)
    hasher = BoundedArgon2PasswordHasher()
    threads = [
        threading.Thread(target=hasher.verify, args=("pw", "argon2$x")) for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # The bound, not a timing: however the threads interleave, never above it.
    assert 1 <= state["peak"] <= 2

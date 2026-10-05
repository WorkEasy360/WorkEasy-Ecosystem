"""Token estimation before a provider call (Task 54), and exact cost after.

**Estimation is deliberately conservative, and it is only an estimate.** Every
vendor tokenizes differently and the platform ships no vendor tokenizer (one
more dependency per vendor, and still wrong for the next model). The estimate
is ``ceil(utf8_bytes / 3)`` plus a small per-message overhead: about a third
more than typical English (~4 characters a token), roughly right for code,
and about one token per character for CJK scripts (three UTF-8 bytes). It is
used for exactly two things, both of which want an *upper* bound:

- refusing a request whose trusted instructions and input cannot fit the
  selected model's context before any money is spent, and
- sizing the spend reservation made before the call.

What is **metered** is never the estimate: it is the token counts the
provider reports in its response, costed at the price version pinned to the
call. If an estimate was low, the reservation was low; the actual usage is
still what is recorded and charged against the budget.
"""

from __future__ import annotations

from typing import Final

MESSAGE_OVERHEAD_TOKENS: Final = 8
MICROS_PER_MTOK_DIVISOR: Final = 1_000_000


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return -(-len(text.encode("utf-8")) // 3) + MESSAGE_OVERHEAD_TOKENS


def cost_micros(
    *,
    input_tokens: int,
    output_tokens: int,
    input_micros_per_mtok: int,
    output_micros_per_mtok: int,
) -> int:
    """Exact integer cost in micro-units, rounded **up** once over the whole
    call - deterministic, never a float, never under-counted (Task 9)."""
    numerator = int(input_tokens) * int(input_micros_per_mtok) + int(output_tokens) * int(
        output_micros_per_mtok
    )
    return -(-numerator // MICROS_PER_MTOK_DIVISOR)

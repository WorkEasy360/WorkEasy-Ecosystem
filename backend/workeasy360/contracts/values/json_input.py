"""JSON that arrived from outside the process (2026-10 audit).

``json.loads`` reports malformed input as ``json.JSONDecodeError`` (a
``ValueError``), and every caller handles that. Deeply nested input is the
exception: CPython's decoder recurses once per level and, at roughly a
thousand levels, raises ``RecursionError`` - which is not a ``ValueError`` and
escaped every request-body parser in ``interfaces/``. An unauthenticated
40 KB body of brackets sent to sign-in was a 500 (verified on staging).

``loads`` is ``json.loads`` with that one difference: too-deep input is reported
as the malformed JSON it is, so the caller's existing ``JSONDecodeError`` or
``ValueError`` handling answers it. The product-ingestion parsers already
caught ``RecursionError`` themselves (Phases 6 and 8); this is the same rule in
one place for everything else.
"""

from __future__ import annotations

import json
from typing import Any


def loads(raw: str | bytes | bytearray, **kwargs: Any) -> Any:
    """``json.loads``, but nesting too deep to parse raises ``JSONDecodeError``."""
    try:
        return json.loads(raw, **kwargs)
    except RecursionError:
        raise json.JSONDecodeError("JSON nested too deeply", "", 0) from None

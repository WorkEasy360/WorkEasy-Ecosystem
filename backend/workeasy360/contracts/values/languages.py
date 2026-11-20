"""The languages a person may choose for themselves on WorkEasy360 Accounts
(ADR-075), as BCP 47 codes and nothing else.

A personal preference only: it may guide how products display things, and it
never changes an organization's reporting, invoices or billing. The name a
person reads is the screen's business (``Intl.DisplayNames``).
"""

from __future__ import annotations

from typing import Final

#: In the order the profile form lists them: English first, then the
#: languages of the platform's markets, then other widely used ones.
LANGUAGES: Final[tuple[str, ...]] = (
    "en",
    "hi",
    "bn",
    "mr",
    "ta",
    "te",
    "gu",
    "kn",
    "ml",
    "pa",
    "ur",
    "ar",
    "fr",
    "de",
    "es",
    "pt",
    "it",
    "nl",
    "ru",
    "tr",
    "id",
    "ms",
    "th",
    "vi",
    "zh",
    "ja",
    "ko",
)

LANGUAGE_CODES: Final[frozenset[str]] = frozenset(LANGUAGES)

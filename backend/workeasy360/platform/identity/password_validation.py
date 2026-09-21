"""Password policy.

12-security.md section 3: minimum 12 characters, a breach-corpus check, **no
composition rules and no forced rotation**. Both of the latter are
counterproductive per current NIST guidance - they push users toward
predictable mutations (``Password1!`` becoming ``Password2!``) without adding
entropy.

The breach-corpus check is the control that actually matters, and it is
stubbed here rather than faked: Phase 1 ships the interface and Django's
common-password list, with the integration point marked. Pretending to check
a corpus we do not have would be worse than declaring the gap.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _


class UserAttributeSimilarityValidator:
    """Reject passwords that are close to the user's own attributes.

    Django ships a validator of this name, but it inspects ``username``,
    ``first_name`` and ``last_name`` - fields our User model does not have.
    This one checks the fields that exist, so the protection is real rather
    than silently inert.
    """

    #: Below this ratio the strings are different enough to accept.
    MAX_SIMILARITY = 0.7

    ATTRIBUTES = ("email", "full_name")

    def validate(self, password: str, user: Any = None) -> None:
        if user is None:
            return

        for attribute in self.ATTRIBUTES:
            value = getattr(user, attribute, None)
            if not value or not isinstance(value, str):
                continue

            # Compare against the whole value and against each part, so
            # "alice@example.com" also protects "alice" and "example".
            candidates = {value.lower(), *re.split(r"[^\w]+", value.lower())}
            for candidate in candidates:
                if len(candidate) < 3:
                    continue
                ratio = SequenceMatcher(a=password.lower(), b=candidate).quick_ratio()
                if ratio >= self.MAX_SIMILARITY:
                    raise ValidationError(
                        _("This password is too similar to your %(verbose_name)s."),
                        code="password_too_similar",
                        params={"verbose_name": attribute.replace("_", " ")},
                    )

    def get_help_text(self) -> str:
        return _("Your password cannot closely resemble your email or name.")


class BreachCorpusValidator:
    """Reject passwords known to appear in breach corpora.

    **Not wired up in Phase 1.** ``check_corpus`` is the integration point for
    a k-anonymity range query (the client sends the first five characters of
    the SHA-1 hash and never the password itself).

    It is registered nowhere by default precisely so that its absence is
    visible. A validator that silently passes everything is worse than one
    that is obviously not installed yet.
    """

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    def validate(self, password: str, user: Any = None) -> None:
        if not self.enabled:
            return
        if self.check_corpus(password):
            raise ValidationError(
                _(
                    "This password has appeared in a known data breach and "
                    "cannot be used."
                ),
                code="password_breached",
            )

    def check_corpus(self, password: str) -> bool:  # pragma: no cover - stub
        raise NotImplementedError(
            "Breach-corpus checking is not implemented. Wire this to a "
            "k-anonymity range API before enabling."
        )

    def get_help_text(self) -> str:
        return _("Your password must not appear in a known data breach.")

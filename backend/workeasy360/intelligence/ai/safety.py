"""The forbidden-secret filter: what never leaves the platform for a provider.

Task 44's floor, implemented once and applied everywhere content crosses to
a vendor - a skill's input, a retrieval query, and every document at ingestion
(before it is chunked, stored or embedded, so a secret is never indexed
either):

- private keys (PEM blocks), and ``otpauth://`` MFA enrolment URIs;
- vendor API keys and tokens with recognisable shapes (cloud, source hosting,
  chat, payments, AI vendors), and this platform's own ``whsec_`` secrets;
- bearer tokens and JWTs (session and identity tokens);
- credentials embedded in connection URLs (``scheme://user:password@host``);
- the value of an explicit ``password=``/``api_key:``/``session_token=`` style
  assignment.

Each match is replaced by ``[REDACTED:<class>]`` and *counted*, never logged:
the execution record carries how many redactions happened, not what they
were.

**What this is not.** It is a filter for secret classes that are both
forbidden and recognisable. It is not PII minimisation: names, addresses and
business content are the organization's data, sent because the organization
enabled AI, chose its providers, and the product's skill contract bounds what
is sent (docs/phase-8/06-security-and-privacy.md). A secret with no
recognisable shape - a bare random string - cannot be told from data by any
filter, which is why products must not put credentials in skill input at all.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Final

_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?"
            r"(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)"
        ),
    ),
    ("mfa_secret", re.compile(r"otpauth://[^\s\"'<>]+", re.IGNORECASE)),
    ("url_credentials", re.compile(r"(?<=://)[^\s/:@\"']+:[^\s/@\"']+(?=@)")),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    ),
    ("bearer_token", re.compile(r"(?i)(?<=bearer )[A-Za-z0-9._~+/=\-]{20,}")),
    ("api_key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}")),
    ("api_key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}")),
    ("api_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("api_key", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}")),
    ("api_key", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}")),
    ("api_key", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("platform_secret", re.compile(r"\bwhsec_[A-Za-z0-9_\-]{20,}")),
    # A password may be a passphrase with spaces: an unquoted value runs to
    # the end of the line (or a delimiter), over-redacting rather than leaving
    # the rest of the phrase behind.
    (
        "credential_assignment",
        re.compile(
            r"(?i)(?<![A-Za-z0-9_])(?:password|passwd|pwd|passphrase)"
            r"[\"']?\s*[:=]\s*"
            r"(\"[^\"\n]{4,}\"|'[^'\n]{4,}'|[^\s,;\"'}][^\n,;\"'}]{3,})"
        ),
    ),
    (
        "credential_assignment",
        re.compile(
            r"(?i)(?<![A-Za-z0-9_])(?:(?:client|app|api)[_\-]?)?(?:secret|api[_\-]?key"
            r"|private[_\-]?key|(?:access|auth|refresh|id|bearer)?[_\-]?token"
            r"|session[_\-]?(?:id|token|key)|(?:mfa|otp|totp)[_\-]?secret)"
            r"[\"']?\s*[:=]\s*"
            r"(\"[^\"\n]{4,}\"|'[^'\n]{4,}'|[^\s,;\"'}]{4,})"
        ),
    ),
)


def redact(text: str) -> tuple[str, Counter[str]]:
    """``text`` with every forbidden secret replaced, and counts by class."""
    counts: Counter[str] = Counter()
    if not text:
        return text, counts
    for name, pattern in _PATTERNS:
        if pattern.groups:
            text, n = pattern.subn(
                lambda m, name=name: m.group(0)[: m.start(1) - m.start(0)]
                + f"[REDACTED:{name}]",
                text,
            )
        else:
            text, n = pattern.subn(f"[REDACTED:{name}]", text)
        if n:
            counts[name] += n
    return text, counts


def redact_value(value: Any) -> tuple[Any, Counter[str]]:
    """Redact every string inside a JSON-shaped value (a skill's input).
    Keys are left alone: they are the product's schema, not its data."""
    counts: Counter[str] = Counter()

    def walk(node: Any) -> Any:
        if isinstance(node, str):
            cleaned, found = redact(node)
            counts.update(found)
            return cleaned
        if isinstance(node, dict):
            return {key: walk(item) for key, item in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    return walk(value), counts

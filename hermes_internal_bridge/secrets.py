"""Secret detection and redaction (spec FR-043: filter before any ledger or log write).

Patterns deliberately over-match: a false positive costs one redaction, a false
negative leaks a credential into the audit trail.
"""

from __future__ import annotations

import re

_SECRET_PATTERN_TEXTS: tuple[str, ...] = (
    r"sk-[A-Za-z0-9_-]{16,}",                                   # OpenAI-style keys
    r"ghp_[A-Za-z0-9]{20,}",                                    # GitHub personal tokens
    r"github_pat_[A-Za-z0-9_]{20,}",                            # GitHub fine-grained tokens
    r"xox[bpars]-[A-Za-z0-9-]{10,}",                            # Slack tokens
    r"AKIA[0-9A-Z]{16}",                                        # AWS access key ids
    r"\d{8,12}:AA[A-Za-z0-9_-]{30,}",                           # Telegram bot tokens
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",  # JWTs
    r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{10,}=*",                 # Authorization headers (before the generic rule)
    r"(?i)\b(api[_-]?key|token|secret|password|passwd|authorization|auth)\b\s*[=:]\s*\S+",
)

REDACTED = "[REDACTED]"

_SECRET_RES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern) for pattern in _SECRET_PATTERN_TEXTS
)


def find_secrets(text: str) -> list[str]:
    """Return every substring in *text* that looks like a secret."""
    if not isinstance(text, str) or not text:
        return []
    found: list[str] = []
    for pattern in _SECRET_RES:
        found.extend(pattern.findall(text))
    return found


def redact(text: str) -> str:
    """Replace every secret-looking substring in *text* with ``[REDACTED]``."""
    if not isinstance(text, str) or not text:
        return text or ""
    result = text
    for pattern in _SECRET_RES:
        result = pattern.sub(REDACTED, result)
    return result


def scrub(value):
    """Deep-redact every string inside a JSON-like structure (dicts, lists, tuples)."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(item) for item in value]
    return value

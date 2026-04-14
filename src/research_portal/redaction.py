"""Generic PII / secret redaction for dashboard event payloads.

Used by :mod:`research_portal.events` before any event is persisted or
broadcast. The goal is first-pass hygiene: remove credentials outright
and scrub recognizable PII patterns from free-text fields. This is
**not** a DLP replacement — it catches the easy mistakes (an API key
accidentally logged, a user email in a query preview) and leaves harder
cases to the application layer.

Extending the redactor
----------------------

Downstream packages (e.g., ``atlas_portal``) can augment the generic
behavior with domain-specific rules via :func:`add_sensitive_keys` and
:func:`add_preview_keys`. Rules are process-wide; call once at startup.
"""

from __future__ import annotations

import re
from typing import Any

# Known sensitive keys in payloads — redacted to "<redacted>".
_SENSITIVE_KEYS: set[str] = {
    "api_key",
    "token",
    "secret",
    "password",
    "authorization",
    "cookie",
    "session_id",
}

# Fields whose values are free-text that may contain PII. These are
# truncated AND scrubbed for emails / phone / SSN patterns.
_PREVIEW_KEYS: set[str] = {
    "query",
    "query_preview",
    "text",
    "text_preview",
    "synthesis",
    "response",
}

# Patterns scrubbed from preview text (not exhaustive — first-pass).
_EMAIL_RX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
_PHONE_RX = re.compile(
    r"\b(?:\+?1[-.\s]?)?\(?[2-9][0-8][0-9]\)?"
    r"[-.\s]?[2-9][0-9]{2}[-.\s]?[0-9]{4}\b"
)
_SSN_RX = re.compile(
    r"\b(?!000|666)(?:[0-6]\d{2}|7(?:[0-6]\d|7[012]))-"
    r"(?!00)\d{2}-(?!0000)\d{4}\b"
)
_CREDIT_CARD_RX = re.compile(r"\b(?:\d[ -]*?){13,19}\b")

MAX_PREVIEW_CHARS = 280


def add_sensitive_keys(*keys: str) -> None:
    """Register additional keys to redact as ``<redacted>``."""
    for k in keys:
        _SENSITIVE_KEYS.add(k.lower())


def add_preview_keys(*keys: str) -> None:
    """Register additional free-text keys to truncate + PII-scrub."""
    for k in keys:
        _PREVIEW_KEYS.add(k.lower())


def redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Scrub sensitive fields and PII patterns from an event payload.

    Never raises. Unknown keys pass through unchanged. Nested dicts
    are redacted recursively; lists of dicts likewise.

    Policy:

    - Keys in the sensitive set → replaced with ``"<redacted>"``.
    - Keys in the preview set → truncated to ``MAX_PREVIEW_CHARS`` and
      PII patterns scrubbed.
    - Anything else passes through.
    """
    if not isinstance(payload, dict):
        return payload
    out: dict[str, Any] = {}
    for k, v in payload.items():
        kl = k.lower()
        if kl in _SENSITIVE_KEYS:
            out[k] = "<redacted>"
            continue
        if kl in _PREVIEW_KEYS and isinstance(v, str):
            out[k] = _scrub_text(v)
            continue
        if isinstance(v, dict):
            out[k] = redact_payload(v)
            continue
        if isinstance(v, list):
            out[k] = [redact_payload(item) if isinstance(item, dict) else item for item in v]
            continue
        out[k] = v
    return out


def _scrub_text(text: str) -> str:
    text = text[:MAX_PREVIEW_CHARS]
    text = _EMAIL_RX.sub("<email>", text)
    text = _PHONE_RX.sub("<phone>", text)
    text = _SSN_RX.sub("<ssn>", text)
    text = _CREDIT_CARD_RX.sub("<cc>", text)
    return text


__all__ = [
    "MAX_PREVIEW_CHARS",
    "add_preview_keys",
    "add_sensitive_keys",
    "redact_payload",
]

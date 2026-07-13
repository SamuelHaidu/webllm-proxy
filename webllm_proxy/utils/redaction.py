"""Scrub secrets out of anything before it's logged or dumped to disk (see
CLAUDE.md secrets discipline). Pure function, no I/O."""

import re

_REDACTED = "***redacted***"

_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "sessiontoken",
        "session_token",
        "accesstoken",
        "access_token",
        "x-csrf-token",
        "csrftoken",
        "csrf_token",
        "token",
        "apikey",
        "api_key",
        "secret",
        "password",
        "bearer",
    }
)

# A bearer/session JWT/JWE: three-or-more dot-separated base64url segments.
_JWT_LIKE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")


def _redact_str(value: str) -> str:
    return _JWT_LIKE.sub(_REDACTED, value)


def redact(value):
    """Copy of `value` with sensitive dict keys and JWT-shaped substrings masked."""
    if isinstance(value, dict):
        return {
            k: (_REDACTED if k.lower() in _SENSITIVE_KEYS else redact(v)) for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _redact_str(value)
    return value

"""Scrub secrets out of anything before it's logged or dumped to disk.

Every debug artifact this tool writes must go through `redact()` first -- see
CLAUDE.md's secrets discipline (never log/persist `sessionToken`/`accessToken`/
cookies) and docs/discovery/2026-07-10-backend-api-capture.md (the sentinel/
session tokens this proxy handles). Pure function, no I/O, so it's trivially
unit-testable.
"""

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
    """Return a copy of `value` with sensitive dict keys and JWT-shaped
    substrings masked. Safe on any JSON-shaped structure (dict/list/str/scalar);
    unknown/scalar types pass through unchanged."""
    if isinstance(value, dict):
        return {
            k: (_REDACTED if k.lower() in _SENSITIVE_KEYS else redact(v)) for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _redact_str(value)
    return value

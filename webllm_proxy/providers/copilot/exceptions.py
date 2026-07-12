"""Exception hierarchy. Anti-bot and throttling are first-class because every
edition surfaces them (differently) and callers must branch on them."""

from __future__ import annotations


class CopilotError(Exception):
    """Base for everything this package raises."""


class AuthError(CopilotError):
    """Missing/expired/rejected credential (token or cookie)."""


class TransportError(CopilotError):
    """WebSocket/HTTP transport failure."""


class ProtocolError(CopilotError):
    """A frame did not match the expected protocol shape."""


class ThrottledError(CopilotError):
    """The service rate-limited this request (`result.value == "Throttled"`)."""


class ConversationLimitError(CopilotError):
    """The per-conversation message cap was reached (start a new conversation)."""

    def __init__(self, used: int | None = None, maximum: int | None = None):
        self.used = used
        self.maximum = maximum
        super().__init__(f"conversation message limit reached ({used}/{maximum})")


class ChallengeError(CopilotError):
    """An in-band anti-bot challenge could not be answered automatically."""


class CaptchaRequired(ChallengeError):
    """An interactive CAPTCHA (e.g. Cloudflare Turnstile, or a SignalR
    `CaptchaChallenge` result) must be solved in a real browser session."""

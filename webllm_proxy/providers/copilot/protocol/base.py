"""Protocol codec contract + internal control signals.

A `ProtocolCodec` turns the normalized "send this turn" request into wire frames
and turns incoming wire frames into normalized `Event`s (from `..models`) plus
the control signals below. A codec instance is **stateful per connection** (it
tracks cumulative-text position, the assembled answer, message ids, etc.), so
create a fresh one per turn.

The two implementations are `signalr.SignalRCodec` (Sydney/ChatHub) and
`events.EventCodec` (consumer `/c/api/chat`). Editions pick one.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


# ---- control signals (not user-facing; handled by the client loop) ---------
@dataclass(slots=True)
class NeedChallenge:
    method: str
    parameter: str
    id: str | None = None


@dataclass(slots=True)
class Completed:
    """Stream is finished at the protocol level (SignalR `type:3`)."""


@dataclass(slots=True)
class Pong:
    """Keepalive reply received."""


@dataclass(slots=True)
class Ack:
    """Benign acknowledgement frame (connected/received/handshake-ack)."""


Control = NeedChallenge | Completed | Pong | Ack


class ProtocolCodec(abc.ABC):
    """Stateful, per-connection encoder/decoder."""

    @abc.abstractmethod
    def open_frames(self) -> list[str]:
        """Frames to send immediately after the socket opens (handshake,
        capability negotiation) — before the first turn."""

    @abc.abstractmethod
    def encode_send(self, text: str, *, conversation_id: str, options: dict) -> list[str]:
        """Frames that submit one user turn. `options` is the edition-specific
        bag from `Edition.send_options` (model field value, plugins, scenario…)."""

    @abc.abstractmethod
    def decode(self, raw: str) -> list[object]:
        """Parse one incoming WebSocket text frame into a list of `Event`
        (models) and/or `Control` signals. May raise `ThrottledError`,
        `CaptchaRequired`, `ConversationLimitError`."""

    def encode_ping(self) -> str | None:
        return None

    def encode_challenge_response(self, challenge: NeedChallenge) -> str | None:
        """Answer an in-band anti-bot challenge, or `None` if unsupported."""
        return None

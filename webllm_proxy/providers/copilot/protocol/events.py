"""Consumer Copilot event codec (wire protocol B).

Used by `copilot.microsoft.com/c/api/chat?api-version=2`. Each WebSocket text
frame is exactly one JSON object with an `"event"` field (no `\\x1e` framing).
Deltas are **incremental** (`appendText`). Anti-bot is an in-band hashcash PoW
(`challenge`/`challengeResponse`). See `docs/protocol/copilot-protocol.md` §2.
"""

from __future__ import annotations

import json

from .. import hashcash
from ..exceptions import CaptchaRequired
from ..models import Delta, Final, Progress
from .base import Ack, NeedChallenge, Pong, ProtocolCodec

# Minimal, plausible capability set sent on connect. Extend as needed; the
# server tolerates a subset. Kept deliberately small.
_SUPPORTED_FEATURES = ["partial-generated-images"]
_SUPPORTED_CARDS = ["image", "chart", "person", "finance", "weather", "local"]
_SUPPORTED_UI: dict[str, str] = {"Markdown": "1.2", "Text": "1.2", "Image": "1.2"}


class EventCodec(ProtocolCodec):
    def __init__(self) -> None:
        self._parts: list[str] = []  # assembled answer text
        self._title: str | None = None
        self._conversation_id: str | None = None

    # ---- encode ----------------------------------------------------------
    def open_frames(self) -> list[str]:
        return [
            json.dumps(
                {
                    "event": "setOptions",
                    "supportedFeatures": _SUPPORTED_FEATURES,
                    "supportedCards": _SUPPORTED_CARDS,
                    "supportedUIComponents": _SUPPORTED_UI,
                    "ads": None,
                    "supportedActions": [],
                }
            ),
            json.dumps({"event": "reportLocalConsents", "grantedConsents": []}),
        ]

    def encode_ping(self) -> str | None:
        return json.dumps({"event": "ping"})

    def encode_send(self, text: str, *, conversation_id: str, options: dict) -> list[str]:
        o = options or {}
        content = o.get("content") or [{"type": "text", "text": text}]
        return [
            json.dumps(
                {
                    "event": "send",
                    "conversationId": conversation_id,
                    "content": content,
                    "mode": o.get("mode", "smart"),
                    "context": o.get("context", {}),
                }
            )
        ]

    def encode_challenge_response(self, challenge: NeedChallenge) -> str | None:
        if challenge.method == "hashcash":
            token = hashcash.solve(challenge.parameter)
            return json.dumps({"event": "challengeResponse", "token": token, "method": "hashcash"})
        raise CaptchaRequired(f"unsupported challenge method: {challenge.method!r}")

    # ---- decode ----------------------------------------------------------
    def decode(self, raw: str) -> list[object]:
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return []
        ev = obj.get("event")
        if ev == "appendText":
            chunk = obj.get("text", "")
            self._parts.append(chunk)
            return [Delta(chunk)] if chunk else []
        if ev == "startMessage":
            self._parts.clear()
            self._conversation_id = obj.get("conversationId")
            return [Ack()]
        if ev == "challenge":
            return [
                NeedChallenge(
                    method=obj.get("method", ""),
                    parameter=obj.get("parameter", ""),
                    id=obj.get("id"),
                )
            ]
        if ev == "titleUpdate":
            self._title = obj.get("title")
            self._conversation_id = obj.get("conversationId") or self._conversation_id
            return [Ack()]
        if ev == "partCompleted":
            return [Progress("generic")]
        if ev == "done":
            return [
                Final(
                    text="".join(self._parts),
                    conversation_id=self._conversation_id,
                    title=self._title,
                )
            ]
        if ev == "pong":
            return [Pong()]
        if ev == "error":
            # Some errors are anti-bot related; surface as CaptchaRequired so the
            # caller escalates to a browser session rather than retrying blindly.
            raise CaptchaRequired(f"server error event: {obj.get('code') or obj}")
        # connected / received / reportLocalConsents ack / unknown
        return [Ack()]

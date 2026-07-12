"""SignalR "Sydney" ChatHub codec (wire protocol A).

Used by M365 BizChat (`substrate.office.com`). Records are `\\x1e`-delimited JSON
with a numeric `type`. See `docs/protocol/copilot-protocol.md` §1.
"""

from __future__ import annotations

import json

from ..exceptions import CaptchaRequired, ConversationLimitError, ThrottledError
from ..models import Citation, Delta, Final, Progress, Suggestion, Throttling
from .base import Ack, Completed, Pong, ProtocolCodec

DELIM = "\x1e"

_DEFAULT_ALLOWED = [
    "Chat", "Progress", "GenerateContentQuery", "SearchQuery", "Disengaged",
    "InternalSearchQuery", "InternalLoaderMessage", "RenderCardRequest", "Suggestion",
]


def _dumps(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":")) + DELIM


def _card_text(message: dict) -> str | None:
    cards = message.get("adaptiveCards") or []
    if not cards:
        return None
    for block in cards[0].get("body") or []:
        if block.get("text"):
            return block["text"]
    return None


def _citations(message: dict) -> list[Citation]:
    out: list[Citation] = []
    for s in message.get("sourceAttributions") or []:
        out.append(
            Citation(
                title=s.get("providerDisplayName") or s.get("title"),
                url=s.get("seeMoreUrl") or s.get("url"),
            )
        )
    return out


class SignalRCodec(ProtocolCodec):
    def __init__(self) -> None:
        self._emitted = 0          # cumulative length already emitted as deltas
        self._invocation_id = 0

    # ---- encode ----------------------------------------------------------
    def open_frames(self) -> list[str]:
        return [_dumps({"protocol": "json", "version": 1}), _dumps({"type": 6})]

    def encode_ping(self) -> str | None:
        return _dumps({"type": 6})

    def encode_send(self, text: str, *, conversation_id: str, options: dict) -> list[str]:
        o = options or {}
        message = {
            "author": "user",
            "inputMethod": "Keyboard",
            "text": text,
            "messageType": "Chat",
            "locale": o.get("locale", "en-us"),
        }
        if o.get("image_url"):
            message["imageUrl"] = o["image_url"]
            message["originalImageUrl"] = o.get("original_image_url", o["image_url"])
        arg: dict = {
            "source": o.get("source", "officeweb"),
            "optionsSets": o.get("optionsSets", []),
            "allowedMessageTypes": o.get("allowedMessageTypes", _DEFAULT_ALLOWED),
            "sliceIds": [],
            "message": message,
            "conversationId": conversation_id,
            "isStartOfSession": bool(o.get("is_start_of_session", self._invocation_id == 0)),
        }
        for key, val in (
            ("scenario", o.get("scenario")),
            ("tone", o.get("tone")),
            ("plugins", o.get("plugins")),
            ("conversationSignature", o.get("conversation_signature")),
        ):
            if val is not None:
                arg[key] = val
        if o.get("context"):
            arg["previousMessages"] = [{
                "author": "user", "description": o["context"],
                "contextType": "WebPage", "messageType": "Context",
            }]
        frame = {
            "arguments": [arg],
            "invocationId": str(self._invocation_id),
            "target": "chat",
            "type": 4,
        }
        self._invocation_id += 1
        return [_dumps(frame)]

    # ---- decode ----------------------------------------------------------
    def decode(self, raw: str) -> list[object]:
        out: list[object] = []
        for part in raw.split(DELIM):
            if not part:
                continue
            try:
                obj = json.loads(part)
            except json.JSONDecodeError:
                continue
            out.extend(self._decode_one(obj))
        return out

    def _decode_one(self, obj: dict) -> list[object]:
        t = obj.get("type")
        if not obj or t is None:          # `{}` handshake ack
            return [Ack()]
        if t == 6:
            return [Pong()]
        if t == 3 or t == 7:
            return [Completed()]
        if t == 1:
            return self._decode_update(obj)
        if t == 2:
            return self._decode_final(obj)
        return [Ack()]

    def _decode_update(self, obj: dict) -> list[object]:
        args = obj.get("arguments") or []
        if not args:
            return []
        messages = (args[0] or {}).get("messages") or []
        out: list[object] = []
        for m in messages:
            if m.get("author") != "bot":
                continue
            mtype = m.get("messageType")
            if mtype == "Progress" or m.get("contentType") == "EarlyProgress":
                out.append(Progress("thinking", m.get("text", "")))
                continue
            if mtype in ("InternalSearchQuery", "SearchQuery"):
                out.append(Progress("search", m.get("text", "")))
                continue
            if mtype in ("GenerateContentQuery", "GenerateGraphicArt"):
                out.append(Progress("image", m.get("text", "")))
                continue
            text = m.get("text") or _card_text(m)
            if text and len(text) > self._emitted:
                out.append(Delta(text[self._emitted:]))
                self._emitted = len(text)
        return out

    def _decode_final(self, obj: dict) -> list[object]:
        item = obj.get("item") or {}
        thr = None
        t = item.get("throttling")
        if t:
            thr = Throttling(
                used=t.get("numUserMessagesInConversation"),
                maximum=t.get("maxNumUserMessagesInConversation"),
            )
        bot = None
        for m in item.get("messages") or []:
            if m.get("author") == "bot":
                bot = m
        if bot is None:
            result = (item.get("result") or {}).get("value")
            if result == "Throttled":
                raise ThrottledError("request throttled")
            if result == "CaptchaChallenge":
                raise CaptchaRequired("solve the CAPTCHA in a browser to continue")
            if thr and thr.at_limit:
                raise ConversationLimitError(thr.used, thr.maximum)
            return [Completed()]
        return [Final(
            text=bot.get("text") or _card_text(bot) or "",
            citations=_citations(bot),
            suggestions=[Suggestion(s.get("text", "")) for s in bot.get("suggestedResponses") or []],
            throttling=thr,
            conversation_id=item.get("conversationId"),
            title=item.get("defaultChatName"),
        )]

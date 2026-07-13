"""Minimal SignalR "Sydney" ChatHub decoder for M365 BizChat.

We only OBSERVE the server->client frames the page's own socket receives (over
CDP), so we just need to decode them into answer deltas + a terminal event.
Records are `\\x1e`-delimited JSON with a numeric `type`.
"""

from __future__ import annotations

import json

DELIM = "\x1e"


def _card_text(message: dict) -> str | None:
    cards = message.get("adaptiveCards") or []
    if not cards:
        return None
    for block in cards[0].get("body") or []:
        if block.get("text"):
            return block["text"]
    return None


class SignalRParse:
    """`feed(text)->events` / `flush()->events`, producing:
      ("content", delta) | ("done", "stop")
    Bot answer text arrives cumulatively; we diff against what we've emitted."""

    def __init__(self):
        self._emitted = 0
        self.finish_reason = "stop"
        self._buf = ""
        self._done = False

    def feed(self, chunk: str):
        events = []
        self._buf += chunk
        while DELIM in self._buf:
            part, self._buf = self._buf.split(DELIM, 1)
            events += self._frame(part)
        return events

    def flush(self):
        events = []
        if self._buf:
            events += self._frame(self._buf)
            self._buf = ""
        if not self._done:
            self._done = True
            events.append(("done", "stop"))
        return events

    def _frame(self, part: str):
        if not part:
            return []
        try:
            obj = json.loads(part)
        except json.JSONDecodeError:
            return []
        t = obj.get("type")
        if t == 1:
            return self._update(obj)
        if t in (2, 3, 7) and not self._done:
            self._done = True
            return [("done", "stop")]
        return []

    def _update(self, obj: dict):
        args = obj.get("arguments") or []
        if not args:
            return []
        messages = (args[0] or {}).get("messages") or []
        out = []
        for m in messages:
            if m.get("author") != "bot":
                continue
            if m.get("messageType") in (
                "Progress",
                "InternalSearchQuery",
                "SearchQuery",
                "GenerateContentQuery",
                "InternalLoaderMessage",
            ):
                continue
            text = m.get("text") or _card_text(m)
            if text and len(text) > self._emitted:
                out.append(("content", text[self._emitted :]))
                self._emitted = len(text)
        return out

"""Parser for ChatGPT's web backend SSE stream (`f/conversation`, `v1` delta
encoding) -> simple events:

    ("content", text)     a chunk of the assistant's answer
    ("reasoning", text)   a chunk of the model's thinking
    ("done", finish)      stream finished

v1 encoding (verified from docs/discovery/samples/sse_f_conversation.redacted.txt):
- Starts with `event: delta_encoding` / `data: "v1"`.
- `event: delta` frames carry JSON-patch ops: `o`=op, `p`=pointer, `v`=value.
- Absent `o`/`p` are INHERITED from the previous delta (a bare `{"v": ...}`
  repeats the last op/path).
- A turn can contain several messages (a reasoning message, then the answer);
  each `add` switches the "current message", text classified by content_type.
- Completion: a `patch` sets `/message/end_turn=true`, then `data: [DONE]`.

Internal tool/recipient messages (web/python/container.exec) are IGNORED (no
native-channel interception) -- only the assistant's own `all`-recipient text
and reasoning are surfaced.
"""

import json

_IGNORED_TYPES = {
    "resume_conversation_token",
    "input_message",
    "message_marker",
    "title_generation",
    "server_ste_metadata",
    "message_stream_complete",
    "conversation_detail_metadata",
    "conversation_detail",
    "sync",
    "url_moderation",
}
_REASONING_TYPES = {
    "thoughts",
    "reasoning_recap",
    "reasoning",
    "thinking",
    "reasoning_summary",
    "cot",
}

# Private-use-area citation markers embedded in web-search answers; stripped.
_PUA_START, _PUA_SEP, _PUA_END = "", "", ""


class V1DeltaParser:
    def __init__(self):
        self.last_o = None
        self.last_p = None
        self.cur_content_type = None
        self.cur_channel = None
        self.cur_role = None
        self.cur_recipient = None
        self._pua_buf = ""
        self.content = ""
        self.reasoning = ""
        self.finish_reason = None
        self.model_slug = None
        self._done = False

    def feed_line(self, line):
        line = line.rstrip("\r\n")
        if not line or line.startswith("event:") or line.startswith(":"):
            return []
        if not line.startswith("data:"):
            return []
        data = line[5:].lstrip()
        if data == "[DONE]":
            if not self._done:
                self._done = True
                return [("done", self.finish_reason or "stop")]
            return []
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            return []
        return self._handle(obj)

    def _handle(self, obj):
        if not isinstance(obj, dict):
            return []
        t = obj.get("type")
        if t in _IGNORED_TYPES:
            if t == "server_ste_metadata":
                self.model_slug = (obj.get("metadata") or {}).get("model_slug", self.model_slug)
            return []
        if any(k in obj for k in ("p", "o", "v", "c")):
            return self._apply(obj)
        return []

    def _apply(self, d):
        o = d.get("o", self.last_o)
        p = d.get("p", self.last_p)
        v = d.get("v")
        self.last_o, self.last_p = o, p
        if o == "add":
            return self._apply_add(v)
        if o == "append":
            return self._emit(v) if isinstance(v, str) else []
        if o == "patch":
            return [ev for sub in (v or []) for ev in self._apply_sub(sub)]
        if o == "replace" and isinstance(p, str):
            return self._apply_sub({"p": p, "o": "replace", "v": v})
        return []

    def _apply_add(self, v):
        if not isinstance(v, dict):
            return []
        msg = v.get("message") or {}
        if not msg:
            return []
        self._set_message(msg)
        first = self._add_message_text(msg.get("content") or {})
        return self._emit(first) if first else []

    def _add_message_text(self, content):
        parts = content.get("parts") or []
        if parts and isinstance(parts[0], str) and parts[0]:
            return parts[0]
        if isinstance(content.get("thoughts"), list):
            return self._thoughts_text(content["thoughts"])
        return ""

    def _set_message(self, msg):
        self.cur_channel = msg.get("channel")
        self.cur_role = (msg.get("author") or {}).get("role")
        self.cur_recipient = msg.get("recipient")
        self.cur_content_type = (msg.get("content") or {}).get("content_type")
        self.model_slug = (msg.get("metadata") or {}).get("model_slug") or self.model_slug

    def _apply_sub(self, sub):
        sp, so, sv = sub.get("p"), sub.get("o"), sub.get("v")
        if sp == "/message/end_turn" and sv is True:
            self.finish_reason = self.finish_reason or "stop"
        if sp == "/message/status" and sv == "finished_successfully":
            self.finish_reason = self.finish_reason or "stop"
        if so == "append" and isinstance(sp, str) and "parts" in sp and isinstance(sv, str):
            return self._emit(sv)
        return []

    def _emit(self, text):
        if not text:
            return []
        if self.cur_role not in (None, "assistant"):
            return []
        # Ignore internal tool/recipient messages entirely (no native channel).
        if self.cur_recipient not in (None, "all"):
            return []
        if self.cur_content_type in _REASONING_TYPES or self.cur_channel == "commentary":
            self.reasoning += text
            return [("reasoning", text)]
        cleaned = self._declutter(text)
        if not cleaned:
            return []
        self.content += cleaned
        return [("content", cleaned)]

    @staticmethod
    def _thoughts_text(thoughts):
        out = []
        for t in thoughts:
            if not isinstance(t, dict):
                continue
            summary = (t.get("summary") or "").strip()
            body = (t.get("content") or "").strip()
            if summary and body:
                out.append(f"**{summary}**\n{body}")
            elif summary or body:
                out.append(summary or body)
        return "\n\n".join(out)

    def _declutter(self, text):
        buf = self._pua_buf + text
        out = []
        i = 0
        while True:
            s = buf.find(_PUA_START, i)
            if s == -1:
                out.append(buf[i:])
                self._pua_buf = ""
                break
            out.append(buf[i:s])
            e = buf.find(_PUA_END, s)
            if e == -1:
                self._pua_buf = buf[s:]
                break
            out.append(self._render_marker(buf[s + 1 : e]))
            i = e + 1
        return "".join(out)

    _URL_MARKER_LABELED_FIELDS = 3
    _URL_MARKER_BARE_FIELDS = 2

    @classmethod
    def _render_marker(cls, token):
        parts = token.split(_PUA_SEP)
        if parts and parts[0] == "url":
            if len(parts) >= cls._URL_MARKER_LABELED_FIELDS and parts[-1].startswith("http"):
                return f"[{parts[1] or parts[-1]}]({parts[-1]})"
            if len(parts) == cls._URL_MARKER_BARE_FIELDS:
                return parts[1]
        return ""

    def finalize(self):
        out = []
        if self._pua_buf:
            leftover, self._pua_buf = self._pua_buf, ""
            s = leftover.find(_PUA_START)
            txt = leftover if s == -1 else leftover[:s]
            if txt:
                self.content += txt
                out.append(("content", txt))
        return out


class StreamAccumulator:
    """Buffers raw stream text (may split mid-line) and yields parser events."""

    def __init__(self):
        self.parser = V1DeltaParser()
        self._buf = ""

    @property
    def finish_reason(self):
        return self.parser.finish_reason or "stop"

    def feed(self, chunk):
        events = []
        self._buf += chunk
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            events += self.parser.feed_line(line)
        return events

    def flush(self):
        events = []
        if self._buf:
            events += self.parser.feed_line(self._buf)
            self._buf = ""
        events += self.parser.finalize()
        return events

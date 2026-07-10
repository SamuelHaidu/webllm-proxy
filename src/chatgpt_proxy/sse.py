"""Parser for ChatGPT's web backend SSE stream (`f/conversation`, `v1` delta
encoding). Turns the raw event-stream text into a sequence of simple events:

    ("content", text)     -> a chunk of the assistant's answer
    ("reasoning", text)   -> a chunk of the model's thinking/reasoning
    ("done", finish)      -> stream finished (finish = "stop" | "length" | ...)

v1 encoding notes (verified from real captures, see
docs/discovery/samples/sse_f_conversation.redacted.txt and the thinking-model
capture):
- Stream starts with `event: delta_encoding` / `data: "v1"`.
- `event: delta` frames carry JSON-patch-like ops: `o` = op
  (add/append/patch/replace), `p` = JSON pointer path, `v` = value.
- IMPORTANT: absent `o`/`p` are INHERITED from the previous delta. So a bare
  `{"v": ...}` repeats the last (o, p). If the last op was `add` at `""`, a
  bare `{"v":{"message":{...}}}` adds a NEW message; if the last op was
  `append` at `/message/content/parts/0`, a bare `{"v":"text"}` appends text.
- A turn can contain several messages (e.g. a `reasoning_recap`/`thoughts`
  message, then the final `text` answer). Each `add` switches the "current
  message"; text is classified by that message's `content_type`.
- Completion: a `patch` sets `/message/end_turn=true`, then `data: [DONE]`.
"""
import json

_IGNORED_TYPES = {
    "resume_conversation_token", "input_message", "message_marker",
    "title_generation", "server_ste_metadata", "message_stream_complete",
    "conversation_detail_metadata", "conversation_detail", "sync",
}
# content_types whose text is the model's thinking, not the answer
_REASONING_TYPES = {"thoughts", "reasoning_recap", "reasoning", "thinking",
                    "reasoning_summary", "cot"}


class V1DeltaParser:
    def __init__(self):
        self.last_o = None
        self.last_p = None
        self.cur_content_type = None
        self.cur_channel = None
        self.cur_role = None
        self.content = ""
        self.reasoning = ""
        self.finish_reason = None
        self.conversation_id = None
        self.message_id = None
        self.model_slug = None
        self._done = False

    def feed_line(self, line):
        """Feed one raw SSE line. Returns a list of
        ("content"|"reasoning"|"done", value) events."""
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

    # ---- internals --------------------------------------------------------
    def _handle(self, obj):
        if not isinstance(obj, dict):
            return []
        t = obj.get("type")
        if t in _IGNORED_TYPES:
            if t == "server_ste_metadata":
                self.model_slug = (obj.get("metadata") or {}).get(
                    "model_slug", self.model_slug)
            elif t == "resume_conversation_token":
                self.conversation_id = obj.get("conversation_id", self.conversation_id)
            return []
        if any(k in obj for k in ("p", "o", "v", "c")):
            return self._apply(obj)
        return []

    def _apply(self, d):
        # inherit o/p from the previous delta when absent (v1 statefulness)
        o = d["o"] if "o" in d else self.last_o
        p = d["p"] if "p" in d else self.last_p
        v = d.get("v")
        self.last_o, self.last_p = o, p

        if o == "add":
            if isinstance(v, dict):
                msg = v.get("message") or {}
                if msg:
                    self._set_message(msg)
                    parts = (msg.get("content") or {}).get("parts") or []
                    first = parts[0] if parts and isinstance(parts[0], str) else ""
                    return self._emit(first) if first else []
            return []
        if o == "append":
            return self._emit(v) if isinstance(v, str) else []
        if o == "patch":
            out = []
            for sub in (v or []):
                out += self._apply_sub(sub)
            return out
        if o == "replace" and isinstance(p, str):
            return self._apply_sub({"p": p, "o": "replace", "v": v})
        return []

    def _set_message(self, msg):
        self.message_id = msg.get("id", self.message_id)
        self.cur_channel = msg.get("channel")
        self.cur_role = (msg.get("author") or {}).get("role")
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
        # only the assistant's own messages are output; skip the user echo etc.
        if self.cur_role not in (None, "assistant"):
            return []
        if self.cur_content_type in _REASONING_TYPES:
            self.reasoning += text
            return [("reasoning", text)]
        self.content += text
        return [("content", text)]


class StreamAccumulator:
    """Buffers raw stream text (which may split mid-line) and yields parser
    events line by line."""
    def __init__(self):
        self.parser = V1DeltaParser()
        self._buf = ""

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
        return events

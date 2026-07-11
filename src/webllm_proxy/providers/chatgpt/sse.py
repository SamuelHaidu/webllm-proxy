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
    "url_moderation",
}
# content_types whose text is the model's thinking, not the answer
_REASONING_TYPES = {"thoughts", "reasoning_recap", "reasoning", "thinking",
                    "reasoning_summary", "cot"}

# Private-use-area citation/link markers embedded in native-tool (web search)
# answers. Format: <START><kind>[<SEP><field>...]<END>. The web UI replaces
# these using content_references metadata; we strip them (rendering `url`
# markers as plain markdown links) so API consumers get clean text.
_PUA_START, _PUA_SEP, _PUA_END = "", "", ""


class V1DeltaParser:
    def __init__(self):
        self.last_o = None
        self.last_p = None
        self.cur_content_type = None
        self.cur_channel = None
        self.cur_role = None
        self.cur_recipient = None
        self._pua_buf = ""
        # Native-channel tool calls: when the model invokes a tool through
        # ChatGPT's own recipient mechanism (recipient == the tool name, content
        # type "code"), the arguments stream as JSON in content.text. We capture
        # them here as [{"name": recipient, "text": raw-json-args}, ...]; the
        # server converts client-tool ones to OpenAI tool_calls.
        self.native_calls = []
        self._cur_native_id = None
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
                    content = msg.get("content") or {}
                    parts = content.get("parts") or []
                    first = parts[0] if parts and isinstance(parts[0], str) else ""
                    # Reasoning delivered as content.thoughts[] (each a {summary,
                    # content} block) rather than parts — the thinking model's
                    # chain-of-thought. content_type is "thoughts" so _emit routes
                    # it to reasoning.
                    if not first and isinstance(content.get("thoughts"), list):
                        first = self._thoughts_text(content["thoughts"])
                    # A native tool call (recipient != all) whose whole payload
                    # arrives in one `add` as content.text with content_type
                    # "code" and NO parts (e.g. `container.exec`: `bash -lc ...`).
                    # The parts-only read misses it, so pull content.text here.
                    if (not first and self.cur_recipient not in (None, "all")
                            and isinstance(content.get("text"), str)):
                        first = content["text"]
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
        # only the assistant's own messages are output; skip the user echo etc.
        if self.cur_role not in (None, "assistant"):
            return []
        # A non-"all" recipient means the model routed this message to a tool via
        # ChatGPT's native channel (search -> "web", or a client tool -> its
        # name). The tool-call arguments (JSON) stream as this message's text —
        # sometimes as content_type "code" (in content.text), sometimes "text"
        # (in parts) — so capture it regardless of content_type. The server keeps
        # only calls whose recipient is a client-declared tool, so ChatGPT's own
        # native tools (web/python) are captured harmlessly and then filtered.
        if self.cur_recipient not in (None, "all"):
            self._native_append(text)
            return []
        # Reasoning: either a thinking content_type, or ChatGPT's "commentary"
        # channel (the status/thinking narration it streams before the answer,
        # e.g. "I'm adding the parser first ..."). The real answer is on channel
        # "final"/null, so this never steals answer text.
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
        """Flatten a `content.thoughts` list ([{summary, content}, ...]) into one
        reasoning string, keeping each block's short summary as a bold heading."""
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

    def _native_append(self, text):
        """Accumulate streamed argument text for the current native tool call,
        starting a new one whenever the message id changes."""
        if self._cur_native_id != self.message_id or not self.native_calls:
            self._cur_native_id = self.message_id
            self.native_calls.append({"name": self.cur_recipient, "text": ""})
        self.native_calls[-1]["text"] += text

    def _declutter(self, text):
        """Strip ChatGPT web citation/link markers (PUA-delimited) from content,
        streaming-safe: an unterminated marker is held until its end arrives."""
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
                self._pua_buf = buf[s:]  # incomplete marker; hold it
                break
            out.append(self._render_marker(buf[s + 1:e]))
            i = e + 1
        return "".join(out)

    @staticmethod
    def _render_marker(token):
        parts = token.split(_PUA_SEP)
        if parts and parts[0] == "url":
            if len(parts) >= 3 and parts[-1].startswith("http"):
                return f"[{parts[1] or parts[-1]}]({parts[-1]})"
            if len(parts) == 2:
                return parts[1]
        return ""  # cite / genui / video / other: drop

    def finalize(self):
        """At stream end: flush any held plain text (dropping a dangling marker)
        and surface captured native-channel tool calls as ("tool_call", ...)."""
        out = []
        if self._pua_buf:
            leftover, self._pua_buf = self._pua_buf, ""
            s = leftover.find(_PUA_START)
            txt = leftover if s == -1 else leftover[:s]
            if txt:
                self.content += txt
                out.append(("content", txt))
        for nc in self.native_calls:
            if nc["text"].strip():
                out.append(("tool_call", {"name": nc["name"], "arguments": nc["text"]}))
        return out


class StreamAccumulator:
    """Buffers raw stream text (which may split mid-line) and yields parser
    events line by line. Satisfies the core's `Accumulator` interface."""
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

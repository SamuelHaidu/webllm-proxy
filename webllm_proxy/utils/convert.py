"""Hand-rolled OpenAI <-> Anthropic conversion (NO litellm).

Two directions, used by the databricks Claude channel:
  - openai_to_anthropic(): an incoming OpenAI Chat Completions request -> an
    Anthropic Messages request body.
  - AnthropicSSE: a streaming decoder that folds the native Anthropic Messages
    SSE into normalized events (content / reasoning / tool-call / done), which
    the caller re-emits as OpenAI `chat.completion.chunk`s.

Validated against the anthropic-openapi reference by the static tests, and by
the OpenAI-SDK smoke suite end to end.
"""

import json

from .openai import message_text

# reasoning-effort ladder rung -> Anthropic extended-thinking budget (tokens).
_EFFORT_BUDGET = {"min": 2048, "standard": 8192, "extended": 16384, "max": 32768}

# Anthropic stop_reason -> OpenAI finish_reason.
_STOP_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
}


# ---- request: OpenAI -> Anthropic -----------------------------------------
def openai_tools_to_anthropic(tools) -> list:
    out = []
    for t in tools or []:
        fn = t.get("function") if isinstance(t, dict) and isinstance(t.get("function"), dict) else t
        if not isinstance(fn, dict) or not fn.get("name"):
            continue
        out.append(
            {
                "name": fn["name"],
                "description": fn.get("description") or "",
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return out


def _assistant_blocks(m: dict) -> list:
    blocks = []
    text = message_text(m)
    if text:
        blocks.append({"type": "text", "text": text})
    for tc in m.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        blocks.append(
            {"type": "tool_use", "id": tc.get("id"), "name": fn.get("name"), "input": args}
        )
    return blocks or [{"type": "text", "text": ""}]


def openai_messages_to_anthropic(messages) -> tuple[list, list]:
    """-> (system_blocks, anthropic_messages). Consecutive tool results are
    merged into one user turn (Anthropic requires tool_result under user)."""
    system: list = []
    out: list = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            txt = message_text(m)
            if txt:
                system.append({"type": "text", "text": txt})
        elif role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": m.get("tool_call_id"),
                "content": message_text(m),
            }
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif role == "assistant":
            out.append({"role": "assistant", "content": _assistant_blocks(m)})
        else:  # user (default)
            out.append({"role": "user", "content": [{"type": "text", "text": message_text(m)}]})
    return system, out


def openai_to_anthropic(req: dict, *, default_max_tokens: int, effort: str | None = None) -> dict:
    """Build an Anthropic Messages request body from an OpenAI request."""
    system, messages = openai_messages_to_anthropic(req.get("messages") or [])
    body: dict = {
        "messages": messages,
        "max_tokens": req.get("max_tokens") or default_max_tokens,
        "stream": bool(req.get("stream", True)),
    }
    if system:
        body["system"] = system
    tools = openai_tools_to_anthropic(req.get("tools"))
    if tools:
        body["tools"] = tools
    tc = req.get("tool_choice")
    if isinstance(tc, dict) and (tc.get("function") or {}).get("name"):
        body["tool_choice"] = {"type": "tool", "name": tc["function"]["name"]}
    elif tc == "required":
        body["tool_choice"] = {"type": "any"}
    if effort and effort in _EFFORT_BUDGET:
        budget = min(_EFFORT_BUDGET[effort], body["max_tokens"] - 1)
        if budget > 0:
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}
    if req.get("temperature") is not None:
        body["temperature"] = req["temperature"]
    return body


# ---- response: Anthropic SSE -> normalized events -------------------------
class AnthropicSSE:
    """Feed native Anthropic Messages SSE text; yields normalized events:
    ("content", str) | ("reasoning", str)
    ("tool_start", {"index": i, "id": str, "name": str})
    ("tool_args", {"index": i, "partial_json": str})
    ("done", finish_reason)
    """

    def __init__(self):
        self._buf = ""
        self._block_types: dict[int, str | None] = {}
        self.finish_reason = "stop"
        self._done = False

    def feed(self, chunk: str):
        events = []
        self._buf += chunk
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            events += self._line(line)
        return events

    def flush(self):
        events = []
        if self._buf:
            events += self._line(self._buf)
            self._buf = ""
        if not self._done:
            self._done = True
            events.append(("done", self.finish_reason))
        return events

    def _line(self, line):
        line = line.rstrip("\r\n")
        if not line.startswith("data:"):
            return []
        data = line[5:].lstrip()
        if not data or data == "[DONE]":
            return []
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            return []
        return self._handle(obj)

    def _handle(self, obj):
        t = obj.get("type")
        if t == "content_block_start":
            idx = obj.get("index", 0)
            block = obj.get("content_block") or {}
            self._block_types[idx] = block.get("type")
            if block.get("type") == "tool_use":
                return [
                    ("tool_start", {"index": idx, "id": block.get("id"), "name": block.get("name")})
                ]
            return []
        if t == "content_block_delta":
            return self._delta(obj)
        if t == "message_delta":
            stop = (obj.get("delta") or {}).get("stop_reason")
            if stop:
                self.finish_reason = _STOP_MAP.get(stop, "stop")
            return []
        if t == "message_stop":
            if not self._done:
                self._done = True
                return [("done", self.finish_reason)]
            return []
        if t == "error":
            msg = (obj.get("error") or {}).get("message", "anthropic stream error")
            return [("error", msg)]
        return []

    def _delta(self, obj):
        idx = obj.get("index", 0)
        delta = obj.get("delta") or {}
        dt = delta.get("type")
        if dt == "text_delta":
            return [("content", delta.get("text", ""))]
        if dt == "thinking_delta":
            return [("reasoning", delta.get("thinking", ""))]
        if dt == "input_json_delta":
            return [("tool_args", {"index": idx, "partial_json": delta.get("partial_json", "")})]
        return []

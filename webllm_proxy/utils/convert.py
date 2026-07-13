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
    """Build an Anthropic Messages request body from an OpenAI request.

    `stream` is always `True` upstream regardless of the OpenAI client's own
    `stream` field: our capture layer (`AnthropicSSE`) only understands
    `text/event-stream`, and a non-streaming request gets a plain buffered
    `application/json` body instead that it can't parse (silently producing
    an empty reply -- found live, see
    docs/discovery/2026-07-13-token-usage-estimation.md). The client-facing
    stream/non-stream choice is handled entirely on our side (`_stream_claude`
    vs `_nonstream_claude` collapsing the SSE), exactly like the Azure/GPT
    channel already does (`build_azure_body` hardcodes `stream: True` too)."""
    system, messages = openai_messages_to_anthropic(req.get("messages") or [])
    body: dict = {
        "messages": messages,
        "max_tokens": req.get("max_tokens") or default_max_tokens,
        "stream": True,
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
        # Real usage, merged in from `message_start.message.usage` (input side)
        # and `message_delta.usage` (output side) as they arrive -- Bedrock's
        # llmproxy channel reports both (see
        # docs/discovery/2026-07-10-databricks-llmproxy.md). Empty if upstream
        # never sent one; read after the turn completes (`openai_usage()`).
        self.usage: dict = {}

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
        handler = {
            "message_start": self._message_start,
            "content_block_start": self._content_block_start,
            "content_block_delta": self._delta,
            "message_delta": self._message_delta,
            "message_stop": self._message_stop,
            "error": self._error,
        }.get(t)
        return handler(obj) if handler else []

    def _message_start(self, obj):
        usage = ((obj.get("message") or {}).get("usage")) or {}
        if usage:
            self.usage.update(usage)
        return []

    def _content_block_start(self, obj):
        idx = obj.get("index", 0)
        block = obj.get("content_block") or {}
        self._block_types[idx] = block.get("type")
        if block.get("type") == "tool_use":
            return [
                ("tool_start", {"index": idx, "id": block.get("id"), "name": block.get("name")})
            ]
        return []

    def _message_delta(self, obj):
        stop = (obj.get("delta") or {}).get("stop_reason")
        if stop:
            self.finish_reason = _STOP_MAP.get(stop, "stop")
        if obj.get("usage"):
            self.usage.update(obj["usage"])
        return []

    def _message_stop(self, _obj):
        if self._done:
            return []
        self._done = True
        return [("done", self.finish_reason)]

    def _error(self, obj):
        msg = (obj.get("error") or {}).get("message", "anthropic stream error")
        return [("error", msg)]

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

    def openai_usage(self) -> dict | None:
        """`self.usage` (Anthropic shape) -> an OpenAI `usage` dict, or `None`
        if upstream never reported one (caller should estimate instead)."""
        return anthropic_usage_to_openai(self.usage)


def anthropic_usage_to_openai(usage: dict) -> dict | None:
    """Anthropic `message.usage`/`message_delta.usage` -> OpenAI `usage` shape.
    `prompt_tokens` sums `input_tokens` + both cache fields (the real total
    context length processed, not just the newly-billed portion). `None` if
    no usable numbers were ever captured."""
    if not usage:
        return None
    prompt = (
        usage.get("input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
    )
    completion = usage.get("output_tokens", 0)
    if not prompt and not completion:
        return None
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }

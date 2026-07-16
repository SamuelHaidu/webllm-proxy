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
import re

from .openai import message_text

# reasoning-effort ladder rung -> Anthropic extended-thinking budget (tokens).
_EFFORT_BUDGET = {"min": 2048, "standard": 8192, "extended": 16384, "max": 32768}

# reasoning-effort ladder rung -> Anthropic adaptive-thinking `effort` level.
_ADAPTIVE_EFFORT = {"min": "low", "standard": "medium", "extended": "high", "max": "max"}

# Anthropic's hard minimum thinking budget, and the head-room kept for the reply
# text above the budget when a client's `max_tokens` is too small to fit both.
_MIN_THINKING_BUDGET = 1024
_THINKING_REPLY_MARGIN = 512

_FAMILY_RE = re.compile(r"(sonnet|opus|haiku|fable|mythos)")
_VERSION_RE = re.compile(r"(\d+)(?:[-.](\d+))?")

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


def _stop_sequences(stop) -> list[str]:
    """OpenAI `stop` (str | list[str] | None) -> Anthropic `stop_sequences`."""
    if isinstance(stop, str):
        return [stop]
    if isinstance(stop, list):
        return [s for s in stop if isinstance(s, str)]
    return []


def _resolve_tool_choice(req: dict, *, thinking_on: bool) -> dict | None:
    """OpenAI `tool_choice`/`parallel_tool_calls` -> Anthropic `tool_choice`.

    Forced tool use (`required` or a named function) is INCOMPATIBLE with
    extended thinking -- Anthropic allows only `auto`/`none` then -- so it's
    downgraded to `auto` whenever `thinking_on`. `parallel_tool_calls: false`
    maps to `disable_parallel_tool_use` (valid on every choice except `none`)."""
    tc = req.get("tool_choice")
    choice: dict | None = None
    if isinstance(tc, dict) and (tc.get("function") or {}).get("name"):
        if thinking_on:
            choice = {"type": "auto"}
        else:
            choice = {"type": "tool", "name": tc["function"]["name"]}
    elif tc == "required":
        choice = {"type": "auto"} if thinking_on else {"type": "any"}
    elif tc == "none":
        choice = {"type": "none"}
    elif tc == "auto":
        choice = {"type": "auto"}
    if req.get("parallel_tool_calls") is False:
        if choice is None:
            choice = {"type": "auto"}
        if choice["type"] != "none":
            choice["disable_parallel_tool_use"] = True
    return choice


def _apply_sampling(body: dict, req: dict, *, thinking_on: bool) -> None:
    """Forward `temperature`/`top_p` -- but only when thinking is OFF: Anthropic
    rejects a non-1 `temperature` and any `top_p`/`top_k` override while extended
    thinking is enabled (a 400 on the databricks channel)."""
    if thinking_on:
        return
    if req.get("temperature") is not None:
        body["temperature"] = req["temperature"]
    if req.get("top_p") is not None:
        body["top_p"] = req["top_p"]


def _supports_adaptive_thinking(model: str | None) -> bool:
    """True if `model` (a bare slug, in either databricks `claude-4-6-sonnet` or
    Anthropic `claude-sonnet-4-6` style) is an adaptive-thinking-capable Claude:
    Sonnet >= 4.6 (incl. Sonnet 5), Opus >= 4.6, and the Fable/Mythos 5 family.
    Everything else -- notably Claude Sonnet 4.5, the only model enabled on the
    databricks channel today -- uses manual thinking. Conservative by design: an
    unrecognized model falls back to manual (the safe default against the
    channel's unknown-field 400). Extend as new families are enabled."""
    if not model:
        return False
    fam = _FAMILY_RE.search(model.lower())
    if not fam:
        return False
    if fam.group(1) in ("fable", "mythos"):
        return True
    if fam.group(1) not in ("sonnet", "opus"):
        return False  # haiku etc. -> manual
    ver = _VERSION_RE.search(model.lower())
    if not ver:
        return False
    return (int(ver.group(1)), int(ver.group(2) or 0)) >= (4, 6)


def _apply_thinking(body: dict, effort: str | None, model: str | None) -> bool:
    """Set `body["thinking"]` from the effort rung; return whether thinking was
    enabled.

    Adaptive-capable models (`_supports_adaptive_thinking`) get
    `{type: adaptive, display: summarized}` + `output_config.effort` -- the
    model decides how much to think, guided by effort, and manual `budget_tokens`
    is rejected on them. Everything else -- e.g. Claude Sonnet 4.5, the only
    model enabled on the databricks channel -- gets manual
    `{type: enabled, budget_tokens}`, honoring Anthropic's 1024-token floor and
    the `max_tokens > budget_tokens` rule (raising `max_tokens` only if a
    too-small client cap can't otherwise fit the minimum budget)."""
    if not effort or effort not in _EFFORT_BUDGET:
        return False
    if _supports_adaptive_thinking(model):
        # `display: summarized` because it defaults to `omitted` (empty thinking
        # text) on the newest models. Adaptive auto-enables interleaved thinking,
        # so no beta is added for it (see llmproxy.build_llmproxy_envelope).
        body["thinking"] = {"type": "adaptive", "display": "summarized"}
        body["output_config"] = {"effort": _ADAPTIVE_EFFORT[effort]}
        return True
    budget = _EFFORT_BUDGET[effort]
    if budget >= body["max_tokens"]:
        budget = body["max_tokens"] - _THINKING_REPLY_MARGIN
    if budget < _MIN_THINKING_BUDGET:
        budget = _MIN_THINKING_BUDGET
        body["max_tokens"] = max(body["max_tokens"], budget + _THINKING_REPLY_MARGIN)
    body["thinking"] = {"type": "enabled", "budget_tokens": budget}
    return True


def openai_to_anthropic(
    req: dict, *, default_max_tokens: int, effort: str | None = None, model: str | None = None
) -> dict:
    """Build an Anthropic Messages request body from an OpenAI request.

    Forwards every OpenAI field with an Anthropic equivalent: `messages`,
    `max_tokens`/`max_completion_tokens`, `tools`, `tool_choice`/
    `parallel_tool_calls`, `temperature`/`top_p` (thinking permitting), `stop`
    -> `stop_sequences`, `user` -> `metadata.user_id`, and `reasoning_effort`
    (via `effort`) -> `thinking`. Fields with no Anthropic equivalent (`n`,
    `presence_penalty`, `frequency_penalty`, `logit_bias`, `logprobs`, `seed`,
    `response_format`, `stream_options`) are intentionally dropped. `model`
    (the bare slug) selects the thinking mode -- see `_apply_thinking`.

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
    max_tokens = req.get("max_tokens") or req.get("max_completion_tokens") or default_max_tokens
    body: dict = {"messages": messages, "max_tokens": max_tokens, "stream": True}
    if system:
        body["system"] = system
    tools = openai_tools_to_anthropic(req.get("tools"))
    if tools:
        body["tools"] = tools
    thinking_on = _apply_thinking(body, effort, model)
    choice = _resolve_tool_choice(req, thinking_on=thinking_on)
    if choice is not None:
        body["tool_choice"] = choice
    _apply_sampling(body, req, thinking_on=thinking_on)
    stops = _stop_sequences(req.get("stop"))
    if stops:
        body["stop_sequences"] = stops
    if req.get("user"):
        body["metadata"] = {"user_id": req["user"]}
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
        # Per-thinking-block `signature` (the encrypted full reasoning Anthropic
        # streams via `signature_delta`), keyed by content-block index. Captured
        # so it isn't silently dropped; NOT re-emitted, because the OpenAI Chat
        # Completions wire has no field to carry an opaque signature back on the
        # next tool turn (see the 2026-07-15 discovery note).
        self.signatures: dict[int, str] = {}

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
        if dt == "signature_delta":
            sig = delta.get("signature")
            if sig:
                self.signatures[idx] = self.signatures.get(idx, "") + sig
            return []
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

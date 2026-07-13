"""Pure OpenAI Chat Completions wire helpers: id/chunk/completion assembly,
message-text extraction, model namespacing (`provider__slug`), and the
reasoning-effort ladder. No Flask, no browser."""

import json
import time
import uuid

from .tokens import estimate_usage, usage

DELIM = "__"

# OpenAI `reasoning_effort` (minimal/low/medium/high, plus 5.1's `none`) mapped
# onto a 4-level ladder (min/standard/extended/max). Providers translate the
# rung to their own knob (chatgpt web `thinking_effort`, anthropic budget).
_EFFORT_MAP = {
    "minimal": "min",
    "min": "min",
    "none": "min",
    "low": "standard",
    "standard": "standard",
    "medium": "extended",
    "extended": "extended",
    "high": "max",
    "max": "max",
}


def new_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex[:24]


def normalize_effort(body: dict) -> str | None:
    """Read `reasoning_effort` (or `reasoning.effort`) -> a ladder rung, or None."""
    v = body.get("reasoning_effort")
    if not v:
        r = body.get("reasoning")
        v = r.get("effort") if isinstance(r, dict) else None
    if not v:
        return None
    return _EFFORT_MAP.get(str(v).strip().lower())


def message_text(m: dict) -> str:
    """Plain-text content of one `messages[]` entry (string or list-of-parts)."""
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        texts = [
            part.get("text", "") if isinstance(part, dict) else part
            for part in c
            if (isinstance(part, dict) and part.get("type") == "text") or isinstance(part, str)
        ]
        return "\n".join(texts)
    return "" if c is None else str(c)


def append_user_suffix(messages: list[dict], suffix: str) -> list[dict]:
    """Return a copy of `messages` with `suffix` appended to the end of the
    LAST `role: user` message's content -- a per-turn reminder (e.g. "stay in
    character") appended right before the model responds, since long web-UI
    chats can otherwise drift a model out of its assigned role over many
    turns. Configured via `utils.config.ProviderConfigBase.user_suffix_for`.
    No-op (returns `messages` as-is) if there's no user message or `suffix`
    is empty."""
    if not suffix:
        return messages
    idx = next(
        (i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"),
        None,
    )
    if idx is None:
        return messages
    out = list(messages)
    m = dict(out[idx])
    content = m.get("content")
    if isinstance(content, list):
        m["content"] = [*content, {"type": "text", "text": suffix}]
    else:
        text = content if isinstance(content, str) else ("" if content is None else str(content))
        m["content"] = f"{text}\n\n{suffix}".strip() if text else suffix
    out[idx] = m
    return out


# ---- model namespacing ----------------------------------------------------
def join_model(provider: str, slug: str) -> str:
    return f"{provider}{DELIM}{slug}"


def split_model(model_id: str | None) -> tuple[str | None, str | None]:
    """`chatgpt__gpt-5` -> ("chatgpt", "gpt-5"); only the FIRST delimiter splits.
    No delimiter -> (None, model_id); empty -> (None, None)."""
    if not model_id:
        return None, None
    provider, sep, slug = model_id.partition(DELIM)
    if not sep:
        return None, model_id
    return provider, slug


# ---- streaming ------------------------------------------------------------
def chunk(cid: str, created: int, model: str, delta: dict, finish: str | None = None) -> str:
    """One `data: {...}\\n\\n` SSE line for a streaming `chat.completion.chunk`."""
    return (
        "data: "
        + json.dumps(
            {
                "id": cid,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
        )
        + "\n\n"
    )


def completion(
    cid: str, created: int, model: str, message: dict, finish: str, usage_dict: dict | None = None
) -> dict:
    """One non-streaming `chat.completion` object. `usage_dict` defaults to
    zeros; pass a real or estimated one when available (see
    `utils.tokens.estimate_usage`)."""
    return {
        "id": cid,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": usage_dict if usage_dict is not None else usage(),
    }


def attach_usage(
    result: dict,
    messages: list[dict],
    tools: list[dict] | None,
    model: str,
    real_usage: dict | None = None,
) -> dict:
    """Post-process a non-streaming `completion()` result: set its `usage` to
    `real_usage` if the provider actually got one from upstream, otherwise
    estimate it from the request `messages`/`tools` plus the assembled reply
    (text + any tool-call name/arguments). No-op on an error dict (no
    `choices`) -- providers can call this unconditionally on their return
    value."""
    if not isinstance(result, dict) or "choices" not in result:
        return result
    if real_usage is not None:
        result["usage"] = real_usage
        return result
    msg = (result["choices"][0] or {}).get("message") or {}
    content = msg.get("content") or ""
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        content += (fn.get("name") or "") + (fn.get("arguments") or "")
    result["usage"] = estimate_usage(messages, tools, content, model)
    return result


def unavailable_error(message: str = "session initializing") -> dict:
    return {"error": {"message": message, "type": "unavailable"}}


# ---- fold an OpenAI SSE back into one completion (databricks Azure) --------
def _accumulate_tool_call_delta(tool_calls: dict, delta_call: dict) -> None:
    slot = tool_calls.setdefault(
        delta_call.get("index", 0),
        {"id": delta_call.get("id"), "type": "function", "function": {"name": "", "arguments": ""}},
    )
    if delta_call.get("id"):
        slot["id"] = delta_call["id"]
    fn = delta_call.get("function") or {}
    slot["function"]["name"] += fn.get("name") or ""
    slot["function"]["arguments"] += fn.get("arguments") or ""


def _sse_json_objects(sse_text: str):
    for raw_line in sse_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            continue


def _apply_choice(ch: dict, content: list, tool_calls: dict, role: str, finish):
    delta = ch.get("delta") or {}
    if delta.get("role"):
        role = delta["role"]
    if delta.get("content"):
        content.append(delta["content"])
    if ch.get("finish_reason"):
        finish = ch["finish_reason"]
    for tc in delta.get("tool_calls") or []:
        _accumulate_tool_call_delta(tool_calls, tc)
    return role, finish


def assemble_completion(
    sse_text: str, model: str, messages: list[dict] | None = None, tools: list[dict] | None = None
) -> dict:
    """Fold an OpenAI streaming SSE into one `chat.completion` (non-stream
    reply). Prefers upstream's own `usage` if the SSE carried one; otherwise,
    if `messages` was passed, falls back to an estimate (`utils.tokens`)."""
    content: list = []
    tool_calls: dict = {}
    role, finish, up_usage = "assistant", None, None
    for obj in _sse_json_objects(sse_text):
        for ch in obj.get("choices") or []:
            role, finish = _apply_choice(ch, content, tool_calls, role, finish)
        if obj.get("usage"):
            up_usage = obj["usage"]
    message = {"role": role, "content": "".join(content) or None}
    if tool_calls:
        message["tool_calls"] = [tool_calls[k] for k in sorted(tool_calls)]
    usage_dict = up_usage
    if usage_dict is None and messages is not None:
        usage_dict = estimate_usage(messages, tools, "".join(content), model)
    return completion(new_id(), int(time.time()), model, message, finish or "stop", usage_dict)

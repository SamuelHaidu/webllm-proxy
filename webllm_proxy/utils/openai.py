"""Pure OpenAI Chat Completions wire helpers: id/chunk/completion assembly,
message-text extraction, model namespacing (`provider__slug`), and the
reasoning-effort ladder. No Flask, no browser."""

import json
import time
import uuid

from .tokens import usage

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


def completion(cid: str, created: int, model: str, message: dict, finish: str) -> dict:
    """One non-streaming `chat.completion` object."""
    return {
        "id": cid,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": usage(),
    }


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


def assemble_completion(sse_text: str, model: str) -> dict:
    """Fold an OpenAI streaming SSE into one `chat.completion` (non-stream reply)."""
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
    out = completion(new_id(), int(time.time()), model, message, finish or "stop")
    if up_usage:
        out["usage"] = up_usage
    return out

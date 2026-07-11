"""Pure OpenAI Chat Completions wire-format helpers, shared by both providers'
OpenAI-shaped surfaces (chatgpt's own completions endpoint, databricks' Azure
GPT-4.1 channel)."""

import json
import time
import uuid


def new_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex[:24]


def message_text(m: dict) -> str:
    """The plain-text content of one `messages[]` entry, whether `content` is
    a string or a list of parts (only `{"type": "text", ...}` parts and bare
    strings contribute; other part types -- images, etc. -- are skipped)."""
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


def normalize_model(model: str | None) -> str | None:
    """None (use the provider's current/default model) for an empty or
    generic alias like "auto"/"default"/"chatgpt"/"gpt"."""
    if not model:
        return None
    if model.strip().lower() in ("auto", "default", "chatgpt", "gpt", ""):
        return None
    return model.strip()


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


def unavailable_error(message: str = "session initializing") -> dict:
    return {"error": {"message": message, "type": "unavailable"}}


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
    """Yield each parsed JSON object from an OpenAI-shaped SSE stream's
    `data: {...}` lines, stopping at `[DONE]` and skipping unparseable lines."""
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
    """Apply one `choices[]` entry's delta onto the running `content`/
    `tool_calls` accumulators; returns the (possibly updated) `(role, finish)`."""
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
    """Fold an OpenAI streaming SSE (`data: {chunk}` lines) into one
    `chat.completion` object, for clients that asked for a non-stream reply.
    Concatenates text deltas and tool_call argument deltas; keeps the last
    finish_reason and any usage."""
    content: list = []
    tool_calls: dict = {}
    role, finish, usage = "assistant", None, None
    for obj in _sse_json_objects(sse_text):
        for ch in obj.get("choices") or []:
            role, finish = _apply_choice(ch, content, tool_calls, role, finish)
        if obj.get("usage"):
            usage = obj["usage"]
    message = {"role": role, "content": "".join(content) or None}
    if tool_calls:
        message["tool_calls"] = [tool_calls[k] for k in sorted(tool_calls)]
    return {
        "id": new_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish or "stop"}],
        "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }

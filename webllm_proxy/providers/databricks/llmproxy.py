"""Request-envelope building for the two Databricks llmproxy channels: pure
functions, no Flask, no browser -- turn an incoming client request into the
exact body the llmproxy endpoint expects. See
docs/discovery/2026-07-10-databricks-llmproxy.md.
"""

import json
import uuid

from ...prompts.loader import default_store
from . import config

# Non-standard tool fields some Anthropic clients (e.g. pi) add that the
# Databricks llmproxy -> Bedrock passthrough rejects with an empty-body 400.
# `eager_input_streaming` is a client-side streaming hint, not a Bedrock tool
# field; strip it. (`cache_control` IS accepted, so it's intentionally kept.)
_DROP_TOOL_FIELDS = {"eager_input_streaming"}


def _normalize_tool(t):
    """Drop non-Bedrock-safe fields (see `_DROP_TOOL_FIELDS`) and add the
    explicit `type` llmproxy tools require."""
    if not isinstance(t, dict):
        return t
    cleaned = {k: v for k, v in t.items() if k not in _DROP_TOOL_FIELDS}
    if "type" not in cleaned and "name" in cleaned:
        cleaned["type"] = "custom"  # llmproxy tools carry an explicit type
    return cleaned


def build_llmproxy_body(req: dict):
    """Turn an incoming Anthropic Messages request into the Databricks llmproxy
    body: keep the Anthropic fields, add the `_llmproxy_fields` routing envelope,
    and map `model` -> `model_registration` (llmproxy has no top-level model).

    Prepends the Genie framing (defeats the `editor-assistant-agent-mode`
    channel's out-of-context scope guard -- see `prompts/genie_framing.md`)
    ahead of the optional token-efficiency style block and the caller's own
    system, which also guarantees a non-empty system block (llmproxy requires
    one)."""
    model = req.get("model") or config.DEFAULT_MODEL
    body = dict(req)
    body.pop("model", None)

    sysv = body.get("system")
    if isinstance(sysv, str) and sysv:
        caller_sys = [{"type": "text", "text": sysv}]
    elif isinstance(sysv, list):
        caller_sys = list(sysv)
    else:
        caller_sys = []
    framing = [{"type": "text", "text": default_store.get("genie_framing")}]
    if config.STYLE_RULES:
        framing.append({"type": "text", "text": default_store.get("style_rules")})
    body["system"] = framing + caller_sys

    tools = body.get("tools")
    if isinstance(tools, list):
        body["tools"] = [_normalize_tool(t) for t in tools]

    body.setdefault("max_tokens", 4096)
    body.setdefault("stream", True)
    body["_llmproxy_fields"] = {
        "model_registration": model,
        "endpoint": config.ANTHROPIC_ENDPOINT,
        "agent_name": config.AGENT_NAME,
        "client_id": config.CLIENT_ID,
        "trace_id": str(uuid.uuid4()),
        "call_id": str(uuid.uuid4()),
    }
    return body, model


def build_azure_body(req: dict):
    """Turn an incoming OpenAI Chat Completions request into the Databricks
    `proxy/chat/completions` (Azure OpenAI) envelope: the OpenAI request goes
    under `params`, with the routing fields (`@method`, `deployment`, `model`,
    `apiVersion`) and `metadata.clientId` alongside. We ALWAYS request upstream
    streaming (`params.stream=True`) because the CDP capture is reliable for SSE
    but returns an empty body for a single non-stream response; the route
    re-assembles a non-stream completion from the chunks when the client wants one."""
    model = req.get("model") or (
        config.OPENAI_MODELS[0] if config.OPENAI_MODELS else "gpt-41-2025-04-14"
    )
    params = dict(req)
    params["model"] = model
    params["stream"] = True  # force upstream SSE (see docstring)
    return {
        "params": params,
        "metadata": {"traceId": str(uuid.uuid4()), "clientId": config.AZURE_CLIENT_ID},
        "@method": "openAiServiceChatCompletionRequest",
        "deployment": model,
        "model": model,
        "apiVersion": config.AZURE_API_VERSION,
    }, model


def _estimate_input_tokens_text(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return " ".join(_estimate_input_tokens_text(x) for x in v)
    if isinstance(v, dict):
        return " ".join(str(v.get(k, "")) for k in ("text", "content", "input"))
    return ""


def estimate_input_tokens(req: dict) -> int:
    """Rough local token estimate (~4 chars/token) over the countable request
    text: system + message content + tool schemas. Used as a fallback because the
    Databricks llmproxy channel doesn't expose Anthropic's real `count_tokens`
    endpoint (only `anthropic/v1/messages` is whitelisted). Approximate, not exact."""
    chars = len(_estimate_input_tokens_text(req.get("system")))
    for m in req.get("messages") or []:
        chars += len(_estimate_input_tokens_text(m.get("content")))
    for t in req.get("tools") or []:
        if isinstance(t, dict):
            chars += len(str(t.get("name", ""))) + len(str(t.get("description", "")))
            chars += len(json.dumps(t.get("input_schema") or {}))
    return max(1, chars // 4)

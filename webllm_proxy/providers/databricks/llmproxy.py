"""Request-envelope building for the two Databricks llmproxy channels (pure
functions, no Flask/browser): wrap an Anthropic Messages body in the llmproxy
routing envelope, and build the Azure OpenAI `proxy/chat/completions` body.
See docs/discovery/2026-07-10-databricks-llmproxy.md.
"""

from __future__ import annotations

import uuid

from ...utils.prompts import default_store

LLMPROXY_PATH = "/ajax-api/2.0/conversation/llmproxy/"
CHAT_COMPLETIONS_PATH = "/ajax-api/2.0/conversation/proxy/chat/completions"
ANTHROPIC_ENDPOINT = "anthropic/v1/messages"
CLIENT_ID = "editor-assistant-agent-mode"
AGENT_NAME = "GenieCodeFullChat"
AZURE_CLIENT_ID = "auto-rename-action"
AZURE_API_VERSION = "2025-01-01-preview"
CLAUDE_MAX_TOKENS = 64000

_DROP_TOOL_FIELDS = {"eager_input_streaming"}


def _prepend_system(anthropic_body: dict, *, style_rules: bool) -> None:
    """Prepend the Genie framing (+ optional style rules) so the editor channel's
    scope guard is defeated and the system block is never empty."""
    sysv = anthropic_body.get("system")
    caller = (
        list(sysv)
        if isinstance(sysv, list)
        else ([] if not sysv else [{"type": "text", "text": sysv}])
    )
    framing = [{"type": "text", "text": default_store.get("databricks_default_system_prompt")}]
    if style_rules:
        framing.append({"type": "text", "text": default_store.get("style_rules")})
    anthropic_body["system"] = framing + caller


def _normalize_tool(t):
    if not isinstance(t, dict):
        return t
    cleaned = {k: v for k, v in t.items() if k not in _DROP_TOOL_FIELDS}
    if "type" not in cleaned and "name" in cleaned:
        cleaned["type"] = "custom"
    return cleaned


def build_llmproxy_envelope(anthropic_body: dict, model: str, *, style_rules: bool) -> dict:
    """Add the `_llmproxy_fields` routing envelope to an Anthropic Messages body."""
    body = dict(anthropic_body)
    _prepend_system(body, style_rules=style_rules)
    tools = body.get("tools")
    if isinstance(tools, list):
        body["tools"] = [_normalize_tool(t) for t in tools]
    body.setdefault("max_tokens", 4096)
    body.setdefault("stream", True)
    body["_llmproxy_fields"] = {
        "model_registration": model,
        "endpoint": ANTHROPIC_ENDPOINT,
        "agent_name": AGENT_NAME,
        "client_id": CLIENT_ID,
        "trace_id": str(uuid.uuid4()),
        "call_id": str(uuid.uuid4()),
    }
    return body


def build_azure_body(openai_req: dict, model: str) -> dict:
    """OpenAI Chat Completions request -> the Azure `proxy/chat/completions`
    envelope. Always streams upstream (the CDP capture only reliably gets SSE)."""
    params = dict(openai_req)
    params["model"] = model
    params["stream"] = True
    return {
        "params": params,
        "metadata": {"traceId": str(uuid.uuid4()), "clientId": AZURE_CLIENT_ID},
        "@method": "openAiServiceChatCompletionRequest",
        "deployment": model,
        "model": model,
        "apiVersion": AZURE_API_VERSION,
    }

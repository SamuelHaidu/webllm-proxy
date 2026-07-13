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


def _prepend_system(anthropic_body: dict, *, system_prompt: str | None, style_rules: bool) -> None:
    """Set `system` to ONLY the configured prompt (if any) + optional style
    rules. The client's own system text -- already converted onto
    `anthropic_body["system"]` by `convert.openai_to_anthropic` -- is dropped:
    the proxy sends a system prompt solely when the operator configured one
    (`utils.config.ProviderConfigBase.system_prompt_for`), never from the
    client. Historically this framing was unconditional because an
    empty/unrecognized system block made the editor channel's scope guard
    refuse the request -- that's now the operator's call via config."""
    framing = []
    if system_prompt:
        framing.append({"type": "text", "text": default_store.get(system_prompt)})
    if style_rules:
        framing.append({"type": "text", "text": default_store.get("style_rules")})
    if framing:
        anthropic_body["system"] = framing
    else:
        anthropic_body.pop("system", None)


def _normalize_tool(t):
    if not isinstance(t, dict):
        return t
    cleaned = {k: v for k, v in t.items() if k not in _DROP_TOOL_FIELDS}
    if "type" not in cleaned and "name" in cleaned:
        cleaned["type"] = "custom"
    return cleaned


def build_llmproxy_envelope(
    anthropic_body: dict, model: str, *, style_rules: bool, system_prompt: str | None = None
) -> dict:
    """Add the `_llmproxy_fields` routing envelope to an Anthropic Messages body."""
    body = dict(anthropic_body)
    _prepend_system(body, system_prompt=system_prompt, style_rules=style_rules)
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


def build_azure_body(openai_req: dict, model: str, *, system_prompt: str | None = None) -> dict:
    """OpenAI Chat Completions request -> the Azure `proxy/chat/completions`
    envelope. Always streams upstream (the CDP capture only reliably gets SSE).
    The client's own `role:"system"` messages are dropped (see
    `_prepend_system`'s docstring); a synthetic one is prepended only if
    `system_prompt` names a configured prompt."""
    params = dict(openai_req)
    messages = [m for m in (params.get("messages") or []) if m.get("role") != "system"]
    if system_prompt:
        messages = [
            {"role": "system", "content": default_store.get(system_prompt)},
            *messages,
        ]
    params["messages"] = messages
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

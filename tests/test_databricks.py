"""databricks: llmproxy envelope, Azure body, ConversationModelStatuses parse."""

from webllm_proxy.providers.databricks import DatabricksProvider, llmproxy
from webllm_proxy.providers.databricks.models import parse_model_statuses


def test_llmproxy_envelope():
    anthropic_body = {
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        "max_tokens": 4096,
    }
    body = llmproxy.build_llmproxy_envelope(
        anthropic_body,
        "claude-4-5-sonnet",
        style_rules=True,
        system_prompt="databricks_default_system_prompt",
    )
    assert body["_llmproxy_fields"]["model_registration"] == "claude-4-5-sonnet"
    assert body["_llmproxy_fields"]["endpoint"] == llmproxy.ANTHROPIC_ENDPOINT
    assert body["_llmproxy_fields"]["client_id"] == llmproxy.CLIENT_ID
    # system framing prepended, never empty
    assert isinstance(body["system"], list) and body["system"]


def test_llmproxy_envelope_strips_bad_tool_fields():
    body = llmproxy.build_llmproxy_envelope(
        {"messages": [], "tools": [{"name": "t", "eager_input_streaming": True}]},
        "claude-4-5-sonnet",
        style_rules=False,
        system_prompt=None,
    )
    assert "eager_input_streaming" not in body["tools"][0]
    assert body["tools"][0]["type"] == "custom"


def test_llmproxy_envelope_no_system_prompt_configured_sends_none():
    """No `system_prompt` configured and no style rules -> no `system` field at
    all, even if the client tried to send one -- the proxy never forwards the
    client's own system content."""
    body = llmproxy.build_llmproxy_envelope(
        {"messages": [], "system": "ignore me, I'm from the client"},
        "claude-4-5-sonnet",
        style_rules=False,
        system_prompt=None,
    )
    assert "system" not in body


def test_llmproxy_envelope_drops_caller_system_when_configured():
    """A configured system_prompt replaces the client's own system content
    outright -- it is never appended after it."""
    body = llmproxy.build_llmproxy_envelope(
        {"messages": [], "system": "ignore me, I'm from the client"},
        "claude-4-5-sonnet",
        style_rules=False,
        system_prompt="databricks_default_system_prompt",
    )
    assert len(body["system"]) == 1
    assert "ignore me" not in body["system"][0]["text"]


def test_azure_body():
    body = llmproxy.build_azure_body(
        {"messages": [{"role": "user", "content": "x"}]}, "gpt-41-2025-04-14"
    )
    assert body["@method"] == "openAiServiceChatCompletionRequest"
    assert body["deployment"] == "gpt-41-2025-04-14"
    assert body["params"]["stream"] is True
    assert body["metadata"]["clientId"] == llmproxy.AZURE_CLIENT_ID


def test_azure_body_drops_client_system_by_default():
    body = llmproxy.build_azure_body(
        {
            "messages": [
                {"role": "system", "content": "ignore me"},
                {"role": "user", "content": "x"},
            ]
        },
        "gpt-41-2025-04-14",
    )
    assert body["params"]["messages"] == [{"role": "user", "content": "x"}]


def test_azure_body_uses_configured_system_prompt_instead():
    body = llmproxy.build_azure_body(
        {
            "messages": [
                {"role": "system", "content": "ignore me"},
                {"role": "user", "content": "x"},
            ]
        },
        "gpt-41-2025-04-14",
        system_prompt="databricks_default_system_prompt",
    )
    msgs = body["params"]["messages"]
    assert msgs[0]["role"] == "system"
    assert "ignore me" not in msgs[0]["content"]
    assert msgs[1] == {"role": "user", "content": "x"}


def _availability(client_id, statuses):
    return {"clientId": client_id, "modelStatuses": statuses}


def _resp(*availability):
    return {
        "data": {"conversationListModelAvailability": {"modelAvailability": list(availability)}}
    }


def test_parse_model_statuses_keeps_available_for_client():
    resp = _resp(
        _availability(
            llmproxy.CLIENT_ID,
            [
                {"name": "claude-4-5-sonnet", "status": "AVAILABLE", "isAvailable": True},
                {"name": "claude-4-6-opus", "status": "MODEL_DISABLED", "isAvailable": False},
                {"name": "gpt-41-2025-04-14", "status": "AVAILABLE", "isAvailable": True},
                {"name": "o3-2025-04-16", "status": "MODEL_NOT_FOUND", "isAvailable": False},
            ],
        ),
        # a different clientId's entitlements must be ignored
        _availability("editor-assistant", [{"name": "gpt-5-2025-08-07", "status": "AVAILABLE"}]),
    )
    assert parse_model_statuses(resp) == ["claude-4-5-sonnet", "gpt-41-2025-04-14"]


def test_parse_model_statuses_client_absent():
    resp = _resp(_availability("some-other-client", [{"name": "x", "status": "AVAILABLE"}]))
    assert parse_model_statuses(resp) == []


def test_parse_model_statuses_empty():
    assert parse_model_statuses({"data": {}}) == []
    assert parse_model_statuses({}) == []
    assert parse_model_statuses(None) == []


def test_apply_user_suffix_noop_when_not_configured():
    provider = DatabricksProvider(None, workspace_url="https://x/?o=1")
    request = {"messages": [{"role": "user", "content": "hi"}]}
    assert provider._apply_user_suffix(request, "claude-4-5-sonnet") is request


def test_apply_user_suffix_appends_configured_text():
    provider = DatabricksProvider(
        None, workspace_url="https://x/?o=1", user_suffix=lambda _slug: "style_rules"
    )
    request = {"messages": [{"role": "user", "content": "hi"}]}
    out = provider._apply_user_suffix(request, "claude-4-5-sonnet")
    assert out is not request
    assert out["messages"][0]["content"].startswith("hi\n\n")
    assert request["messages"][0]["content"] == "hi"  # original untouched

"""databricks: llmproxy envelope, Azure body, ConversationModelStatuses parse."""

from webllm_proxy.providers.databricks import llmproxy
from webllm_proxy.providers.databricks.models import parse_model_statuses


def test_llmproxy_envelope():
    anthropic_body = {
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        "max_tokens": 4096,
    }
    body = llmproxy.build_llmproxy_envelope(anthropic_body, "claude-4-5-sonnet", style_rules=True)
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
    )
    assert "eager_input_streaming" not in body["tools"][0]
    assert body["tools"][0]["type"] == "custom"


def test_azure_body():
    body = llmproxy.build_azure_body(
        {"messages": [{"role": "user", "content": "x"}]}, "gpt-41-2025-04-14"
    )
    assert body["@method"] == "openAiServiceChatCompletionRequest"
    assert body["deployment"] == "gpt-41-2025-04-14"
    assert body["params"]["stream"] is True
    assert body["metadata"]["clientId"] == llmproxy.AZURE_CLIENT_ID


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

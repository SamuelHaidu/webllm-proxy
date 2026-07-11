"""Databricks request-mapping unit tests (no browser): an Anthropic Messages
request -> the llmproxy body (Anthropic fields + routing envelope)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from webllm_proxy.providers.databricks.routes import build_llmproxy_body  # noqa: E402
from webllm_proxy.providers.databricks import config  # noqa: E402


def test_maps_model_to_registration_and_strips_top_level_model():
    body, model = build_llmproxy_body({"model": "claude-4-5-sonnet",
                                       "messages": [{"role": "user", "content": "hi"}]})
    assert model == "claude-4-5-sonnet"
    assert "model" not in body
    f = body["_llmproxy_fields"]
    assert f["model_registration"] == "claude-4-5-sonnet"
    assert f["endpoint"] == "anthropic/v1/messages"
    assert f["client_id"] == config.CLIENT_ID
    assert f["trace_id"] and f["call_id"]


def test_string_system_is_wrapped():
    body, _ = build_llmproxy_body({"system": "be terse", "messages": []})
    assert body["system"] == [{"type": "text", "text": "be terse"}]


def test_tools_get_custom_type():
    body, _ = build_llmproxy_body({
        "messages": [], "tools": [{"name": "ping", "description": "p",
                                   "input_schema": {"type": "object"}}]})
    assert body["tools"][0]["type"] == "custom"
    assert body["tools"][0]["name"] == "ping"


def test_defaults_max_tokens_and_stream():
    body, _ = build_llmproxy_body({"messages": []})
    assert body["max_tokens"] >= 1
    assert body["stream"] is True


def test_missing_model_uses_default():
    _, model = build_llmproxy_body({"messages": []})
    assert model == config.DEFAULT_MODEL


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print("ALL PASS (%d)" % len(fns))

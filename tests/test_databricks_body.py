"""Databricks request-mapping unit tests (no browser): an Anthropic Messages
request -> the llmproxy body (Anthropic fields + routing envelope)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from webllm_proxy.providers.databricks.routes import build_llmproxy_body, _estimate_input_tokens  # noqa: E402
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


def test_string_system_is_wrapped_after_genie_preamble():
    body, _ = build_llmproxy_body({"system": "be terse", "messages": []})
    # Genie framing is prepended; the caller's system follows as its own block.
    assert body["system"][0]["type"] == "text" and "Genie" in body["system"][0]["text"]
    assert body["system"][-1] == {"type": "text", "text": "be terse"}


def test_genie_preamble_prepended_when_no_system():
    body, _ = build_llmproxy_body({"messages": []})
    # Non-empty system block is required by llmproxy; the Genie framing supplies it.
    assert body["system"] and "Genie" in body["system"][0]["text"]


def test_list_system_preserved_after_genie_preamble():
    caller = [{"type": "text", "text": "sys A", "cache_control": {"type": "ephemeral"}}]
    body, _ = build_llmproxy_body({"system": caller, "messages": []})
    assert "Genie" in body["system"][0]["text"]
    assert body["system"][-1] == caller[0]


def test_style_rules_prepended_when_enabled():
    # With STYLE_RULES on (default), the token-efficiency block sits between the
    # Genie framing and the caller's system, and the caller's block stays last.
    caller = [{"type": "text", "text": "caller sys"}]
    body, _ = build_llmproxy_body({"system": caller, "messages": []})
    joined = " ".join(b["text"] for b in body["system"])
    assert "Genie" in body["system"][0]["text"]
    assert "Response style" in joined and "concise" in joined
    assert body["system"][-1] == caller[0]


def test_tools_get_custom_type():
    body, _ = build_llmproxy_body({
        "messages": [], "tools": [{"name": "ping", "description": "p",
                                   "input_schema": {"type": "object"}}]})
    assert body["tools"][0]["type"] == "custom"
    assert body["tools"][0]["name"] == "ping"


def test_tools_drop_eager_input_streaming():
    # pi adds `eager_input_streaming: true`, which the llmproxy/Bedrock passthrough
    # rejects with an empty-body 400; it must be stripped (cache_control kept).
    src = [{"name": "bash", "description": "run", "input_schema": {"type": "object"},
            "eager_input_streaming": True, "cache_control": {"type": "ephemeral"}}]
    body, _ = build_llmproxy_body({"messages": [], "tools": src})
    tool = body["tools"][0]
    assert "eager_input_streaming" not in tool
    assert tool["cache_control"] == {"type": "ephemeral"}
    assert tool["type"] == "custom"
    # caller's dict must not be mutated
    assert src[0]["eager_input_streaming"] is True


def test_defaults_max_tokens_and_stream():
    body, _ = build_llmproxy_body({"messages": []})
    assert body["max_tokens"] >= 1
    assert body["stream"] is True


def test_missing_model_uses_default():
    _, model = build_llmproxy_body({"messages": []})
    assert model == config.DEFAULT_MODEL


def test_estimate_input_tokens_counts_text():
    # ~4 chars/token over system + message + tool text; monotonic and >= 1.
    small = _estimate_input_tokens({"messages": [{"role": "user", "content": "hi"}]})
    big = _estimate_input_tokens({
        "system": "a long system prompt " * 20,
        "messages": [{"role": "user", "content": "please count these tokens " * 20}],
        "tools": [{"name": "bash", "description": "run a command",
                   "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}}}]})
    assert small >= 1 and big > small
    # list content blocks are counted too
    assert _estimate_input_tokens(
        {"messages": [{"role": "user", "content": [{"type": "text", "text": "x" * 40}]}]}) >= 10


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print("ALL PASS (%d)" % len(fns))

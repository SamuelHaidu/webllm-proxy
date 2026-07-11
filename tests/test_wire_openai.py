"""OpenAI wire-format unit tests (no browser): message text extraction, model
normalization, and SSE assembly (shared by chatgpt's own completions and
databricks' Azure GPT-4.1 channel)."""

from webllm_proxy.wire.openai import assemble_completion, message_text, normalize_model


def test_message_text_string_content():
    assert message_text({"content": "hi"}) == "hi"


def test_message_text_list_content_skips_non_text_parts():
    m = {
        "content": [
            {"type": "text", "text": "a"},
            {"type": "image_url", "image_url": {"url": "x"}},
            "b",
        ]
    }
    assert message_text(m) == "a\nb"


def test_message_text_none_content_is_empty():
    assert message_text({"content": None}) == ""


def test_normalize_model_generic_aliases_are_none():
    assert normalize_model("auto") is None
    assert normalize_model("") is None
    assert normalize_model(None) is None


def test_normalize_model_real_name_passes_through():
    assert normalize_model(" gpt-5-mini ") == "gpt-5-mini"


def test_assemble_completion_text():
    sse = (
        'data: {"choices":[{"delta":{"role":"assistant"}}]}\n'
        'data: {"choices":[{"delta":{"content":"Hel"}}]}\n'
        'data: {"choices":[{"delta":{"content":"lo"}}]}\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
        '"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n'
        "data: [DONE]\n"
    )
    c = assemble_completion(sse, "gpt-41-2025-04-14")
    assert c["object"] == "chat.completion" and c["model"] == "gpt-41-2025-04-14"
    msg = c["choices"][0]["message"]
    assert msg["role"] == "assistant" and msg["content"] == "Hello"
    assert c["choices"][0]["finish_reason"] == "stop"
    assert c["usage"]["total_tokens"] == 5


def test_assemble_completion_tool_calls():
    sse = (
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1",'
        '"function":{"name":"get_", "arguments":"{\\"x\\":"}}]}}]}\n'
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        '"function":{"name":"weather","arguments":"1}"}}]},"finish_reason":"tool_calls"}]}\n'
        "data: [DONE]\n"
    )
    c = assemble_completion(sse, "gpt-41-mini-2025-04-14")
    tc = c["choices"][0]["message"]["tool_calls"][0]
    assert tc["id"] == "call_1" and tc["function"]["name"] == "get_weather"
    assert tc["function"]["arguments"] == '{"x":1}'
    assert c["choices"][0]["finish_reason"] == "tool_calls"

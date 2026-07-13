"""utils.openai: model namespacing, effort ladder, SSE folding."""

from webllm_proxy.utils import openai as wire


def test_split_and_join():
    assert wire.join_model("chatgpt", "gpt-5") == "chatgpt__gpt-5"
    assert wire.split_model("chatgpt__gpt-5") == ("chatgpt", "gpt-5")
    # only the first delimiter splits (slug may itself contain __)
    assert wire.split_model("copilot__Gpt_5_5__deep") == ("copilot", "Gpt_5_5__deep")
    assert wire.split_model("bare") == (None, "bare")
    assert wire.split_model("") == (None, None)


def test_normalize_effort():
    assert wire.normalize_effort({"reasoning_effort": "high"}) == "max"
    assert wire.normalize_effort({"reasoning_effort": "minimal"}) == "min"
    assert wire.normalize_effort({"reasoning": {"effort": "medium"}}) == "extended"
    assert wire.normalize_effort({}) is None


def test_message_text_parts():
    m = {"content": [{"type": "text", "text": "a"}, {"type": "image_url"}, "b"]}
    assert wire.message_text(m) == "a\nb"


def test_assemble_completion_from_sse():
    sse = (
        'data: {"choices":[{"delta":{"role":"assistant","content":"Hel"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )
    out = wire.assemble_completion(sse, "databricks__gpt-41")
    assert out["object"] == "chat.completion"
    assert out["choices"][0]["message"]["content"] == "Hello"
    assert out["choices"][0]["finish_reason"] == "stop"

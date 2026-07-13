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
    # no `messages` passed -> no estimate, falls back to zeros (unchanged default)
    assert out["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_assemble_completion_estimates_usage_when_messages_given():
    sse = (
        'data: {"choices":[{"delta":{"role":"assistant","content":"Hello"}}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )
    out = wire.assemble_completion(sse, "databricks__gpt-41", [{"role": "user", "content": "hi"}])
    assert out["usage"]["prompt_tokens"] > 0
    assert out["usage"]["completion_tokens"] > 0


def test_assemble_completion_prefers_upstream_usage():
    sse = (
        'data: {"choices":[{"delta":{"role":"assistant","content":"Hi"}}],'
        '"usage":{"prompt_tokens":11,"completion_tokens":22,"total_tokens":33}}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )
    out = wire.assemble_completion(sse, "databricks__gpt-41", [{"role": "user", "content": "hi"}])
    assert out["usage"] == {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33}


def test_completion_usage_dict_override():
    msg = {"role": "assistant", "content": "hi"}
    out = wire.completion(
        "id", 0, "m", msg, "stop", {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}
    )
    assert out["usage"] == {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}


def test_completion_usage_defaults_to_zeros():
    msg = {"role": "assistant", "content": "hi"}
    out = wire.completion("id", 0, "m", msg, "stop")
    assert out["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_attach_usage_estimates_from_result():
    result = wire.completion(
        "id", 0, "chatgpt__gpt-5", {"role": "assistant", "content": "hello there"}, "stop"
    )
    out = wire.attach_usage(result, [{"role": "user", "content": "hi"}], None, "chatgpt__gpt-5")
    assert out["usage"]["prompt_tokens"] > 0
    assert out["usage"]["completion_tokens"] > 0


def test_attach_usage_noop_on_error_dict():
    err = {"error": {"message": "boom"}}
    out = wire.attach_usage(err, [{"role": "user", "content": "hi"}], None, "chatgpt__gpt-5")
    assert out is err


def test_attach_usage_prefers_real_usage_over_estimate():
    result = wire.completion(
        "id", 0, "databricks__claude-4-5-sonnet", {"role": "assistant", "content": "hi"}, "stop"
    )
    real = {"prompt_tokens": 111, "completion_tokens": 222, "total_tokens": 333}
    out = wire.attach_usage(
        result,
        [{"role": "user", "content": "hi"}],
        None,
        "databricks__claude-4-5-sonnet",
        real_usage=real,
    )
    assert out["usage"] == real

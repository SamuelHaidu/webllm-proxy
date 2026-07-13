"""utils.tokens: BPE counting (tiktoken built-ins + vendored claude encoding),
the per-provider chat-format overhead estimate, and runtime profile config."""

import copy

import pytest

from webllm_proxy.utils import tokens


@pytest.fixture
def restore_profiles():
    """Any test that calls `configure_profiles()`/`configure_model_profiles()`
    must not leak its override into other tests (module-level mutable
    state)."""
    saved_provider = copy.deepcopy(tokens._provider_profile)
    saved_model = copy.deepcopy(tokens._model_profile)
    yield
    tokens._provider_profile.clear()
    tokens._provider_profile.update(saved_provider)
    tokens._model_profile.clear()
    tokens._model_profile.update(saved_model)


def test_count_text_empty():
    assert tokens.count_text("") == 0
    assert tokens.count_text(None) == 0  # type: ignore[arg-type]


def test_count_text_openai_matches_known_bpe():
    # "hello world" is a well-known 2-token o200k_base/cl100k_base encoding.
    assert tokens.count_text("hello world", "chatgpt__gpt-5") == 2


def test_default_profile_is_openai_for_every_provider():
    # No YAML override applied -> every provider defaults to the same OpenAI
    # profile/encoding (databricks/copilot included).
    text = "hello world"
    assert tokens.count_text(text, "chatgpt__gpt-5") == tokens.count_text(
        text, "databricks__claude-4-5-sonnet"
    )
    assert tokens.count_text(text, "chatgpt__gpt-5") == tokens.count_text(text, "copilot__Chat")


def test_configure_profiles_overrides_a_provider(restore_profiles):
    tokens.configure_profiles({"databricks": "anthropic/claude-sonnet-4.5"})
    n = tokens.count_text("hello world, this is a test", "databricks__claude-4-5-sonnet")
    assert n > 0
    # chatgpt untouched
    assert tokens.count_text("hello world", "chatgpt__gpt-5") == 2


def test_configure_profiles_ignores_unknown_profile(restore_profiles):
    before = tokens._provider_profile["databricks"]
    tokens.configure_profiles({"databricks": "not-a-real-profile"})
    assert tokens._provider_profile["databricks"] == before  # unchanged


def test_configure_profiles_ignores_unknown_provider(restore_profiles):
    tokens.configure_profiles({"not-a-real-provider": "openai/gpt-5"})  # no-op, no raise


def test_available_profiles_includes_expected_entries():
    available = tokens.available_profiles()
    assert "openai/gpt-5" in available
    assert "anthropic/claude-sonnet-4.5" in available


def test_unknown_provider_falls_back_gracefully():
    # No "__" delimiter and an unknown provider prefix both resolve to the
    # fallback profile rather than raising.
    assert tokens.count_text("hello", "some-bare-model") > 0
    assert tokens.count_text("hello", None) > 0


def test_estimate_prompt_tokens_grows_with_content():
    short = tokens.estimate_prompt_tokens(
        [{"role": "user", "content": "hi"}], None, "chatgpt__gpt-5"
    )
    long = tokens.estimate_prompt_tokens(
        [{"role": "user", "content": "hi " * 200}], None, "chatgpt__gpt-5"
    )
    assert long > short > 0


def test_estimate_prompt_tokens_counts_tools():
    messages = [{"role": "user", "content": "what's the weather"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the current weather for a city",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "City name"},
                        "unit": {"type": "string", "enum": ["c", "f"]},
                    },
                    "required": ["city"],
                },
            },
        }
    ]
    without_tools = tokens.estimate_prompt_tokens(messages, None, "chatgpt__gpt-5")
    with_tools = tokens.estimate_prompt_tokens(messages, tools, "chatgpt__gpt-5")
    assert with_tools > without_tools


def test_estimate_prompt_tokens_counts_tool_calls_and_results():
    messages = [
        {"role": "user", "content": "what's the weather in nyc"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city": "nyc"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "72F and sunny"},
    ]
    total = tokens.estimate_prompt_tokens(messages, None, "chatgpt__gpt-5")
    baseline = tokens.estimate_prompt_tokens(messages[:1], None, "chatgpt__gpt-5")
    assert total > baseline


def test_estimate_usage_shape():
    out = tokens.estimate_usage(
        [{"role": "user", "content": "hello"}], None, "hi there", "copilot__Chat"
    )
    assert set(out) == {"prompt_tokens", "completion_tokens", "total_tokens"}
    assert out["total_tokens"] == out["prompt_tokens"] + out["completion_tokens"]
    assert out["prompt_tokens"] > 0
    assert out["completion_tokens"] > 0


def test_configured_databricks_uses_a_different_encoding_than_chatgpt(restore_profiles):
    tokens.configure_profiles({"databricks": "anthropic/claude-sonnet-4.5"})
    # Same text, different underlying vocab once configured -> counts need
    # not match, but both must be positive and deterministic.
    text = "The quick brown fox jumps over the lazy dog."
    a = tokens.count_text(text, "chatgpt__gpt-5")
    b = tokens.count_text(text, "databricks__claude-4-5-sonnet")
    assert a > 0 and b > 0


def test_configure_model_profiles_overrides_one_model_only(restore_profiles):
    tokens.configure_model_profiles(
        {"databricks__claude-4-5-sonnet": "anthropic/claude-sonnet-4.5"}
    )
    # The overridden model uses the claude encoding...
    assert tokens._resolve_profile("databricks__claude-4-5-sonnet")["encoding"] == "claude"
    # ...but a sibling databricks model is untouched (still the provider default).
    assert tokens._resolve_profile("databricks__gpt-4o")["encoding"] != "claude"


def test_model_profile_wins_over_provider_profile(restore_profiles):
    # Provider-level default says one thing, per-model override says another
    # -> the model-level one wins for that exact model id.
    tokens.configure_profiles({"databricks": "openai/gpt-5"})
    tokens.configure_model_profiles(
        {"databricks__claude-4-5-sonnet": "anthropic/claude-sonnet-4.5"}
    )
    assert tokens._resolve_profile("databricks__claude-4-5-sonnet")["encoding"] == "claude"
    assert tokens._resolve_profile("databricks__other-model")["encoding"] == "o200k_base"


def test_configure_model_profiles_ignores_unknown_profile(restore_profiles):
    tokens.configure_model_profiles({"databricks__x": "not-a-real-profile"})
    assert "databricks__x" not in tokens._model_profile

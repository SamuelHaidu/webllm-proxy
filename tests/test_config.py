"""utils.config: YAML + pydantic parsing."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from webllm_proxy.utils.config import Config, load_config


def test_load_sample_config(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        """
server:
  host: 0.0.0.0
  port: 6000
providers:
  chatgpt:
    enabled: true
  databricks:
    enabled: true
    workspace_url: "https://x.databricks.com/?o=123"
""",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.server.port == 6000
    assert cfg.enabled_providers() == ["chatgpt", "databricks"]
    assert cfg.providers.databricks.workspace_url.endswith("o=123")


def test_defaults_when_empty(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("", encoding="utf-8")
    cfg = load_config(p)
    assert isinstance(cfg, Config)
    assert cfg.server.port == 5100
    assert cfg.enabled_providers() == []


def test_profile_dir_override(tmp_path):
    # Compare Path == Path (both go through the same OS-native separator
    # normalization), not Path == a hardcoded POSIX-style string -- the
    # latter fails on Windows, where Path("/tmp/x") stringifies with
    # backslashes.
    cfg = Config.model_validate({"providers": {"chatgpt": {"profile_dir": "/tmp/x"}}})
    assert cfg.profile_dir("chatgpt") == Path("/tmp/x")


def test_tokenizer_defaults_to_openai_for_every_provider():
    cfg = Config()
    assert cfg.providers.chatgpt.tokenizer == "openai/gpt-5"
    assert cfg.providers.databricks.tokenizer == "openai/gpt-5"
    assert cfg.providers.copilot.tokenizer == "openai/gpt-5"
    assert cfg.tokenizer_profiles() == {
        "chatgpt": "openai/gpt-5",
        "databricks": "openai/gpt-5",
        "copilot": "openai/gpt-5",
    }


def test_tokenizer_override():
    cfg = Config.model_validate(
        {"providers": {"databricks": {"tokenizer": "anthropic/claude-sonnet-4.5"}}}
    )
    assert cfg.providers.databricks.tokenizer == "anthropic/claude-sonnet-4.5"
    assert cfg.tokenizer_profiles()["databricks"] == "anthropic/claude-sonnet-4.5"


def test_tokenizer_rejects_unknown_profile():
    with pytest.raises(ValidationError, match="unknown tokenizer profile"):
        Config.model_validate({"providers": {"chatgpt": {"tokenizer": "not-a-real-profile"}}})


def test_per_model_tokenizer_override():
    cfg = Config.model_validate(
        {
            "providers": {
                "databricks": {
                    "tokenizer": "openai/gpt-5",  # provider default
                    "models": {"claude-4-5-sonnet": {"tokenizer": "anthropic/claude-sonnet-4.5"}},
                }
            }
        }
    )
    assert cfg.providers.databricks.models["claude-4-5-sonnet"].tokenizer == (
        "anthropic/claude-sonnet-4.5"
    )
    assert cfg.model_tokenizer_overrides() == {
        "databricks__claude-4-5-sonnet": "anthropic/claude-sonnet-4.5"
    }
    # Provider-level default is untouched, still separate from the per-model one.
    assert cfg.tokenizer_profiles()["databricks"] == "openai/gpt-5"


def test_per_model_tokenizer_rejects_unknown_profile():
    with pytest.raises(ValidationError, match="unknown tokenizer profile"):
        Config.model_validate(
            {"providers": {"databricks": {"models": {"x": {"tokenizer": "not-a-real-profile"}}}}}
        )


def test_model_tokenizer_overrides_empty_by_default():
    assert Config().model_tokenizer_overrides() == {}


def test_system_prompt_none_by_default():
    """The proxy sends no system prompt at all unless the operator configures
    one -- provider default and per-model override both null."""
    assert Config().providers.chatgpt.system_prompt_for("gpt-5-mini") is None
    assert Config().providers.chatgpt.system_prompt_for(None) is None


def test_system_prompt_provider_default():
    cfg = Config.model_validate({"providers": {"chatgpt": {"system_prompt": "webui_agent_prompt"}}})
    assert cfg.providers.chatgpt.system_prompt_for("gpt-5-mini") == "webui_agent_prompt"
    assert cfg.providers.chatgpt.system_prompt_for(None) == "webui_agent_prompt"


def test_system_prompt_per_model_override_wins():
    cfg = Config.model_validate(
        {
            "providers": {
                "databricks": {
                    "system_prompt": "databricks_default_system_prompt",
                    "models": {
                        "claude-4-5-sonnet": {
                            "tokenizer": "anthropic/claude-sonnet-4.5",
                            "system_prompt": "webui_agent_prompt",
                        }
                    },
                }
            }
        }
    )
    assert cfg.providers.databricks.system_prompt_for("claude-4-5-sonnet") == "webui_agent_prompt"
    # A model with no override falls back to the provider-level default.
    assert cfg.providers.databricks.system_prompt_for("gpt-41-2025-04-14") == (
        "databricks_default_system_prompt"
    )


def test_user_suffix_none_by_default():
    """No text is appended to the user turn unless the operator configures
    one -- provider default and per-model override both null."""
    assert Config().providers.chatgpt.user_suffix_for("gpt-5-mini") is None
    assert Config().providers.chatgpt.user_suffix_for(None) is None


def test_user_suffix_provider_default():
    cfg = Config.model_validate({"providers": {"chatgpt": {"user_suffix": "webui_agent_prompt"}}})
    assert cfg.providers.chatgpt.user_suffix_for("gpt-5-mini") == "webui_agent_prompt"
    assert cfg.providers.chatgpt.user_suffix_for(None) == "webui_agent_prompt"


def test_user_suffix_per_model_override_wins():
    cfg = Config.model_validate(
        {
            "providers": {
                "databricks": {
                    "user_suffix": "style_rules",
                    "models": {
                        "claude-4-5-sonnet": {
                            "tokenizer": "anthropic/claude-sonnet-4.5",
                            "user_suffix": "webui_agent_prompt",
                        }
                    },
                }
            }
        }
    )
    assert cfg.providers.databricks.user_suffix_for("claude-4-5-sonnet") == "webui_agent_prompt"
    # A model with no override falls back to the provider-level default.
    assert cfg.providers.databricks.user_suffix_for("gpt-41-2025-04-14") == "style_rules"

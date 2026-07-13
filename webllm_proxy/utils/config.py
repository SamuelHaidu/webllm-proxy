"""Config for the unified server: parse `webllm-proxy.yaml` with pyyaml and
validate/type it with pydantic. One server, one port, N enabled providers."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from . import env, tokens
from .openai import join_model


def _check_tokenizer(v: str) -> str:
    available = tokens.available_profiles()
    if v not in available:
        raise ValueError(f"unknown tokenizer profile {v!r}; choose one of: {', '.join(available)}")
    return v


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 5100


class ModelConfig(BaseModel):
    """Per-model override, keyed by the model's slug (the part after
    `<provider>__` in its wire id) under `providers.<name>.models`."""

    # Which vendored BPE-vocab + chat-overhead profile (see
    # `utils.tokens.available_profiles()`) this specific model's `usage`
    # estimate uses -- overrides the provider-level `tokenizer` default for
    # just this one model.
    tokenizer: str

    @field_validator("tokenizer")
    @classmethod
    def _known_tokenizer(cls, v: str) -> str:
        return _check_tokenizer(v)


class ProviderConfigBase(BaseModel):
    enabled: bool = False
    headless: bool = True
    profile_dir: str | None = None
    # Which vendored BPE-vocab + chat-overhead profile (see
    # `utils.tokens.available_profiles()`) this provider's `usage` estimate
    # uses by default. Default is OpenAI's current flagship for every
    # provider; set it explicitly (e.g. "anthropic/claude-sonnet-4.5") when
    # you know what model is actually behind the provider, so counts come
    # out right. Use `models` below instead for a per-model override (e.g.
    # one databricks model routes to Claude, another to GPT).
    tokenizer: str = "openai/gpt-5"
    # Per-model overrides, keyed by slug (e.g. "claude-4-5-sonnet" for the
    # wire id "databricks__claude-4-5-sonnet") -- wins over `tokenizer` above
    # for that one model.
    models: dict[str, ModelConfig] = Field(default_factory=dict)

    @field_validator("tokenizer")
    @classmethod
    def _known_tokenizer(cls, v: str) -> str:
        return _check_tokenizer(v)


class ChatgptConfig(ProviderConfigBase):
    pass


class DatabricksConfig(ProviderConfigBase):
    workspace_url: str = ""
    style_rules: bool = True


class CopilotConfig(ProviderConfigBase):
    edition: str = "m365"
    url: str | None = None


class ProvidersConfig(BaseModel):
    chatgpt: ChatgptConfig = Field(default_factory=ChatgptConfig)
    databricks: DatabricksConfig = Field(default_factory=DatabricksConfig)
    copilot: CopilotConfig = Field(default_factory=CopilotConfig)


class Config(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)

    def enabled_providers(self) -> list[str]:
        out = []
        for name in ("chatgpt", "databricks", "copilot"):
            if getattr(self.providers, name).enabled:
                out.append(name)
        return out

    def profile_dir(self, provider: str) -> Path:
        pc = getattr(self.providers, provider)
        override = getattr(pc, "profile_dir", None)
        if override:
            return Path(override)
        return env.data_dir(f"{provider}-proxy") / "profile"

    def tokenizer_profiles(self) -> dict[str, str]:
        """`{provider: tokenizer}` for every known provider -- feeds
        `utils.tokens.configure_profiles()` (the provider-level default) at
        server boot."""
        return {
            name: getattr(self.providers, name).tokenizer
            for name in ("chatgpt", "databricks", "copilot")
        }

    def model_tokenizer_overrides(self) -> dict[str, str]:
        """`{"<provider>__<slug>": tokenizer}` for every per-model override --
        feeds `utils.tokens.configure_model_profiles()` at server boot."""
        out: dict[str, str] = {}
        for name in ("chatgpt", "databricks", "copilot"):
            for slug, override in getattr(self.providers, name).models.items():
                out[join_model(name, slug)] = override.tokenizer
        return out


def load_config(path: str | Path) -> Config:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"config file not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return Config.model_validate(raw)

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
    # Name of a `prompts/system_prompts/<name>.md` file (looked up via
    # `utils.prompts.default_store`) to send as this model's system prompt --
    # overrides the provider-level `system_prompt` below for just this one
    # model. `None` (the default) falls back to the provider-level setting.
    system_prompt: str | None = None
    # Literal text (typed directly here, NOT a `prompts/system_prompts/`
    # file name) appended to the end of the CURRENT turn's user message
    # before it's sent upstream -- a per-turn "stay in character/role" nudge,
    # since long web-UI chats can otherwise drift a model out of its
    # assigned role. Overrides the provider-level `user_suffix` below for
    # just this one model. `None` (the default) falls back to the
    # provider-level setting.
    user_suffix: str | None = None

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
    # Name of a `prompts/system_prompts/<name>.md` file to send as this
    # provider's default system prompt. `None` (the default) means the proxy
    # sends NO system prompt at all -- the client's own system messages are
    # always ignored (see providers' code); a system prompt is only ever sent
    # when explicitly configured here or per-model below.
    system_prompt: str | None = None
    # Literal text (typed directly here, NOT a `prompts/system_prompts/`
    # file name) appended to the end of the CURRENT turn's user message
    # before it's sent upstream -- a per-turn reminder (e.g. "stay in
    # character/role") rather than a one-time system prompt, since long
    # web-UI chats can otherwise drift a model out of its assigned role over
    # many turns. `None` (the default) appends nothing. Per-model override:
    # `models.<slug>.user_suffix`.
    user_suffix: str | None = None
    # Load the user's *installed* Chrome extensions into this provider's stealth
    # browser profile. Opt-in and conservative: only the public `Extensions/`
    # folder of the chosen Chrome profile is ever read (never cookies, saved
    # passwords, or `Local State`). The read+copy happens on an explicit step
    # (`webllm-proxy login` / `webllm-proxy import-extensions`); the server only
    # loads what was already copied. See `utils/chrome_import.py`.
    import_chrome_extensions: bool = False
    # Which installed-Chrome profile to import extensions from (e.g. "Default",
    # "Profile 1"). Only used when `import_chrome_extensions` is true.
    chrome_profile: str = "Default"
    # Override the auto-detected Chrome "User Data" dir (Windows:
    # `%LOCALAPPDATA%\Google\Chrome\User Data`). `None` auto-detects. Only used
    # when `import_chrome_extensions` is true.
    chrome_user_data_dir: str | None = None
    # Which browser drives this provider. "stealth" (default) = the bundled
    # anti-detect CloakBrowser (its own isolated profile). "edge"/"chrome" =
    # your *installed* Edge/Chrome opening its *real* profile directly, so every
    # extension and login already works with no copying. Trade-offs: no stealth
    # (use for databricks/copilot, NOT chatgpt's anti-bot) and the browser must
    # be fully closed first. See `utils/system_browser.py`.
    browser: str = "stealth"
    # Which profile of the installed browser to open (e.g. "Default",
    # "Profile 1"). Only used when `browser` is "edge"/"chrome".
    browser_profile: str = "Default"
    # Override the installed browser's "User Data" dir (Windows Edge:
    # `%LOCALAPPDATA%\Microsoft\Edge\User Data`). `None` auto-detects. Only used
    # when `browser` is "edge"/"chrome".
    browser_user_data_dir: str | None = None

    @field_validator("tokenizer")
    @classmethod
    def _known_tokenizer(cls, v: str) -> str:
        return _check_tokenizer(v)

    @field_validator("browser")
    @classmethod
    def _known_browser(cls, v: str) -> str:
        if v not in ("stealth", "edge", "chrome"):
            raise ValueError(f"unknown browser {v!r}; choose one of: stealth, edge, chrome")
        return v

    def system_prompt_for(self, slug: str | None) -> str | None:
        """Resolve which named prompt (if any) to send for `slug`: the
        per-model override wins, else the provider-level default, else
        `None` (send nothing)."""
        if slug:
            override = self.models.get(slug)
            if override is not None and override.system_prompt:
                return override.system_prompt
        return self.system_prompt

    def user_suffix_for(self, slug: str | None) -> str | None:
        """Same resolution order as `system_prompt_for`, but the result is
        literal text to append, not a prompt-file name to look up: per-model
        override wins, else the provider-level default, else `None` (append
        nothing)."""
        if slug:
            override = self.models.get(slug)
            if override is not None and override.user_suffix:
                return override.user_suffix
        return self.user_suffix


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
